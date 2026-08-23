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


def _lexical_path(path: Path) -> Path:
    """Make a path absolute without following symbolic links.

    Resolving would search the link target's ancestors instead, disagreeing
    with Ruff about a vendored directory that is a link.

    Args:
        path: The path to normalize.

    Returns:
        An absolute path with '..' removed and links intact.
    """
    return Path(os.path.abspath(path))


def _repository_root(start: Path) -> Path:
    """Find the repository the caller is standing in.

    Discovery is bounded by this, so it has to be the project: bounded at the
    working directory, `cd pkg && readability check mod.py` would miss the
    config the repository root declares, which Ruff reads.

    Args:
        start: The directory to search upward from.

    Returns:
        The nearest ancestor holding a .git entry, or start if none does.
    """
    start = _lexical_path(start)
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return start


def _config_root(
    path: Path,
    boundary: Path,
    config_files: Sequence[str],
    tool_name: str | None,
) -> Path | None:
    """Find the nearest directory up to a boundary that configures a tool.

    Walking up is what the tools do. Stopping at the boundary keeps the answer
    from depending on what happens to sit above the project, which callers
    scoring against a fixed baseline rely on.

    Args:
        path: The file or directory being checked.
        boundary: The outermost directory the search may consider.
        config_files: Dedicated config filenames the project may define.
        tool_name: The pyproject.toml [tool.<name>] section, or None for a
            tool that has no pyproject.toml representation.

    Returns:
        The nearest configuring directory, or None if none does.
    """
    start = _lexical_path(path if path.is_dir() else path.parent)
    root = _lexical_path(boundary)

    # Outside the boundary there is no project speaking for the path
    if start != root and root not in start.parents:
        return None

    for directory in (start, *start.parents):
        if _has_project_config(directory, config_files, tool_name):
            return directory
        if directory == root:
            return None
    return None


def _vcs_args(project_root: Path) -> list[str]:
    """Point Biome at the project's ignore files when using a bundled config.

    A config outside the project leaves Biome nothing to root ignore
    discovery in, so it has to be told.

    Args:
        project_root: The directory holding the repository's ignore files.

    Returns:
        VCS arguments, or an empty list when there is no ignore file.
    """
    ignore_files = (".gitignore", ".ignore", ".git/info/exclude")
    if not any((project_root / name).is_file() for name in ignore_files):
        return []
    return [
        "--vcs-enabled=true",
        "--vcs-client-kind=git",
        "--vcs-use-ignore-file=true",
        f"--vcs-root={project_root}",
    ]


def _config_groups(
    targets: Sequence[str], boundary: Path, tool: str
) -> list[tuple[list[str], list[str]]]:
    """Group a tool's files by the configuration arguments they need.

    A tool only needs telling which config to use when the file has none,
    because it resolves its own hierarchy: one Ruff invocation over two
    packages declaring different line lengths reports each against its own.
    So the question is binary and two groups cover any tree.

    Args:
        targets: The files the tool owns, as strings.
        boundary: The outermost directory config discovery may consider.
        tool: The tool's name, keying CONFIG_SOURCES.

    Returns:
        One (config arguments, targets) pair per non-empty group. Configured
        files carry no config argument at all, which is what lets the tool
        apply its own precedence.
    """
    config_files, section, flag = CONFIG_SOURCES[tool]
    configured: list[str] = []
    unconfigured: list[str] = []
    for target in targets:
        root = _config_root(Path(target), boundary, config_files, section)
        (configured if root is not None else unconfigured).append(target)

    bundled = [flag, str(_bundled_config(tool))]
    return [
        (args, group)
        for args, group in (([], configured), (bundled, unconfigured))
        if group
    ]


def _plan(
    tool: str,
    binary: Sequence[str],
    config: Sequence[str],
    targets: Sequence[str],
) -> ToolPlan:
    """Build one tool plan, every phase sharing the same config and targets.

    Args:
        tool: The tool's name, keying TOOL_PHASES and TOOL_EXTENSIONS.
        binary: The argv prefix that reaches the executable.
        config: Configuration arguments, empty when the file has its own.
        targets: The files to hand the tool.

    Returns:
        The plan, with every phase the tool supports populated.
    """
    # Named rather than unpacked, so the type checker still sees the fields
    phases = {
        phase: (*binary, *words, *config, *targets)
        for phase, words in TOOL_PHASES[tool].items()
    }
    return ToolPlan(
        name=tool,
        extensions=TOOL_EXTENSIONS[tool],
        targets=tuple(targets),
        check=phases.get("check", ()),
        check_format=phases.get("check_format", ()),
        fix=phases.get("fix", ()),
        format=phases.get("format", ()),
    )


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

# Every canonical tool skips these when it walks a directory itself, but it can
# only do that for a walk it performed. Naming files explicitly is what lets a
# mixed tree be checked against the config that governs each part of it, and it
# bypasses those exclusions: Pyrefly ignores project excludes for an explicit
# path, so a virtualenv's contents would be type-checked as project code.
PRUNED_DIRECTORIES = frozenset(
    ".git .hg .svn .venv venv .tox .nox node_modules "
    "__pycache__ .mypy_cache .pytest_cache .ruff_cache".split()
)

# Per tool: the config filenames a project may use, the pyproject.toml section
# that configures it (None for a tool with no pyproject form), and the flag
# that names a config file.
CONFIG_SOURCES = {
    "ruff": (("ruff.toml", ".ruff.toml"), "ruff", "--config"),
    "pyrefly": (("pyrefly.toml",), "pyrefly", "--config"),
    "biome": (("biome.json", "biome.jsonc"), None, "--config-path"),
}

# The words each phase of a tool's command needs, before config and targets.
TOOL_PHASES = {
    "ruff": {
        "check": ("check", "--force-exclude"),
        "check_format": ("format", "--check", "--force-exclude"),
        "fix": ("check", "--fix", "--force-exclude"),
        "format": ("format", "--force-exclude"),
    },
    # Type checker only: reports findings, cannot fix or format
    "pyrefly": {"check": ("check",)},
    "biome": {
        "check": ("lint", "--no-errors-on-unmatched"),
        "check_format": ("format", "--no-errors-on-unmatched"),
        "fix": ("lint", "--write", "--no-errors-on-unmatched"),
        "format": ("format", "--write", "--no-errors-on-unmatched"),
    },
    "gofmt": {"check_format": ("-l",), "format": ("-w",)},
}


def _matching_paths(path: Path, extensions: Sequence[str]) -> list[str]:
    """Return files under a requested path owned by one tool.

    Args:
        path: Requested file or directory.
        extensions: Extensions canonically assigned to the tool.

    Returns:
        The requested file, or sorted matching files below the directory,
        skipping directories no tool would have walked into itself.
    """
    if path.is_file():
        return [str(path)] if path.suffix in extensions else []

    matches: list[str] = []
    for directory, subdirectories, filenames in os.walk(path):
        # Pruned in place, which is what stops os.walk descending into them
        subdirectories[:] = [
            name for name in subdirectories if name not in PRUNED_DIRECTORIES
        ]
        matches.extend(
            str(Path(directory) / filename)
            for filename in filenames
            if Path(filename).suffix in extensions
        )
    return sorted(matches)


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
        path: The path being checked, expanded to the files each tool owns.
        project_root: The project root, bounding configuration discovery and
            used to find project-local tool installs and ignore files.

    Returns:
        Typed executable plans, one per tool per group of files needing the
        same configuration. Two plans can share a name, which is why the
        report tracks tools as a set.
    """
    # Resolved once so every command for a tool reaches the same executable
    ruff = _tool_command("ruff", project_root)
    pyrefly = _tool_command("pyrefly", project_root)
    biome = _tool_command("biome", project_root)
    python_files = _matching_paths(path, TOOL_EXTENSIONS["ruff"])
    biome_files = _matching_paths(path, TOOL_EXTENSIONS["biome"])
    gofmt_targets = _matching_paths(path, TOOL_EXTENSIONS["gofmt"])

    # Discovery is per tool: configuring one of them cannot opt a path out of
    # the others, so each groups its own files and falls back alone.
    plans = [
        _plan("ruff", ruff, config, targets)
        for config, targets in _config_groups(
            python_files, project_root, "ruff"
        )
    ]

    for config, targets in _config_groups(biome_files, project_root, "biome"):
        # Ignore discovery stays at the project root, the only place a
        # repository-wide .gitignore can be, and only matters for the group
        # falling back to a config outside the project.
        vcs = _vcs_args(project_root) if config else []
        plans.append(_plan("biome", biome, [*config, *vcs], targets))

    plans.append(_plan("gofmt", ("gofmt",), [], gofmt_targets))

    # An explicit path switches Pyrefly to single-file mode, where the
    # project's includes and excludes are deliberately ignored. Only a
    # directory that owns its config is a project check: a config further up
    # reaches wider than what was asked for, and answering a request for one
    # package with a scan of the whole repository is the wrong kind of
    # thorough.
    files, section, _ = CONFIG_SOURCES["pyrefly"]
    pyrefly_root = _config_root(path, project_root, files, section)
    if path.is_dir() and pyrefly_root == _lexical_path(path):
        plans.append(
            ToolPlan(
                name="pyrefly",
                check=(*pyrefly, "check"),
                cwd=pyrefly_root,
                extensions=TOOL_EXTENSIONS["pyrefly"],
            )
        )
        return plans

    plans.extend(
        _plan("pyrefly", pyrefly, config, targets)
        for config, targets in _config_groups(
            python_files, project_root, "pyrefly"
        )
    )
    return plans
