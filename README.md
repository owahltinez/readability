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
  guides (Python, Shell, C++, Java, JS/TS, Go, etc.) converted to Markdown,
  navigable by outline and by section rather than read whole.
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

# Print where the guide already lives, to search it without copying it
readability guide python --path
grep -n "Naming" "$(readability guide python --path)"

# Synchronize all supported style guides to the local cache
readability sync
```

A guide can exceed 100 KB, so `--path` is the cheap way to consult one: the
file is already on disk, and searching it beats reading it whole or writing
out a second copy. It is also what a coding agent should use, rather than
redirecting the guide into a repository.

### Navigating a Guide

`--outline` lists a guide's headings and `--section` prints just one of them,
which turns "read 200 KB" into "list the sections, fetch the one you need":

```bash
# List every heading, with the index to pass to --section
readability guide cpp --outline

# Only the top two levels, for a bird's eye view of a large guide
readability guide cpp --outline --depth 2

# Print one section: its heading and everything nested under it
readability guide shell --section "Function Comments"
readability guide cpp --section 10.4

# Sections can be saved like whole guides can
readability guide python --section "Imports" --output imports.md
```

A section reference can be any of the following:

| Reference | Example |
|-----------|---------|
| Section index, as shown by `--outline` | `--section 2.2.4` |
| Heading text, case-insensitive, or its slug | `--section "function comments"` |
| A parent-scoped path, spaces around the `>` | `--section "Imports > Decision"` |

Whole matches are preferred; a reference that matches nothing in full is
retried as a substring of the heading text.

Three of the shipped guides — Python, JavaScript, and Java — number their own
sections, and those numbers are the index. A rule cited from the outline then
matches the published guide exactly, including where the guide skips a
number: the Python guide has no 2.15 at all, so its 2.16 is listed as 2.16
rather than renumbered. The other eleven guides number nothing, so their
index comes from each heading's position in the tree.

Either way the index is unique, which is what makes a repeated heading
addressable — `Definition`, `Pros`, `Cons`, and `Decision` appear under every
rule in the Python guide. For a reference stored and used later, prefer a
printed section number or the heading text over a positional index, since
positional indices shift when an unnumbered guide is re-synced.

A reference that matches several headings is reported rather than guessed at,
listing the index and path of every candidate on stderr:

```bash
$ readability guide python --section Decision
Error: 'Decision' matches 19 headings in the 'python' guide. Repeat with one of:
  --section 2.1.4 (Python Language Rules > Lint > Decision)
  --section 2.2.4 (Python Language Rules > Imports > Decision)
  ...
```

Content goes to stdout and diagnostics to stderr, so both flags are safe to
pipe. Headings inside fenced code blocks are ignored, which matters for the
Shell and Python guides where `#` starts a comment.

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

### Releasing

Releases are published to PyPI as
[`readability-cli`](https://pypi.org/project/readability-cli/) via trusted
publishing: pushing a `v*` tag triggers the `publish.yml` GitHub Actions
workflow, which builds the package with `uv build` and uploads it.

```bash
# 1. Bump the version in pyproject.toml, commit, and push
# 2. Tag the release and push the tag
git tag v0.4.1
git push origin v0.4.1
```
