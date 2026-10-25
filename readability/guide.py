"""Fetch and cache Google style guides."""

import functools
from importlib.resources import files
import logging
import os
import types

import click

logger = logging.getLogger("readability")


@functools.cache
def load_sync_deps() -> tuple[types.ModuleType, types.ModuleType]:
    """Imports the libraries only the guide refresh needs.

    Guides ship pre-converted, so this stack stays out of the default
    install rather than charging every user for `sync`.

    Returns:
        The requests and markdownify modules.

    Raises:
        click.ClickException: If the sync extra is not installed.
    """
    try:
        import markdownify  # noqa: PLC0415
        import requests  # noqa: PLC0415
    except ImportError as e:
        raise click.ClickException(
            "Refreshing style guides needs extra libraries. Install them "
            "with: pip install 'readability-cli[sync]'"
        ) from e

    return requests, markdownify


def get_guides_dir() -> str:
    """Get the directory where style guides are cached.

    Defaults to the bundled guides in the package, but can be overridden by
    the READABILITY_CACHE environment variable.

    Returns:
        The path to the guides directory.
    """
    bundled_guides = files("readability").joinpath("guides")
    return os.getenv("READABILITY_CACHE") or str(bundled_guides)


LANGUAGE_MAP = {
    "python": "pyguide.md",
    "shell": "shellguide.md",
    "objc": "objcguide.md",
    "objective-c": "objcguide.md",
    "r": "Rguide.md",
    "csharp": "csharpguide.md",
    "c#": "csharpguide.md",
    "docguide": "docguide/style.md",
    "markdown": "docguide/style.md",
    "go": "go/guide.md",
    "cpp": "cppguide.html",
    "c++": "cppguide.html",
    "java": "javaguide.html",
    "js": "jsguide.md",
    "javascript": "jsguide.md",
    "ts": "tsguide.html",
    "typescript": "tsguide.html",
    "html": "htmlcssguide.md",
    "css": "htmlcssguide.md",
    "json": "jsoncguide.md",
    "vim": "vimscriptguide.md",
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
    requests, _ = load_sync_deps()
    logger.info("Fetching style guide from %s", url)

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch content from %s: %s", url, e)
        raise click.ClickException(
            f"Failed to fetch style guide from {url}: {e}"
        ) from e

    return response.text


def convert_to_markdown(content: str, filename: str) -> str:
    """Convert the raw content to markdown based on file extension.

    Args:
        content: The raw text content to convert.
        filename: The original filename to determine conversion logic.

    Returns:
        The converted markdown content.
    """
    _, markdownify = load_sync_deps()
    logger.debug("Converting content for %s", filename)

    if filename.endswith(".md"):
        return content

    if filename.endswith(".html"):
        return markdownify.markdownify(content, **MARKDOWNIFY_OPTIONS)

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
    flattened = filename.replace("/", "-")
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
    filename = LANGUAGE_MAP.get(language.lower())
    if not filename:
        error_msg = f"Language '{language}' is not supported."
        logger.warning(error_msg)
        raise click.UsageError(
            f"{error_msg} Supported languages: "
            f"{', '.join(sorted(LANGUAGE_MAP.keys()))}"
        )

    local_path = get_local_path(filename)

    if not remote and os.path.exists(local_path):
        logger.info("Reading style guide from local file: %s", local_path)
        with open(local_path, encoding="utf-8") as f:
            return f.read()

    return refresh_guide(filename)
