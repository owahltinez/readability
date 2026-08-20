import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
import requests
from click.testing import CliRunner

from readability.cli import cli
from readability.guide import (
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


@patch("readability.guide.get_guide_content")
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
    with patch("readability.guide.get_guides_dir", return_value=str(tmp_path)):
        content = get_guide("python", remote=True)
        assert content == "# Python Guide"
        assert os.path.exists(os.path.join(tmp_path, "pyguide.md"))


def test_cli_unsupported() -> None:
    """Tests CLI output for unsupported language."""
    runner = CliRunner()
    result = runner.invoke(cli, ["guide", "nonexistent"])
    assert result.exit_code == 1
    assert "Error" in result.output


@patch("readability.cli.get_guide")
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


@patch("readability.guide.get_guide_content")
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

    with patch("readability.guide.get_guides_dir", return_value=str(tmp_path)):
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
    with patch("readability.guide.get_guides_dir", return_value=str(tmp_path)):
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
        assert Path(guides_dir).parent == Path(__file__).parent / "readability"
