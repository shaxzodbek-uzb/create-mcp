# Contributing to create-mcp

Thanks for helping! This project scaffolds MCP servers, so the most important
rule is: **the generated projects must always be green.**

## Development setup

```bash
git clone https://github.com/blaze-uz/create-mcp
cd create-mcp
uv sync
uv run create-mcp demo --preset minimal --yes -o /tmp   # try it
```

## Running the tests

```bash
uv run pytest          # unit tests for the generator + CLI
uv run ruff check .
uv run ruff format --check .
```

## How it works

- `src/create_mcp/cli.py` — the Typer CLI (prompts, flags, post-scaffold steps).
- `src/create_mcp/generator.py` — renders the templates to disk.
- `src/create_mcp/presets.py` — the preset registry.
- `src/create_mcp/templates/` — the Jinja templates, in layers:
  - `base/` — always rendered.
  - `presets/<preset>/` — overlaid per preset (provides `tools.py` + its tests).
  - `auth/` — overlaid when `--auth oauth` (adds `auth.py` + its tests).

Templates use **non-default Jinja delimiters** — `[[ var ]]` and `[% if %]` — so
the templates can contain `{{ }}` and GitHub Actions `${{ }}` verbatim. Path
tokens: `__pkg__` → package name, `dot-foo` → `.foo`, and a trailing `.jinja`
is stripped.

## The golden rule: test the output, not just the generator

The CI matrix generates a project for **every preset × auth mode**, then runs
`uv sync`, `ruff`, `mypy` and `pytest` inside it. If you change a template, make
sure a freshly generated project still passes all four. The fastest local check:

```bash
uv run create-mcp t --preset agent-tools --auth oauth --yes --no-git -o /tmp \
  && cd /tmp/t && uv sync && uv run ruff check . && uv run pytest
```

## Keeping up with FastMCP

Generated projects pin FastMCP via `create_mcp.FASTMCP_TARGET`. When you bump it,
regenerate and re-run the matrix so the templates track the current API.
