# Readability

A simple CLI tool to pull the latest style guides from Google for various programming languages in Markdown format. This is ideal for AI agents or developers who need quick access to style conventions without browsing HTML pages.

## Features

- Supports multiple languages (Python, Shell, C++, Java, JS/TS, Go, etc.).
- **Offline Mode**: Local caching of style guides for fast, offline access.
- **Sync Command**: Easily update all local guides with a single command.
- Converts HTML-based style guides to Markdown using `markdownify`.
- Allows saving the guide to a file or printing it to stdout.

## Quick Start (No Installation)

You can run the tool directly without cloning the repository using `uvx`:

```bash
# Fetch the Python style guide
uvx --from git+https://github.com/owahltinez/readability.git readability fetch python

# Save the C++ style guide to a file
uvx --from git+https://github.com/owahltinez/readability.git readability fetch cpp -o cppguide.md
```

Alternatively, you can install it as a tool:

```bash
# Install the readability tool
uv tool install git+https://github.com/owahltinez/readability.git

# Use it anywhere
readability fetch python
```

## Installation (For Development)

This project uses `uv` for dependency management.

```bash
# Clone the repository
git clone https://github.com/owahltinez/readability.git
cd readability

# Install dependencies and create a virtual environment
uv sync

# (Optional) Populate the local cache for offline use
uv run readability sync
```

## Usage

You can run the tool using `uv run readability` or after installing it as a tool:

```bash
# Fetch the Python style guide (uses local cache if available)
readability fetch python

# Force fetching the latest version from the web
readability fetch python --remote

# Save a style guide to a file
readability fetch cpp --output cpp-style.md

# Synchronize all supported style guides to the local cache
readability sync

# List all supported languages and their aliases
readability languages
```

### Supported Languages

Use `readability languages` to see a full list of supported languages and their aliases. Commonly supported: `python`, `shell`, `objc`, `r`, `csharp`, `go`, `cpp`, `java`, `js`, `ts`, `html`, `css`, `json`, `vim`.

## Automatic Updates

The style guides in the `guides/` directory are automatically synchronized weekly from the official [Google Style Guides](https://google.github.io/styleguide/) repository via GitHub Actions.

## Offline Mode

The tool stores local copies of the style guides in the `guides/` directory. By default, `fetch` will use these local files if they exist. Use the `sync` command to refresh these files from the web.

### Custom Cache Location

You can override the default `guides/` directory by setting the `READABILITY_CACHE` environment variable. This is useful if you want to store the guides in a specific location or share them across different installations.

```bash
export READABILITY_CACHE=/path/to/my/guides
readability fetch python
```

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
