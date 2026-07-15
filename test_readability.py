import logging
import os
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
import requests
from click.testing import CliRunner

from readability import (
    _bundled_config,
    _get_tool_definitions,
    _has_project_config,
    cli,
    convert_to_markdown,
    get_guide,
    get_guide_content,
    get_guides_dir,
    get_local_path,
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
def test_cli_success(mock_guide: MagicMock) -> None:
    """Tests successful CLI execution.

    Args:
        mock_guide: The mocked get_guide function.
    """
    mock_guide.return_value = "# Style Guide"
    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python"])
    assert result.exit_code == 0
    assert "# Style Guide" in result.output


@patch("readability.get_guide")
def test_cli_output_file(mock_guide: MagicMock, tmp_path: Path) -> None:
    """Tests CLI saving to a file.

    Args:
        mock_guide: The mocked get_guide function.
        tmp_path: The temporary directory fixture.
    """
    mock_guide.return_value = "# Style Guide"
    output_file = tmp_path / "style.md"
    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "-o", str(output_file)])
    assert result.exit_code == 0
    assert output_file.read_text() == "# Style Guide"


@patch("readability.get_guide")
def test_cli_verbose(mock_guide: MagicMock) -> None:
    """Tests CLI with verbose flag.

    Args:
        mock_guide: The mocked get_guide function.
    """
    mock_guide.return_value = "# Style Guide"
    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "python", "--verbose"])
    assert result.exit_code == 0
    assert "# Style Guide" in result.output


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
        with caplog.at_level(logging.INFO):
            runner = CliRunner()
            result = runner.invoke(cli, ["sync"])
            assert result.exit_code == 0
            assert "Sync complete" in caplog.text
            # Check if at least one guide was "synced" (written to tmp_path)
            assert len(os.listdir(tmp_path)) > 0


def test_languages_command() -> None:
    """Tests the languages command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["languages"])
    assert result.exit_code == 0
    assert "Supported languages and their aliases:" in result.output
    # Check for some common languages
    assert "python" in result.output
    assert "c++, cpp" in result.output
    assert "c#, csharp" in result.output


def test_languages_command_with_cache(tmp_path: Path) -> None:
    """Tests the languages command shows [cached] label correctly.

    Args:
        tmp_path: The temporary directory fixture.
    """
    # Mock get_guides_dir to point to tmp_path
    with patch("readability.get_guides_dir", return_value=str(tmp_path)):
        # Create a mock cached file for python (pyguide.md -> pyguide.md)
        python_cache = tmp_path / "pyguide.md"
        python_cache.write_text("content")

        runner = CliRunner()
        result = runner.invoke(cli, ["languages"])

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
def test_check_command_no_trigger(
    mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
) -> None:
    """Tests the check command when trigger files are missing.

    Args:
        mock_run: The mocked subprocess.run function.
        mock_which: The mocked shutil.which function.
        tmp_path: The temporary directory fixture.
    """
    # No pyproject.toml created
    py_file = tmp_path / "script.py"
    py_file.touch()

    # Mock shutil.which to say ruff exists
    mock_which.side_effect = lambda x: x if x == "ruff" else None

    # Mock subprocess.run to return success
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("script.py").touch()

        # Run check on a file without its trigger (pyproject.toml)
        result = runner.invoke(cli, ["check", "script.py"])

    assert result.exit_code == 0
    # Ruff should NOT be called because trigger is missing
    assert mock_run.call_count == 0


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
        "biome",
        "lint",
        "--no-errors-on-unmatched",
        "script.js",
    ] in called_cmds
    assert [
        "npx",
        "-y",
        "biome",
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


def test_bundled_default_configs_are_valid(tmp_path: Path) -> None:
    """Tests that the bundled default configs exist and parse as TOML."""
    # Both bundled configs must exist and be valid TOML
    for tool in ("ruff", "pyrefly"):
        config_path = _bundled_config(tool)
        assert config_path.exists()
        tomllib.loads(config_path.read_text())

    # The ruff defaults follow the Google Python style guide
    ruff_config = tomllib.loads(_bundled_config("ruff").read_text())
    assert ruff_config["line-length"] == 80
    assert ruff_config["lint"]["pydocstyle"]["convention"] == "google"


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
