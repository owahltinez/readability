"""Fetch and cache Google style guides."""

from importlib.resources import files
import logging
import os
import warnings

from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning
import click
from markdownify import markdownify as md
import requests

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger("readability")


def get_guides_dir() -> str:
    """Get the directory where style guides are cached.

    Defaults to the bundled guides in the package, but can be overridden by
    the READABILITY_CACHE environment variable.

    Returns:
        The path to the guides directory.
    """
    bundled_guides = files("readability").joinpath("guides")
    return os.getenv("READABILITY_CACHE") or str(bundled_guides)


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

# Escaping underscores would leave 'from \_\_future\_\_ import' in the text
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
