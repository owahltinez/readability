# Readability

A simple CLI tool to pull the latest style guides from Google for various programming languages in Markdown format. This is ideal for AI agents or developers who need quick access to style conventions without browsing HTML pages.

## Features

- Supports multiple languages (Python, Shell, C++, Java, JS/TS, Go, etc.).
- **Offline Mode**: Local caching of style guides for fast, offline access.
- **Sync Command**: Easily update all local guides with a single command.
- Converts HTML-based style guides to Markdown using `markdownify`.
- Allows saving the guide to a file or printing it to stdout.

## Installation

This project uses `uv` for dependency management.

```bash
# Clone the repository
git clone <repository-url>
cd readability

# Install dependencies and create a virtual environment
uv sync

# (Optional) Populate the local cache for offline use
uv run readability sync
```

## Usage

You can run the tool using `uv run readability`:

```bash
# Fetch the Python style guide (uses local cache if available)
uv run readability fetch python

# Force fetching the latest version from the web
uv run readability fetch python --remote

# Save a style guide to a file
uv run readability fetch cpp --output cpp-style.md

# Synchronize all supported style guides to the local cache
uv run readability sync
```

### Supported Languages

`python`, `shell`, `objc`, `r`, `csharp`, `go`, `cpp`, `java`, `js`, `ts`, `html`, `css`, `json`, `vim`, and more aliases.

## Offline Mode

The tool stores local copies of the style guides in the `guides/` directory. By default, `fetch` will use these local files if they exist. Use the `sync` command to refresh these files from the web.

## Development

Run tests with `pytest`:

```bash
uv run pytest
```

Check code style with `ruff`:

```bash
uv run ruff check .
uv run ruff format .
```
