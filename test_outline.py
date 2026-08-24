import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from readability.cli import cli, main
from readability.guide import (
    LANGUAGE_MAP,
    get_local_path,
)
from readability.outline import (
    _iter_heading_lines,
    _unique_reference,
    extract_section,
    find_headings,
    find_mentions,
    parse_headings,
)

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


# Evidence the scanner need not be a Markdown parser; a failure means it is


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

        # A fenced line would carry a comment marker or a shebang
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

        # A guide's own numbers drift, so only 'some reference resolves' holds
        for position, heading in enumerate(headings):
            reference = _unique_reference(headings, position).strip('"')
            matches = find_headings(headings, reference)
            assert matches == [position], (
                f"{language}: {reference!r} for {heading.title!r} "
                f"resolved to {matches}, expected [{position}]"
            )


# Common words, plus 'truefalse' which appears only inside pyguide's anchors
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


@patch("readability.cli.cli")
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


@patch("readability.guide.get_guide_content")
def test_sync_accepts_languages(
    mock_get_content: MagicMock, tmp_path: Path
) -> None:
    """Refreshing one guide should not mean refetching all fourteen."""
    mock_get_content.return_value = "# Guide"

    with patch("readability.guide.get_guides_dir", return_value=str(tmp_path)):
        runner = CliRunner()
        result = runner.invoke(cli, ["sync", "python", "shell"])

    assert result.exit_code == 0
    assert sorted(os.listdir(tmp_path)) == ["pyguide.md", "shellguide.md"]


@patch("readability.guide.get_guide_content")
def test_sync_deduplicates_aliases(
    mock_get_content: MagicMock, tmp_path: Path
) -> None:
    """Aliases share one guide, so naming both must not fetch it twice."""
    mock_get_content.return_value = "# Guide"

    with patch("readability.guide.get_guides_dir", return_value=str(tmp_path)):
        runner = CliRunner()
        result = runner.invoke(cli, ["sync", "cpp", "c++"])

    assert result.exit_code == 0
    assert mock_get_content.call_count == 1


def test_sync_rejects_an_unsupported_language(tmp_path: Path) -> None:
    """An unknown language is a typo, not a reason to sync everything."""
    with patch("readability.guide.get_guides_dir", return_value=str(tmp_path)):
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
