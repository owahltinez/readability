"""Plan the checkers that need a whole project: tsc, go vet and clippy."""

import os
from pathlib import Path
import shutil

from readability.tools import _config_groups
from readability.tools import _config_root
from readability.tools import _config_roots_below
from readability.tools import _matching_paths
from readability.tools import TOOL_EXTENSIONS
from readability.tools import ToolPlan


def _project_checker_plans(path: Path, boundary: Path) -> list[ToolPlan]:
    """Plan tsc, go vet and clippy, which only run inside a project.

    Each checks whole projects or packages rather than the named files, since
    none can check a file without its imports.

    Args:
        path: The path being checked.
        boundary: The outermost directory config discovery may consider.

    Returns:
        One plan per tsconfig.json, Go module and Cargo package the path
        reaches.
    """
    plans = [
        ToolPlan(
            name="tsc",
            extensions=TOOL_EXTENSIONS["tsc"],
            check=(*_tsc_command(root), "--noEmit", "-p", str(root)),
        )
        for root, _, _ in _config_groups(path, boundary, "tsc")
        if root is not None
    ]

    # ./... skips nested modules, so each of those needs its own run
    modules = _config_roots_below(path, ("go.mod",), None, "go vet")
    if _config_root(path, boundary, ("go.mod",), None):
        if path.is_dir():
            # A pattern matching no package is an error, not a pass
            if _owns_files(path, modules, "go vet"):
                modules.append(path.resolve())
        else:
            plans.append(_go_vet_plan(path.parent.resolve(), "."))
    plans.extend(_go_vet_plan(module, "./...") for module in modules)
    plans.extend(
        _clippy_plan(package) for package in _cargo_packages(path, boundary)
    )
    return plans


def _owns_files(path: Path, nested: list[Path], tool: str) -> bool:
    """Report whether a directory holds a tool's files outside nested projects.

    Args:
        path: The directory being checked.
        nested: Resolved project roots below it.
        tool: The tool's name, keying TOOL_EXTENSIONS.

    Returns:
        True if any matching file belongs to no nested project.
    """
    return any(
        not any(root in Path(file).resolve().parents for root in nested)
        for file in _matching_paths(path, TOOL_EXTENSIONS[tool])
    )


def _tsc_command(project: Path) -> list[str]:
    """Build the argv prefix that reaches a project's TypeScript compiler.

    Args:
        project: The directory holding the tsconfig.json.

    Returns:
        The nearest installed tsc, else whatever PATH resolves.
    """
    # Node resolves upward, which is where workspaces hoist it
    for directory in (project, *project.parents):
        local = directory / "node_modules" / ".bin" / "tsc"
        if local.is_file() and os.access(local, os.X_OK):
            return [str(local)]
    return [shutil.which("tsc") or "tsc"]


def _go_vet_plan(directory: Path, pattern: str) -> ToolPlan:
    """Plan go vet over a package pattern, run where the pattern resolves.

    Args:
        directory: The directory the pattern is relative to.
        pattern: The package pattern to vet.

    Returns:
        The plan.
    """
    return ToolPlan(
        name="go vet",
        extensions=TOOL_EXTENSIONS["go vet"],
        check=("go", "vet", pattern),
        cwd=directory,
    )


def _cargo_packages(path: Path, boundary: Path) -> list[Path]:
    """Find the Cargo packages whose Rust files a path reaches.

    Args:
        path: The path being checked.
        boundary: The outermost directory config discovery may consider.

    Returns:
        Resolved package roots: those below a directory, plus the one around
        the path when it holds files no nested package claims. A virtual
        workspace root holds none, so only its members run.
    """
    packages = _config_roots_below(path, ("Cargo.toml",), None, "clippy")
    around = _config_root(path, boundary, ("Cargo.toml",), None)
    if around is not None and (
        path.is_file() or _owns_files(path, packages, "clippy")
    ):
        packages.append(around)
    return packages


def _clippy_plan(package: Path) -> ToolPlan:
    """Plan clippy over one Cargo package, run from it to honor its config.

    Args:
        package: The directory holding the package's Cargo.toml.

    Returns:
        The plan. Warnings are denied, since clippy otherwise exits zero.
    """
    return ToolPlan(
        name="clippy",
        extensions=TOOL_EXTENSIONS["clippy"],
        check=("cargo", "clippy", "--all-targets", "--", "-D", "warnings"),
        # Fixing an uncommitted or unversioned tree is the point of --fix
        fix=(
            "cargo",
            "clippy",
            "--fix",
            "--allow-dirty",
            "--allow-no-vcs",
            "--all-targets",
        ),
        cwd=package,
    )
