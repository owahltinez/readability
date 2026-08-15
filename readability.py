"""CLI for fetching Google style guides and running code quality tools."""

import dataclasses
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
import warnings
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Optional

import click
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from markdownify import markdownify as md

# Suppress BeautifulSoup warning when parsing XML as HTML
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger("readability")

# Default timeout for subprocess calls in seconds
DEFAULT_TIMEOUT = 60


def get_guides_dir() -> str:
    """Get the directory where style guides are cached.

    Defaults to 'guides/' in the same directory as this script, but can be
    overridden by the READABILITY_CACHE environment variable.

    Returns:
        The path to the guides directory.
    """
    return os.getenv("READABILITY_CACHE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "guides"
    )


# Mapping of languages to their Google Style Guide file paths
LANGUAGE_MAP = {
    "python": "pyguide.md",
    "shell": "shellguide.md",
    "objc": "objcguide.md",
    "objective-c": "objcguide.md",
    "r": "Rguide.md",
    "csharp": "csharp-style.md",
    "c#": "csharp-style.md",
    "docguide": "docguide/style.md",
    "markdown": "docguide/style.md",
    "go": "go/guide.md",
    "cpp": "cppguide.html",
    "c++": "cppguide.html",
    "java": "javaguide.html",
    "js": "jsguide.html",
    "javascript": "jsguide.html",
    "ts": "tsguide.html",
    "typescript": "tsguide.html",
    "html": "htmlcssguide.html",
    "css": "htmlcssguide.html",
    "json": "jsoncstyleguide.xml",
    "vim": "vimscriptguide.xml",
}

BASE_URL = "https://google.github.io/styleguide/"

# Style guides are dense with snake_case and dunder identifiers, and escaping
# their underscores leaves 'from \_\_future\_\_ import' in the text a caller
# greps or reads. CommonMark would render a bare '__future__' as emphasis, but
# these guides are consumed as plain text, where fidelity matters more.
MARKDOWNIFY_OPTIONS = {"heading_style": "ATX", "escape_underscores": False}


def get_guide_content(url: str) -> str:
    """Fetch raw content from the specified URL.

    Args:
        url: The URL to fetch content from.

    Returns:
        The raw text content from the URL.

    Raises:
        click.ClickException: If the HTTP request fails.
    """
    logger.info("Fetching style guide from %s", url)

    # Perform the HTTP GET request with a timeout
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch content from %s: %s", url, e)
        raise click.ClickException(
            f"Failed to fetch style guide from {url}: {e}"
        )

    return response.text


def convert_to_markdown(content: str, filename: str) -> str:
    """Convert the raw content to markdown based on file extension.

    Args:
        content: The raw text content to convert.
        filename: The original filename to determine conversion logic.

    Returns:
        The converted markdown content.
    """
    logger.debug("Converting content for %s", filename)

    # Handle Markdown files directly
    if filename.endswith(".md"):
        return content

    # Strip XML prologue if present to avoid it leaking into the output
    if content.lstrip().startswith("<?xml"):
        content = content.split("?>", 1)[-1].lstrip()

    # Handle XML files (used for Vim script guide and JSON style guide)
    if filename.endswith(".xml"):
        soup = BeautifulSoup(content, "html.parser")

        # Add titles as headers
        guide = soup.find("guide")
        if guide:
            title = guide.get("title")
            if isinstance(title, str) and title:
                h1 = soup.new_tag("h1")
                h1.string = title
                guide.insert(0, h1)

        for category in soup.find_all("category"):
            title = category.get("title")
            if isinstance(title, str) and title:
                h2 = soup.new_tag("h2")
                h2.string = title
                category.insert(0, h2)

        for sp in soup.find_all("stylepoint"):
            title = sp.get("title")
            if isinstance(title, str) and title:
                h3 = soup.new_tag("h3")
                h3.string = title
                sp.insert(0, h3)

        for summary in soup.find_all("summary"):
            summary.name = "p"
            # Wrap content in strong tags
            content_str = summary.decode_contents()
            summary.clear()
            strong = soup.new_tag("strong")
            strong.append(BeautifulSoup(content_str, "html.parser"))
            summary.append(strong)

        for snippet in soup.find_all(["code_snippet", "bad_code_snippet"]):
            is_bad = snippet.name == "bad_code_snippet"
            snippet.name = "pre"
            code = soup.new_tag("code")
            code.string = snippet.get_text()
            snippet.clear()
            if is_bad:
                p = soup.new_tag("p")
                strong = soup.new_tag("strong")
                strong.string = "BAD:"
                p.append(strong)
                snippet.append(p)
            snippet.append(code)

        # Convert the modified soup to string and then to markdown
        return md(str(soup), **MARKDOWNIFY_OPTIONS)

    # Handle HTML files by converting them to Markdown
    if filename.endswith(".html"):
        return md(content, **MARKDOWNIFY_OPTIONS)

    # Fallback to returning raw content
    return content


def get_local_path(filename: str) -> str:
    """Get the local path for a given style guide filename.

    Flattens the filename by replacing path separators with dashes and ensures
    the file has a .md extension for uniform storage.

    Args:
        filename: The original filename or relative path from the style guide
            repository (e.g., 'pyguide.md' or 'go/guide.md').

    Returns:
        The full local path to the cached markdown file.
    """
    # Flatten the filename by replacing '/' with '-'
    flattened = filename.replace("/", "-")
    # Use the flattened filename and change extension to .md for uniform storage
    base_name = flattened.rsplit(".", 1)[0]
    return os.path.join(get_guides_dir(), f"{base_name}.md")


def refresh_guide(filename: str) -> str:
    """Fetch one guide from the web, convert it, and replace the local copy.

    Args:
        filename: The guide's path within the style guide repository.

    Returns:
        The markdown content of the style guide.

    Raises:
        click.ClickException: If the HTTP request fails.
    """
    content = get_guide_content(f"{BASE_URL}{filename}")
    markdown_content = convert_to_markdown(content, filename)

    if not os.path.exists(get_guides_dir()):
        os.makedirs(get_guides_dir(), exist_ok=True)

    local_path = get_local_path(filename)
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    logger.debug("Cached style guide locally: %s", local_path)

    return markdown_content


def get_guide(language: str, remote: bool = False) -> str:
    """Orchestrate fetching and converting the style guide for a given language.

    Args:
        language: The language to fetch the guide for.
        remote: Whether to force fetching from the web instead of local cache.

    Returns:
        The markdown content of the style guide.

    Raises:
        click.UsageError: If the language is not supported.
    """
    # Look up the filename in the mapping
    filename = LANGUAGE_MAP.get(language.lower())
    if not filename:
        error_msg = f"Language '{language}' is not supported."
        logger.warning(error_msg)
        raise click.UsageError(
            f"{error_msg} Supported languages: "
            f"{', '.join(sorted(LANGUAGE_MAP.keys()))}"
        )

    local_path = get_local_path(filename)

    # If remote is False, check for local file first
    if not remote and os.path.exists(local_path):
        logger.info("Reading style guide from local file: %s", local_path)
        with open(local_path, "r", encoding="utf-8") as f:
            return f.read()

    return refresh_guide(filename)


# A fence opens or closes a code block; anything inside is sample code, not
# document structure. Guides for `#`-commented languages (shell, Python) would
# otherwise report hundreds of code comments as headings.
FENCE_PATTERN = re.compile(r"^\s{0,3}(?:```|~~~)")

# Closing hashes are optional in ATX headings and must be space-separated, so
# a title such as 'C#' keeps its trailing character.
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")

# A section number the document itself prints, e.g. '2.2' in '2.2 Imports'.
DOCUMENT_NUMBER_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")

# Guides put a heading's link targets on the lines above it, which would
# otherwise trail the end of the preceding section.
ANCHOR_PATTERN = re.compile(r'^<a id="[^"]*"></a>$')

# The separator in a scoped reference such as 'Imports > Decision'. Spaces on
# both sides are required so that a heading like '`Array<T>` Type' stays whole.
PATH_SEPARATOR_PATTERN = re.compile(r"\s+>\s+")


@dataclasses.dataclass(frozen=True)
class Heading:
    """A Markdown heading found in a style guide.

    Attributes:
        level: Heading depth, 1 for '#' through 6 for '######'.
        text: The heading as written, including any number the guide prints.
        index: Positional index derived from the heading tree ('2.2.1'), unique
            within the guide. Empty for the document title.
        number: The section number the document prints itself, or an empty
            string for the eleven guides that number nothing.
        title: The heading text without the number the document prints.
        line: Zero-based index of the heading's line within the guide.
    """

    level: int
    text: str
    index: str
    number: str
    title: str
    line: int


def _iter_heading_lines(content: str) -> Iterator[tuple[int, int, str]]:
    """Yield the headings of a Markdown document, skipping fenced code.

    This is a heading scanner, NOT a Markdown parser, and must not grow into
    one. It is sound only because the job is narrow and the input is known:
    block-level ATX headings, over the fourteen guides shipped here, every
    one of which is asserted in the test suite. Fenced code is skipped
    because a guide whose language comments with '#' would otherwise offer
    145 fragments of sample code as sections.

    Reach for a real CommonMark parser (mistletoe has no dependencies of its
    own; markdown-it-py is far more widely deployed) the moment anything
    needs more than this: inline structure such as links or emphasis, block
    nesting, setext headings, indented code blocks, or Markdown from a
    source other than these guides. Extending the regexes to cover those is
    how a scanner turns into a bad parser.

    Args:
        content: The full Markdown text of a style guide.

    Yields:
        Tuples of (line index, heading level, heading text).
    """
    in_fence = False
    for line_number, line in enumerate(content.splitlines()):
        # Fences toggle: a line inside one is sample code whatever it says
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        match = HEADING_PATTERN.match(line)
        if match:
            yield line_number, len(match.group(1)), match.group(2).strip()


def _next_index(open_headings: list[list[Any]], level: int) -> str:
    """Advance the heading tree by one heading and render its index.

    Numbering counts siblings under a shared parent rather than counting each
    heading level independently. Guides skip levels (an h3 following an h4),
    which a per-level counter would give the same index twice.

    Args:
        open_headings: Mutable stack of [level, index, child count] for the
            headings still open above this one, updated in place. It starts
            with a level 0 root so that the top level always has a parent.
        level: The level of the heading being numbered.

    Returns:
        The dotted positional index for the heading, e.g. '2.2.1'.
    """
    # Close every heading this one is not nested inside of, leaving its parent
    while open_headings[-1][0] >= level:
        open_headings.pop()

    parent = open_headings[-1]
    parent[2] += 1
    index = f"{parent[1]}.{parent[2]}" if parent[1] else str(parent[2])

    open_headings.append([level, index, 0])
    return index


def _split_document_number(text: str, numbered: bool) -> tuple[str, str]:
    """Separate the number a guide prints in a heading from its title.

    Args:
        text: The heading text as written.
        numbered: Whether the document numbers its headings at all. Guides that
            do not still have headings that open with a digit (C++'s '0 and
            nullptr/NULL'), and those digits are part of the title.

    Returns:
        A tuple of (number, title); the number is empty when there is none.
    """
    match = DOCUMENT_NUMBER_PATTERN.match(text) if numbered else None
    if not match:
        return "", text
    return match.group(1), match.group(2).strip()


def parse_headings(content: str) -> list[Heading]:
    """Parse the heading tree of a style guide.

    Args:
        content: The full Markdown text of a style guide.

    Returns:
        The guide's headings in document order.
    """
    raw = list(_iter_heading_lines(content))
    if not raw:
        return []

    # Only three of the shipped guides number their headings. A dotted number
    # ('2.2') is the reliable signal; a bare leading digit is not.
    document_numbers = [
        match.group(1)
        for _, _, text in raw
        if (match := DOCUMENT_NUMBER_PATTERN.match(text))
    ]
    numbered = any("." in number for number in document_numbers)

    # A lone top-level heading is the document title: it roots the tree rather
    # than being the first section, so numbering starts with its children.
    top_level = min(level for _, level, _ in raw)
    top_level_lines = [line for line, level, _ in raw if level == top_level]
    title_line = top_level_lines[0] if len(top_level_lines) == 1 else None

    headings = []
    open_headings: list[list[Any]] = [[0, "", 0]]
    for line, level, text in raw:
        index = "" if line == title_line else _next_index(open_headings, level)
        number, title = _split_document_number(text, numbered)

        # A guide that numbers its own sections is the authority on what they
        # are called, so its numbers address them. A positional index would
        # drift wherever the guide skips one — pyguide has no 2.15 at all,
        # and calling its 2.16 by that name would cite a section that does
        # not exist. The tree still advances above, so headings the guide
        # leaves unnumbered keep a positional index to be reachable by.
        if number:
            index = number
        headings.append(
            Heading(
                level=level,
                text=text,
                index=index,
                number=number,
                title=title,
                line=line,
            )
        )

    return headings


def _slugify(text: str) -> str:
    """Reduce heading text to a form that survives punctuation and casing.

    Args:
        text: The text to normalize.

    Returns:
        A lowercase slug with runs of non-alphanumeric characters as dashes.
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# Sections this long are worth warning about before one is fetched. Measured
# over the shipped corpus, it marks the 4% that are expensive while leaving
# the rest unannotated: a size on every line would read '0' on 59% of them,
# which is noise in the one output whose job is to be scanned quickly.
LARGE_SECTION_WORDS = 1200


def _format_size(words: int) -> str:
    """Describe a section's length, or nothing if it is unremarkable.

    Words rather than bytes or lines: lines mislead, because code blocks are
    line-dense and information-sparse, and bytes need dividing by four before
    they mean anything. Words are exact, need no tokenizer, and both a reader
    and an agent can convert them.

    Args:
        words: Number of words in the section, including its subsections.

    Returns:
        An annotation such as '  (1.6k words)', or an empty string.
    """
    if words < LARGE_SECTION_WORDS:
        return ""
    return f"  ({words / 1000:.1f}k words)"


def format_outline(headings: Sequence[Heading], content: str = "") -> str:
    """Render a heading tree, one heading per line.

    The tree is never trimmed. Every shipped guide outlines in under 6 KB
    where the guide itself reaches 200 KB, so there is nothing to save by
    showing less, and a trimmed outline hides the section a caller wanted.

    Args:
        headings: The headings of a guide, in document order.
        content: The guide's Markdown, used to size each section. Sizes are
            left off when it is empty.

    Returns:
        The outline as text: indentation for depth, the index to pass as REF,
        then the heading's own text verbatim so a rule can be cited exactly
        as the guide writes it. Sections long enough to be worth knowing
        about before fetching carry their length.
    """
    if not headings:
        return ""

    top_level = min(heading.level for heading in headings)
    lines = []
    for position, heading in enumerate(headings):
        # The document title roots the tree and has no index to print
        indent = "  " * (heading.level - top_level)
        prefix = f"{heading.index}  " if heading.index else ""

        # The title's 'section' is the whole guide, which --full already covers
        size = ""
        if content and heading.index:
            section = extract_section(content, headings, position)
            size = _format_size(len(section.split()))

        # The index already carries the guide's own number where it has one,
        # so printing the heading verbatim would show it twice.
        lines.append(f"{indent}{prefix}{heading.title}{size}")

    return "\n".join(lines)


def _matches_component(heading: Heading, component: str, exact: bool) -> bool:
    """Test one reference component against a heading.

    Args:
        heading: The heading to test.
        component: One component of a section reference.
        exact: Whether the text must match in full rather than as a substring.

    Returns:
        True if the component identifies the heading.
    """
    # Numbers are only ever compared in full; a substring of a number would
    # match unrelated sections.
    wanted = component.strip().rstrip(".")
    if wanted and wanted in {heading.index, heading.number}:
        return True

    slug = _slugify(component)
    if not slug:
        return False
    candidates = {_slugify(heading.title), _slugify(heading.text)}
    if exact:
        return slug in candidates
    return any(slug in candidate for candidate in candidates)


def _iter_ancestors(
    headings: Sequence[Heading], position: int
) -> Iterator[Heading]:
    """Yield the headings that enclose a heading, innermost first.

    Args:
        headings: The headings of a guide, in document order.
        position: Index into headings of the heading to walk out from.

    Yields:
        Each enclosing heading, skipping the sibling subtrees in between.
    """
    level = headings[position].level
    for candidate in reversed(headings[:position]):
        if candidate.level < level:
            level = candidate.level
            yield candidate


def _matches_path(
    headings: Sequence[Heading],
    position: int,
    components: Sequence[str],
    exact: bool,
) -> bool:
    """Test whether a heading and its ancestors satisfy a reference path.

    Args:
        headings: The headings of a guide, in document order.
        position: Index into headings of the candidate heading.
        components: Reference components, outermost first.
        exact: Whether text components must match in full.

    Returns:
        True if the last component matches the heading and the remaining ones
        match its ancestors, in order. Intervening ancestors may be skipped, so
        'Imports > Decision' works without naming every level in between.
    """
    if not _matches_component(headings[position], components[-1], exact):
        return False

    # Consume the outer components as the ancestors satisfying them are met
    remaining = list(components[:-1])
    for ancestor in _iter_ancestors(headings, position):
        if not remaining:
            break
        if _matches_component(ancestor, remaining[-1], exact):
            remaining.pop()

    return not remaining


def find_headings(headings: Sequence[Heading], reference: str) -> list[int]:
    """Locate the headings a section reference names.

    Args:
        headings: The headings of a guide, in document order.
        reference: A positional index ('2.2.1'), a section number the guide
            prints, heading text, or a parent-scoped path
            ('Imports > Decision').

    Returns:
        Positions of every matching heading, in document order. More than one
        means the reference is ambiguous.
    """
    components = [
        part.strip()
        for part in PATH_SEPARATOR_PATTERN.split(reference)
        if part.strip()
    ]
    if not components:
        return []

    # Prefer whole matches; fall back to substrings only when nothing matches
    # in full, so 'Imports' does not also select 'Imports and Exports'.
    for exact in (True, False):
        matches = [
            position
            for position in range(len(headings))
            if _matches_path(headings, position, components, exact)
        ]
        if matches:
            return matches

    return []


def extract_section(
    content: str, headings: Sequence[Heading], position: int
) -> str:
    """Extract one section of a guide.

    Args:
        content: The full Markdown text of the guide.
        headings: The headings of the guide, in document order.
        position: Index into headings of the section to extract.

    Returns:
        The heading and everything below it up to the next heading of the same
        or a higher level, so nested subsections travel with their parent.
    """
    lines = content.splitlines()
    heading = headings[position]

    end = len(lines)
    for following in headings[position + 1 :]:
        if following.level <= heading.level:
            end = following.line
            break

    # Shed the trailing anchors that belong to the following heading
    section = lines[heading.line : end]
    while section and (
        not section[-1].strip() or ANCHOR_PATTERN.match(section[-1].strip())
    ):
        section.pop()

    return "\n".join(section)


def _describe_heading(headings: Sequence[Heading], position: int) -> str:
    """Describe a heading by its path, for disambiguation messages.

    Args:
        headings: The headings of a guide, in document order.
        position: Index into headings of the heading to describe.

    Returns:
        The heading's title preceded by those of its ancestors, e.g.
        'Language Rules > Imports > Decision'. The document title is left out,
        since it roots every path and so tells the caller nothing.
    """
    path = [headings[position].title]
    path.extend(
        ancestor.title
        for ancestor in _iter_ancestors(headings, position)
        if ancestor.index
    )

    return " > ".join(reversed(path))


def _unique_reference(headings: Sequence[Heading], position: int) -> str:
    """Build a reference that selects one heading and no other.

    Args:
        headings: The headings of a guide, in document order.
        position: Index into headings of the heading to refer to.

    Returns:
        The heading's outline index when that is unambiguous, and its path
        otherwise. An index can collide with a number the guide prints when
        the two drift apart, and suggesting it would send the caller back to
        the same complaint.
    """
    index = headings[position].index
    if index and find_headings(headings, index) == [position]:
        return index

    path = _describe_heading(headings, position)
    return f'"{path}"'


# Beyond this many candidates a disambiguation list stops being readable
MAX_REPORTED_MATCHES = 15


def find_mentions(
    content: str, headings: Sequence[Heading], text: str
) -> list[int]:
    """Find the sections whose body mentions some text.

    This is a locator, not a search: it reports which sections to go and
    read, and deliberately does not print the matching lines. Searching the
    text of a guide is grep's job, and `--full` feeds it.

    Args:
        content: The full Markdown text of the guide.
        headings: The headings of the guide, in document order.
        text: The text to look for, matched as a case-insensitive substring.

    Returns:
        Positions of the innermost section holding each mention, in document
        order and without repeats.
    """
    needle = text.lower()
    if not needle:
        return []

    # Walk headings alongside the lines so each mention knows what encloses it
    positions: list[int] = []
    position = -1
    upcoming = list(headings)
    for number, line in enumerate(content.splitlines()):
        while upcoming and upcoming[0].line == number:
            upcoming.pop(0)
            position += 1

        # A mention above the first heading belongs to no section, and one
        # section is worth reporting once however many times it mentions it
        if position >= 0 and needle in line.lower():
            if not positions or positions[-1] != position:
                positions.append(position)

    return positions


def _example_reference(headings: Sequence[Heading]) -> str:
    """Pick a reference from a guide to show the caller what one looks like.

    The deepest heading is chosen because its index demonstrates the dotted
    form, which is the part a caller is least likely to guess.

    Args:
        headings: The headings of a guide, in document order.

    Returns:
        A real index from the guide, or an empty string if it has none.
    """
    addressable = [heading for heading in headings if heading.index]
    if not addressable:
        return ""
    return max(addressable, key=lambda heading: heading.level).index


def _echo_outline(content: str, language: str) -> None:
    """Print a guide's outline, and how to act on it.

    The outline is what a bare invocation prints, so a caller arrives here
    having read nothing, and the index column is the only thing on screen
    that needs explaining. One line on stderr costs a pipeline nothing and
    saves that caller a trip through --help.

    Args:
        content: The full Markdown text of the guide.
        language: The language whose guide is being read.
    """
    headings = parse_headings(content)
    click.echo(format_outline(headings, content))

    example = _example_reference(headings)
    if example:
        count = sum(1 for heading in headings if heading.index)
        # stdout is block-buffered when redirected, so without this the hint
        # lands above the outline it is meant to follow
        sys.stdout.flush()
        click.echo(
            f"# {count} sections · print one:  "
            f"readability guide {language} {example}",
            err=True,
        )


def _report_no_heading(
    content: str,
    headings: Sequence[Heading],
    reference: str,
    language: str,
) -> None:
    """Fail a reference that names no heading, saying where to look instead.

    A guide discusses plenty that no heading is named after, so a miss is
    often a vocabulary mismatch rather than a mistake. Reporting the sections
    that mention the words turns a dead end into the next command.

    Args:
        content: The full Markdown text of the guide.
        headings: The headings of the guide, in document order.
        reference: The section reference given on the command line.
        language: The language whose guide is being read.

    Raises:
        SystemExit: Always; this reports a failure.
    """
    click.echo(
        f"Error: Found no heading matching '{reference}' in the "
        f"'{language}' guide.",
        err=True,
    )

    mentions = find_mentions(content, headings, reference)
    if not mentions:
        click.echo(
            f"Run 'readability guide {language}' to list its sections.",
            err=True,
        )
        sys.exit(1)

    click.echo("It appears in these sections:", err=True)
    for position in mentions[:MAX_REPORTED_MATCHES]:
        suggestion = _unique_reference(headings, position)
        click.echo(
            f"  {suggestion}  {_describe_heading(headings, position)}",
            err=True,
        )
    if len(mentions) > MAX_REPORTED_MATCHES:
        click.echo(
            f"  ... and {len(mentions) - MAX_REPORTED_MATCHES} more", err=True
        )
    sys.exit(1)


def _select_section(content: str, reference: str, language: str) -> str:
    """Resolve a section reference against a guide and extract that section.

    Args:
        content: The full Markdown text of the guide.
        reference: The section reference given on the command line.
        language: The language whose guide is being read, for error messages.

    Returns:
        The Markdown of the requested section.

    Raises:
        SystemExit: If the reference matches no heading, or more than one.
    """
    headings = parse_headings(content)
    matches = find_headings(headings, reference)

    if not matches:
        _report_no_heading(content, headings, reference, language)

    # Reporting every candidate beats returning the first one silently, since
    # guides repeat headings ('Decision' appears under every Python rule).
    if len(matches) > 1:
        click.echo(
            f"Error: '{reference}' matches {len(matches)} headings in the "
            f"'{language}' guide. Repeat with one of:",
            err=True,
        )
        for position in matches[:MAX_REPORTED_MATCHES]:
            suggestion = _unique_reference(headings, position)
            description = _describe_heading(headings, position)
            # A path suggestion already reads as its own description
            if suggestion.strip('"') != description:
                suggestion = f"{suggestion} ({description})"
            click.echo(f"  {suggestion}", err=True)
        if len(matches) > MAX_REPORTED_MATCHES:
            click.echo(
                f"  ... and {len(matches) - MAX_REPORTED_MATCHES} more",
                err=True,
            )
        sys.exit(1)

    return extract_section(content, headings, matches[0])


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def cli(ctx: click.Context, verbose: bool) -> None:
    """Pulls the latest Google style guide in markdown format."""
    if verbose:
        logger.setLevel(logging.DEBUG)


@cli.command()
@click.argument("language", required=False)
@click.argument("reference", required=False, metavar="[REF]")
@click.option(
    "--full",
    is_flag=True,
    help="Print the whole guide, for grepping rather than reading.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def guide(
    language: Optional[str],
    reference: Optional[str],
    full: bool,
    verbose: bool,
) -> None:
    """Read the Google style guide for LANGUAGE.

    With no LANGUAGE, lists the languages that have one. With no REF, prints
    the guide's outline, which is a few kilobytes where the guide itself can
    be two hundred. REF then names a section to print: heading text, a
    parent-scoped path ('Imports > Decision'), or an index from the outline
    ('2.2.1').
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    # No language names no guide, so the useful answer is which ones exist
    if not language:
        _echo_languages()
        return

    # Refusing beats picking a winner: a silent precedence rule is how the
    # caller ends up reading the wrong thing without being told.
    if full and reference:
        raise click.UsageError("--full takes the whole guide, so REF cannot.")

    logger.info("Processing style guide for: %s", language)

    try:
        content = get_guide(language)

        if full:
            click.echo(content)
        elif reference:
            click.echo(_select_section(content, reference, language))
        else:
            _echo_outline(content, language)

    except (click.ClickException, click.UsageError) as e:
        logger.error("Execution failed: %s", e)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _resolve_filenames(languages: Sequence[str]) -> list[str]:
    """Map languages to the guides that back them.

    Args:
        languages: Language names or aliases, or empty for every guide.

    Returns:
        The guide filenames, deduplicated because aliases share a guide.

    Raises:
        click.UsageError: If a language has no guide.
    """
    if not languages:
        return sorted(set(LANGUAGE_MAP.values()))

    filenames = []
    for language in languages:
        filename = LANGUAGE_MAP.get(language.lower())
        if not filename:
            raise click.UsageError(
                f"Language '{language}' is not supported. Supported "
                f"languages: {', '.join(sorted(LANGUAGE_MAP.keys()))}"
            )
        # Aliases such as 'cpp' and 'c++' resolve to one guide, which is
        # fetched once however many of its names were given
        if filename not in filenames:
            filenames.append(filename)

    return filenames


@cli.command()
@click.argument("languages", nargs=-1, metavar="[LANGUAGE]...")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def sync(languages: Sequence[str], verbose: bool) -> None:
    """Refetch style guides from the web, replacing the local copies.

    With no LANGUAGE every guide is refetched; naming one or more refreshes
    just those.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    filenames = _resolve_filenames(languages)
    click.echo(f"Synchronizing {len(filenames)} style guide(s)...", err=True)

    if not os.path.exists(get_guides_dir()):
        os.makedirs(get_guides_dir(), exist_ok=True)

    success_count = 0
    failure_count = 0

    for filename in filenames:
        click.echo(f"Syncing {filename}...", err=True)
        try:
            refresh_guide(filename)
            click.echo(f"  synced to {get_local_path(filename)}", err=True)
            success_count += 1
        except Exception as e:
            logger.error("Failed to sync %s: %s", filename, e)
            failure_count += 1

    click.echo(
        f"Sync complete. Successes: {success_count}, Failures: {failure_count}",
        err=True,
    )


def _echo_languages() -> None:
    """Print the languages that have a guide, with their aliases."""
    # Group languages by their target guide so aliases share one line
    guides: dict[str, list[str]] = {}
    for lang, filename in LANGUAGE_MAP.items():
        guides.setdefault(filename, []).append(lang)

    click.echo("Supported languages and their aliases:")
    for filename in sorted(guides.keys()):
        aliases = sorted(guides[filename])
        cached = " [cached]" if os.path.exists(get_local_path(filename)) else ""
        click.echo(f"  - {', '.join(aliases)}{cached}")


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--fix", is_flag=True, help="Automatically fix issues if possible."
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def check(paths: Sequence[str], fix: bool, verbose: bool) -> None:
    """Run relevant formatters and linters for given paths.

    Exits with a non-zero status code if any tool reports findings, so the
    command can gate scripts and CI.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    # Resolve project root once for trigger file checking
    project_root = Path.cwd()

    # Process each provided path independently, tracking findings across
    # all of them so the exit code reflects the overall result
    found_issues = False
    for path_str in paths:
        found_issues |= _check_path(Path(path_str), project_root, fix=fix)

    if found_issues:
        sys.exit(1)

    # Findings are the only thing this command printed, so a clean run said
    # nothing at all and left the caller unable to tell it from a no-op.
    click.echo(f"No findings in {len(paths)} path(s).", err=True)


def _check_path(path: Path, project_root: Path, fix: bool = False) -> bool:
    """Apply relevant tools to a single path.

    Args:
        path: The path (file or directory) to check.
        project_root: The root of the project for trigger file discovery.
        fix: Whether to apply automatic fixes.

    Returns:
        True if any tool reported findings, False otherwise.
    """
    logger.info("Checking path: %s", path)

    # Iterate through all supported tool definitions
    found_issues = False
    for tool in _get_tool_definitions(path, project_root):
        if _should_run_tool(tool, path, project_root):
            found_issues |= _run_tool(tool["name"], tool, fix=fix)

    return found_issues


def _should_run_tool(
    tool: dict[str, Any], path: Path, project_root: Path
) -> bool:
    """Determine if a tool should run based on triggers and extensions.

    Args:
        tool: The tool configuration dictionary.
        path: The path being checked.
        project_root: The project root directory.

    Returns:
        True if the tool should run, False otherwise.
    """
    # Check if any trigger files (like pyproject.toml) exist in the project root
    has_trigger = any((project_root / t).exists() for t in tool["trigger"])

    # For files, also check if the extension matches one of the supported ones
    if path.is_file():
        return has_trigger and path.suffix in tool["extensions"]

    # For directories, the existence of a trigger file is sufficient
    return has_trigger


def _bundled_config(tool_name: str) -> Path:
    """Get the path to the bundled default configuration for a tool.

    Args:
        tool_name: The name of the tool (e.g. "ruff", "pyrefly").

    Returns:
        The path to the bundled default config file.
    """
    return Path(__file__).parent / "configs" / f"{tool_name}.toml"


def _has_project_config(
    project_root: Path, config_files: Sequence[str], tool_name: str
) -> bool:
    """Determine whether the project defines its own configuration for a tool.

    Args:
        project_root: The project root directory.
        config_files: Dedicated config filenames to look for (e.g. ruff.toml).
        tool_name: The pyproject.toml [tool.<name>] section to look for.

    Returns:
        True if the project has its own configuration, False otherwise.
    """
    # Dedicated config files take precedence over pyproject.toml sections
    if any((project_root / f).exists() for f in config_files):
        return True

    # Otherwise look for a [tool.<name>] section in pyproject.toml
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("Failed to parse %s: %s", pyproject, e)
        return False
    return tool_name in data.get("tool", {})


def _default_config_args(
    project_root: Path, config_files: Sequence[str], tool_name: str
) -> list[str]:
    """Build --config arguments pointing at the bundled defaults for a tool.

    Args:
        project_root: The project root directory.
        config_files: Dedicated config filenames the project may define.
        tool_name: The name of the tool, matching a bundled config file.

    Returns:
        --config arguments for the bundled defaults, or an empty list when the
        project defines its own configuration (which must take precedence).
    """
    if _has_project_config(project_root, config_files, tool_name):
        return []
    return ["--config", str(_bundled_config(tool_name))]


def _get_tool_definitions(
    path: Path, project_root: Path
) -> list[dict[str, Any]]:
    """Define supported tools with their triggers, extensions, and commands.

    Args:
        path: The path being checked.
        project_root: The project root, used to resolve default configurations.

    Returns:
        A list of tool configuration dictionaries.
    """
    path_str = str(path)

    # Fall back to the bundled default configs unless the project has its own
    ruff_config = _default_config_args(
        project_root, ["ruff.toml", ".ruff.toml"], "ruff"
    )
    pyrefly_config = _default_config_args(
        project_root, ["pyrefly.toml"], "pyrefly"
    )

    return [
        {
            "name": "ruff",
            "check": [
                "ruff",
                "check",
                "--force-exclude",
                *ruff_config,
                path_str,
            ],
            "check_format": [
                "ruff",
                "format",
                "--check",
                "--force-exclude",
                *ruff_config,
                path_str,
            ],
            "fix": [
                "ruff",
                "check",
                "--fix",
                "--force-exclude",
                *ruff_config,
                path_str,
            ],
            "format": [
                "ruff",
                "format",
                "--force-exclude",
                *ruff_config,
                path_str,
            ],
            "trigger": ["pyproject.toml", "ruff.toml", ".ruff.toml"],
            "extensions": [".py"],
        },
        {
            # Type checker only: it reports findings but cannot fix or format
            "name": "pyrefly",
            "check": ["pyrefly", "check", *pyrefly_config, path_str],
            "trigger": ["pyproject.toml", "pyrefly.toml"],
            "extensions": [".py"],
        },
        {
            "name": "biome",
            "check": [
                "npx",
                "-y",
                "biome",
                "lint",
                "--no-errors-on-unmatched",
                path_str,
            ],
            "check_format": [
                "npx",
                "-y",
                "biome",
                "format",
                "--no-errors-on-unmatched",
                path_str,
            ],
            "fix": [
                "npx",
                "-y",
                "biome",
                "lint",
                "--write",
                "--no-errors-on-unmatched",
                path_str,
            ],
            "format": [
                "npx",
                "-y",
                "biome",
                "format",
                "--write",
                "--no-errors-on-unmatched",
                path_str,
            ],
            "trigger": ["biome.json", "biome.jsonc"],
            "extensions": [
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".json",
                ".jsonc",
                ".css",
                ".html",
            ],
        },
        {
            "name": "prettier",
            "check_format": [
                "npx",
                "-y",
                "prettier",
                "--check",
                "--no-error-on-unmatched-pattern",
                path_str,
            ],
            "format": [
                "npx",
                "-y",
                "prettier",
                "--write",
                "--no-error-on-unmatched-pattern",
                path_str,
            ],
            "trigger": [
                ".prettierrc",
                ".prettierrc.json",
                ".prettierrc.yml",
                ".prettierrc.yaml",
                ".prettierrc.js",
                "prettier.config.js",
                "prettier.config.cjs",
            ],
            "extensions": [
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".json",
                ".css",
                ".scss",
                ".html",
                ".md",
                ".yml",
                ".yaml",
            ],
        },
        {
            "name": "go fmt",
            "check_format": ["gofmt", "-l", path_str],
            "format": ["go", "fmt", path_str],
            "trigger": ["go.mod"],
            "extensions": [".go"],
        },
    ]


def _run_tool(
    tool_name: str,
    tool_config: dict[str, Any],
    fix: bool = False,
) -> bool:
    """Orchestrate the execution of a specific formatting or linting tool.

    Args:
        tool_name: The name of the tool to run.
        tool_config: The tool configuration dictionary.
        fix: Whether to apply automatic fixes.

    Returns:
        True if the tool reported findings, False otherwise.
    """
    # Identify the primary command to check for executable availability
    cmd = (
        tool_config.get("format")
        or tool_config.get("check")
        or tool_config.get("fix")
        or tool_config.get("check_format")
    )
    if not cmd:
        return False

    executable = str(cmd[0])
    if not shutil.which(executable):
        logger.debug(
            "Tool %s (%s) not found in PATH, skipping.", tool_name, executable
        )
        return False

    logger.info("Running %s...", tool_name)
    found_issues = False
    try:
        if fix:
            # 1. Run formatters (if available) - these are expected to
            # modify files
            if "format" in tool_config:
                _execute_tool_command(tool_config["format"])

            # 2. Run fixers (if available) - these apply automatic linting fixes
            if "fix" in tool_config:
                _execute_tool_command(tool_config["fix"])
        # 1. Run check_format (if available) - check-only
        elif "check_format" in tool_config:
            logger.debug("Executing: %s", " ".join(tool_config["check_format"]))
            result = subprocess.run(
                tool_config["check_format"],
                capture_output=True,
                text=True,
                check=False,
                timeout=DEFAULT_TIMEOUT,
            )
            if result.returncode != 0 or (
                tool_name == "go fmt" and result.stdout.strip()
            ):
                found_issues = True
                click.echo(
                    f"--- {tool_name} formatting findings ---\n"
                    f"{result.stdout}\n{result.stderr}"
                )

        # 3. Run checks and report findings - these provide feedback to the user
        if "check" in tool_config:
            logger.debug("Executing: %s", " ".join(tool_config["check"]))
            result = subprocess.run(
                tool_config["check"],
                capture_output=True,
                text=True,
                check=False,
                timeout=DEFAULT_TIMEOUT,
            )
            if result.returncode != 0:
                found_issues = True
                click.echo(
                    f"--- {tool_name} findings ---\n"
                    f"{result.stdout}\n{result.stderr}"
                )

    except subprocess.CalledProcessError as e:
        logger.warning("%s failed with exit code %d", tool_name, e.returncode)
        if e.stdout:
            logger.debug("STDOUT: %s", e.stdout)
        if e.stderr:
            logger.debug("STDERR: %s", e.stderr)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Unexpected error while running %s: %s", tool_name, e)

    return found_issues


def _execute_tool_command(cmd: list[str]) -> None:
    """Execute a tool command, raising if it exits with a non-zero code.

    Args:
        cmd: The command list to execute.

    Raises:
        subprocess.CalledProcessError: If the command returns a non-zero
            exit code.
    """
    logger.debug("Executing: %s", " ".join(cmd))
    subprocess.run(
        cmd, capture_output=True, check=True, timeout=DEFAULT_TIMEOUT
    )


# Main entry point for the CLI
def main() -> None:
    """Main entry point for the CLI."""
    # Configure logging here rather than at import time so that importing this
    # module as a library (e.g. from lemming) has no side effects
    # WARNING, not INFO: everything logged below it narrates progress, which
    # is what --verbose is for. Leaving it on meant there was no quiet mode
    # and the flag could only add DEBUG on top.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    cli()


if __name__ == "__main__":
    main()
