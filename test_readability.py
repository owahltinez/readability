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
    LANGUAGE_MAP,
    _bundled_config,
    _get_tool_definitions,
    _has_project_config,
    _iter_heading_lines,
    _unique_reference,
    cli,
    convert_to_markdown,
    find_headings,
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


def test_languages_command() -> None:
    """Tests the languages command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["guide"])
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
