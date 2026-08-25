"""Build executable plans for readability's canonical tools."""

from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
import logging
import os
from pathlib import Path
import shutil
import tomllib

logger = logging.getLogger("readability")


@dataclass(frozen=True)
class ToolPlan:
    """Commands and owned file types for one canonical tool."""

    name: str
    extensions: tuple[str, ...]
    check: tuple[str, ...] = ()
    check_format: tuple[str, ...] = ()
    fix: tuple[str, ...] = ()
    unsafe_fix: tuple[str, ...] = ()
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
    # Every command for a tool starts with the same executable
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
        tool_name: The pyproject.toml section, or None if it has none.

    Returns:
        True if the directory configures the tool, False otherwise.
    """
    # Dedicated config files take precedence over pyproject.toml sections
    if any((directory / f).exists() for f in config_files):
        return True

    # Biome has no pyproject form, so a stray [tool.biome] must not count
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


def _repository_root(start: Path) -> Path | None:
    """Find the repository the caller is in, which bounds discovery.

    Args:
        start: The directory to search upward from.

    Returns:
        The nearest ancestor holding a .git entry, or None if none does, which
        leaves discovery unbounded so an exported tree still agrees with Ruff.
    """
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return None


def _config_root(
    path: Path,
    boundary: Path,
    config_files: Sequence[str],
    tool_name: str | None,
) -> Path | None:
    """Find the nearest directory up to a boundary that configures a tool.

    Walking up is what the tools do. Both sides are resolved, as Ruff resolves
    them, so one spelling of a path cannot answer differently from another.

    Args:
        path: The file or directory being checked.
        boundary: Outermost directory to consider, the filesystem root for
            no bound.
        config_files: Dedicated config filenames the project may define.
        tool_name: The pyproject.toml section, or None if it has none.

    Returns:
        The nearest configuring directory, or None if none does.
    """
    start = (path if path.is_dir() else path.parent).resolve()
    root = boundary.resolve()

    # Outside the boundary there is no project speaking for the path
    if start != root and root not in start.parents:
        return None

    for directory in (start, *start.parents):
        if _has_project_config(directory, config_files, tool_name):
            return directory
        if directory == root:
            return None
    return None


def _config_roots_below(
    path: Path, config_files: Sequence[str], tool_name: str | None, tool: str
) -> list[Path]:
    """Find the projects nested inside a path that configure a tool.

    A config file with none of the tool's files beside it is data, not a
    project disagreeing, and treating it as one used to cost the whole tree
    its handed-over path for nothing.

    Args:
        path: The path being checked.
        config_files: Dedicated config filenames the project may define.
        tool_name: The pyproject.toml section, or None if it has none.
        tool: The tool's name, keying TOOL_EXTENSIONS.

    Returns:
        Resolved directories strictly below path that configure the tool and
        hold at least one file it owns.
    """
    found = []
    for directory, subdirectories, _ in os.walk(path):
        subdirectories[:] = [
            name for name in subdirectories if name not in PRUNED_DIRECTORIES
        ]
        candidate = Path(directory)
        if directory == str(path) or not _has_project_config(
            candidate, config_files, tool_name
        ):
            continue
        if _matching_paths(candidate, TOOL_EXTENSIONS[tool]):
            found.append(candidate.resolve())
    return found


def _vcs_args(project_root: Path) -> list[str]:
    """Point Biome at the ignore files a config outside the project hides.

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
    path: Path, boundary: Path, tool: str
) -> list[tuple[Path | None, list[str] | None, list[Path]]]:
    """Group what to hand a tool by the project each part belongs to.

    Handing over a path is what keeps the tool walking, and only a walk it
    performed applies its own ignore files: Ruff checks an explicitly named
    path whatever .gitignore says, so naming files there is how --fix came to
    rewrite generated code. A nested project is therefore carved out with the
    tool's exclude flag rather than by naming everything around it. Biome has
    no such flag, but honours its ignore file for named paths, so for it the
    files are named instead.

    Args:
        path: The file or directory being checked.
        boundary: The outermost directory config discovery may consider.
        tool: The tool's name, keying CONFIG_SOURCES.

    Returns:
        One (config root, targets, excludes) triple per group. A config root of
        None means nothing configures those files; targets of None means hand
        over the path itself.
    """
    config_files, section, _, exclude_flag = CONFIG_SOURCES[tool]
    own = _config_root(path, boundary, config_files, section)
    nested = _config_roots_below(path, config_files, section, tool)
    if not nested:
        return [(own, None, [])]

    # Without an exclude flag a path minus part of it is inexpressible
    if exclude_flag is None:
        named: dict[Path | None, list[str]] = {}
        for target in _matching_paths(path, TOOL_EXTENSIONS[tool]):
            root = _config_root(Path(target), boundary, config_files, section)
            named.setdefault(root, []).append(target)
        return [(root, targets, []) for root, targets in named.items()]

    groups: list[tuple[Path | None, list[str] | None, list[Path]]] = [
        (own, None, nested)
    ]
    groups.extend(
        (root, [str(root)], [r for r in nested if root in r.parents])
        for root in nested
    )
    return groups


def _plan(
    tool: str,
    binary: Sequence[str],
    config: Sequence[str],
    targets: Sequence[str] | None,
    path: Path,
    excludes: Sequence[Path] = (),
    cwd: Path | None = None,
) -> ToolPlan:
    """Build one tool plan, every phase sharing its config and targets.

    Args:
        tool: The tool's name, keying TOOL_PHASES and TOOL_EXTENSIONS.
        binary: The argv prefix that reaches the executable.
        config: Configuration arguments, empty when the file has its own.
        targets: Files to name, or None to hand over the path itself.
        path: The path being checked, used when targets is None.
        excludes: Nested projects to carve out of the targets.
        cwd: Directory to run in, for a tool that resolves config from it.

    Returns:
        The plan, with every phase the tool supports populated.
    """
    argv = [str(path)] if targets is None else list(targets)
    # A target is only reachable from another directory if it is absolute
    if cwd is not None:
        argv = [str(Path(target).resolve()) for target in argv]
    carved = []
    if excludes:
        flag = CONFIG_SOURCES[tool][3]
        carved = [
            argument
            for exclude in excludes
            for argument in (str(flag), str(exclude))
        ]
    # Every tool spells its own opt-in, so callers only ask for the intent
    words_by_phase: dict[str, tuple[str, ...]] = dict(TOOL_PHASES[tool])
    unsafe_flag = UNSAFE_FLAGS.get(tool)
    if unsafe_flag and "fix" in words_by_phase:
        words_by_phase["unsafe_fix"] = (*words_by_phase["fix"], unsafe_flag)

    # Named rather than unpacked, so the type checker still sees the fields
    phases = {
        phase: (*binary, *words, *config, *carved, *argv)
        for phase, words in words_by_phase.items()
    }
    return ToolPlan(
        name=tool,
        extensions=TOOL_EXTENSIONS[tool],
        targets=None if targets is None else tuple(targets),
        cwd=cwd,
        check=phases.get("check", ()),
        check_format=phases.get("check_format", ()),
        fix=phases.get("fix", ()),
        unsafe_fix=phases.get("unsafe_fix", ()),
        format=phases.get("format", ()),
    )


# Cached fetches for a tool nobody installed; floors only, never ceilings
TOOL_RUNNERS = {
    "ruff": ["uvx", "ruff>=0.15"],
    "pyrefly": ["uvx", "pyrefly>=1.2"],
    "biome": ["npx", "-y", "@biomejs/biome@>=2.5"],
}

# One formatter owner per extension; configuration never changes selection
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

# Headroom below common argv limits; long target lists are split before exec
MAX_COMMAND_BYTES = 16 * 1024

# Named files bypass a tool's own exclusions, and Pyrefly ignores its excludes
PRUNED_DIRECTORIES = frozenset(
    ".git .hg .svn .venv venv .tox .nox node_modules "
    "__pycache__ .mypy_cache .pytest_cache .ruff_cache".split()
)

# Per tool: config filenames, pyproject section (None if it has none), flag
CONFIG_SOURCES = {
    "ruff": (
        ("ruff.toml", ".ruff.toml"),
        "ruff",
        "--config",
        "--extend-exclude",
    ),
    "pyrefly": (
        ("pyrefly.toml",),
        "pyrefly",
        "--config",
        "--project-excludes",
    ),
    "biome": (("biome.json", "biome.jsonc"), None, "--config-path", None),
}

# Words each phase needs, before config and targets
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

# Per tool: how it names fixes that may change behavior or drop comments
UNSAFE_FLAGS = {"ruff": "--unsafe-fixes", "biome": "--unsafe"}


def _matching_paths(path: Path, extensions: Sequence[str]) -> list[str]:
    """Return files under a requested path owned by one tool.

    Args:
        path: Requested file or directory.
        extensions: Extensions canonically assigned to the tool.

    Returns:
        The requested file, or sorted matching files below the directory,
        minus the directories no tool would have walked into.
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
    # npm hides a project's tools from PATH; a venv on PATH needs no lookup
    local = project_root / "node_modules" / ".bin" / binary
    # Existing is not being runnable, and a dead match hides the live ones
    if local.is_file() and os.access(local, os.X_OK):
        return [str(local)]

    # Then whatever the machine already has, which needs no download at all
    found = shutil.which(binary)
    if found:
        return [found]

    return TOOL_RUNNERS.get(binary, [binary])


def _get_tool_definitions(
    path: Path, project_root: Path, boundary: Path | None = None
) -> list[ToolPlan]:
    """Define supported tools with their extensions and commands.

    Args:
        path: The path being checked.
        project_root: Locates project-local tool installs and ignore files.
        boundary: Outermost directory config discovery may consider, or None
            for no bound. Defaults to project_root.

    Returns:
        Plans, one per tool per group needing the same config. Two can share a
        name, which is why the report tracks tools as a set.
    """
    if boundary is None:
        boundary = project_root
    # Resolved once so every command for a tool reaches the same executable
    ruff = _tool_command("ruff", project_root)
    pyrefly = _tool_command("pyrefly", project_root)
    biome = _tool_command("biome", project_root)

    def group_plans(tool: str, binary: Sequence[str]) -> list[ToolPlan]:
        """Build a plan per project the tool's files belong to.

        Args:
            tool: The tool's name.
            binary: The argv prefix that reaches the executable.

        Returns:
            One plan per group, each pointed at the project it belongs to.
        """
        built = []
        for root, targets, excludes in _config_groups(path, boundary, tool):
            config = []
            if root is None:
                flag = CONFIG_SOURCES[tool][2]
                config = [flag, str(_bundled_config(tool))]
                # Only Biome needs telling where the ignore files are
                if tool == "biome":
                    config += _vcs_args(project_root)
            # Biome alone resolves config from where it was started
            cwd = root if tool == "biome" else None
            built.append(
                _plan(tool, binary, config, targets, path, excludes, cwd)
            )
        return built

    # Per tool: configuring one cannot opt a path out of the others
    plans = group_plans("ruff", ruff)
    plans.extend(group_plans("biome", biome))
    plans.append(
        _plan(
            "gofmt",
            ("gofmt",),
            [],
            _matching_paths(path, TOOL_EXTENSIONS["gofmt"]),
            path,
        )
    )

    # A directory owning its config alone lets Pyrefly select the files
    files, section = CONFIG_SOURCES["pyrefly"][:2]
    groups = _config_groups(path, boundary, "pyrefly")
    if (
        len(groups) == 1
        and _config_root(path, boundary, files, section) == path.resolve()
    ):
        plans.append(
            ToolPlan(
                name="pyrefly",
                check=(*pyrefly, "check"),
                cwd=path.resolve(),
                extensions=TOOL_EXTENSIONS["pyrefly"],
            )
        )
        return plans

    plans.extend(group_plans("pyrefly", pyrefly))
    return plans
