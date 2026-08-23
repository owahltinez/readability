"""Command-line interface for readability."""

import logging
import os
import sys
from collections.abc import Sequence

import click

from readability.checking import check_paths
from readability.guide import (
    LANGUAGE_MAP,
    get_guide,
    get_guides_dir,
    get_local_path,
    refresh_guide,
)
from readability.outline import _echo_outline, _select_section

logger = logging.getLogger("readability")


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def cli(ctx: click.Context, verbose: bool) -> None:
    """Pulls the latest Google style guide in markdown format."""
    if verbose:
        logger.setLevel(logging.DEBUG)


@cli.command()
@click.argument("language", required=False)
@click.argument("reference", required=False, metavar="[REF]")
@click.option(
    "--full",
    is_flag=True,
    help="Print the whole guide, for grepping rather than reading.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def guide(
    language: str | None,
    reference: str | None,
    full: bool,
    verbose: bool,
) -> None:
    """Read the Google style guide for LANGUAGE.

    With no LANGUAGE, lists the languages that have one. With no REF, prints
    the guide's outline, which is a few kilobytes where the guide itself can
    be two hundred. REF then names a section to print: heading text, a
    parent-scoped path ('Imports > Decision'), or an index from the outline
    ('2.2.1').
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    # No language names no guide, so the useful answer is which ones exist
    if not language:
        _echo_languages()
        return

    # Refusing beats a silent precedence rule the caller cannot see
    if full and reference:
        raise click.UsageError("--full takes the whole guide, so REF cannot.")

    logger.info("Processing style guide for: %s", language)

    try:
        content = get_guide(language)

        if full:
            click.echo(content)
        elif reference:
            click.echo(_select_section(content, reference, language))
        else:
            _echo_outline(content, language)

    except (click.ClickException, click.UsageError) as e:
        logger.error("Execution failed: %s", e)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _resolve_filenames(languages: Sequence[str]) -> list[str]:
    """Map languages to the guides that back them.

    Args:
        languages: Language names or aliases, or empty for every guide.

    Returns:
        The guide filenames, deduplicated because aliases share a guide.

    Raises:
        click.UsageError: If a language has no guide.
    """
    if not languages:
        return sorted(set(LANGUAGE_MAP.values()))

    filenames = []
    for language in languages:
        filename = LANGUAGE_MAP.get(language.lower())
        if not filename:
            raise click.UsageError(
                f"Language '{language}' is not supported. Supported "
                f"languages: {', '.join(sorted(LANGUAGE_MAP.keys()))}"
            )
        # Aliases share a guide, fetched once however many names were given
        if filename not in filenames:
            filenames.append(filename)

    return filenames


@cli.command()
@click.argument("languages", nargs=-1, metavar="[LANGUAGE]...")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def sync(languages: Sequence[str], verbose: bool) -> None:
    """Refetch style guides from the web, replacing the local copies.

    With no LANGUAGE every guide is refetched; naming one or more refreshes
    just those.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    filenames = _resolve_filenames(languages)
    click.echo(f"Synchronizing {len(filenames)} style guide(s)...", err=True)

    if not os.path.exists(get_guides_dir()):
        os.makedirs(get_guides_dir(), exist_ok=True)

    success_count = 0
    failure_count = 0

    for filename in filenames:
        click.echo(f"Syncing {filename}...", err=True)
        try:
            refresh_guide(filename)
            click.echo(f"  synced to {get_local_path(filename)}", err=True)
            success_count += 1
        except Exception as e:
            logger.error("Failed to sync %s: %s", filename, e)
            failure_count += 1

    click.echo(
        f"Sync complete. Successes: {success_count}, Failures: {failure_count}",
        err=True,
    )


def _echo_languages() -> None:
    """Print the languages that have a guide, with their aliases."""
    # Group languages by their target guide so aliases share one line
    guides: dict[str, list[str]] = {}
    for lang, filename in LANGUAGE_MAP.items():
        guides.setdefault(filename, []).append(lang)

    click.echo("Supported languages and their aliases:")
    for filename in sorted(guides.keys()):
        aliases = sorted(guides[filename])
        cached = " [cached]" if os.path.exists(get_local_path(filename)) else ""
        click.echo(f"  - {', '.join(aliases)}{cached}")


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--fix", is_flag=True, help="Automatically fix issues if possible."
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def check(paths: Sequence[str], fix: bool, verbose: bool) -> None:
    """Run relevant formatters and linters for given paths.

    Exits with a non-zero status code if any tool reports findings, so the
    command can gate scripts and CI.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    report = check_paths(paths, fix=fix)

    # Coverage the caller does not know is missing reads as coverage
    if report.skipped:
        click.echo(
            f"Warning: not installed, so not run: "
            f"{', '.join(sorted(report.skipped))}.",
            err=True,
        )

    # A tool that never ran checked nothing, so its silence is not a pass
    if report.failed:
        click.echo(
            f"Error: Could not run: {', '.join(sorted(report.failed))}. "
            "Their findings, if any, are unknown.",
            err=True,
        )
        sys.exit(1)

    # Findings fail the check even if the tool processed no files
    if report.findings:
        sys.exit(1)

    # Having run nothing is not a pass, it is a silent no-op gating nothing
    if not report.ran and report.skipped:
        click.echo(
            f"Error: Every tool for {len(paths)} path(s) is missing, so "
            "nothing was verified. Install them, or pass paths they cover.",
            err=True,
        )
        sys.exit(1)

    # No tool applying is a fact, not a fault, but still not a clean result
    if not report.ran:
        click.echo(
            f"No tool applies to {len(paths)} path(s); nothing was checked.",
            err=True,
        )
        return

    checked_path_count = len(paths) - len(report.unverified_paths)
    if report.unverified_paths:
        click.echo(
            f"Warning: nothing was checked for "
            f"{len(report.unverified_paths)} path(s): "
            f"{', '.join(map(str, report.unverified_paths))}.",
            err=True,
        )

    # Without this a clean run printed nothing and read as a no-op
    click.echo(
        f"No findings in {checked_path_count} path(s) "
        f"({', '.join(sorted(report.ran))}).",
        err=True,
    )


# Main entry point for the CLI
def main() -> None:
    """Main entry point for the CLI."""
    # Configured here, not at import, so library use has no side effects
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    cli()


if __name__ == "__main__":
    main()
