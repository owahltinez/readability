"""Build executable plans for readability's canonical tools."""

import logging
import os
import shutil
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

logger = logging.getLogger("readability")


@dataclass(frozen=True)
class ToolPlan:
    """Commands and owned file types for one canonical tool."""

    name: str
    extensions: tuple[str, ...]
    check: tuple[str, ...] = ()
    check_format: tuple[str, ...] = ()
    fix: tuple[str, ...] = ()
    format: tuple[str, ...] = ()
    targets: tuple[str, ...] | None = None
    cwd: Path | None = None


def _should_run_tool(tool: ToolPlan, path: Path) -> bool:
    """Determine if a tool owns any files under the requested path.

    Args:
        tool: The executable tool plan.
        path: The path being checked.

    Returns:
        True if the tool should run, False otherwise.
    """
    if tool.targets is not None:
        return bool(tool.targets)
    return bool(_matching_paths(path, tool.extensions))


def _tool_is_installed(tool: ToolPlan) -> bool:
    """Report whether a tool's executable is on PATH.

    Args:
        tool: The executable plan to inspect.

    Returns:
        True if the tool can be run.
    """
    # Every command for a tool starts with the same executable, so any of
    # them answers the question
    cmd = tool.format or tool.check or tool.fix or tool.check_format
    if not cmd:
        return False

    executable = str(cmd[0])
    if not shutil.which(executable):
        logger.debug("Tool %s (%s) not found in PATH.", tool.name, executable)
        return False

    return True


def _bundled_config(tool_name: str) -> Path:
    """Get the path to the bundled default configuration for a tool.

    Args:
        tool_name: The name of the tool (e.g. "ruff", "pyrefly", "biome").

    Returns:
        The path to the bundled default config file.
    """
    filename = (
        "biome-default.json" if tool_name == "biome" else f"{tool_name}.toml"
    )
    return Path(str(files("readability").joinpath("configs", filename)))


def _has_project_config(
    directory: Path, config_files: Sequence[str], tool_name: str | None
) -> bool:
    """Determine whether one directory configures a tool.

    Args:
        directory: The directory to inspect.
        config_files: Dedicated config filenames to look for (e.g. ruff.toml).
        tool_name: The pyproject.toml [tool.<name>] section to look for, or
            None for a tool that has no pyproject.toml representation.

    Returns:
        True if the directory configures the tool, False otherwise.
    """
    # Dedicated config files take precedence over pyproject.toml sections
    if any((directory / f).exists() for f in config_files):
        return True

    # Biome passes None: it has no pyproject.toml form, so a stray
    # [tool.biome] section would otherwise suppress the bundled default.
    if tool_name is None:
        return False

    # Otherwise look for a [tool.<name>] section in pyproject.toml
    pyproject = directory / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("Failed to parse %s: %s", pyproject, e)
        return False
    return tool_name in data.get("tool", {})


def _config_root(
    path: Path, config_files: Sequence[str], tool_name: str | None
) -> Path | None:
    """Find the nearest directory that configures a tool for a path.

    Every canonical tool resolves configuration by walking up from the files
    it is handed, stopping at no boundary. Testing one directory instead meant
    a project keeping its config in a subdirectory was reported against a
    style it had rejected, and --fix rewrote its files to match.

    Bounding this walk reproduces that same bug one level up rather than
    fixing it: bounded at the working directory, `cd pkg && readability check
    mod.py` misses the configuration the repository root declares, which Ruff
    reads. Agreeing with the tools is the property worth having, so the search
    ends where theirs does.

    Args:
        path: The file or directory being checked. Resolved before walking, so
            a relative path searches its real ancestors instead of running out
            of parents at the working directory.
        config_files: Dedicated config filenames the project may define.
        tool_name: The pyproject.toml [tool.<name>] section, or None for a
            tool that has no pyproject.toml representation.

    Returns:
        The nearest configuring directory, or None if no ancestor configures
        the tool.
    """
    start = (path if path.is_dir() else path.parent).resolve()
    return next(
        (
            directory
            for directory in (start, *start.parents)
            if _has_project_config(directory, config_files, tool_name)
        ),
        None,
    )


def _bundled_config_args(config_root: Path | None, tool_name: str) -> list[str]:
    """Build --config arguments pointing at the bundled defaults for a tool.

    Args:
        config_root: The directory configuring the tool, or None if none does.
        tool_name: The name of the tool, matching a bundled config file.

    Returns:
        --config arguments for the bundled defaults, or an empty list when the
        project configures the tool itself. Passing nothing in that case lets
        the tool apply its own documented precedence, which an explicit config
        argument would override.
    """
    if config_root is not None:
        return []
    return ["--config", str(_bundled_config(tool_name))]


# How to reach a tool nobody installed. Both runners cache what they fetch,
# so the download happens once per machine and later runs are served from
# disk and work offline, which is why this package carries no linters of its
# own: vendoring ruff and pyrefly made it thirteen times larger, and charged
# that to everyone using only `guide`.
#
# Floors, not ranges. A floor keeps a tool new enough to understand the
# bundled configs; a ceiling would freeze whoever installed nothing at the
# version shipped here and demand a release of this package to track every
# release of theirs. Ruff alone has published seventeen minor series, one
# roughly every six weeks, and the first ceiling written here was stale two
# days later. Anyone who needs a fixed version installs it, and an
# installed tool always wins over a fetched one.
#
# The npm package name is not always the executable's: 'biome' on npm is an
# unrelated environment-variable helper that exits 0 whatever it is given.
TOOL_RUNNERS = {
    "ruff": ["uvx", "ruff>=0.15"],
    "pyrefly": ["uvx", "pyrefly>=1.2"],
    "biome": ["npx", "-y", "@biomejs/biome@>=2.5"],
}

# Each supported extension has one formatter owner. Pyrefly additionally checks
# Python types, but project configuration never changes tool selection.
TOOL_EXTENSIONS = {
    "ruff": (".py",),
    "pyrefly": (".py",),
    "biome": (
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".json",
        ".jsonc",
        ".css",
        ".html",
    ),
    "gofmt": (".go",),
}

# Leave headroom below common operating-system argv limits. Commands with many
# explicit targets are split before execution so directory checks stay scoped
# without failing on large trees.
MAX_COMMAND_BYTES = 16 * 1024


def _matching_paths(path: Path, extensions: Sequence[str]) -> list[str]:
    """Return files under a requested path owned by one tool.

    Args:
        path: Requested file or directory.
        extensions: Extensions canonically assigned to the tool.

    Returns:
        The requested file, or sorted matching files below the directory.
    """
    if path.is_file():
        return [str(path)] if path.suffix in extensions else []

    return sorted(
        str(candidate)
        for candidate in path.rglob("*")
        if candidate.is_file() and candidate.suffix in extensions
    )


def _command_batches(cmd: Sequence[str], target_count: int) -> list[list[str]]:
    """Split trailing file targets into bounded commands.

    Args:
        cmd: Complete command with file targets at the end.
        target_count: Number of trailing arguments that are file targets.

    Returns:
        One or more commands under the conservative argv budget.
    """
    if target_count == 0:
        return [list(cmd)]

    prefix = list(cmd[:-target_count])
    targets = cmd[-target_count:]
    prefix_size = sum(len(argument.encode()) + 1 for argument in prefix)
    batches: list[list[str]] = []
    batch: list[str] = []
    batch_size = prefix_size

    for target in targets:
        target_size = len(target.encode()) + 1
        if batch and batch_size + target_size > MAX_COMMAND_BYTES:
            batches.append([*prefix, *batch])
            batch = []
            batch_size = prefix_size
        batch.append(target)
        batch_size += target_size

    batches.append([*prefix, *batch])
    return batches


def _tool_command(binary: str, project_root: Path) -> list[str]:
    """Build the argv prefix that reaches a tool.

    A project that installed a tool chose that version, and the findings it
    is used to were written against it, so an installed copy always wins
    over one a runner would fetch.

    Args:
        binary: The executable's name.
        project_root: The project root, searched for local installs.

    Returns:
        The command prefix to put the tool's own arguments after. Falls back
        to the bare name when the tool has no runner, leaving PATH to
        resolve it as before.
    """
    # npm keeps a project's tools out of PATH, so this is the only way to
    # reach the version it installed. A Python virtualenv is deliberately
    # not searched: activating one already puts its tools on PATH, so the
    # lookup would add nothing except running a binary chosen by whichever
    # directory the caller happened to be standing in.
    local = project_root / "node_modules" / ".bin" / binary
    # Existing is not being runnable: a plain file or a directory of the
    # right name would otherwise win here and fail the next gate, hiding
    # both the copy on PATH and the runner behind it.
    if local.is_file() and os.access(local, os.X_OK):
        return [str(local)]

    # Then whatever the machine already has, which needs no download at all
    found = shutil.which(binary)
    if found:
        return [found]

    return TOOL_RUNNERS.get(binary, [binary])


def _get_tool_definitions(path: Path, project_root: Path) -> list[ToolPlan]:
    """Define supported tools with their extensions and commands.

    Args:
        path: The path being checked, which also anchors config discovery.
        project_root: The project root, used to find project-local tool
            installs and to anchor ignore-file discovery.

    Returns:
        Typed executable plans for every canonical tool.
    """
    path_str = str(path)

    # Discovery is per tool: configuring one of them cannot opt a path out of
    # the others, so each searches for its own config and falls back alone.
    ruff_root = _config_root(path, ["ruff.toml", ".ruff.toml"], "ruff")
    ruff_config = _bundled_config_args(ruff_root, "ruff")
    pyrefly_root = _config_root(path, ["pyrefly.toml"], "pyrefly")
    pyrefly_config = _bundled_config_args(pyrefly_root, "pyrefly")
    # An explicit path switches Pyrefly to single-file mode, where the
    # project's includes, excludes, and project-rooted import resolution are
    # deliberately ignored. Only a directory that owns its config is a project
    # check: when the config sits further up, its includes reach wider than
    # what was asked for, and answering a request for one package with a scan
    # of the whole repository is the wrong kind of thorough.
    pyrefly_project_mode = path.is_dir() and pyrefly_root == path.resolve()
    pyrefly_targets = [] if pyrefly_project_mode else [path_str]
    biome_root = _config_root(path, ["biome.json", "biome.jsonc"], None)
    biome_config = []
    if biome_root is None:
        biome_config = ["--config-path", str(_bundled_config("biome"))]
        ignore_files = (
            project_root / ".gitignore",
            project_root / ".ignore",
            project_root / ".git" / "info" / "exclude",
        )
        if any(ignore_file.is_file() for ignore_file in ignore_files):
            biome_config.extend(
                [
                    "--vcs-enabled=true",
                    "--vcs-client-kind=git",
                    "--vcs-use-ignore-file=true",
                    f"--vcs-root={project_root}",
                ]
            )

    # Resolved once so every command for a tool reaches the same executable
    ruff = _tool_command("ruff", project_root)
    pyrefly = _tool_command("pyrefly", project_root)
    biome = _tool_command("biome", project_root)
    biome_targets = _matching_paths(path, TOOL_EXTENSIONS["biome"])
    gofmt_targets = _matching_paths(path, TOOL_EXTENSIONS["gofmt"])

    return [
        ToolPlan(
            name="ruff",
            check=(
                *ruff,
                "check",
                "--force-exclude",
                *ruff_config,
                path_str,
            ),
            check_format=(
                *ruff,
                "format",
                "--check",
                "--force-exclude",
                *ruff_config,
                path_str,
            ),
            fix=(
                *ruff,
                "check",
                "--fix",
                "--force-exclude",
                *ruff_config,
                path_str,
            ),
            format=(
                *ruff,
                "format",
                "--force-exclude",
                *ruff_config,
                path_str,
            ),
            extensions=TOOL_EXTENSIONS["ruff"],
        ),
        ToolPlan(
            # Type checker only: it reports findings but cannot fix or format
            name="pyrefly",
            check=(
                *pyrefly,
                "check",
                *pyrefly_config,
                *pyrefly_targets,
            ),
            cwd=pyrefly_root if pyrefly_project_mode else None,
            extensions=TOOL_EXTENSIONS["pyrefly"],
        ),
        ToolPlan(
            name="biome",
            check=(
                *biome,
                "lint",
                *biome_config,
                "--no-errors-on-unmatched",
                *biome_targets,
            ),
            check_format=(
                *biome,
                "format",
                *biome_config,
                "--no-errors-on-unmatched",
                *biome_targets,
            ),
            fix=(
                *biome,
                "lint",
                "--write",
                *biome_config,
                "--no-errors-on-unmatched",
                *biome_targets,
            ),
            format=(
                *biome,
                "format",
                "--write",
                *biome_config,
                "--no-errors-on-unmatched",
                *biome_targets,
            ),
            extensions=TOOL_EXTENSIONS["biome"],
            targets=tuple(biome_targets),
        ),
        ToolPlan(
            name="gofmt",
            check_format=("gofmt", "-l", *gofmt_targets),
            format=("gofmt", "-w", *gofmt_targets),
            extensions=TOOL_EXTENSIONS["gofmt"],
            targets=tuple(gofmt_targets),
        ),
    ]
