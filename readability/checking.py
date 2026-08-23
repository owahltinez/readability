"""Run readability checks and report their coverage."""

import dataclasses
import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

import click

from readability.tools import (
    ToolPlan,
    _command_batches,
    _get_tool_definitions,
    _repository_root,
    _should_run_tool,
    _tool_is_installed,
)

logger = logging.getLogger("readability")
DEFAULT_TIMEOUT = 60


@dataclasses.dataclass
class CheckReport:
    """What a check actually did, as opposed to what it was asked to do.

    Attributes:
        findings: Whether any tool that ran reported something.
        ran: Names of the tools that processed at least one file. This records
            what happened, not what was intended. A tool that resolved but
            never started belongs in failed, and a tool that explicitly
            reported processing no files is not recorded as having run.
        skipped: Names of the tools that were applicable but not installed.
            Tools that own no requested file are in neither set, so passing
            without them is not a gap in coverage.
        failed: Names of the tools that started and could not finish, by
            failing to exec or by running past the timeout.
        unverified_paths: Requested paths for which no tool processed a file.
            Keeping these paths through aggregation prevents a checked path
            from hiding another path that a tool ignored.
    """

    findings: bool = False
    ran: set[str] = dataclasses.field(default_factory=set)
    skipped: set[str] = dataclasses.field(default_factory=set)
    failed: set[str] = dataclasses.field(default_factory=set)
    unverified_paths: list[Path] = dataclasses.field(default_factory=list)

    def absorb(self, other: "CheckReport") -> None:
        """Fold another report into this one.

        Args:
            other: The report to merge, typically for one more path.
        """
        self.findings |= other.findings
        self.ran |= other.ran
        self.skipped |= other.skipped
        self.failed |= other.failed
        self.unverified_paths.extend(other.unverified_paths)


def check_paths(
    paths: Sequence[str | Path],
    project_root: Path | None = None,
    fix: bool = False,
) -> CheckReport:
    """Run relevant checks for paths and aggregate what the tools did.

    Detailed findings from the underlying tools are written as they are for
    the command-line interface. This function does not print CLI status prose
    or turn the report into a process exit code; callers decide how to handle
    the result.

    Args:
        paths: Files or directories to check, as strings or paths. Relative
            paths are interpreted from the current working directory.
        project_root: Bounds config discovery, so the same files get the
            same verdict wherever the tree sits, and locates tool installs
            and ignore files. Defaults to the repository the process is in.
            It does not rebase paths.
        fix: Whether to apply automatic fixes.

    Returns:
        A report aggregated across all provided paths.

    Raises:
        FileNotFoundError: If any requested path does not exist. Every path is
            validated before any tools run.
    """
    root = (
        _repository_root(Path.cwd()) if project_root is None else project_root
    )
    requested_paths = [Path(path) for path in paths]
    missing_path = next(
        (path for path in requested_paths if not path.exists()), None
    )
    if missing_path is not None:
        raise FileNotFoundError(f"Path does not exist: {missing_path}")

    report = CheckReport()
    for path in requested_paths:
        path_report = _check_path(path, root, fix=fix)
        if not path_report.ran:
            path_report.unverified_paths.append(path)
        report.absorb(path_report)
    return report


def _check_path(
    path: Path, project_root: Path, fix: bool = False
) -> CheckReport:
    """Apply relevant tools to a single path.

    Args:
        path: The path (file or directory) to check.
        project_root: Bounds config discovery, locates tool installs and
            ignore files.
        fix: Whether to apply automatic fixes.

    Returns:
        What the tools applicable to this path did.
    """
    logger.info("Checking path: %s", path)

    # Iterate through all supported tool definitions
    report = CheckReport()
    for tool in _get_tool_definitions(path, project_root):
        if not _should_run_tool(tool, path):
            continue

        # A wanted but absent tool is a hole in coverage, not a clean result
        if not _tool_is_installed(tool):
            report.skipped.add(tool.name)
            continue

        report.absorb(_run_tool(tool, fix=fix))

    return report


def _run_tool(
    tool: ToolPlan,
    fix: bool = False,
) -> CheckReport:
    """Orchestrate the execution of a specific formatting or linting tool.

    Args:
        tool: The executable plan to run.
        fix: Whether to apply automatic fixes.

    Returns:
        What the tool did: whether it reported findings, and whether it
        completed at all. A tool that could not be started or ran past the
        timeout verified nothing, and saying so is the difference between a
        pass and a command that only looks like one.
    """
    logger.info("Running %s...", tool.name)

    report = CheckReport()
    target_count = len(tool.targets or ())
    try:
        if fix:
            # Leftovers exit non-zero, which is a finding rather than a failure
            for phase in ("format", "fix"):
                configured_command = getattr(tool, phase)
                if configured_command:
                    commands = _command_batches(
                        configured_command, target_count
                    )
                    for command in commands:
                        result = _capture_tool_command(command, cwd=tool.cwd)
                        if _tool_checked_files(tool.name, result):
                            report.ran.add(tool.name)
                        if result.returncode != 0:
                            report.findings = True
                            finding_type = (
                                "formatting findings"
                                if phase == "format"
                                else "findings"
                            )
                            click.echo(
                                f"--- {tool.name} {finding_type} ---\n"
                                f"{result.stdout}\n{result.stderr}"
                            )
        elif tool.check_format:
            for command in _command_batches(tool.check_format, target_count):
                result = _capture_tool_command(command, cwd=tool.cwd)
                if _tool_checked_files(tool.name, result):
                    report.ran.add(tool.name)
                # gofmt reports by naming files rather than by exit code
                if result.returncode != 0 or (
                    tool.name == "gofmt" and result.stdout.strip()
                ):
                    report.findings = True
                    click.echo(
                        f"--- {tool.name} formatting findings ---\n"
                        f"{result.stdout}\n{result.stderr}"
                    )

        if tool.check:
            for command in _command_batches(tool.check, target_count):
                result = _capture_tool_command(command, cwd=tool.cwd)
                if _tool_checked_files(tool.name, result):
                    report.ran.add(tool.name)
                if result.returncode != 0:
                    report.findings = True
                    click.echo(
                        f"--- {tool.name} findings ---\n"
                        f"{result.stdout}\n{result.stderr}"
                    )

    # Never starting, or timing out, means this tool checked nothing
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Could not run %s: %s", tool.name, e)
        report.failed.add(tool.name)

    return report


def _tool_checked_files(
    tool_name: str, result: subprocess.CompletedProcess
) -> bool:
    """Report whether a completed command actually processed any files.

    Args:
        tool_name: The tool whose command completed.
        result: The completed subprocess.

    Returns:
        False when the tool explicitly reports that it processed no files.
    """
    # Biome says this for an unmatched target, Ruff when excludes cover them all
    zero_file_reports = {
        "biome": ("Checked 0 files", "Formatted 0 files"),
        "ruff": ("No Python files found",),
    }
    summaries = zero_file_reports.get(tool_name)
    if summaries is None:
        return True

    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    return not any(summary in output for summary in summaries)


def _capture_tool_command(
    cmd: list[str], cwd: Path | None = None
) -> subprocess.CompletedProcess:
    """Run a tool and capture what it said, whatever its exit code.

    Args:
        cmd: The command list to execute.
        cwd: Directory in which the tool should run. Defaults to the caller's
            current working directory.

    Returns:
        The completed process, including captured output and its exit code.

    Raises:
        subprocess.SubprocessError: If the command outlives the timeout.
        OSError: If the command cannot be started.
    """
    logger.debug("Executing: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=DEFAULT_TIMEOUT,
        cwd=cwd,
    )
