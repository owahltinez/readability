import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
import requests
from click.testing import CliRunner

from readability import (
    cli,
    convert_to_markdown,
    get_guide,
    get_guide_content,
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
    assert os.path.basename(get_local_path("docguide/style.md")) == "docguide-style.md"
    # File with different extension
    assert os.path.basename(get_local_path("cppguide.html")) == "cppguide.md"


@patch("readability.get_guide_content")
def test_get_guide_integration(mock_get_content: MagicMock, tmp_path: Path) -> None:
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
    mock_get_content: MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
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


def test_readability_cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests that READABILITY_CACHE environment variable is respected.

    Args:
        tmp_path: The temporary directory fixture.
        monkeypatch: The monkeypatch fixture.
    """
    from readability import get_guides_dir

    custom_cache = str(tmp_path / "custom_guides")
    monkeypatch.setenv("READABILITY_CACHE", custom_cache)
    assert get_guides_dir() == custom_cache


def test_default_guides_dir() -> None:
    """Tests the default guides directory."""
    from readability import get_guides_dir

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
    from unittest.mock import MagicMock

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
    # Verify one of the calls was ruff check
    called_cmds = [call.args[0] for call in mock_run.call_args_list]
    assert ["ruff", "check", "script.py"] in called_cmds


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
    from unittest.mock import MagicMock

    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("script.py").touch()

        result = runner.invoke(cli, ["check", "--fix", "script.py"])

    assert result.exit_code == 0
    called_cmds = [call.args[0] for call in mock_run.call_args_list]
    # Verify ruff check --fix and ruff format were called
    assert ["ruff", "check", "--fix", "script.py"] in called_cmds
    assert ["ruff", "format", "script.py"] in called_cmds


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
    from unittest.mock import MagicMock

    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("pyproject.toml").touch()
        Path("subdir").mkdir()
        Path("subdir/script.py").touch()

        # Check the subdirectory
        result = runner.invoke(cli, ["check", "subdir"])

    assert result.exit_code == 0
    called_cmds = [call.args[0] for call in mock_run.call_args_list]
    # Verify ruff was called with the directory 'subdir'
    assert ["ruff", "check", "subdir"] in called_cmds


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
    from unittest.mock import MagicMock

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
    from unittest.mock import MagicMock

    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("biome.json").touch()
        Path("script.js").touch()

        result = runner.invoke(cli, ["check", "script.js"])

    assert result.exit_code == 0
    # npx biome lint and npx biome format should be called
    called_cmds = [call.args[0] for call in mock_run.call_args_list]
    assert ["npx", "biome", "lint", "script.js"] in called_cmds
    assert ["npx", "biome", "format", "script.js"] in called_cmds
