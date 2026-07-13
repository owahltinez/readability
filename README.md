# Readability

A CLI tool that keeps code aligned with Google style conventions. It runs the
right linters, formatters, and type checkers for your project with sensible
defaults, and serves the official Google style guides in Markdown format. This
is ideal for AI agents or developers who want consistent code quality checks
and quick access to style conventions without browsing HTML pages.

## Features

- **Linting & Formatting**: A `check` command that automatically detects and
  runs relevant tools (Ruff, Pyrefly, Biome, Prettier, gofmt) for your project.
- **Sensible Defaults**: Bundled Google-style configurations for Ruff and
  Pyrefly are used automatically when a project does not define its own.
- **Style Guides**: A `guide` command that fetches the latest Google style
  guides (Python, Shell, C++, Java, JS/TS, Go, etc.) converted to Markdown.
- **Offline Mode**: Local caching of style guides for fast, offline access,
  kept fresh with a single `sync` command.

## Quick Start

You can run the tool directly without installing it using `uvx`:

```bash
# Check and fix formatting for the current directory
uvx --from readability-cli readability check . --fix

# Get the Python style guide
uvx --from readability-cli readability guide python
```

## Installation

Install it as a global tool with `uv`:

```bash
# Install the readability tool
uv tool install readability-cli

# Use it anywhere
readability check .
readability guide python
```

### For Development

This project uses `uv` for dependency management:

```bash
# Clone the repository
git clone https://github.com/owahltinez/readability.git
cd readability

# Install dependencies and create a virtual environment
uv sync

# (Optional) Populate the local cache for offline use
uv run readability sync
```

## Checking and Formatting

The `check` command identifies and runs relevant linting and formatting tools
based on file extensions and the presence of configuration files (triggers) in
your project root:

```bash
# Run checks on the current directory
readability check .

# Check specific files or directories
readability check src/ tests/ main.py

# Automatically fix and format files
readability check . --fix
```

### Supported Tools

| Tool | Supported Extensions | Trigger Files |
|------|----------------------|---------------|
| **Ruff** | `.py` | `pyproject.toml`, `ruff.toml`, `.ruff.toml` |
| **Pyrefly** | `.py` | `pyproject.toml`, `pyrefly.toml` |
| **Biome** | `.js`, `.ts`, `.jsx`, `.tsx`, `.json`, `.jsonc`, `.css`, `.html` | `biome.json`, `biome.jsonc` |
| **Prettier** | `.js`, `.ts`, `.jsx`, `.tsx`, `.json`, `.css`, `.scss`, `.html`, `.md`, `.yml`, `.yaml` | `.prettierrc*`, `prettier.config.*` |
| **gofmt** | `.go` | `go.mod` |

The command will only run a tool if its trigger file exists in the current
working directory and the tool is available in your `PATH`. For `biome` and
`prettier`, it attempts to run them via `npx`.

### Default Configurations

For Ruff and Pyrefly, bundled defaults based on the
[Google Python style guide](https://google.github.io/styleguide/pyguide.html)
(80-column lines, Google docstring convention, import ordering, full type
checking) are applied when the project does not define its own configuration.
To override them, add a `[tool.ruff]` or `[tool.pyrefly]` section to your
`pyproject.toml` (or a dedicated `ruff.toml` / `pyrefly.toml`) — any
project-level configuration takes full precedence over the bundled defaults.

## Style Guides

The `guide` command prints a Google style guide as Markdown, using the local
cache when available:

```bash
# Get the Python style guide (uses local cache if available)
readability guide python

# Force fetching the latest version from the web
readability guide python --remote

# Save a style guide to a file
readability guide cpp --output cpp-style.md

# Synchronize all supported style guides to the local cache
readability sync
```

### Supported Languages

Use `readability languages` to see a full list of supported languages and
their aliases. This command also indicates which guides are currently
available in the local cache with a `[cached]` label:

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

### Offline Mode

The tool stores local copies of the style guides in the `guides/` directory
and the `guide` command uses these local files when they exist. The bundled
copies are automatically synchronized weekly from the official
[Google Style Guides](https://google.github.io/styleguide/) repository via
GitHub Actions, and you can refresh your local cache at any time with the
`sync` command.

You can override the default `guides/` directory by setting the
`READABILITY_CACHE` environment variable. This is useful if you want to store
the guides in a specific location or share them across different
installations:

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
