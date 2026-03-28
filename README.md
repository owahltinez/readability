# Readability

A simple CLI tool to pull the latest style guides from Google for various programming languages in Markdown format. This is ideal for AI agents or developers who need quick access to style conventions without browsing HTML pages.

## Features

- Supports multiple languages (Python, Shell, C++, Java, JS/TS, Go, etc.).
- **Linting & Formatting**: A `check` command that automatically detects and runs relevant tools (Ruff, Biome, Prettier, Go Fmt) for your project.
- **Offline Mode**: Local caching of style guides for fast, offline access.
- **Sync Command**: Easily update all local guides with a single command.
- Converts HTML-based style guides to Markdown using `markdownify`.
- Allows saving the guide to a file or printing it to stdout.

## Quick Start (No Installation)

You can run the tool directly without cloning the repository using `uvx`:

```bash
# Get the Python style guide
uvx --from git+https://github.com/owahltinez/readability.git readability guide python

# Save the C++ style guide to a file
uvx --from git+https://github.com/owahltinez/readability.git readability guide cpp -o cppguide.md
```

Alternatively, you can install it as a tool:

```bash
# Install the readability tool
uv tool install git+https://github.com/owahltinez/readability.git

# Use it anywhere
readability guide python
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
# Get the Python style guide (uses local cache if available)
readability guide python

# Force fetching the latest version from the web
readability guide python --remote

# Save a style guide to a file
readability guide cpp --output cpp-style.md

# Synchronize all supported style guides to the local cache
readability sync

# List all supported languages and their aliases
readability languages

# Check and format your code
readability check .
```

### Checking and Formatting

The `check` command identifies and runs relevant linting and formatting tools based on file extensions and the presence of configuration files (triggers) in your project root.

```bash
# Run checks on the current directory
readability check .

# Check specific files or directories
readability check src/ tests/ main.py

# Automatically fix and format files
readability check . --fix
```

#### Supported Tools

| Tool | Supported Extensions | Trigger Files |
|------|----------------------|---------------|
| **Ruff** | `.py` | `pyproject.toml`, `ruff.toml`, `.ruff.toml` |
| **Biome** | `.js`, `.ts`, `.jsx`, `.tsx`, `.json`, `.jsonc`, `.css`, `.html` | `biome.json`, `biome.jsonc` |
| **Prettier** | `.js`, `.ts`, `.jsx`, `.tsx`, `.json`, `.css`, `.scss`, `.html`, `.md`, `.yml`, `.yaml` | `.prettierrc*`, `prettier.config.*` |
| **Go Fmt** | `.go` | `go.mod` |

The command will only run a tool if its trigger file exists in the current working directory and the tool is available in your `PATH`. For `biome` and `prettier`, it attempts to run them via `npx`.

### Supported Languages

Use `readability languages` to see a full list of supported languages and their aliases. This command also indicates which guides are currently available in the local cache with a `[cached]` label:

```bash
$ readability languages
Supported languages and their aliases:
  - r [cached]
  - c++, cpp [cached]
  - c#, csharp [cached]
  - docguide, markdown [cached]
  - go [cached]
  - css, html [cached]
  - java [cached]
  - javascript, js [cached]
  - json [cached]
  - objc, objective-c [cached]
  - python [cached]
  - shell [cached]
  - ts, typescript [cached]
  - vim [cached]
```

## Automatic Updates

The style guides in the `guides/` directory are automatically synchronized weekly from the official [Google Style Guides](https://google.github.io/styleguide/) repository via GitHub Actions.

## Offline Mode

The tool stores local copies of the style guides in the `guides/` directory. By default, `guide` will use these local files if they exist. Use the `sync` command to refresh these files from the web.

### Custom Cache Location

You can override the default `guides/` directory by setting the `READABILITY_CACHE` environment variable. This is useful if you want to store the guides in a specific location or share them across different installations.

```bash
export READABILITY_CACHE=/path/to/my/guides
readability guide python
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
