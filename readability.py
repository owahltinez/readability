"""CLI for fetching Google style guides and running code quality tools."""

import dataclasses
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
import warnings
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Optional

import click
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from markdownify import markdownify as md

# Suppress BeautifulSoup warning when parsing XML as HTML
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger("readability")

# Default timeout for subprocess calls in seconds
DEFAULT_TIMEOUT = 60


def get_guides_dir() -> str:
    """Get the directory where style guides are cached.

    Defaults to 'guides/' in the same directory as this script, but can be
    overridden by the READABILITY_CACHE environment variable.

    Returns:
        The path to the guides directory.
    """
    return os.getenv("READABILITY_CACHE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "guides"
    )


# Mapping of languages to their Google Style Guide file paths
LANGUAGE_MAP = {
    "python": "pyguide.md",
    "shell": "shellguide.md",
    "objc": "objcguide.md",
    "objective-c": "objcguide.md",
    "r": "Rguide.md",
    "csharp": "csharp-style.md",
    "c#": "csharp-style.md",
    "docguide": "docguide/style.md",
    "markdown": "docguide/style.md",
    "go": "go/guide.md",
    "cpp": "cppguide.html",
    "c++": "cppguide.html",
    "java": "javaguide.html",
    "js": "jsguide.html",
    "javascript": "jsguide.html",
    "ts": "tsguide.html",
    "typescript": "tsguide.html",
    "html": "htmlcssguide.html",
    "css": "htmlcssguide.html",
    "json": "jsoncstyleguide.xml",
    "vim": "vimscriptguide.xml",
}

BASE_URL = "https://google.github.io/styleguide/"


def get_guide_content(url: str) -> str:
    """Fetch raw content from the specified URL.

    Args:
        url: The URL to fetch content from.

    Returns:
        The raw text content from the URL.

    Raises:
        click.ClickException: If the HTTP request fails.
    """
    logger.info("Fetching style guide from %s", url)

    # Perform the HTTP GET request with a timeout
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch content from %s: %s", url, e)
        raise click.ClickException(
            f"Failed to fetch style guide from {url}: {e}"
        )

    return response.text


def convert_to_markdown(content: str, filename: str) -> str:
    """Convert the raw content to markdown based on file extension.

    Args:
        content: The raw text content to convert.
        filename: The original filename to determine conversion logic.

    Returns:
        The converted markdown content.
    """
    logger.debug("Converting content for %s", filename)

    # Handle Markdown files directly
    if filename.endswith(".md"):
        return content

    # Strip XML prologue if present to avoid it leaking into the output
    if content.lstrip().startswith("<?xml"):
        content = content.split("?>", 1)[-1].lstrip()

    # Handle XML files (used for Vim script guide and JSON style guide)
    if filename.endswith(".xml"):
        soup = BeautifulSoup(content, "html.parser")

        # Add titles as headers
        guide = soup.find("guide")
        if guide:
            title = guide.get("title")
            if isinstance(title, str) and title:
                h1 = soup.new_tag("h1")
                h1.string = title
                guide.insert(0, h1)

        for category in soup.find_all("category"):
            title = category.get("title")
            if isinstance(title, str) and title:
                h2 = soup.new_tag("h2")
                h2.string = title
                category.insert(0, h2)

        for sp in soup.find_all("stylepoint"):
            title = sp.get("title")
            if isinstance(title, str) and title:
                h3 = soup.new_tag("h3")
                h3.string = title
                sp.insert(0, h3)

        for summary in soup.find_all("summary"):
            summary.name = "p"
            # Wrap content in strong tags
            content_str = summary.decode_contents()
            summary.clear()
            strong = soup.new_tag("strong")
            strong.append(BeautifulSoup(content_str, "html.parser"))
            summary.append(strong)

        for snippet in soup.find_all(["code_snippet", "bad_code_snippet"]):
            is_bad = snippet.name == "bad_code_snippet"
            snippet.name = "pre"
            code = soup.new_tag("code")
            code.string = snippet.get_text()
            snippet.clear()
            if is_bad:
                p = soup.new_tag("p")
                strong = soup.new_tag("strong")
                strong.string = "BAD:"
                p.append(strong)
                snippet.append(p)
            snippet.append(code)

        # Convert the modified soup to string and then to markdown
        return md(str(soup), heading_style="ATX")

    # Handle HTML files by converting them to Markdown
    if filename.endswith(".html"):
        return md(content, heading_style="ATX")

    # Fallback to returning raw content
    return content


def get_local_path(filename: str) -> str:
    """Get the local path for a given style guide filename.

    Flattens the filename by replacing path separators with dashes and ensures
    the file has a .md extension for uniform storage.

    Args:
        filename: The original filename or relative path from the style guide
            repository (e.g., 'pyguide.md' or 'go/guide.md').

    Returns:
        The full local path to the cached markdown file.
    """
    # Flatten the filename by replacing '/' with '-'
    flattened = filename.replace("/", "-")
    # Use the flattened filename and change extension to .md for uniform storage
    base_name = flattened.rsplit(".", 1)[0]
    return os.path.join(get_guides_dir(), f"{base_name}.md")


def get_guide(language: str, remote: bool = False) -> str:
    """Orchestrate fetching and converting the style guide for a given language.

    Args:
        language: The language to fetch the guide for.
        remote: Whether to force fetching from the web instead of local cache.

    Returns:
        The markdown content of the style guide.

    Raises:
        click.UsageError: If the language is not supported.
    """
    # Look up the filename in the mapping
    filename = LANGUAGE_MAP.get(language.lower())
    if not filename:
        error_msg = f"Language '{language}' is not supported."
        logger.warning(error_msg)
        raise click.UsageError(
            f"{error_msg} Supported languages: "
            f"{', '.join(sorted(LANGUAGE_MAP.keys()))}"
        )

    local_path = get_local_path(filename)

    # If remote is False, check for local file first
    if not remote and os.path.exists(local_path):
        logger.info("Reading style guide from local file: %s", local_path)
        with open(local_path, "r", encoding="utf-8") as f:
            return f.read()

    # Build the full URL and fetch the raw content
    url = f"{BASE_URL}{filename}"
    content = get_guide_content(url)

    # Convert the content to Markdown format
    markdown_content = convert_to_markdown(content, filename)

    # Save to local cache for future use
    if not os.path.exists(get_guides_dir()):
        os.makedirs(get_guides_dir(), exist_ok=True)

    with open(local_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    logger.debug("Cached style guide locally: %s", local_path)

    return markdown_content


# A fence opens or closes a code block; anything inside is sample code, not
# document structure. Guides for `#`-commented languages (shell, Python) would
# otherwise report hundreds of code comments as headings.
FENCE_PATTERN = re.compile(r"^\s{0,3}(?:```|~~~)")

# Closing hashes are optional in ATX headings and must be space-separated, so
# a title such as 'C#' keeps its trailing character.
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")

# A section number the document itself prints, e.g. '2.2' in '2.2 Imports'.
DOCUMENT_NUMBER_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")

# Guides put a heading's link targets on the lines above it, which would
# otherwise trail the end of the preceding section.
ANCHOR_PATTERN = re.compile(r'^<a id="[^"]*"></a>$')

# The separator in a scoped reference such as 'Imports > Decision'. Spaces on
# both sides are required so that a heading like '`Array<T>` Type' stays whole.
PATH_SEPARATOR_PATTERN = re.compile(r"\s+>\s+")


@dataclasses.dataclass(frozen=True)
class Heading:
    """A Markdown heading found in a style guide.

    Attributes:
        level: Heading depth, 1 for '#' through 6 for '######'.
        text: The heading as written, including any number the guide prints.
        index: Positional index derived from the heading tree ('2.2.1'), unique
            within the guide. Empty for the document title.
        number: The section number the document prints itself, or an empty
            string for the eleven guides that number nothing.
        title: The heading text without the number the document prints.
        line: Zero-based index of the heading's line within the guide.
    """

    level: int
    text: str
    index: str
    number: str
    title: str
    line: int


def _iter_heading_lines(content: str) -> Iterator[tuple[int, int, str]]:
    """Yield the headings of a Markdown document, skipping fenced code.

    This is a heading scanner, NOT a Markdown parser, and must not grow into
    one. It is sound only because the job is narrow and the input is known:
    block-level ATX headings, over the fourteen guides shipped here, every
    one of which is asserted in the test suite. Fenced code is skipped
    because a guide whose language comments with '#' would otherwise offer
    145 fragments of sample code as sections.

    Reach for a real CommonMark parser (mistletoe has no dependencies of its
    own; markdown-it-py is far more widely deployed) the moment anything
    needs more than this: inline structure such as links or emphasis, block
    nesting, setext headings, indented code blocks, or Markdown from a
    source other than these guides. Extending the regexes to cover those is
    how a scanner turns into a bad parser.

    Args:
        content: The full Markdown text of a style guide.

    Yields:
        Tuples of (line index, heading level, heading text).
    """
    in_fence = False
    for line_number, line in enumerate(content.splitlines()):
        # Fences toggle: a line inside one is sample code whatever it says
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        match = HEADING_PATTERN.match(line)
        if match:
            yield line_number, len(match.group(1)), match.group(2).strip()


def _next_index(open_headings: list[list[Any]], level: int) -> str:
    """Advance the heading tree by one heading and render its index.

    Numbering counts siblings under a shared parent rather than counting each
    heading level independently. Guides skip levels (an h3 following an h4),
    which a per-level counter would give the same index twice.

    Args:
        open_headings: Mutable stack of [level, index, child count] for the
            headings still open above this one, updated in place. It starts
            with a level 0 root so that the top level always has a parent.
        level: The level of the heading being numbered.

    Returns:
        The dotted positional index for the heading, e.g. '2.2.1'.
    """
    # Close every heading this one is not nested inside of, leaving its parent
    while open_headings[-1][0] >= level:
        open_headings.pop()

    parent = open_headings[-1]
    parent[2] += 1
    index = f"{parent[1]}.{parent[2]}" if parent[1] else str(parent[2])

    open_headings.append([level, index, 0])
    return index


def _split_document_number(text: str, numbered: bool) -> tuple[str, str]:
    """Separate the number a guide prints in a heading from its title.

    Args:
        text: The heading text as written.
        numbered: Whether the document numbers its headings at all. Guides that
            do not still have headings that open with a digit (C++'s '0 and
            nullptr/NULL'), and those digits are part of the title.

    Returns:
        A tuple of (number, title); the number is empty when there is none.
    """
    match = DOCUMENT_NUMBER_PATTERN.match(text) if numbered else None
    if not match:
        return "", text
    return match.group(1), match.group(2).strip()


def parse_headings(content: str) -> list[Heading]:
    """Parse the heading tree of a style guide.

    Args:
        content: The full Markdown text of a style guide.

    Returns:
        The guide's headings in document order.
    """
    raw = list(_iter_heading_lines(content))
    if not raw:
        return []

    # Only three of the shipped guides number their headings. A dotted number
    # ('2.2') is the reliable signal; a bare leading digit is not.
    document_numbers = [
        match.group(1)
        for _, _, text in raw
        if (match := DOCUMENT_NUMBER_PATTERN.match(text))
    ]
    numbered = any("." in number for number in document_numbers)

    # A lone top-level heading is the document title: it roots the tree rather
    # than being the first section, so numbering starts with its children.
    top_level = min(level for _, level, _ in raw)
    top_level_lines = [line for line, level, _ in raw if level == top_level]
    title_line = top_level_lines[0] if len(top_level_lines) == 1 else None

    headings = []
    open_headings: list[list[Any]] = [[0, "", 0]]
    for line, level, text in raw:
        index = "" if line == title_line else _next_index(open_headings, level)
        number, title = _split_document_number(text, numbered)

        # A guide that numbers its own sections is the authority on what they
        # are called, so its numbers address them. A positional index would
        # drift wherever the guide skips one — pyguide has no 2.15 at all,
        # and calling its 2.16 by that name would cite a section that does
        # not exist. The tree still advances above, so headings the guide
        # leaves unnumbered keep a positional index to be reachable by.
        if number:
            index = number
        headings.append(
            Heading(
                level=level,
                text=text,
                index=index,
                number=number,
                title=title,
                line=line,
            )
        )

    return headings


def _slugify(text: str) -> str:
    """Reduce heading text to a form that survives punctuation and casing.

    Args:
        text: The text to normalize.

    Returns:
        A lowercase slug with runs of non-alphanumeric characters as dashes.
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def format_outline(
    headings: Sequence[Heading], depth: Optional[int] = None
) -> str:
    """Render a heading tree, one heading per line.

    Args:
        headings: The headings of a guide, in document order.
        depth: Maximum number of heading levels to show, or None for all.

    Returns:
        The outline as text: indentation for depth, the positional index to
        pass to --section, then the heading's own text verbatim so a rule can
        be cited exactly as the guide writes it.
    """
    if not headings:
        return ""

    top_level = min(heading.level for heading in headings)
    lines = []
    for heading in headings:
        if depth is not None and heading.level - top_level >= depth:
            continue

        # The document title roots the tree and has no index to print
        indent = "  " * (heading.level - top_level)
        prefix = f"{heading.index}  " if heading.index else ""
        # The index already carries the guide's own number where it has one,
        # so printing the heading verbatim would show it twice.
        lines.append(f"{indent}{prefix}{heading.title}")

    return "\n".join(lines)


def _matches_component(heading: Heading, component: str, exact: bool) -> bool:
    """Test one reference component against a heading.

    Args:
        heading: The heading to test.
        component: One component of a section reference.
        exact: Whether the text must match in full rather than as a substring.

    Returns:
        True if the component identifies the heading.
    """
    # Numbers are only ever compared in full; a substring of a number would
    # match unrelated sections.
    wanted = component.strip().rstrip(".")
    if wanted and wanted in {heading.index, heading.number}:
        return True

    slug = _slugify(component)
    if not slug:
        return False
    candidates = {_slugify(heading.title), _slugify(heading.text)}
    if exact:
        return slug in candidates
    return any(slug in candidate for candidate in candidates)


def _iter_ancestors(
    headings: Sequence[Heading], position: int
) -> Iterator[Heading]:
    """Yield the headings that enclose a heading, innermost first.

    Args:
        headings: The headings of a guide, in document order.
        position: Index into headings of the heading to walk out from.

    Yields:
        Each enclosing heading, skipping the sibling subtrees in between.
    """
    level = headings[position].level
    for candidate in reversed(headings[:position]):
        if candidate.level < level:
            level = candidate.level
            yield candidate


def _matches_path(
    headings: Sequence[Heading],
    position: int,
    components: Sequence[str],
    exact: bool,
) -> bool:
    """Test whether a heading and its ancestors satisfy a reference path.

    Args:
        headings: The headings of a guide, in document order.
        position: Index into headings of the candidate heading.
        components: Reference components, outermost first.
        exact: Whether text components must match in full.

    Returns:
        True if the last component matches the heading and the remaining ones
        match its ancestors, in order. Intervening ancestors may be skipped, so
        'Imports > Decision' works without naming every level in between.
    """
    if not _matches_component(headings[position], components[-1], exact):
        return False

    # Consume the outer components as the ancestors satisfying them are met
    remaining = list(components[:-1])
    for ancestor in _iter_ancestors(headings, position):
        if not remaining:
            break
        if _matches_component(ancestor, remaining[-1], exact):
            remaining.pop()

    return not remaining


def find_headings(headings: Sequence[Heading], reference: str) -> list[int]:
    """Locate the headings a section reference names.

    Args:
        headings: The headings of a guide, in document order.
        reference: A positional index ('2.2.1'), a section number the guide
            prints, heading text, or a parent-scoped path
            ('Imports > Decision').

    Returns:
        Positions of every matching heading, in document order. More than one
        means the reference is ambiguous.
    """
    components = [
        part.strip()
        for part in PATH_SEPARATOR_PATTERN.split(reference)
        if part.strip()
    ]
    if not components:
        return []

    # Prefer whole matches; fall back to substrings only when nothing matches
    # in full, so 'Imports' does not also select 'Imports and Exports'.
    for exact in (True, False):
        matches = [
            position
            for position in range(len(headings))
            if _matches_path(headings, position, components, exact)
        ]
        if matches:
            return matches

    return []


def extract_section(
    content: str, headings: Sequence[Heading], position: int
) -> str:
    """Extract one section of a guide.

    Args:
        content: The full Markdown text of the guide.
        headings: The headings of the guide, in document order.
        position: Index into headings of the section to extract.

    Returns:
        The heading and everything below it up to the next heading of the same
        or a higher level, so nested subsections travel with their parent.
    """
    lines = content.splitlines()
    heading = headings[position]

    end = len(lines)
    for following in headings[position + 1 :]:
        if following.level <= heading.level:
            end = following.line
            break

    # Shed the trailing anchors that belong to the following heading
    section = lines[heading.line : end]
    while section and (
        not section[-1].strip() or ANCHOR_PATTERN.match(section[-1].strip())
    ):
        section.pop()

    return "\n".join(section)


def _describe_heading(headings: Sequence[Heading], position: int) -> str:
    """Describe a heading by its path, for disambiguation messages.

    Args:
        headings: The headings of a guide, in document order.
        position: Index into headings of the heading to describe.

    Returns:
        The heading's title preceded by those of its ancestors, e.g.
        'Language Rules > Imports > Decision'. The document title is left out,
        since it roots every path and so tells the caller nothing.
    """
    path = [headings[position].title]
    path.extend(
        ancestor.title
        for ancestor in _iter_ancestors(headings, position)
        if ancestor.index
    )

    return " > ".join(reversed(path))


def _unique_reference(headings: Sequence[Heading], position: int) -> str:
    """Build a reference that selects one heading and no other.

    Args:
        headings: The headings of a guide, in document order.
        position: Index into headings of the heading to refer to.

    Returns:
        The heading's outline index when that is unambiguous, and its path
        otherwise. An index can collide with a number the guide prints when
        the two drift apart, and suggesting it would send the caller back to
        the same complaint.
    """
    index = headings[position].index
    if index and find_headings(headings, index) == [position]:
        return index

    path = _describe_heading(headings, position)
    return f'"{path}"'


# Beyond this many candidates a disambiguation list stops being readable
MAX_REPORTED_MATCHES = 15


def _select_section(content: str, reference: str, language: str) -> str:
    """Resolve a section reference against a guide and extract that section.

    Args:
        content: The full Markdown text of the guide.
        reference: The section reference given on the command line.
        language: The language whose guide is being read, for error messages.

    Returns:
        The Markdown of the requested section.

    Raises:
        SystemExit: If the reference matches no heading, or more than one.
    """
    headings = parse_headings(content)
    matches = find_headings(headings, reference)

    if not matches:
        click.echo(
            f"Error: Found no heading matching '{reference}' in the "
            f"'{language}' guide. Run 'readability guide {language} "
            "--outline' to list its sections.",
            err=True,
        )
        sys.exit(1)

    # Reporting every candidate beats returning the first one silently, since
    # guides repeat headings ('Decision' appears under every Python rule).
    if len(matches) > 1:
        click.echo(
            f"Error: '{reference}' matches {len(matches)} headings in the "
            f"'{language}' guide. Repeat with one of:",
            err=True,
        )
        for position in matches[:MAX_REPORTED_MATCHES]:
            suggestion = _unique_reference(headings, position)
            description = _describe_heading(headings, position)
            # A path suggestion already reads as its own description
            if suggestion.strip('"') != description:
                suggestion = f"{suggestion} ({description})"
            click.echo(f"  --section {suggestion}", err=True)
        if len(matches) > MAX_REPORTED_MATCHES:
            click.echo(
                f"  ... and {len(matches) - MAX_REPORTED_MATCHES} more",
                err=True,
            )
        sys.exit(1)

    return extract_section(content, headings, matches[0])


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def cli(ctx: click.Context, verbose: bool) -> None:
    """Pulls the latest Google style guide in markdown format."""
    if verbose:
        logger.setLevel(logging.DEBUG)


@cli.command()
@click.argument("language")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Path to save the style guide markdown.",
)
@click.option(
    "--remote", "-r", is_flag=True, help="Force fetching from the web."
)
@click.option(
    "--outline",
    is_flag=True,
    help="Print the guide's heading tree instead of its contents.",
)
@click.option(
    "--section",
    "section",
    metavar="REF",
    help=(
        "Print one section, named by heading text, a parent-scoped path "
        "('Imports > Decision'), or an outline index ('2.2.1')."
    ),
)
@click.option(
    "--depth",
    type=click.IntRange(min=1),
    help="Limit --outline to this many heading levels.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def guide(
    language: str,
    output: Optional[str],
    remote: bool,
    outline: bool,
    section: Optional[str],
    depth: Optional[int],
    verbose: bool,
) -> None:
    """Fetch the style guide for a specific LANGUAGE.

    With no other option the whole guide is printed, which for a large guide
    is better piped than read: --outline lists its sections and --section
    prints one. --outline takes precedence over --section.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Processing style guide for: %s", language)

    try:
        # Fetch and process the style guide
        markdown_content = get_guide(language, remote=remote)

        # Navigation replaces the contents with the part that was asked for,
        # so --output and stdout keep working the same way for both
        if outline:
            markdown_content = format_outline(
                parse_headings(markdown_content), depth=depth
            )
        elif section:
            markdown_content = _select_section(
                markdown_content, section, language
            )

        # Handle output: either save to file or print to stdout
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(markdown_content)
            # A write the caller asked for is an outcome, not narration, and
            # goes to stderr so it never contaminates piped guide content.
            click.echo(f"Saved to {output}", err=True)
        else:
            click.echo(markdown_content)

    except (click.ClickException, click.UsageError) as e:
        logger.error("Execution failed: %s", e)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def sync(verbose: bool) -> None:
    """Synchronize all supported style guides from the web to local storage."""
    if verbose:
        logger.setLevel(logging.DEBUG)

    click.echo("Synchronizing all style guides...", err=True)

    if not os.path.exists(get_guides_dir()):
        os.makedirs(get_guides_dir(), exist_ok=True)

    # Get unique filenames to avoid redundant downloads
    filenames = set(LANGUAGE_MAP.values())

    success_count = 0
    failure_count = 0

    for filename in sorted(filenames):
        click.echo(f"Syncing {filename}...", err=True)
        try:
            url = f"{BASE_URL}{filename}"
            content = get_guide_content(url)
            markdown_content = convert_to_markdown(content, filename)
            local_path = get_local_path(filename)

            with open(local_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            click.echo(f"  synced to {local_path}", err=True)
            success_count += 1
        except Exception as e:
            logger.error("Failed to sync %s: %s", filename, e)
            failure_count += 1

    click.echo(
        f"Sync complete. Successes: {success_count}, Failures: {failure_count}",
        err=True,
    )


@cli.command()
def languages() -> None:
    """List all supported languages and their aliases."""
    # Group languages by their target guide
    guides = {}
    for lang, filename in LANGUAGE_MAP.items():
        if filename not in guides:
            guides[filename] = []
        guides[filename].append(lang)

    click.echo("Supported languages and their aliases:")
    for filename in sorted(guides.keys()):
        aliases = sorted(guides[filename])

        # Check if the guide is cached
        local_path = get_local_path(filename)
        cached_label = " [cached]" if os.path.exists(local_path) else ""

        click.echo(f"  - {', '.join(aliases)}{cached_label}")


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

    # Resolve project root once for trigger file checking
    project_root = Path.cwd()

    # Process each provided path independently, tracking findings across
    # all of them so the exit code reflects the overall result
    found_issues = False
    for path_str in paths:
        found_issues |= _check_path(Path(path_str), project_root, fix=fix)

    if found_issues:
        sys.exit(1)

    # Findings are the only thing this command printed, so a clean run said
    # nothing at all and left the caller unable to tell it from a no-op.
    click.echo(f"No findings in {len(paths)} path(s).", err=True)


def _check_path(path: Path, project_root: Path, fix: bool = False) -> bool:
    """Apply relevant tools to a single path.

    Args:
        path: The path (file or directory) to check.
        project_root: The root of the project for trigger file discovery.
        fix: Whether to apply automatic fixes.

    Returns:
        True if any tool reported findings, False otherwise.
    """
    logger.info("Checking path: %s", path)

    # Iterate through all supported tool definitions
    found_issues = False
    for tool in _get_tool_definitions(path, project_root):
        if _should_run_tool(tool, path, project_root):
            found_issues |= _run_tool(tool["name"], tool, fix=fix)

    return found_issues


def _should_run_tool(
    tool: dict[str, Any], path: Path, project_root: Path
) -> bool:
    """Determine if a tool should run based on triggers and extensions.

    Args:
        tool: The tool configuration dictionary.
        path: The path being checked.
        project_root: The project root directory.

    Returns:
        True if the tool should run, False otherwise.
    """
    # Check if any trigger files (like pyproject.toml) exist in the project root
    has_trigger = any((project_root / t).exists() for t in tool["trigger"])

    # For files, also check if the extension matches one of the supported ones
    if path.is_file():
        return has_trigger and path.suffix in tool["extensions"]

    # For directories, the existence of a trigger file is sufficient
    return has_trigger


def _bundled_config(tool_name: str) -> Path:
    """Get the path to the bundled default configuration for a tool.

    Args:
        tool_name: The name of the tool (e.g. "ruff", "pyrefly").

    Returns:
        The path to the bundled default config file.
    """
    return Path(__file__).parent / "configs" / f"{tool_name}.toml"


def _has_project_config(
    project_root: Path, config_files: Sequence[str], tool_name: str
) -> bool:
    """Determine whether the project defines its own configuration for a tool.

    Args:
        project_root: The project root directory.
        config_files: Dedicated config filenames to look for (e.g. ruff.toml).
        tool_name: The pyproject.toml [tool.<name>] section to look for.

    Returns:
        True if the project has its own configuration, False otherwise.
    """
    # Dedicated config files take precedence over pyproject.toml sections
    if any((project_root / f).exists() for f in config_files):
        return True

    # Otherwise look for a [tool.<name>] section in pyproject.toml
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("Failed to parse %s: %s", pyproject, e)
        return False
    return tool_name in data.get("tool", {})


def _default_config_args(
    project_root: Path, config_files: Sequence[str], tool_name: str
) -> list[str]:
    """Build --config arguments pointing at the bundled defaults for a tool.

    Args:
        project_root: The project root directory.
        config_files: Dedicated config filenames the project may define.
        tool_name: The name of the tool, matching a bundled config file.

    Returns:
        --config arguments for the bundled defaults, or an empty list when the
        project defines its own configuration (which must take precedence).
    """
    if _has_project_config(project_root, config_files, tool_name):
        return []
    return ["--config", str(_bundled_config(tool_name))]


def _get_tool_definitions(
    path: Path, project_root: Path
) -> list[dict[str, Any]]:
    """Define supported tools with their triggers, extensions, and commands.

    Args:
        path: The path being checked.
        project_root: The project root, used to resolve default configurations.

    Returns:
        A list of tool configuration dictionaries.
    """
    path_str = str(path)

    # Fall back to the bundled default configs unless the project has its own
    ruff_config = _default_config_args(
        project_root, ["ruff.toml", ".ruff.toml"], "ruff"
    )
    pyrefly_config = _default_config_args(
        project_root, ["pyrefly.toml"], "pyrefly"
    )

    return [
        {
            "name": "ruff",
            "check": [
                "ruff",
                "check",
                "--force-exclude",
                *ruff_config,
                path_str,
            ],
            "check_format": [
                "ruff",
                "format",
                "--check",
                "--force-exclude",
                *ruff_config,
                path_str,
            ],
            "fix": [
                "ruff",
                "check",
                "--fix",
                "--force-exclude",
                *ruff_config,
                path_str,
            ],
            "format": [
                "ruff",
                "format",
                "--force-exclude",
                *ruff_config,
                path_str,
            ],
            "trigger": ["pyproject.toml", "ruff.toml", ".ruff.toml"],
            "extensions": [".py"],
        },
        {
            # Type checker only: it reports findings but cannot fix or format
            "name": "pyrefly",
            "check": ["pyrefly", "check", *pyrefly_config, path_str],
            "trigger": ["pyproject.toml", "pyrefly.toml"],
            "extensions": [".py"],
        },
        {
            "name": "biome",
            "check": [
                "npx",
                "-y",
                "biome",
                "lint",
                "--no-errors-on-unmatched",
                path_str,
            ],
            "check_format": [
                "npx",
                "-y",
                "biome",
                "format",
                "--no-errors-on-unmatched",
                path_str,
            ],
            "fix": [
                "npx",
                "-y",
                "biome",
                "lint",
                "--write",
                "--no-errors-on-unmatched",
                path_str,
            ],
            "format": [
                "npx",
                "-y",
                "biome",
                "format",
                "--write",
                "--no-errors-on-unmatched",
                path_str,
            ],
            "trigger": ["biome.json", "biome.jsonc"],
            "extensions": [
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".json",
                ".jsonc",
                ".css",
                ".html",
            ],
        },
        {
            "name": "prettier",
            "check_format": [
                "npx",
                "-y",
                "prettier",
                "--check",
                "--no-error-on-unmatched-pattern",
                path_str,
            ],
            "format": [
                "npx",
                "-y",
                "prettier",
                "--write",
                "--no-error-on-unmatched-pattern",
                path_str,
            ],
            "trigger": [
                ".prettierrc",
                ".prettierrc.json",
                ".prettierrc.yml",
                ".prettierrc.yaml",
                ".prettierrc.js",
                "prettier.config.js",
                "prettier.config.cjs",
            ],
            "extensions": [
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".json",
                ".css",
                ".scss",
                ".html",
                ".md",
                ".yml",
                ".yaml",
            ],
        },
        {
            "name": "go fmt",
            "check_format": ["gofmt", "-l", path_str],
            "format": ["go", "fmt", path_str],
            "trigger": ["go.mod"],
            "extensions": [".go"],
        },
    ]


def _run_tool(
    tool_name: str,
    tool_config: dict[str, Any],
    fix: bool = False,
) -> bool:
    """Orchestrate the execution of a specific formatting or linting tool.

    Args:
        tool_name: The name of the tool to run.
        tool_config: The tool configuration dictionary.
        fix: Whether to apply automatic fixes.

    Returns:
        True if the tool reported findings, False otherwise.
    """
    # Identify the primary command to check for executable availability
    cmd = (
        tool_config.get("format")
        or tool_config.get("check")
        or tool_config.get("fix")
        or tool_config.get("check_format")
    )
    if not cmd:
        return False

    executable = str(cmd[0])
    if not shutil.which(executable):
        logger.debug(
            "Tool %s (%s) not found in PATH, skipping.", tool_name, executable
        )
        return False

    logger.info("Running %s...", tool_name)
    found_issues = False
    try:
        if fix:
            # 1. Run formatters (if available) - these are expected to
            # modify files
            if "format" in tool_config:
                _execute_tool_command(tool_config["format"])

            # 2. Run fixers (if available) - these apply automatic linting fixes
            if "fix" in tool_config:
                _execute_tool_command(tool_config["fix"])
        # 1. Run check_format (if available) - check-only
        elif "check_format" in tool_config:
            logger.debug("Executing: %s", " ".join(tool_config["check_format"]))
            result = subprocess.run(
                tool_config["check_format"],
                capture_output=True,
                text=True,
                check=False,
                timeout=DEFAULT_TIMEOUT,
            )
            if result.returncode != 0 or (
                tool_name == "go fmt" and result.stdout.strip()
            ):
                found_issues = True
                click.echo(
                    f"--- {tool_name} formatting findings ---\n"
                    f"{result.stdout}\n{result.stderr}"
                )

        # 3. Run checks and report findings - these provide feedback to the user
        if "check" in tool_config:
            logger.debug("Executing: %s", " ".join(tool_config["check"]))
            result = subprocess.run(
                tool_config["check"],
                capture_output=True,
                text=True,
                check=False,
                timeout=DEFAULT_TIMEOUT,
            )
            if result.returncode != 0:
                found_issues = True
                click.echo(
                    f"--- {tool_name} findings ---\n"
                    f"{result.stdout}\n{result.stderr}"
                )

    except subprocess.CalledProcessError as e:
        logger.warning("%s failed with exit code %d", tool_name, e.returncode)
        if e.stdout:
            logger.debug("STDOUT: %s", e.stdout)
        if e.stderr:
            logger.debug("STDERR: %s", e.stderr)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Unexpected error while running %s: %s", tool_name, e)

    return found_issues


def _execute_tool_command(cmd: list[str]) -> None:
    """Execute a tool command, raising if it exits with a non-zero code.

    Args:
        cmd: The command list to execute.

    Raises:
        subprocess.CalledProcessError: If the command returns a non-zero
            exit code.
    """
    logger.debug("Executing: %s", " ".join(cmd))
    subprocess.run(
        cmd, capture_output=True, check=True, timeout=DEFAULT_TIMEOUT
    )


# Main entry point for the CLI
def main() -> None:
    """Main entry point for the CLI."""
    # Configure logging here rather than at import time so that importing this
    # module as a library (e.g. from lemming) has no side effects
    # WARNING, not INFO: everything logged below it narrates progress, which
    # is what --verbose is for. Leaving it on meant there was no quiet mode
    # and the flag could only add DEBUG on top.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    cli()


if __name__ == "__main__":
    main()
