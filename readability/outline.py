"""Parse, search, and render style-guide outlines."""

import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import click

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


@dataclass(frozen=True)
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


def _section_bounds(
    lines: Sequence[str], headings: Sequence[Heading], position: int
) -> tuple[int, int]:
    """Locate the lines a section occupies within a guide.

    Everything that decides what a section holds lives here, so that a caller
    asking which section holds a line and a caller printing that section
    cannot disagree about the answer.

    Args:
        lines: The guide's lines, as split by the caller.
        headings: The headings of the guide, in document order.
        position: Index into headings of the section to bound.

    Returns:
        A half-open range of line indices, from the heading itself up to the
        next heading of the same or a higher level, so nested subsections
        travel with their parent.
    """
    heading = headings[position]

    end = len(lines)
    for following in headings[position + 1 :]:
        if following.level <= heading.level:
            end = following.line
            break

    # Shed the trailing anchors that belong to the following heading
    while end > heading.line and (
        not lines[end - 1].strip()
        or ANCHOR_PATTERN.match(lines[end - 1].strip())
    ):
        end -= 1

    return heading.line, end


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
    start, end = _section_bounds(lines, headings, position)

    return "\n".join(lines[start:end])


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
        order and without repeats. Every one of them prints the text and can
        be named on the command line.
    """
    needle = text.lower()
    if not needle:
        return []

    lines = content.splitlines()

    # Only an indexed heading can be offered. The document title has no index
    # because it is the whole guide rather than a section within it, so
    # crediting the preamble to it would suggest reading everything.
    sections = [
        (position, _section_bounds(lines, headings, position))
        for position in range(len(headings))
        if headings[position].index
    ]

    found: set[int] = set()
    for number, line in enumerate(lines):
        if needle not in line.lower():
            continue

        # Bounds nest, so the last section still covering the line is the
        # innermost one that prints it. A section sheds the anchors of the
        # heading below it, and those fall to an ancestor rather than to the
        # section they merely trail.
        holders = [
            position
            for position, (start, end) in sections
            if start <= number < end
        ]
        if holders:
            found.add(holders[-1])

    return sorted(found)


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
