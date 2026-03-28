import pytest
import os
import requests
from click.testing import CliRunner
from readability import cli, fetch_guide, fetch_guide_content, convert_to_markdown
from unittest.mock import patch
import click
import logging


def test_fetch_guide_unsupported():
    """
    Test that an unsupported language raises a UsageError.
    """
    with pytest.raises(click.UsageError) as excinfo:
        fetch_guide("nonexistent")
    assert "not supported" in str(excinfo.value)


@patch("requests.get")
def test_fetch_guide_content_success(mock_get):
    """
    Test successful content fetching.
    """
    mock_get.return_value.text = "raw content"
    mock_get.return_value.status_code = 200

    content = fetch_guide_content("http://example.com")
    assert content == "raw content"
    mock_get.assert_called_once_with("http://example.com", timeout=10)


@patch("requests.get")
def test_fetch_guide_content_failure(mock_get):
    """
    Test content fetching failure.
    """
    mock_get.side_effect = requests.exceptions.RequestException("Network error")

    with pytest.raises(click.ClickException) as excinfo:
        fetch_guide_content("http://example.com")
    assert "Failed to fetch style guide" in str(excinfo.value)


def test_convert_to_markdown_md():
    """
    Test conversion for Markdown files (should remain unchanged).
    """
    content = "# Markdown"
    result = convert_to_markdown(content, "guide.md")
    assert result == content


def test_convert_to_markdown_html():
    """
    Test conversion for HTML files.
    """
    content = "<h1>Header</h1>"
    result = convert_to_markdown(content, "guide.html")
    assert "# Header" in result


def test_convert_to_markdown_xml():
    """
    Test conversion for XML files.
    """
    content = "<guide><title>Vim</title></guide>"
    result = convert_to_markdown(content, "guide.xml")
    assert "Vim" in result


@patch("readability.fetch_guide_content")
def test_fetch_guide_integration(mock_fetch_content, tmp_path):
    """
    Test the orchestration in fetch_guide.
    """
    mock_fetch_content.return_value = "# Python Guide"

    # Mock GUIDES_DIR to point to tmp_path
    with patch("readability.GUIDES_DIR", str(tmp_path)):
        content = fetch_guide("python", remote=True)
        assert content == "# Python Guide"
        assert os.path.exists(os.path.join(tmp_path, "pyguide.md"))


def test_cli_unsupported():
    """
    Test CLI output for unsupported language.
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["fetch", "nonexistent"])
    assert result.exit_code == 1
    assert "Error" in result.output


@patch("readability.fetch_guide")
def test_cli_success(mock_fetch):
    """
    Test successful CLI execution.
    """
    mock_fetch.return_value = "# Style Guide"
    runner = CliRunner()
    result = runner.invoke(cli, ["fetch", "python"])
    assert result.exit_code == 0
    assert "# Style Guide" in result.output


@patch("readability.fetch_guide")
def test_cli_output_file(mock_fetch, tmp_path):
    """
    Test CLI saving to a file.
    """
    mock_fetch.return_value = "# Style Guide"
    output_file = tmp_path / "style.md"
    runner = CliRunner()
    result = runner.invoke(cli, ["fetch", "python", "-o", str(output_file)])
    assert result.exit_code == 0
    assert output_file.read_text() == "# Style Guide"


@patch("readability.fetch_guide")
def test_cli_verbose(mock_fetch):
    """
    Test CLI with verbose flag.
    """
    mock_fetch.return_value = "# Style Guide"
    runner = CliRunner()
    result = runner.invoke(cli, ["fetch", "python", "--verbose"])
    assert result.exit_code == 0
    assert "# Style Guide" in result.output


@patch("readability.fetch_guide_content")
def test_sync_command(mock_fetch_content, tmp_path, caplog):
    """
    Test the sync command.
    """
    mock_fetch_content.return_value = "content"

    with patch("readability.GUIDES_DIR", str(tmp_path)):
        with caplog.at_level(logging.INFO):
            runner = CliRunner()
            result = runner.invoke(cli, ["sync"])
            assert result.exit_code == 0
            assert "Sync complete" in caplog.text
            # Check if at least one guide was "synced" (written to tmp_path)
            assert len(os.listdir(tmp_path)) > 0
