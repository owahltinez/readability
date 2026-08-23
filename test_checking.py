import json
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from readability.checking import CheckReport, check_paths
from readability.cli import cli
from readability.tools import (
    TOOL_RUNNERS,
    _bundled_config,
    _get_tool_definitions,
    _has_project_config,
)


def _source_file(directory: Path, name: str) -> Path:
    """Create an empty source file for tool-resolution tests.

    Tool plans now carry the files they will be handed, so a plan only exists
    for a path that has files in it. Resolution tests therefore need a file
    that exists rather than a bare name.

    Args:
        directory: Where to create the file.
        name: The filename, whose suffix decides the owning tool.

    Returns:
        The created file's path.
    """
    path = directory / name
    path.touch()
    return path


@patch("shutil.which")
@patch("subprocess.run")
def test_check_command_ruff(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Tests the check command with ruff.

    Args:
        mock_run: The mocked subprocess.run function.
        mock_which: The mocked shutil.which function.
        tmp_path: The temporary directory fixture.
    """
    # Create a dummy pyproject.toml to trigger ruff
    (tmp_path / "pyproject.toml").touch()
    py_file = tmp_path / "script.py"
    py_file.touch()

    # Mock shutil.which to say ruff exists
    mock_which.side_effect = lambda x: x if x == "ruff" else None

    # Mock subprocess.run to return success
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # We need to recreate the files because isolated_filesystem changes cwd
        Path("pyproject.toml").touch()
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "script.py"])

    assert result.exit_code == 0
    # ruff should be called at least twice (check and check_format)
    assert mock_run.call_count >= 2
    # Verify ruff check ran with the bundled default config injected
    cfg = str(_bundled_config("ruff"))
    called_cmds = [call.args[0] for call in mock_run.call_args_list]
    assert [
        "ruff",
        "check",
        "--force-exclude",
        "--config",
        cfg,
        "script.py",
    ] in called_cmds


@patch("shutil.which")
@patch("subprocess.run")
def test_check_command_fix(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Tests the check command with --fix.

    Args:
        mock_run: The mocked subprocess.run function.
        mock_which: The mocked shutil.which function.
        tmp_path: The temporary directory fixture.
    """
    # Mock shutil.which to say ruff exists
    mock_which.side_effect = lambda x: x if x == "ruff" else None

    # Mock subprocess.run to return success
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "--fix", "script.py"])

    assert result.exit_code == 0
    cfg = str(_bundled_config("ruff"))
    called_cmds = [call.args[0] for call in mock_run.call_args_list]
    # Verify ruff check --fix and ruff format were called
    assert [
        "ruff",
        "check",
        "--fix",
        "--force-exclude",
        "--config",
        cfg,
        "script.py",
    ] in called_cmds
    assert [
        "ruff",
        "format",
        "--force-exclude",
        "--config",
        cfg,
        "script.py",
    ] in called_cmds


@patch("shutil.which")
@patch("subprocess.run")
def test_check_command_directory(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Tests the check command with a directory.

    Args:
        mock_run: The mocked subprocess.run function.
        mock_which: The mocked shutil.which function.
        tmp_path: The temporary directory fixture.
    """
    # Create a dummy pyproject.toml to trigger ruff
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "script.py").touch()

    # Mock shutil.which to say ruff exists
    mock_which.side_effect = lambda x: x if x == "ruff" else None

    # Mock subprocess.run to return success
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("subdir").mkdir()
        Path("subdir/script.py").touch()

        # Check the subdirectory
        result = runner.invoke(cli, ["check", "subdir"])

    assert result.exit_code == 0
    cfg = str(_bundled_config("ruff"))
    called_cmds = [call.args[0] for call in mock_run.call_args_list]
    # A directory is expanded to the files it holds, which is what lets each
    # file be checked against the configuration that governs it
    assert [
        "ruff",
        "check",
        "--force-exclude",
        "--config",
        cfg,
        "subdir/script.py",
    ] in called_cmds


@patch("shutil.which")
@patch("subprocess.run")
def test_check_command_biome(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Tests the check command with biome (npx).

    Args:
        mock_run: The mocked subprocess.run function.
        mock_which: The mocked shutil.which function.
        tmp_path: The temporary directory fixture.
    """
    # Mock shutil.which to say npx exists
    mock_which.side_effect = lambda x: x if x == "npx" else None

    # Mock subprocess.run to return success
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("biome.json").touch()
        Path("script.js").touch()

        result = runner.invoke(cli, ["check", "script.js"])

    assert result.exit_code == 0
    # npx biome lint and npx biome format should be called
    called_cmds = [call.args[0] for call in mock_run.call_args_list]
    assert [
        "npx",
        "-y",
        "@biomejs/biome@>=2.5",
        "lint",
        "--no-errors-on-unmatched",
        "script.js",
    ] in called_cmds
    assert [
        "npx",
        "-y",
        "@biomejs/biome@>=2.5",
        "format",
        "--no-errors-on-unmatched",
        "script.js",
    ] in called_cmds


@patch("shutil.which")
@patch("subprocess.run")
def test_check_command_pyrefly(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Tests the check command runs pyrefly with the bundled default config.

    Args:
        mock_run: The mocked subprocess.run function.
        mock_which: The mocked shutil.which function.
        tmp_path: The temporary directory fixture.
    """
    # Mock shutil.which to say pyrefly exists
    mock_which.side_effect = lambda x: x if x == "pyrefly" else None
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "script.py"])

    assert result.exit_code == 0
    cfg = str(_bundled_config("pyrefly"))
    called_cmds = [call.args[0] for call in mock_run.call_args_list]
    assert ["pyrefly", "check", "--config", cfg, "script.py"] in called_cmds


@patch("shutil.which")
@patch("subprocess.run")
def test_check_command_runs_configured_directory_as_pyrefly_project(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """The CLI lets project-owned includes and excludes select Python files."""
    mock_which.side_effect = lambda name: name if name == "pyrefly" else None
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyrefly.toml").write_text(
            'project-excludes = ["**/excluded.py"]\n'
        )
        Path("main.py").touch()
        project_root = Path.cwd()

        result = runner.invoke(cli, ["check", "."])

    assert result.exit_code == 0
    pyrefly_calls = [
        invocation
        for invocation in mock_run.call_args_list
        if invocation.args[0][0] == "pyrefly"
    ]
    assert len(pyrefly_calls) == 1
    assert pyrefly_calls[0].args[0] == ["pyrefly", "check"]
    assert pyrefly_calls[0].kwargs["cwd"] == project_root


def test_has_project_config(tmp_path: Path) -> None:
    """Tests project config detection via dedicated files and pyproject.

    Args:
        tmp_path: The temporary directory fixture.
    """
    # No config anywhere
    assert _has_project_config(tmp_path, ["ruff.toml"], "ruff") is False

    # pyproject.toml without a [tool.ruff] section
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert _has_project_config(tmp_path, ["ruff.toml"], "ruff") is False

    # pyproject.toml with a [tool.ruff] section
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    assert _has_project_config(tmp_path, ["ruff.toml"], "ruff") is True

    # Dedicated config file wins even without a pyproject section
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    (tmp_path / "ruff.toml").touch()
    assert _has_project_config(tmp_path, ["ruff.toml"], "ruff") is True

    # A tool with no pyproject.toml form ignores sections named after it
    (tmp_path / "ruff.toml").unlink()
    (tmp_path / "pyproject.toml").write_text("[tool.biome]\n")
    assert _has_project_config(tmp_path, ["biome.json"], None) is False

    # Unparseable pyproject.toml is treated as no config
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "pyproject.toml").write_text("not [ valid toml")
    assert (
        _has_project_config(tmp_path / "broken", ["ruff.toml"], "ruff") is False
    )


def test_default_configs_omitted_when_project_configured(
    tmp_path: Path,
) -> None:
    """Tests that bundled defaults are not injected when the project has config.

    Args:
        tmp_path: The temporary directory fixture.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n\n[tool.pyrefly]\n")
    py_file = tmp_path / "script.py"
    py_file.touch()

    tools = {
        tool.name: tool for tool in _get_tool_definitions(py_file, tmp_path)
    }
    assert "--config" not in tools["ruff"].check
    assert "--config" not in tools["ruff"].format
    assert "--config" not in tools["pyrefly"].check

    ts_file = tmp_path / "script.ts"
    ts_file.touch()
    (tmp_path / "biome.json").write_text("{}")
    tools = {
        tool.name: tool for tool in _get_tool_definitions(ts_file, tmp_path)
    }
    for command in ("check", "check_format", "fix", "format"):
        assert "--config-path" not in getattr(tools["biome"], command)


def test_ruff_uses_config_from_the_checked_paths_own_package(
    tmp_path: Path,
) -> None:
    """A package below the root configures the files inside it.

    Discovery used to test only the root, so checking pkg/mod.py from above
    reported E501 at 80 columns against a package declaring 200, and --fix
    rewrote the file to match. Pyrefly is unconfigured here and must keep its
    bundled default: configuring one tool cannot opt a path out of another.
    """
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "pyproject.toml").write_text("[tool.ruff]\nline-length = 200\n")
    module = package / "mod.py"
    module.touch()

    tools = {
        tool.name: tool for tool in _get_tool_definitions(module, tmp_path)
    }

    for command_name in ("check", "check_format", "fix", "format"):
        assert "--config" not in getattr(tools["ruff"], command_name)
    assert str(_bundled_config("pyrefly")) in tools["pyrefly"].check


def test_biome_uses_config_from_the_checked_paths_own_directory(
    tmp_path: Path,
) -> None:
    """A synthesized --config-path defeats Biome's own nested-config rules."""
    web = tmp_path / "web"
    web.mkdir()
    (web / "biome.json").write_text('{"formatter": {"lineWidth": 100}}')
    script = web / "debug.ts"
    script.touch()

    tools = {
        tool.name: tool for tool in _get_tool_definitions(script, tmp_path)
    }

    for command_name in ("check", "check_format", "fix", "format"):
        assert "--config-path" not in getattr(tools["biome"], command_name)


def test_bundled_configs_apply_when_no_ancestor_configures_a_tool(
    tmp_path: Path,
) -> None:
    """Walking up must not stop the bundled defaults reaching bare files."""
    package = tmp_path / "pkg"
    package.mkdir()
    module = package / "mod.py"
    module.touch()

    tools = {
        tool.name: tool for tool in _get_tool_definitions(module, tmp_path)
    }

    assert str(_bundled_config("ruff")) in tools["ruff"].check
    assert str(_bundled_config("pyrefly")) in tools["pyrefly"].check


def test_a_directory_target_honours_a_nested_package_config(
    tmp_path: Path,
) -> None:
    """A configured package keeps its style when reached through a directory.

    Choosing one config for a whole run cannot describe a mixed tree, so the
    bundled default was forced over packages that had rejected it, and --fix
    rewrote them irreversibly. The tools resolve per file themselves, so the
    only question is whether a file has a config at all.
    """
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "pyproject.toml").write_text("[tool.ruff]\nline-length = 200\n")
    configured = package / "mod.py"
    configured.touch()
    bare = tmp_path / "loose.py"
    bare.touch()

    plans = [
        plan
        for plan in _get_tool_definitions(tmp_path, tmp_path)
        if plan.name == "ruff"
    ]
    by_target = {
        target: plan for plan in plans for target in plan.targets or ()
    }

    assert "--config" not in by_target[str(configured)].check
    assert str(_bundled_config("ruff")) in by_target[str(bare)].check


def test_discovery_stops_at_the_declared_root(tmp_path: Path) -> None:
    """A caller that names a root gets the same answer wherever it sits.

    check_paths is used to score output against a fixed baseline, so a
    pyproject.toml above the declared root silently re-baselining every run
    is worse than disagreeing with Ruff about a file nobody asked about.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 200\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    module = workspace / "mod.py"
    module.touch()

    plans = [
        plan
        for plan in _get_tool_definitions(module, workspace)
        if plan.name == "ruff"
    ]

    assert str(_bundled_config("ruff")) in plans[0].check


def test_a_symlinked_directory_is_not_followed_out_of_the_project(
    tmp_path: Path,
) -> None:
    """Ruff reads the path it was given, so discovery must not resolve links."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.ruff]\nline-length = 200\n")
    outside = tmp_path / "elsewhere" / "pkg"
    outside.mkdir(parents=True)
    (outside / "mod.py").touch()
    (project / "vendored").symlink_to(outside)

    plans = [
        plan
        for plan in _get_tool_definitions(project / "vendored", project)
        if plan.name == "ruff"
    ]

    assert "--config" not in plans[0].check


def test_the_cli_bounds_discovery_at_the_repository_not_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standing in a package must not hide the repository's own config.

    The bound has to be the project, not wherever the caller happened to be:
    bounded at the working directory, `cd pkg && readability check mod.py`
    reports findings against the bundled defaults while Ruff, run the same
    way, reads the repository root's config and passes.
    """
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 200\n")
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "mod.py").touch()
    monkeypatch.chdir(package)

    with patch("readability.checking._check_path") as check_path:
        check_paths([Path("mod.py")])

    assert check_path.call_args.args[1] == tmp_path


def test_relative_and_absolute_paths_discover_the_same_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative path must not run out of parents at the working directory.

    Path("mod.py").parent is Path("."), whose only candidate is the working
    directory itself, so an unnormalized walk can never look above it, and
    the same file would be checked against a different style depending on
    how it was named.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 200\n")
    package = tmp_path / "pkg"
    package.mkdir()
    module = package / "mod.py"
    module.touch()
    monkeypatch.chdir(package)

    def ruff_config(path: Path) -> tuple[str, ...]:
        plans = [
            plan
            for plan in _get_tool_definitions(path, tmp_path)
            if plan.name == "ruff"
        ]
        return tuple(
            argument for argument in plans[0].check if argument == "--config"
        )

    assert ruff_config(Path("mod.py")) == ruff_config(module)
    assert ruff_config(Path("mod.py")) == ()


def test_pyrefly_project_mode_needs_the_config_at_the_checked_directory(
    tmp_path: Path,
) -> None:
    """A config further up covers more than the caller asked to check.

    Project mode passes no targets, so pyrefly would check everything the
    ancestor's project-includes reach. Answering `check pkg` with a scan of
    the whole repository is worse than the bug being fixed, so the request
    wins and pyrefly gets the explicit path.
    """
    (tmp_path / "pyrefly.toml").write_text('project-excludes = ["**/x.py"]\n')
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "mod.py").touch()

    with patch("shutil.which", side_effect=lambda name: name):
        tools = {
            tool.name: tool for tool in _get_tool_definitions(package, tmp_path)
        }

    assert tools["pyrefly"].check == (
        "pyrefly",
        "check",
        str(package / "mod.py"),
    )
    assert tools["pyrefly"].cwd is None


def test_pyrefly_project_mode_uses_the_directory_that_owns_the_config(
    tmp_path: Path,
) -> None:
    """A directory holding its own config is a project check, run in place."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "pyrefly.toml").write_text('project-excludes = ["**/x.py"]\n')
    (package / "mod.py").touch()

    with patch("shutil.which", side_effect=lambda name: name):
        tools = {
            tool.name: tool for tool in _get_tool_definitions(package, tmp_path)
        }

    assert tools["pyrefly"].check == ("pyrefly", "check")
    assert tools["pyrefly"].cwd == package


@patch("shutil.which")
@patch("subprocess.run")
def test_ruff_checking_no_files_is_not_reported_as_clean(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """An ancestor's exclude can leave Ruff with nothing, which is not a pass.

    Now that a project's own config is honoured, its extend-exclude reaches
    the files readability hands over, and Ruff answers by processing none of
    them. Reporting that as a clean result is the failure the report type
    exists to prevent.

    Args:
        mock_run: The mocked subprocess.run function.
        mock_which: The mocked shutil.which function.
        tmp_path: The temporary directory fixture.
    """
    mock_which.side_effect = lambda x: x if x == "ruff" else None
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="",
        stderr="warning: No Python files found under the given path(s)\n",
    )

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").write_text(
            '[tool.ruff]\nextend-exclude = ["pkg"]\n'
        )
        Path("pkg").mkdir()
        Path("pkg/mod.py").touch()

        result = runner.invoke(cli, ["check", "pkg/mod.py"])

    assert result.exit_code != 0
    assert "No findings" not in result.stderr


@pytest.mark.parametrize(
    ("filename", "tool", "source", "flag"),
    (
        ("ruff.toml", "ruff", "mod.py", "--config"),
        (".ruff.toml", "ruff", "mod.py", "--config"),
        ("pyrefly.toml", "pyrefly", "mod.py", "--config"),
        ("biome.json", "biome", "app.ts", "--config-path"),
        ("biome.jsonc", "biome", "app.ts", "--config-path"),
    ),
)
def test_every_recognized_config_filename_defers_to_the_project(
    tmp_path: Path, filename: str, tool: str, source: str, flag: str
) -> None:
    """Each documented filename has to be honoured, not just the common one.

    Args:
        tmp_path: The temporary directory fixture.
        filename: The config filename the project declares.
        tool: The tool that filename configures.
        source: A source file that tool owns.
        flag: The tool's flag for naming a config.
    """
    package = tmp_path / "pkg"
    package.mkdir()
    (package / filename).write_text("{}" if "biome" in filename else "")
    (package / source).touch()

    plans = [
        plan
        for plan in _get_tool_definitions(package, tmp_path)
        if plan.name == tool
    ]

    assert plans, f"no {tool} plan for {filename}"
    assert all(flag not in plan.check for plan in plans)


@pytest.mark.parametrize(
    "pruned", (".venv", "node_modules", "__pycache__", ".git", ".ruff_cache")
)
def test_expanding_a_directory_skips_what_the_tools_would_skip(
    tmp_path: Path, pruned: str
) -> None:
    """Explicit paths bypass the exclusions a tool applies to its own walk.

    Pyrefly ignores project excludes for an explicit path, so handing it
    every file under a directory type-checked a virtualenv's contents as
    project code and failed the run on third-party stubs.

    Args:
        tmp_path: The temporary directory fixture.
        pruned: A directory name no tool should be handed files from.
    """
    (tmp_path / "mod.py").touch()
    buried = tmp_path / pruned / "vendored"
    buried.mkdir(parents=True)
    (buried / "dep.py").touch()
    (buried / "dep.ts").touch()

    plans = _get_tool_definitions(tmp_path, tmp_path)

    targets = [target for plan in plans for target in plan.targets or ()]
    assert str(tmp_path / "mod.py") in targets
    assert not [target for target in targets if pruned in target]


def test_biome_ignore_discovery_stays_at_the_project_root(
    tmp_path: Path,
) -> None:
    """A repository-wide ignore file sits at the root, not beside the files.

    Config discovery follows the checked path, but ignore files cannot: only
    the root can hold the .gitignore that covers the whole repository.
    """
    (tmp_path / ".gitignore").write_text("dist/\n")
    web = tmp_path / "web"
    web.mkdir()
    (web / "app.ts").touch()

    plans = [
        plan
        for plan in _get_tool_definitions(web, tmp_path)
        if plan.name == "biome"
    ]

    assert f"--vcs-root={tmp_path}" in plans[0].check


def test_a_pyproject_biome_section_does_not_suppress_the_bundled_config(
    tmp_path: Path,
) -> None:
    """Biome has no pyproject representation, so [tool.biome] means nothing."""
    (tmp_path / "pyproject.toml").write_text("[tool.biome]\nlineWidth = 200\n")
    script = tmp_path / "script.ts"
    script.touch()

    tools = {
        tool.name: tool for tool in _get_tool_definitions(script, tmp_path)
    }

    assert str(_bundled_config("biome")) in tools["biome"].check


def test_pyrefly_uses_project_mode_only_for_configured_directories(
    tmp_path: Path,
) -> None:
    """Directories use config discovery while explicit files stay explicit."""
    (tmp_path / "pyrefly.toml").write_text(
        'project-excludes = ["**/excluded.py"]\n'
    )
    py_file = tmp_path / "main.py"
    py_file.touch()

    with patch("shutil.which", side_effect=lambda name: name):
        directory_tools = {
            tool.name: tool
            for tool in _get_tool_definitions(tmp_path, tmp_path)
        }
        file_tools = {
            tool.name: tool for tool in _get_tool_definitions(py_file, tmp_path)
        }

    assert directory_tools["pyrefly"].check == ("pyrefly", "check")
    assert directory_tools["pyrefly"].cwd == tmp_path
    assert file_tools["pyrefly"].check == (
        "pyrefly",
        "check",
        str(py_file),
    )
    assert file_tools["pyrefly"].cwd is None


def test_pyrefly_bundled_config_keeps_explicit_targets(
    tmp_path: Path,
) -> None:
    """Project mode cannot use a config rooted in the installed package."""
    module = tmp_path / "main.py"
    module.touch()

    with patch("shutil.which", side_effect=lambda name: name):
        tools = {
            tool.name: tool
            for tool in _get_tool_definitions(tmp_path, tmp_path)
        }

    assert tools["pyrefly"].check == (
        "pyrefly",
        "check",
        "--config",
        str(_bundled_config("pyrefly")),
        str(module),
    )
    assert tools["pyrefly"].cwd is None


def test_biome_bundled_config_is_injected_into_every_command(
    tmp_path: Path,
) -> None:
    """Linting, formatting, and their fix forms share the safe default."""
    ts_file = tmp_path / "script.ts"
    ts_file.touch()

    tools = {
        tool.name: tool for tool in _get_tool_definitions(ts_file, tmp_path)
    }
    config_path = str(_bundled_config("biome"))

    for command_name in ("check", "check_format", "fix", "format"):
        command = getattr(tools["biome"], command_name)
        assert command[command.index("--config-path") + 1] == config_path
        assert "--vcs-enabled=true" not in command


@pytest.mark.parametrize(
    "ignore_file", (".gitignore", ".ignore", ".git/info/exclude")
)
def test_biome_bundled_config_respects_project_ignore_files(
    tmp_path: Path, ignore_file: str
) -> None:
    """An external default keeps ignore discovery rooted in the project."""
    ts_file = tmp_path / "script.ts"
    ts_file.touch()
    ignore_path = tmp_path / ignore_file
    ignore_path.parent.mkdir(parents=True, exist_ok=True)
    ignore_path.write_text("node_modules/\n")

    tools = {
        tool.name: tool for tool in _get_tool_definitions(ts_file, tmp_path)
    }

    for command_name in ("check", "check_format", "fix", "format"):
        command = getattr(tools["biome"], command_name)
        assert "--vcs-enabled=true" in command
        assert "--vcs-client-kind=git" in command
        assert "--vcs-use-ignore-file=true" in command
        assert f"--vcs-root={tmp_path}" in command


def test_bundled_default_configs_are_valid() -> None:
    """Tests that the bundled default configs exist and parse."""
    # Both Python configs must exist and be valid TOML
    for tool in ("ruff", "pyrefly"):
        config_path = _bundled_config(tool)
        assert config_path.exists()
        tomllib.loads(config_path.read_text())

    biome_config_path = _bundled_config("biome")
    assert biome_config_path.exists()
    biome_config = json.loads(biome_config_path.read_text())
    assert biome_config["formatter"] == {
        "enabled": True,
        "indentStyle": "space",
        "indentWidth": 2,
        "lineWidth": 80,
    }
    assert biome_config["linter"]["rules"]["recommended"] is True
    assert biome_config["html"]["parser"]["interpolation"] is True

    # The ruff defaults follow the Google Python style guide
    ruff_config = tomllib.loads(_bundled_config("ruff").read_text())
    assert ruff_config["line-length"] == 80
    assert ruff_config["lint"]["pydocstyle"]["convention"] == "google"


@patch("readability.checking._check_path")
def test_check_paths_aggregates_str_and_path_inputs(
    mock_check_path: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public API folds each per-path result into one limited report."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "example.py").touch()
    (tmp_path / "main.go").touch()
    monkeypatch.chdir(tmp_path)
    mock_check_path.side_effect = [
        CheckReport(ran={"ruff"}, skipped={"pyrefly"}),
        CheckReport(
            findings=True,
            ran={"gofmt"},
            failed={"biome"},
        ),
    ]

    report = check_paths(
        ["src/example.py", Path("main.go")],
        project_root=project_root,
        fix=True,
    )

    assert report == CheckReport(
        findings=True,
        ran={"gofmt", "ruff"},
        skipped={"pyrefly"},
        failed={"biome"},
    )
    assert mock_check_path.call_args_list == [
        call(Path("src/example.py"), project_root, fix=True),
        call(Path("main.go"), project_root, fix=True),
    ]


@patch("readability.checking._check_path")
def test_check_paths_preserves_unverified_paths_when_another_path_ran(
    mock_check_path: MagicMock,
    tmp_path: Path,
) -> None:
    """One checked path must not hide another path that processed no files."""
    checked = tmp_path / "src"
    unchecked = tmp_path / "ignored"
    checked.mkdir()
    unchecked.mkdir()
    mock_check_path.side_effect = [
        CheckReport(ran={"biome"}),
        CheckReport(),
    ]

    report = check_paths([checked, unchecked], project_root=tmp_path)

    assert report == CheckReport(
        ran={"biome"},
        unverified_paths=[unchecked],
    )


@patch(
    "readability.checking._check_path",
    return_value=CheckReport(ran={"ruff"}),
)
def test_check_paths_defaults_project_root_to_cwd(
    mock_check_path: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public API discovers configuration from the caller's directory."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "example.py").touch()

    check_paths([Path("example.py")])

    mock_check_path.assert_called_once_with(
        Path("example.py"), tmp_path, fix=False
    )


@patch("readability.checking._check_path")
def test_check_paths_with_no_paths_reports_that_nothing_ran(
    mock_check_path: MagicMock,
) -> None:
    """An empty request must not look like a verified clean result."""
    report = check_paths([])

    assert report == CheckReport()
    assert not report.ran
    mock_check_path.assert_not_called()


@patch("readability.checking._check_path")
def test_check_paths_rejects_missing_paths_before_dispatch(
    mock_check_path: MagicMock,
    tmp_path: Path,
) -> None:
    """A missing path prevents partial checks and fixes on earlier paths."""
    existing = tmp_path / "exists.py"
    missing = tmp_path / "missing.py"
    existing.touch()

    with pytest.raises(FileNotFoundError) as excinfo:
        check_paths([existing, missing], project_root=tmp_path, fix=True)

    assert str(missing) in str(excinfo.value)
    mock_check_path.assert_not_called()


@patch(
    "readability.checking._check_path",
    return_value=CheckReport(
        findings=True,
        skipped={"pyrefly"},
        failed={"ruff"},
    ),
)
def test_check_paths_returns_report_without_cli_status_prose(
    mock_check_path: MagicMock,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Library callers receive report states without CLI policy or summaries."""
    path = tmp_path / "example.py"
    path.touch()

    report = check_paths([path])

    assert report.findings is True
    assert report.skipped == {"pyrefly"}
    assert report.failed == {"ruff"}
    assert capsys.readouterr() == ("", "")
    mock_check_path.assert_called_once()


@patch("readability.cli.check_paths", return_value=CheckReport(ran={"ruff"}))
def test_check_command_delegates_to_public_api(
    mock_check_paths: MagicMock, tmp_path: Path
) -> None:
    """The CLI keeps its output policy while delegating check execution."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("script.py").touch()
        result = runner.invoke(cli, ["check", "script.py"])

    mock_check_paths.assert_called_once_with(("script.py",), fix=False)
    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == "No findings in 1 path(s) (ruff).\n"


@patch("shutil.which")
@patch("subprocess.run")
def test_check_command_exits_nonzero_on_format_findings(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Tests that formatting findings produce a non-zero exit code.

    Args:
        mock_run: The mocked subprocess.run function.
        mock_which: The mocked shutil.which function.
        tmp_path: The temporary directory fixture.
    """
    mock_which.side_effect = lambda x: x if x == "ruff" else None

    # Only `ruff format --check` reports findings
    def run_side_effect(cmd, **kwargs):
        if "format" in cmd:
            return MagicMock(
                returncode=1, stdout="Would reformat: script.py", stderr=""
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = run_side_effect

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "script.py"])

    assert "formatting findings" in result.output
    assert result.exit_code == 1


@patch("shutil.which")
@patch("subprocess.run")
def test_check_command_exits_nonzero_on_check_findings(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Tests that linter findings produce a non-zero exit code.

    Args:
        mock_run: The mocked subprocess.run function.
        mock_which: The mocked shutil.which function.
        tmp_path: The temporary directory fixture.
    """
    mock_which.side_effect = lambda x: x if x == "ruff" else None

    # Only `ruff check` reports findings
    def run_side_effect(cmd, **kwargs):
        if "check" in cmd and "format" not in cmd:
            return MagicMock(
                returncode=1, stdout="E501 line too long", stderr=""
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = run_side_effect

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "script.py"])

    assert "findings" in result.output
    assert result.exit_code == 1


@patch("shutil.which")
@patch("subprocess.run")
def test_check_command_fix_exits_nonzero_on_remaining_findings(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Tests that findings remaining after --fix produce a non-zero exit.

    Args:
        mock_run: The mocked subprocess.run function.
        mock_which: The mocked shutil.which function.
        tmp_path: The temporary directory fixture.
    """
    mock_which.side_effect = lambda x: x if x == "ruff" else None

    # Fixers succeed, but the check step still reports findings
    def run_side_effect(cmd, **kwargs):
        if "check" in cmd and "--fix" not in cmd:
            return MagicMock(
                returncode=1, stdout="E501 line too long", stderr=""
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = run_side_effect

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "--fix", "script.py"])

    assert result.exit_code == 1


@patch("shutil.which")
def test_check_fails_when_no_tool_could_run(
    mock_which: MagicMock, tmp_path: Path
) -> None:
    """A check that ran nothing must not report a clean bill of health.

    This is how the command became a no-op in a container image: the tools
    it shells out to were absent, every one was skipped, and it exited 0
    saying it found nothing, so the gate went green having checked nothing.
    """
    mock_which.return_value = None

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "script.py"])

    assert result.exit_code != 0
    output = (result.stdout + result.stderr).lower()
    assert "no findings" not in output
    # The caller has to be told which tools were missing to fix it
    assert "ruff" in output
    assert "pyrefly" in output


@patch("shutil.which")
@patch("subprocess.run")
def test_check_reports_partially_skipped_tools(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Passing on half the tools is not the same as passing."""
    mock_which.side_effect = lambda x: x if x == "ruff" else None
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "script.py"])

    # Something ran and found nothing, so this is a pass, but a partial one
    assert result.exit_code == 0
    output = result.stdout + result.stderr
    assert "no findings" in output.lower()
    assert "pyrefly" in output


@patch("shutil.which")
@patch("subprocess.run")
def test_check_does_not_report_inapplicable_tools(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """A tool that owns no requested file is not reported as skipped."""
    mock_which.side_effect = lambda x: x if x in ("ruff", "pyrefly") else None
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "script.py"])

    assert result.exit_code == 0
    output = result.stdout + result.stderr
    # Neither tool owns Python, so neither is a skipped tool.
    assert "biome" not in output


@patch("shutil.which")
@patch("subprocess.run")
def test_check_runs_bundled_default_tools_without_a_trigger(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Bundled defaults exist so a project needs no config of its own.

    Gating ruff on a config file made those defaults unreachable by the
    projects that most needed them: an empty pyproject.toml was the whole
    difference between checking a file and checking nothing.
    """
    mock_which.side_effect = lambda x: x if x in ("ruff", "pyrefly") else None
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "script.py"])

    assert result.exit_code == 0
    assert "nothing was checked" not in (result.stdout + result.stderr)
    called = [call.args[0][0] for call in mock_run.call_args_list]
    assert "ruff" in called
    assert "pyrefly" in called


@patch("shutil.which")
@patch("subprocess.run")
def test_check_runs_biome_without_a_project_config(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """A bundled Biome config covers web files in unconfigured projects."""
    mock_which.side_effect = lambda x: x
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("script.ts").touch()

        result = runner.invoke(cli, ["check", "script.ts"])

    assert result.exit_code == 0
    called = [call.args[0] for call in mock_run.call_args_list]
    assert any(command[0] == "biome" for command in called)
    config_path = str(_bundled_config("biome"))
    biome_commands = [command for command in called if command[0] == "biome"]
    assert biome_commands
    assert all(
        command[command.index("--config-path") + 1] == config_path
        for command in biome_commands
    )


@pytest.mark.parametrize(
    "extension", (".md", ".yml", ".yaml", ".scss", ".jsonl")
)
@patch("shutil.which")
@patch("subprocess.run")
def test_unsupported_formats_run_no_tool(
    mock_run: MagicMock,
    mock_which: MagicMock,
    tmp_path: Path,
    extension: str,
) -> None:
    """Unsupported document formats run no tool."""
    mock_which.side_effect = lambda name: name

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(f"document{extension}").touch()

        result = runner.invoke(cli, ["check", f"document{extension}"])

    assert result.exit_code == 0
    assert "nothing was checked" in result.stderr
    mock_run.assert_not_called()


@patch("shutil.which")
@patch("subprocess.run")
def test_mixed_directory_scopes_biome_to_owned_files(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Biome receives its files, never an unrestricted mixed-format tree."""
    mock_which.side_effect = lambda name: name
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("project").mkdir()
        Path("project/README.md").touch()
        Path("project/data.json").touch()
        Path("project/script.js").touch()

        result = runner.invoke(cli, ["check", "project"])

    assert result.exit_code == 0
    commands = [invocation.args[0] for invocation in mock_run.call_args_list]
    biome_commands = [command for command in commands if command[0] == "biome"]
    assert len(biome_commands) == 2
    assert all(
        command[-2:] == ["project/data.json", "project/script.js"]
        for command in biome_commands
    )
    assert all("project" not in command for command in commands)


@patch("shutil.which")
@patch("subprocess.run")
def test_direct_files_and_directory_have_the_same_formatter_owners(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Path shape changes tool targets, not extension ownership."""
    mock_which.side_effect = lambda name: name
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("project").mkdir()
        paths = (
            Path("project/README.md"),
            Path("project/data.json"),
            Path("project/script.js"),
        )
        for path in paths:
            path.touch()

        directory_result = runner.invoke(cli, ["check", "project"])
        directory_owners = {
            invocation.args[0][0] for invocation in mock_run.call_args_list
        }
        mock_run.reset_mock()
        file_result = runner.invoke(
            cli, ["check", *(str(path) for path in paths)]
        )
        file_owners = {
            invocation.args[0][0] for invocation in mock_run.call_args_list
        }

    assert directory_result.exit_code == 0
    assert file_result.exit_code == 0
    assert directory_owners == file_owners == {"biome"}


@pytest.mark.parametrize("fix", (False, True))
@patch("shutil.which")
@patch("subprocess.run")
def test_configless_go_uses_gofmt_for_check_and_fix(
    mock_run: MagicMock,
    mock_which: MagicMock,
    tmp_path: Path,
    fix: bool,
) -> None:
    """Standalone Go files use symmetric gofmt commands without go.mod."""
    mock_which.side_effect = lambda name: name if name == "gofmt" else None
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("main.go").touch()
        arguments = ["check", "main.go"]
        if fix:
            arguments.append("--fix")

        result = runner.invoke(cli, arguments)

    assert result.exit_code == 0
    expected = ["gofmt", "-w" if fix else "-l", "main.go"]
    assert [invocation.args[0] for invocation in mock_run.call_args_list] == [
        expected
    ]


@patch("shutil.which")
@patch("subprocess.run")
def test_gofmt_scopes_a_directory_to_go_files(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Gofmt receives recursive Go files instead of an invalid directory."""
    mock_which.side_effect = lambda name: name if name == "gofmt" else None
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("src/nested").mkdir(parents=True)
        Path("src/main.go").touch()
        Path("src/nested/helper.go").touch()
        Path("src/notes.txt").touch()

        result = runner.invoke(cli, ["check", "src"])

    assert result.exit_code == 0
    assert [invocation.args[0] for invocation in mock_run.call_args_list] == [
        ["gofmt", "-l", "src/main.go", "src/nested/helper.go"]
    ]


@patch("shutil.which")
@patch("subprocess.run")
def test_gofmt_fix_failure_is_reported(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """A failed gofmt write cannot be reported as a clean fix."""
    mock_which.side_effect = lambda name: name if name == "gofmt" else None
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="invalid Go syntax"
    )

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("main.go").touch()

        result = runner.invoke(cli, ["check", "main.go", "--fix"])

    assert result.exit_code == 1
    assert "gofmt formatting findings" in result.output


@patch("shutil.which")
@patch("subprocess.run")
def test_large_biome_directory_uses_bounded_commands(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Explicit file scoping cannot exceed a conservative argv budget."""
    mock_which.side_effect = lambda name: name if name == "biome" else None
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("src").mkdir()
        for index in range(600):
            name = f"{index:04d}_{'x' * 180}.js"
            Path("src", name).touch()

        result = runner.invoke(cli, ["check", "src"])

    assert result.exit_code == 0
    commands = [invocation.args[0] for invocation in mock_run.call_args_list]
    assert len(commands) > 2
    assert all(
        sum(len(argument) + 1 for argument in command) < 20_000
        for command in commands
    )


@patch("shutil.which", return_value=None)
def test_missing_gofmt_is_reported_for_configless_go(
    mock_which: MagicMock, tmp_path: Path
) -> None:
    """A missing gofmt leaves standalone Go explicitly unverified."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("main.go").touch()

        result = runner.invoke(cli, ["check", "main.go"])

    assert result.exit_code == 1
    assert "gofmt" in result.stderr
    assert "nothing was verified" in result.stderr


@patch("shutil.which")
@patch("subprocess.run")
def test_bundled_tools_do_not_claim_an_unsupported_directory(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """A zero-file tool run must not turn nothing checked into a clean pass."""
    mock_which.side_effect = lambda x: x
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("notes.txt").touch()

        result = runner.invoke(cli, ["check", "."])

    assert result.exit_code == 0
    output = result.stdout + result.stderr
    assert "nothing was checked" in output
    assert "No findings" not in output
    mock_run.assert_not_called()


@patch("shutil.which")
@patch("subprocess.run")
def test_biome_zero_file_result_is_not_reported_as_clean(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Ignored matches must not make an unverified directory look clean."""
    mock_which.side_effect = lambda x: x if x == "biome" else None
    checked_nothing = MagicMock(
        returncode=0,
        stdout="",
        stderr="Checked 0 files in 1ms. No fixes applied.\n",
    )
    formatted_nothing = MagicMock(
        returncode=0,
        stdout="Formatted 0 files in 1ms. No fixes applied.\n",
        stderr="",
    )

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # An ignored directory the walk does not prune, so Biome is still
        # handed the file and gets to report that it checked nothing
        Path(".gitignore").write_text("generated/\n")
        Path("generated/example").mkdir(parents=True)
        Path("generated/example/index.js").touch()
        Path("README.txt").touch()

        for extra_args, expected_calls in (([], 2), (["--fix"], 3)):
            mock_run.side_effect = (
                [checked_nothing, checked_nothing]
                if not extra_args
                else [formatted_nothing, checked_nothing, checked_nothing]
            )
            result = runner.invoke(cli, ["check", ".", *extra_args])

            assert result.exit_code == 0
            output = result.stdout + result.stderr
            assert "nothing was checked" in output
            assert "No findings" not in output
            assert mock_run.call_count == expected_calls
            mock_run.reset_mock()


@patch("shutil.which")
@patch("subprocess.run")
def test_biome_zero_file_path_is_not_hidden_by_a_checked_path(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """A clean aggregate identifies requested paths Biome did not inspect."""
    mock_which.side_effect = lambda x: x if x == "biome" else None
    checked_files = MagicMock(returncode=0, stdout="", stderr="")
    checked_nothing = MagicMock(
        returncode=0,
        stdout="Checked 0 files in 1ms. No fixes applied.\n",
        stderr="",
    )

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".gitignore").write_text("ignored/\n")
        Path("src").mkdir()
        Path("src/good.js").touch()
        Path("ignored").mkdir()
        Path("ignored/generated.js").touch()
        mock_run.side_effect = [
            checked_files,
            checked_files,
            checked_nothing,
            checked_nothing,
        ]

        result = runner.invoke(cli, ["check", "src", "ignored"])

    assert result.exit_code == 0
    assert "nothing was checked for 1 path(s): ignored" in result.stderr
    assert "No findings in 1 path(s) (biome)." in result.stderr
    assert "No findings in 2 path(s)" not in result.stderr


@patch("shutil.which")
@patch("subprocess.run")
def test_biome_zero_file_failure_still_exits_nonzero(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """A configuration failure must not become a successful no-tool result."""
    mock_which.side_effect = lambda x: x if x == "biome" else None
    checked_nothing = MagicMock(
        returncode=1,
        stdout="Checked 0 files in 1ms.\nConfiguration error.\n",
        stderr="",
    )
    formatted_nothing = MagicMock(
        returncode=1,
        stdout="Formatted 0 files in 1ms.\nConfiguration error.\n",
        stderr="",
    )

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("script.ts").touch()

        for extra_args in ([], ["--fix"]):
            mock_run.side_effect = (
                [checked_nothing, checked_nothing]
                if not extra_args
                else [formatted_nothing, checked_nothing, checked_nothing]
            )
            result = runner.invoke(cli, ["check", "script.ts", *extra_args])

            assert result.exit_code == 1
            output = result.stdout + result.stderr
            assert "biome findings" in output
            assert "nothing was checked" not in output
            mock_run.reset_mock()


@patch("shutil.which")
@patch("subprocess.run")
def test_bundled_biome_applies_to_a_nested_web_file(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Directory applicability still finds nested web files."""
    mock_which.side_effect = lambda x: x if x == "biome" else None
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("src").mkdir()
        Path("src/script.ts").touch()

        result = runner.invoke(cli, ["check", "."])

    assert result.exit_code == 0
    assert "No findings in 1 path(s) (biome)." in result.stderr
    called = [call.args[0][0] for call in mock_run.call_args_list]
    assert called == ["biome", "biome"]


@patch("shutil.which")
@patch("subprocess.run")
def test_check_with_project_config_runs_biome(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """With biome.json present, biome runs as it always did."""
    mock_which.side_effect = lambda x: x
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("biome.json").touch()
        Path("script.ts").touch()

        result = runner.invoke(cli, ["check", "script.ts"])

    assert result.exit_code == 0
    # Reached directly because `which` finds it; npx is only the fallback
    called = [call.args[0][0] for call in mock_run.call_args_list]
    assert "biome" in called


def test_biome_is_invoked_by_its_real_package_name(tmp_path: Path) -> None:
    """'biome' on npm is an unrelated package, not the linter.

    `npx -y biome` fetched and ran biome@0.3.3, "a simple way to manage
    environment variables on a per-project basis", which exits 0 whatever
    it is handed. Biome linting therefore never ran at all.
    """
    tools = {
        tool.name: tool
        for tool in _get_tool_definitions(
            _source_file(tmp_path, "f.ts"), tmp_path
        )
    }
    biome = tools["biome"]

    for key in ("check", "check_format", "fix", "format"):
        command = getattr(biome, key)
        assert "biome" not in command, (
            f"{key} names the wrong npm package: {command}"
        )
        assert any(a.startswith("@biomejs/biome") for a in command)


def test_node_tools_prefer_a_project_local_install(tmp_path: Path) -> None:
    """A project's own version beats whatever npx would fetch."""
    local = tmp_path / "node_modules" / ".bin"
    local.mkdir(parents=True)
    binary = local / "biome"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    tools = {
        tool.name: tool
        for tool in _get_tool_definitions(
            _source_file(tmp_path, "f.ts"), tmp_path
        )
    }

    cmd = tools["biome"].check
    assert cmd[0] == str(binary)
    assert "npx" not in cmd


def test_node_tools_fall_back_to_npx(tmp_path: Path) -> None:
    """With nothing installed, npx stays the way to reach them."""
    tools = {
        tool.name: tool
        for tool in _get_tool_definitions(
            _source_file(tmp_path, "f.ts"), tmp_path
        )
    }

    cmd = tools["biome"].check
    assert cmd[:2] == ("npx", "-y")
    assert cmd[2].startswith("@biomejs/biome@")


def test_python_tools_fall_back_to_a_floored_runner(tmp_path: Path) -> None:
    """A tool nobody installed is still reachable, and still reproducible.

    uvx downloads once and serves every later run from its cache, so the
    package need not carry 50 MB of binaries to make check work. A floor
    keeps what it fetches new enough for the bundled configs.
    """
    with patch("shutil.which", return_value=None):
        tools = {
            tool.name: tool
            for tool in _get_tool_definitions(
                _source_file(tmp_path, "f.py"), tmp_path
            )
        }

    ruff = tools["ruff"].check
    assert ruff[0] == "uvx"
    assert ruff[1].startswith("ruff>=")

    pyrefly = tools["pyrefly"].check
    assert pyrefly[0] == "uvx"
    assert pyrefly[1].startswith("pyrefly>=")


def test_an_installed_tool_beats_the_runner(tmp_path: Path) -> None:
    """Nothing is downloaded when the machine already has the tool."""
    with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
        tools = {
            tool.name: tool
            for tool in _get_tool_definitions(
                _source_file(tmp_path, "f.py"), tmp_path
            )
        }

    assert tools["ruff"].check[0] == "/usr/bin/ruff"
    assert "uvx" not in tools["ruff"].check


def test_a_local_file_that_cannot_run_is_not_chosen(tmp_path: Path) -> None:
    """Existing is not the same as being runnable.

    A non-executable file, or a directory, used to win resolution and then
    fail the very next gate, hiding both the copy on PATH and the runner.
    Three real findings disappeared and the command exited 0.
    """
    node = tmp_path / "node_modules" / ".bin"
    node.mkdir(parents=True)
    (node / "biome").touch(mode=0o644)

    with patch("shutil.which", return_value=None):
        tools = {
            tool.name: tool
            for tool in _get_tool_definitions(
                _source_file(tmp_path, "f.ts"), tmp_path
            )
        }

    assert tools["biome"].check[0] == "npx"


def test_resolution_ignores_a_project_virtualenv(tmp_path: Path) -> None:
    """A venv on the path is already on PATH; trusting the directory is not.

    Preferring ./.venv/bin meant `readability check` executed a binary
    belonging to whatever directory the caller happened to stand in, which
    under --fix was handed write access to their sources. Activating a venv
    puts its tools on PATH anyway, so the lookup bought nothing.
    """
    local = tmp_path / ".venv" / "bin"
    local.mkdir(parents=True)
    binary = local / "ruff"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    with patch("shutil.which", return_value=None):
        tools = {
            tool.name: tool
            for tool in _get_tool_definitions(
                _source_file(tmp_path, "f.py"), tmp_path
            )
        }

    assert str(binary) not in tools["ruff"].check
    assert tools["ruff"].check[0] == "uvx"


def test_every_runner_floors_a_version(tmp_path: Path) -> None:
    """A floor keeps a fetched tool new enough for the bundled configs.

    No ceilings: one would freeze whoever installed nothing at whatever was
    current when this shipped, and tie a release of this package to every
    release of theirs. The first ceiling written here was stale in two days.
    """
    for binary, runner in TOOL_RUNNERS.items():
        spec = runner[-1]
        assert ">=" in spec, f"{binary} has no floor: {spec}"
        assert "<" not in spec, f"{binary} has a ceiling: {spec}"


@patch("shutil.which")
@patch("subprocess.run")
def test_a_tool_that_cannot_launch_is_not_counted_as_having_run(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Resolving a tool is not the same as a process completing.

    A venv whose interpreter has moved leaves an executable script that
    fails at exec. That was logged and then counted among the tools that
    ran, so check reported a clean pass naming a tool which never started.
    """
    mock_which.side_effect = lambda x: x if x == "ruff" else None
    mock_run.side_effect = OSError(2, "No such file or directory")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "script.py"])

    assert result.exit_code != 0
    output = (result.stdout + result.stderr).lower()
    assert "no findings" not in output
    assert "ruff" in output


@patch("shutil.which")
@patch("subprocess.run")
def test_a_tool_that_times_out_is_a_failure_not_a_pass(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """A tool killed at the timeout verified nothing."""
    mock_which.side_effect = lambda x: x if x == "ruff" else None
    mock_run.side_effect = subprocess.TimeoutExpired("ruff", 60)

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "script.py"])

    assert result.exit_code != 0
    assert "no findings" not in (result.stdout + result.stderr).lower()


@patch("shutil.which")
@patch("subprocess.run")
def test_fix_still_reports_what_it_could_not_fix(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """A fixer exits non-zero when findings remain, which is not a failure.

    Treating that as one aborted the run before the check that reports what
    is left, so --fix exited 0 claiming clean while the plain check exited 1
    on the very same files.
    """
    mock_which.side_effect = lambda x: x if x == "ruff" else None
    mock_run.return_value = MagicMock(returncode=1, stdout="E999", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "--fix", "script.py"])

    # The reporting check runs after the fixers, so findings still surface
    commands = [call.args[0] for call in mock_run.call_args_list]
    assert any("--fix" not in c and "check" in c for c in commands)
    assert result.exit_code != 0
