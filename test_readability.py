import json
import logging
import os
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import click
import pytest
import requests
from click.testing import CliRunner

from readability import (
    LANGUAGE_MAP,
    TOOL_RUNNERS,
    CheckReport,
    _bundled_config,
    _get_tool_definitions,
    _has_project_config,
    _iter_heading_lines,
    _unique_reference,
    check_paths,
    cli,
    convert_to_markdown,
    extract_section,
    find_headings,
    find_mentions,
    get_guide,
    get_guide_content,
    get_guides_dir,
    get_local_path,
    main,
    parse_headings,
)


def test_get_guide_unsupported() -> None:
    """Tests that an unsupported language raises a UsageError."""
    with pytest.raises(click.UsageError) as excinfo:
        get_guide("nonexistent")
    assert "not supported" in str(excinfo.value)


@patch("requests.get")
def test_get_guide_content_success(mock_get: MagicMock) -> None:
    """Tests successful content fetching.

    Args:
        mock_get: The mocked requests.get function.
    """
    mock_get.return_value.text = "raw content"
    mock_get.return_value.status_code = 200

    content = get_guide_content("http://example.com")
    assert content == "raw content"
    mock_get.assert_called_once_with("http://example.com", timeout=10)


@patch("requests.get")
def test_get_guide_content_failure(mock_get: MagicMock) -> None:
    """Tests content fetching failure.

    Args:
        mock_get: The mocked requests.get function.
    """
    mock_get.side_effect = requests.exceptions.RequestException("Network error")

    with pytest.raises(click.ClickException) as excinfo:
        get_guide_content("http://example.com")
    assert "Failed to fetch style guide" in str(excinfo.value)


def test_convert_to_markdown_md() -> None:
    """Tests conversion for Markdown files (should remain unchanged)."""
    content = "# Markdown"
    result = convert_to_markdown(content, "guide.md")
    assert result == content


def test_convert_to_markdown_html() -> None:
    """Tests conversion for HTML files."""
    content = "<h1>Header</h1>"
    result = convert_to_markdown(content, "guide.html")
    assert "# Header" in result


def test_convert_to_markdown_keeps_underscores() -> None:
    """Tests that identifiers survive conversion without escapes."""
    content = "<h3>thread_local Variables</h3><p>from __future__ import</p>"
    result = convert_to_markdown(content, "guide.html")
    assert "thread_local" in result
    assert "from __future__ import" in result
    assert "\\_" not in result


def test_convert_to_markdown_xml() -> None:
    """Tests conversion for XML files."""
    content = "<guide><title>Vim</title></guide>"
    result = convert_to_markdown(content, "guide.xml")
    assert "Vim" in result


def test_get_local_path() -> None:
    """Tests the get_local_path function for flattening filenames."""
    # Simple filename
    assert os.path.basename(get_local_path("pyguide.md")) == "pyguide.md"
    # Nested filename
    assert os.path.basename(get_local_path("go/guide.md")) == "go-guide.md"
    # Another nested filename
    assert (
        os.path.basename(get_local_path("docguide/style.md"))
        == "docguide-style.md"
    )
    # File with different extension
    assert os.path.basename(get_local_path("cppguide.html")) == "cppguide.md"


@patch("readability.get_guide_content")
def test_get_guide_integration(
    mock_get_content: MagicMock, tmp_path: Path
) -> None:
    """Tests the orchestration in get_guide.

    Args:
        mock_get_content: The mocked get_guide_content function.
        tmp_path: The temporary directory fixture.
    """
    mock_get_content.return_value = "# Python Guide"

    # Mock get_guides_dir to point to tmp_path
    with patch("readability.get_guides_dir", return_value=str(tmp_path)):
        content = get_guide("python", remote=True)
        assert content == "# Python Guide"
        assert os.path.exists(os.path.join(tmp_path, "pyguide.md"))


def test_cli_unsupported() -> None:
    """Tests CLI output for unsupported language."""
    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "nonexistent"])
    assert result.exit_code == 1
    assert "Error" in result.output


@patch("readability.get_guide")
def test_cli_verbose(mock_guide: MagicMock) -> None:
    """Tests CLI with verbose flag.

    Args:
        mock_guide: The mocked get_guide function.
    """
    mock_guide.return_value = "# Style Guide\n\n## Section\n\nBody.\n"
    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "--verbose"])
    assert result.exit_code == 0
    assert "Section" in result.stdout


@patch("readability.get_guide_content")
def test_sync_command(
    mock_get_content: MagicMock,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tests the sync command.

    Args:
        mock_get_content: The mocked get_guide_content function.
        tmp_path: The temporary directory fixture.
        caplog: The log capture fixture.
    """
    mock_get_content.return_value = "content"

    with patch("readability.get_guides_dir", return_value=str(tmp_path)):
        runner = CliRunner()
        result = runner.invoke(cli, ["sync"])

        assert result.exit_code == 0
        # Progress is an outcome the caller asked for, so it is reported
        # regardless of log level rather than logged as narration.
        assert "Sync complete" in result.stderr
        # Check if at least one guide was "synced" (written to tmp_path)
        assert len(os.listdir(tmp_path)) > 0


def test_guide_without_a_language_lists_them() -> None:
    """Naming no language names no guide, so the answer is which exist."""
    runner = CliRunner()
    result = runner.invoke(cli, ["guide"])
    assert result.exit_code == 0
    assert "Supported languages and their aliases:" in result.output
    # Check for some common languages
    assert "python" in result.output
    assert "c++, cpp" in result.output
    assert "c#, csharp" in result.output


def test_guide_language_list_marks_the_cached_ones(tmp_path: Path) -> None:
    """Tests that the language list shows [cached] correctly.

    Args:
        tmp_path: The temporary directory fixture.
    """
    # Mock get_guides_dir to point to tmp_path
    with patch("readability.get_guides_dir", return_value=str(tmp_path)):
        # Create a mock cached file for python (pyguide.md -> pyguide.md)
        python_cache = tmp_path / "pyguide.md"
        python_cache.write_text("content")

        runner = CliRunner()
        result = runner.invoke(cli, ["guide"])

        assert result.exit_code == 0
        assert "python [cached]" in result.output
        # cpp is not cached
        assert "c++, cpp [cached]" not in result.output
        assert "c++, cpp" in result.output


def test_readability_cache_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests that READABILITY_CACHE environment variable is respected.

    Args:
        tmp_path: The temporary directory fixture.
        monkeypatch: The monkeypatch fixture.
    """
    custom_cache = str(tmp_path / "custom_guides")
    monkeypatch.setenv("READABILITY_CACHE", custom_cache)
    assert get_guides_dir() == custom_cache


def test_default_guides_dir() -> None:
    """Tests the default guides directory."""
    # Ensure environment variable is not set
    with patch.dict(os.environ, clear=True):
        guides_dir = get_guides_dir()
        assert guides_dir.endswith("guides")
        assert os.path.dirname(guides_dir) == os.path.dirname(
            os.path.abspath("readability.py")
        )


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
    # Verify ruff was called with the directory 'subdir'
    assert [
        "ruff",
        "check",
        "--force-exclude",
        "--config",
        cfg,
        "subdir",
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

    tools = {t["name"]: t for t in _get_tool_definitions(py_file, tmp_path)}
    assert "--config" not in tools["ruff"]["check"]
    assert "--config" not in tools["ruff"]["format"]
    assert "--config" not in tools["pyrefly"]["check"]

    ts_file = tmp_path / "script.ts"
    ts_file.touch()
    (tmp_path / "biome.json").write_text("{}")
    tools = {t["name"]: t for t in _get_tool_definitions(ts_file, tmp_path)}
    for command in ("check", "check_format", "fix", "format"):
        assert "--config-path" not in tools["biome"][command]


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
            tool["name"]: tool
            for tool in _get_tool_definitions(tmp_path, tmp_path)
        }
        file_tools = {
            tool["name"]: tool
            for tool in _get_tool_definitions(py_file, tmp_path)
        }

    assert directory_tools["pyrefly"]["check"] == ["pyrefly", "check"]
    assert directory_tools["pyrefly"]["cwd"] == tmp_path
    assert file_tools["pyrefly"]["check"] == [
        "pyrefly",
        "check",
        str(py_file),
    ]
    assert "cwd" not in file_tools["pyrefly"]


def test_pyrefly_bundled_config_keeps_an_explicit_directory(
    tmp_path: Path,
) -> None:
    """Project mode cannot use a config rooted in the installed package."""
    (tmp_path / "main.py").touch()

    with patch("shutil.which", side_effect=lambda name: name):
        tools = {
            tool["name"]: tool
            for tool in _get_tool_definitions(tmp_path, tmp_path)
        }

    assert tools["pyrefly"]["check"] == [
        "pyrefly",
        "check",
        "--config",
        str(_bundled_config("pyrefly")),
        str(tmp_path),
    ]
    assert "cwd" not in tools["pyrefly"]


def test_biome_bundled_config_is_injected_into_every_command(
    tmp_path: Path,
) -> None:
    """Linting, formatting, and their fix forms share the safe default."""
    ts_file = tmp_path / "script.ts"
    ts_file.touch()

    tools = {t["name"]: t for t in _get_tool_definitions(ts_file, tmp_path)}
    config_path = str(_bundled_config("biome"))

    for command_name in ("check", "check_format", "fix", "format"):
        command = tools["biome"][command_name]
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

    tools = {t["name"]: t for t in _get_tool_definitions(ts_file, tmp_path)}

    for command_name in ("check", "check_format", "fix", "format"):
        command = tools["biome"][command_name]
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


@patch("readability._check_path")
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


@patch("readability._check_path")
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


@patch("readability._check_path", return_value=CheckReport(ran={"ruff"}))
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


@patch("readability._check_path")
def test_check_paths_with_no_paths_reports_that_nothing_ran(
    mock_check_path: MagicMock,
) -> None:
    """An empty request must not look like a verified clean result."""
    report = check_paths([])

    assert report == CheckReport()
    assert not report.ran
    mock_check_path.assert_not_called()


@patch("readability._check_path")
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
    "readability._check_path",
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


@patch("readability.check_paths", return_value=CheckReport(ran={"ruff"}))
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


# A miniature guide with numbered headings, repeated leaf headings, and a
# fenced code block whose comments look like Markdown headings.
NUMBERED_GUIDE = """# Sample Style Guide

Intro body.

## 1 Background\x20

Background body.

## 2 Language Rules\x20

### 2.1 Lint\x20

Lint body.

#### 2.1.4 Decision\x20

Run the linter over your code.

### 2.2 Imports\x20

Imports body.

#### 2.2.4 Decision\x20

Use full package paths.

##### 2.2.4.1 Exemptions\x20

Exemptions body.

## 3 Style\x20

```bash
# Not a heading
## Also not a heading
```

Style body.
"""

# A miniature guide in the shape of the unnumbered majority (shell, ts, cpp).
UNNUMBERED_GUIDE = """# Shell Sample Guide

## Background

### Which Shell to Use

Use bash.

## Comments

### File Header

```bash
#!/bin/bash
# Perform hot backups of Oracle databases.
```

Header body.

### Function Comments

Function body.
"""


def _write_guide(
    tmp_path: Path, content: str, name: str = "pyguide.md"
) -> None:
    """Places a guide in a temporary cache directory."""
    (tmp_path / name).write_text(content)


def _outline_entry(lines: list[str], heading_text: str) -> str:
    """Returns the single outline line that ends with the given heading."""
    matches = [ln for ln in lines if ln.rstrip().endswith(heading_text)]
    assert len(matches) == 1, f"expected one entry for {heading_text!r}"
    return matches[0].strip()


def test_parse_headings_ignores_fenced_code_blocks() -> None:
    """Tests that comments inside code fences are not read as headings."""
    headings = parse_headings(NUMBERED_GUIDE)

    titles = [h.title for h in headings]
    assert "Not a heading" not in titles
    assert "Also not a heading" not in titles
    assert titles.count("Decision") == 2


def test_parse_headings_splits_section_numbers() -> None:
    """Tests that a leading section number is split from the heading text."""
    headings = {h.title: h for h in parse_headings(NUMBERED_GUIDE)}

    assert headings["Imports"].number == "2.2"
    assert headings["Imports"].level == 3
    assert headings["Imports"].text == "2.2 Imports"

    # Unnumbered guides leave the number empty and keep the full title
    unnumbered = {h.title: h for h in parse_headings(UNNUMBERED_GUIDE)}
    assert unnumbered["Which Shell to Use"].number == ""
    assert unnumbered["Which Shell to Use"].text == "Which Shell to Use"


def test_parse_headings_uses_the_guides_own_numbers() -> None:
    """Tests that a guide which numbers its sections addresses them that way.

    Its numbers are what the published guide calls its rules, so a citation
    taken from here has to match. A positional index would drift wherever
    the guide skips a number.
    """
    headings = parse_headings(NUMBERED_GUIDE)

    # The document title is the tree root, so it carries no index
    assert headings[0].index == ""

    indices = {h.text: h.index for h in headings}
    assert indices["1 Background"] == "1"
    assert indices["2.2 Imports"] == "2.2"

    # Repeated headings keep the numbers the guide gives them
    assert indices["2.1.4 Decision"] == "2.1.4"
    assert indices["2.2.4 Decision"] == "2.2.4"
    assert indices["2.2.4.1 Exemptions"] == "2.2.4.1"


def test_parse_headings_numbers_a_guide_that_does_not() -> None:
    """Tests that positional indices cover the eleven unnumbered guides."""
    unnumbered = [h.index for h in parse_headings(UNNUMBERED_GUIDE)]

    assert unnumbered == ["", "1", "1.1", "2", "2.1", "2.2"]


def test_parse_headings_index_follows_a_skipped_number() -> None:
    """Tests that a gap in the guide's numbering is preserved, not closed.

    pyguide has no 2.15 at all: calling its 2.16 by that name would cite a
    section the published guide does not have.
    """
    headings = parse_headings("# Guide\n\n## 2.14 A\n\n## 2.16 B\n")

    assert [h.index for h in headings] == ["", "2.14", "2.16"]


def test_parse_headings_indices_survive_skipped_levels() -> None:
    """Tests that a shallower heading after a deeper one gets its own index.

    Guides skip levels (tsguide follows an h4 with an h3). Counting each level
    independently rather than counting siblings hands both the same index.
    """
    content = "# Guide\n\n## One\n\n#### Deep\n\n### Back\n\n## Two\n"

    assert [h.index for h in parse_headings(content)] == [
        "",
        "1",
        "1.1",
        "1.2",
        "2",
    ]


def test_cli_outline_prints_the_heading_tree(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that a bare invocation prints the tree, without bodies."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()

    # The document title heads the tree and carries no index of its own
    assert lines[0].strip() == "Sample Style Guide"

    # The index is the guide's own number, so it appears once per line
    assert "1  Background" in lines[1]
    assert any(line.strip() == "2.2  Imports" for line in lines)
    assert any(line.strip() == "2.2.4  Decision" for line in lines)
    assert "2.2 2.2 Imports" not in result.stdout

    # Bodies stay out of the outline
    assert "Imports body." not in result.stdout


def test_cli_outline_ignores_headings_inside_code_blocks(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that comments in fenced samples never surface as sections."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python"])

    assert result.exit_code == 0
    assert "Not a heading" not in result.stdout
    assert "Also not a heading" not in result.stdout


def test_cli_section_by_number(tmp_path: Path, monkeypatch) -> None:
    """Tests that a section number works where the guide provides one."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "2.2"])

    assert result.exit_code == 0
    assert result.stdout.startswith("### 2.2 Imports")
    # A section carries its subsections but stops at the next same-level one
    assert "Use full package paths." in result.stdout
    assert "Exemptions body." in result.stdout
    assert "Style body." not in result.stdout
    assert "Lint body." not in result.stdout


def test_cli_section_by_title_is_case_insensitive(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that heading text alone selects a section, ignoring case."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "imports"])

    assert result.exit_code == 0
    assert result.stdout.startswith("### 2.2 Imports")


def test_cli_section_by_slug_in_an_unnumbered_guide(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests lookup in a guide that numbers nothing, which is the norm."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, UNNUMBERED_GUIDE, name="shellguide.md")

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "shell", "which-shell-to-use"])

    assert result.exit_code == 0
    assert result.stdout.startswith("### Which Shell to Use")
    assert "Use bash." in result.stdout


def test_cli_section_by_positional_index_without_numbering(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that the index reaches a section in a guide with no numbers."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, UNNUMBERED_GUIDE, name="shellguide.md")

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "shell", "2.1"])

    assert result.exit_code == 0
    assert result.stdout.startswith("### File Header")
    assert "Header body." in result.stdout
    assert "Function body." not in result.stdout


def test_cli_section_keeps_fenced_headings_in_the_body(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that a fenced comment does not truncate the section it sits in."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, UNNUMBERED_GUIDE, name="shellguide.md")

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "shell", "File Header"])

    assert result.exit_code == 0
    assert "# Perform hot backups of Oracle databases." in result.stdout
    assert "Header body." in result.stdout
    assert "Function body." not in result.stdout


def test_cli_section_ambiguous_reports_every_match(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that a repeated heading reports candidates instead of guessing."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "Decision"])

    assert result.exit_code == 1
    # Nothing goes to stdout, so a pipeline sees no half-right section
    assert result.stdout == ""
    assert "matches 2 headings" in result.stderr
    assert "Lint > Decision" in result.stderr
    assert "Imports > Decision" in result.stderr


def test_cli_section_path_disambiguates(tmp_path: Path, monkeypatch) -> None:
    """Tests that a parent-scoped path picks one of several same-named ones."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "Imports > Decision"])

    assert result.exit_code == 0
    assert result.stdout.startswith("#### 2.2.4 Decision")
    assert "Use full package paths." in result.stdout
    assert "Run the linter over your code." not in result.stdout


def test_cli_section_unknown_reference_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that an unmatched reference fails loudly rather than silently."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "Concurrency"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "no heading" in result.stderr.lower()
    assert "readability guide python" in result.stderr


def test_cli_section_rejects_an_unsupported_language(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that navigation validates the language like the guide does."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "nonexistent"])

    assert result.exit_code == 1
    assert "not supported" in result.output


def test_cli_section_drops_the_next_sections_anchors(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that HTML anchors belonging to the next heading are trimmed.

    Guides place a section's anchors on the lines above its heading, so a
    naive cut would end each section with the next one's link targets.
    """
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(
        tmp_path,
        '# Guide\n\n## First\n\nFirst body.\n\n<a id="s2-second"></a>\n\n'
        "## Second\n\nSecond body.\n",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "First"])

    assert result.exit_code == 0
    assert result.stdout.strip().endswith("First body.")


def test_cli_section_number_never_collides_with_an_index(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that a guide's own number addresses exactly its own section.

    While indices were positional this fixture was ambiguous: '3' named both
    the third section and the one the guide prints as 3. Taking the index
    from the guide's numbering removes that class of collision entirely.
    """
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(
        tmp_path,
        "# Guide\n\n## 1 Alpha\n\nA.\n\n### 1.1 Detail\n\nD.\n\n"
        "## 3 Beta\n\nB.\n\n## 4 Gamma\n\nG.\n",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "3"])

    assert result.exit_code == 0
    assert result.stdout.startswith("## 3 Beta")


def test_cli_section_suggestions_always_resolve(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that every reference offered for an ambiguous one is usable.

    Repeated titles stay ambiguous whatever the numbering: pyguide has 19
    headings called 'Decision'. Each suggestion must select one on its own.
    """
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "Decision"])

    assert result.exit_code == 1
    assert "matches 2 headings" in result.stderr

    # Follow each suggestion; all of them must select a single section
    suggestions = [
        line.split("(", 1)[0].strip().strip('"')
        for line in result.stderr.splitlines()
        if line.startswith("  ")
    ]
    assert len(suggestions) == 2
    for suggestion in suggestions:
        followed = runner.invoke(cli, ["guide", "python", suggestion])
        assert followed.exit_code == 0, f"{suggestion!r} did not resolve"


def test_cli_section_heading_containing_an_angle_bracket(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that '>' inside a heading is not read as a path separator.

    The TypeScript guide has a heading named '`Array<T>` Type'.
    """
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(
        tmp_path,
        "# Guide\n\n## Types\n\n### `Array<T>` Type\n\nArray body.\n",
        name="tsguide.md",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "typescript", "Array<T> Type"])

    assert result.exit_code == 0
    assert result.stdout.startswith("### `Array<T>` Type")
    assert "Array body." in result.stdout


# The heading scanner is deliberately not a Markdown parser (see the note on
# _iter_heading_lines). These tests are the evidence for that decision: the
# scope it covers, the constructs it rejects, and its behaviour over every
# guide actually shipped. A failure here means the scope has been outgrown.


def test_heading_scanner_rejects_non_headings() -> None:
    """Only ATX headings count, at the levels Markdown defines."""
    content = "\n".join(
        [
            "####### seven hashes is not a heading",
            "#NoSpaceAfterHash",
            "text # not at line start",
            "## Real Heading ##",
        ]
    )

    headings = [text for _, _, text in _iter_heading_lines(content)]

    assert headings == ["Real Heading"]


def test_heading_scanner_handles_both_fence_styles() -> None:
    """Guides use backticks, but tildes are equally valid Markdown."""
    content = "\n".join(
        [
            "# Title",
            "~~~",
            "# tilde-fenced comment",
            "~~~",
            "```python",
            "# backtick-fenced comment",
            "```",
            "   ```",
            "# indented-fence comment",
            "   ```",
            "## After",
        ]
    )

    headings = [text for _, _, text in _iter_heading_lines(content)]

    assert headings == ["Title", "After"]


def test_heading_scanner_treats_an_unclosed_fence_as_open() -> None:
    """A truncated document must not resume reporting code as headings."""
    content = "# Title\n```\n# still code\n"

    headings = [text for _, _, text in _iter_heading_lines(content)]

    assert headings == ["Title"]


def test_heading_scanner_reports_line_numbers() -> None:
    """Section slicing depends on the line each heading sits on."""
    content = "intro\n\n# Title\nbody\n\n## Next\n"

    found = [(line, level) for line, level, _ in _iter_heading_lines(content)]

    assert found == [(2, 1), (5, 2)]


def _shipped_guides() -> list[tuple[str, str]]:
    """Return (language, content) for every guide present on disk."""
    seen: set[str] = set()
    guides = []
    for language, filename in sorted(LANGUAGE_MAP.items()):
        path = get_local_path(filename)
        if filename in seen or not os.path.exists(path):
            continue
        seen.add(filename)
        with open(path, "r", encoding="utf-8") as f:
            guides.append((language, f.read()))
    return guides


def test_every_shipped_guide_parses_into_unique_sections() -> None:
    """The whole corpus is the evidence for a hand-rolled scanner.

    Each guide must yield at least one heading, indices unique enough to
    address any section, and no heading drawn from inside a code sample.
    """
    guides = _shipped_guides()
    assert len(guides) >= 10, f"expected the shipped corpus, got {len(guides)}"

    for language, content in guides:
        headings = parse_headings(content)
        assert headings, f"{language}: no headings found"

        indices = [h.index for h in headings]
        assert len(set(indices)) == len(indices), (
            f"{language}: duplicate positional indices"
        )

        # A heading taken from inside a fence would carry a comment marker
        # or a shebang, neither of which appears in a real guide heading.
        for heading in headings:
            assert not heading.title.startswith("!"), (
                f"{language}: shebang parsed as heading: {heading.title!r}"
            )


def test_every_shipped_guide_heading_is_addressable() -> None:
    """Every heading in the corpus must be reachable by some reference.

    A guide's own title carries no positional index — it is the whole
    document rather than a section within it — so it is addressed by name.
    Every other heading resolves by its index alone.
    """
    for language, content in _shipped_guides():
        headings = parse_headings(content)

        titles = [h for h in headings if not h.index]
        assert len(titles) == 1, (
            f"{language}: expected one unindexed title, got {len(titles)}"
        )
        assert titles[0] is headings[0], f"{language}: title is not first"

        # An index alone is not always enough: where a guide prints its own
        # numbers those drift from the tree, so one string can name two
        # headings. What must always hold is that some reference resolves.
        for position, heading in enumerate(headings):
            reference = _unique_reference(headings, position).strip('"')
            matches = find_headings(headings, reference)
            assert matches == [position], (
                f"{language}: {reference!r} for {heading.title!r} "
                f"resolved to {matches}, expected [{position}]"
            )


# Words the corpus uses often enough to exercise many sections at once.
# 'truefalse' appears only inside pyguide's anchors, which is the shape that
# used to be credited to whichever section happened to precede them.
MENTION_PROBES = (
    "truefalse",
    "indent",
    "naming",
    "comment",
    "false",
    "spaces",
)


def test_every_shipped_guide_suggestion_prints_the_words() -> None:
    """A suggested section must be addressable and must hold the words.

    Anchors made the two disagree: find_mentions walked lines and credited
    them to the heading above, while extract_section hands a trailing anchor
    to the heading below. Reading a suggestion has to show what was searched
    for, in every guide, or the suggestion is a dead end.
    """
    guides = _shipped_guides()
    assert len(guides) >= 10, f"expected the shipped corpus, got {len(guides)}"

    for language, content in guides:
        headings = parse_headings(content)
        for probe in MENTION_PROBES:
            for position in find_mentions(content, headings, probe):
                heading = headings[position]
                assert heading.index, (
                    f"{language}: {probe!r} suggested the unindexed "
                    f"{heading.title!r}"
                )

                section = extract_section(content, headings, position)
                assert probe in section.lower(), (
                    f"{language}: {probe!r} suggested {heading.index} "
                    f"({heading.title!r}), which does not contain it"
                )


@patch("readability.cli")
@patch("logging.basicConfig")
def test_logging_is_quiet_by_default(
    mock_config: MagicMock, mock_cli: MagicMock
) -> None:
    """Narration belongs behind --verbose, not on every invocation.

    CliRunner never reaches main(), so the level it installs can only be
    checked here — which is also where the defect lived: INFO by default
    left --verbose with nothing to enable but DEBUG.
    """
    main()

    assert mock_config.call_args.kwargs["level"] == logging.WARNING


def test_cli_full_prints_the_whole_guide(tmp_path: Path, monkeypatch) -> None:
    """--full is the escape hatch for grepping wording no heading names."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "--full"])

    assert result.exit_code == 0
    assert "Background body." in result.stdout
    assert "Imports body." in result.stdout


def test_cli_full_and_a_reference_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """A silent precedence rule is how a caller reads the wrong thing."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "2.2", "--full"])

    assert result.exit_code != 0
    assert "--full" in result.output


def test_cli_outline_is_the_default(tmp_path: Path, monkeypatch) -> None:
    """The cheap answer is the default; the 200 KB one is opt-in."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python"])

    assert result.exit_code == 0
    assert "2.2  Imports" in result.stdout
    assert "Imports body." not in result.stdout


def test_check_reports_that_it_found_nothing(tmp_path: Path) -> None:
    """Silence cannot be the only signal that a check ran and passed."""
    clean = tmp_path / "clean.py"
    clean.write_text('"""Docstring."""\n')

    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(clean)])

    assert result.exit_code == 0
    assert "no findings" in (result.stdout + result.stderr).lower()


def test_outline_annotates_only_the_expensive_sections(
    tmp_path: Path, monkeypatch
) -> None:
    """A size on every line is noise; 59% of sections would read '0'."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    big = " ".join(["word"] * 1500)
    _write_guide(
        tmp_path,
        f"# Guide\n\n## 1 Small\n\nShort.\n\n### 1.1 Detail\n\nD.\n\n"
        f"## 2 Large\n\n{big}\n",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python"])

    assert result.exit_code == 0
    assert "2  Large  (1.5k words)" in result.stdout
    # The small section carries no annotation at all
    assert "1  Small\n" in result.stdout


def test_outline_size_counts_nested_subsections(
    tmp_path: Path, monkeypatch
) -> None:
    """The number is what you would receive, so it includes children."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    half = " ".join(["word"] * 800)
    _write_guide(
        tmp_path,
        f"# Guide\n\n## 1 Parent\n\n{half}\n\n### 1.1 Child\n\n{half}\n",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python"])

    assert result.exit_code == 0
    assert "1  Parent  (1.6k words)" in result.stdout


def test_outline_hint_names_a_reference_that_resolves(
    tmp_path: Path, monkeypatch
) -> None:
    """The outline must not be a dead end, and the example must be usable."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python"])

    assert result.exit_code == 0
    # The hint is a diagnostic, so it never contaminates a piped outline
    assert "sections" in result.stderr
    assert "readability guide python" in result.stderr

    example = result.stderr.rsplit("readability guide python", 1)[1].strip()
    followed = runner.invoke(cli, ["guide", "python", example])
    assert followed.exit_code == 0, f"{example!r} did not resolve"


def test_section_and_full_print_no_hint(tmp_path: Path, monkeypatch) -> None:
    """The hint answers 'what now' after an outline, and only then."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    for argv in (["guide", "python", "2.2"], ["guide", "python", "--full"]):
        result = runner.invoke(cli, argv)
        assert result.exit_code == 0
        assert "sections" not in result.stderr


@patch("readability.get_guide_content")
def test_sync_accepts_languages(
    mock_get_content: MagicMock, tmp_path: Path
) -> None:
    """Refreshing one guide should not mean refetching all fourteen."""
    mock_get_content.return_value = "# Guide"

    with patch("readability.get_guides_dir", return_value=str(tmp_path)):
        runner = CliRunner()
        result = runner.invoke(cli, ["sync", "python", "shell"])

    assert result.exit_code == 0
    assert sorted(os.listdir(tmp_path)) == ["pyguide.md", "shellguide.md"]


@patch("readability.get_guide_content")
def test_sync_deduplicates_aliases(
    mock_get_content: MagicMock, tmp_path: Path
) -> None:
    """Aliases share one guide, so naming both must not fetch it twice."""
    mock_get_content.return_value = "# Guide"

    with patch("readability.get_guides_dir", return_value=str(tmp_path)):
        runner = CliRunner()
        result = runner.invoke(cli, ["sync", "cpp", "c++"])

    assert result.exit_code == 0
    assert mock_get_content.call_count == 1


def test_sync_rejects_an_unsupported_language(tmp_path: Path) -> None:
    """An unknown language is a typo, not a reason to sync everything."""
    with patch("readability.get_guides_dir", return_value=str(tmp_path)):
        runner = CliRunner()
        result = runner.invoke(cli, ["sync", "cobol"])

    assert result.exit_code != 0
    assert "not supported" in result.output


def test_unmatched_reference_reports_where_the_words_appear(
    tmp_path: Path, monkeypatch
) -> None:
    """A miss should hand back the sections that mention it, not a dead end."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(
        tmp_path,
        "# Guide\n\n## 1 Strings\n\nPrefer an f-string here.\n\n"
        "### 1.1 Logging\n\nNever pass an f-string to a logger.\n\n"
        "## 2 Imports\n\nUse full paths.\n",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "f-string"])

    assert result.exit_code == 1
    # Nothing partial reaches a pipeline
    assert result.stdout == ""
    assert "1  Strings" in result.stderr
    assert "1.1  Strings > Logging" in result.stderr
    # A section that never mentions it is not offered
    assert "Imports" not in result.stderr


def test_unmatched_reference_suggestions_resolve(
    tmp_path: Path, monkeypatch
) -> None:
    """Every reference offered must select a section on its own."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(
        tmp_path,
        "# Guide\n\n## 1 Strings\n\nPrefer an f-string here.\n\n"
        "### 1.1 Logging\n\nNever pass an f-string to a logger.\n",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "f-string"])

    offered = [
        line.split("  ")[0].strip()
        for line in result.stderr.splitlines()
        if line.startswith("  ")
    ]
    assert offered
    for reference in offered:
        followed = runner.invoke(cli, ["guide", "python", reference])
        assert followed.exit_code == 0, f"{reference!r} did not resolve"


def test_unmatched_reference_found_nowhere_points_at_the_outline(
    tmp_path: Path, monkeypatch
) -> None:
    """With nothing to offer, the outline is the only useful next step."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "concurrency"])

    assert result.exit_code == 1
    assert "readability guide python" in result.stderr
    assert "appears in" not in result.stderr


def test_mentions_in_the_preamble_belong_to_no_section() -> None:
    """A guide's preamble sits under its title, which is the whole document.

    pyguide opens with an 87-line table of contents. Crediting that to the
    title would suggest a reference that prints every byte of the guide.
    """
    content = (
        "# Guide\n\nEvery rule here is advisory.\n\n"
        "## 1 Strings\n\nPrefer str.format.\n"
    )
    headings = parse_headings(content)

    assert find_mentions(content, headings, "advisory") == []


def test_mentions_never_offer_the_whole_guide(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests that a preamble mention points at the outline, not the title."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(
        tmp_path,
        "# Guide\n\nEvery rule here is advisory.\n\n"
        "## 1 Strings\n\nPrefer str.format.\n",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "advisory"])

    assert result.exit_code == 1
    assert '"Guide"' not in result.stderr
    assert "readability guide python" in result.stderr


def test_mentions_land_on_a_section_that_prints_them() -> None:
    """Tests that an anchor is credited to a section which still holds it.

    A heading's anchors sit on the lines above it, and extract_section sheds
    them from the section they trail. The section that merely precedes an
    anchor therefore does not print it; the nearest ancestor still does.
    """
    content = (
        "# Guide\n\n## 1 Language Rules\n\n### 1.1 Alpha\n\nAlpha body.\n\n"
        '<a id="beta-widget"></a>\n\n### 1.2 Beta\n\nBeta body.\n'
    )
    headings = parse_headings(content)
    mentions = find_mentions(content, headings, "widget")

    assert [headings[position].index for position in mentions] == ["1"]
    for position in mentions:
        assert "widget" in extract_section(content, headings, position)


def test_mentions_credit_the_innermost_section() -> None:
    """Tests that a mention names the subsection holding it, not an ancestor.

    A section carries its subsections, so every ancestor of a mention also
    contains it. Only the innermost is worth reading.
    """
    content = (
        "# Guide\n\n## 1 Language Rules\n\nRules follow.\n\n"
        "### 1.1 Alpha\n\nAlpha uses a widget.\n"
    )
    headings = parse_headings(content)
    mentions = find_mentions(content, headings, "widget")

    assert [headings[position].index for position in mentions] == ["1.1"]


def test_grep_flag_is_gone(tmp_path: Path, monkeypatch) -> None:
    """Text search belongs to grep, which --full already feeds."""
    monkeypatch.setenv("READABILITY_CACHE", str(tmp_path))
    _write_guide(tmp_path, NUMBERED_GUIDE)

    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "--grep", "imports"])

    assert result.exit_code != 0
    assert "no such option" in result.output.lower()


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
        Path(".gitignore").write_text("node_modules/\n")
        Path("node_modules/example").mkdir(parents=True)
        Path("node_modules/example/index.js").touch()
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
        t["name"]: t for t in _get_tool_definitions(Path("f.ts"), tmp_path)
    }
    biome = tools["biome"]

    for key in ("check", "check_format", "fix", "format"):
        assert "biome" not in biome[key], (
            f"{key} names the wrong npm package: {biome[key]}"
        )
        assert any(a.startswith("@biomejs/biome") for a in biome[key])


def test_node_tools_prefer_a_project_local_install(tmp_path: Path) -> None:
    """A project's own version beats whatever npx would fetch."""
    local = tmp_path / "node_modules" / ".bin"
    local.mkdir(parents=True)
    binary = local / "biome"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    tools = {
        t["name"]: t for t in _get_tool_definitions(Path("f.ts"), tmp_path)
    }

    cmd = tools["biome"]["check"]
    assert cmd[0] == str(binary)
    assert "npx" not in cmd


def test_node_tools_fall_back_to_npx(tmp_path: Path) -> None:
    """With nothing installed, npx stays the way to reach them."""
    tools = {
        t["name"]: t for t in _get_tool_definitions(Path("f.ts"), tmp_path)
    }

    cmd = tools["biome"]["check"]
    assert cmd[:2] == ["npx", "-y"]
    assert cmd[2].startswith("@biomejs/biome@")


def test_python_tools_fall_back_to_a_floored_runner(tmp_path: Path) -> None:
    """A tool nobody installed is still reachable, and still reproducible.

    uvx downloads once and serves every later run from its cache, so the
    package need not carry 50 MB of binaries to make check work. A floor
    keeps what it fetches new enough for the bundled configs.
    """
    with patch("shutil.which", return_value=None):
        tools = {
            t["name"]: t for t in _get_tool_definitions(Path("f.py"), tmp_path)
        }

    ruff = tools["ruff"]["check"]
    assert ruff[0] == "uvx"
    assert ruff[1].startswith("ruff>=")

    pyrefly = tools["pyrefly"]["check"]
    assert pyrefly[0] == "uvx"
    assert pyrefly[1].startswith("pyrefly>=")


def test_an_installed_tool_beats_the_runner(tmp_path: Path) -> None:
    """Nothing is downloaded when the machine already has the tool."""
    with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
        tools = {
            t["name"]: t for t in _get_tool_definitions(Path("f.py"), tmp_path)
        }

    assert tools["ruff"]["check"][0] == "/usr/bin/ruff"
    assert "uvx" not in tools["ruff"]["check"]


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
            t["name"]: t for t in _get_tool_definitions(Path("f.ts"), tmp_path)
        }

    assert tools["biome"]["check"][0] == "npx"


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
            t["name"]: t for t in _get_tool_definitions(Path("f.py"), tmp_path)
        }

    assert str(binary) not in tools["ruff"]["check"]
    assert tools["ruff"]["check"][0] == "uvx"


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
