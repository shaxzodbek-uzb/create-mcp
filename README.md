<div align="center">

# create-mcp

**The `create-next-app` for production MCP servers.**

One command scaffolds a typed, tested, auth-ready Python [MCP](https://modelcontextprotocol.io) server you can *ship* — not just run.

[![PyPI](https://img.shields.io/pypi/v/create-mcp.svg)](https://pypi.org/project/create-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/create-mcp.svg)](https://pypi.org/project/create-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

</div>

```bash
uvx create-mcp my-server
```

<p align="center">
  <img src="docs/demo.svg" alt="create-mcp — scaffold a production MCP server in one command, with tests green out of the box" width="760">
</p>

## Why

The official MCP SDK quickstart and `create-mcp-server` hand you a single
hello-world tool and stop. You still have to add tests, typing, linting, CI, a
Dockerfile, config, and — the hard one — spec-compliant **OAuth 2.1**. Most MCP
servers in the wild are hello-world demos that never reach production.

`create-mcp` generates the *whole* repo, green on the first run:

- ⚡ **`uvx create-mcp` — zero install.** Same ergonomics as `create-next-app`.
- 🧱 **Production defaults, not a toy.** Tests, CI, Docker, ruff, mypy, pre-commit, `.env`, a real README.
- 🔐 **OAuth 2.1 in one flag.** `--auth oauth` scaffolds an [RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728) resource server (Protected Resource Metadata + `401`/`WWW-Authenticate` discovery + JWT validation). Timed for the 2026 MCP authorization spec.
- 🌊 **Streamable HTTP first.** The modern transport (SSE is deprecated), plus a stdio preset for Claude Desktop / Cursor.
- 🧪 **Tests pass out of the box.** Generated tests use FastMCP's in-memory client — no network, milliseconds.
- 🎯 **Presets, not questionnaires.** Scaffold a *use case*: `minimal`, `api-wrapper`, `db`, `agent-tools`.
- 📄 **`--from-openapi`.** Point it at a spec and get one typed tool per operation — enums as `Literal`, request bodies flattened into named arguments, descriptions carried into the docstrings.
- 🐍 **Typed end-to-end.** Pydantic models for tool I/O; ruff- and mypy-clean.

Built on [FastMCP](https://gofastmcp.com) + [`uv`](https://docs.astral.sh/uv/).

## Usage

```bash
# Interactive
uvx create-mcp

# Scripted / non-interactive
uvx create-mcp pay-tools --preset api-wrapper --auth oauth --yes

# From an OpenAPI document — one typed tool per operation
uvx create-mcp stripe-tools --from-openapi ./openapi.json --yes
```

Then:

```bash
cd pay-tools
uv sync
uv run pay_tools     # start the server
uv run pytest        # green ✓
```

### Options

| Flag | Values | Default | Description |
|------|--------|---------|-------------|
| `--preset` `-p` | `minimal`, `api-wrapper`, `db`, `agent-tools` | `minimal` | Starting set of tools/resources/prompts |
| `--from-openapi` | file path or URL | — | Generate one typed tool per operation from a spec |
| `--openapi-tag` | tag name (repeatable) | — | Only include operations carrying that OpenAPI tag |
| `--transport` `-t` | `streamable-http`, `stdio` | `streamable-http` | MCP transport |
| `--auth` `-a` | `none`, `oauth` | `none` | OAuth 2.1 resource server (RFC 9728) |
| `--package-name` | identifier | derived | Override the Python package name |
| `--output-dir` `-o` | path | `.` | Where to create the project |
| `--no-git` / `--git` | | `--git` | Initialise a git repo + first commit |
| `--no-install` / `--install` | | `--install` | Run `uv sync` after scaffolding |
| `--no-precommit` / `--precommit` | | `--precommit` | Install pre-commit hooks |
| `--force` | | off | Overwrite a non-empty target directory |
| `--yes` `-y` | | off | Accept all defaults; never prompt (CI) |

### Presets

| Preset | What you get |
|--------|--------------|
| **minimal** | A clean typed server: one tool (structured output), a resource, a prompt. |
| **api-wrapper** | Wrap an HTTP/JSON API as MCP tools, with the network call isolated for easy mocking. |
| **db** | A SQLite-backed store exposed as CRUD tools (swap in your real DB). |
| **agent-tools** | A toolbox for agents: safe calculator, scratchpad memory, clock. |

## From an OpenAPI document

Point `--from-openapi` at a JSON or YAML spec — a local file or a URL — and every
operation becomes a typed MCP tool:

```bash
uvx create-mcp petstore-tools --from-openapi https://petstore3.swagger.io/api/v3/openapi.json --yes
```

```python
# src/petstore_tools/tools.py — generated
@mcp.tool
async def find_pets_by_status(status: Literal["available", "pending", "sold"]) -> Any:
    """Finds Pets by status.

    Args:
        status: Status values that need to be considered for filter.
    """
    return await _request("GET", "/pet/findByStatus", query={"status": status})
```

What it carries over from the spec:

- **Real types.** `integer` → `int`, `array` → `list[str]`, and an `enum` becomes a
  `Literal` — so the client validates the argument instead of the API rejecting it.
- **Required vs optional.** Optional parameters get `| None = None` and are dropped from
  the request when unset, rather than sent as literal `null`.
- **Request bodies, flattened.** A JSON object body becomes named arguments, so the model
  can see what the endpoint wants. Non-object bodies fall back to a single `body` argument.
- **Descriptions.** Operation summaries and parameter descriptions become the docstring —
  which is exactly what an MCP client shows the model when it picks a tool.
- **Auth.** A declared `bearer` or `apiKey` scheme wires up `API_TOKEN` / `API_KEY` from
  the environment.

Every call routes through one `_request` function, so the generated `tests/test_tools.py`
patches that and runs offline — no key, no network, still green on the first run.

### YAML specs

JSON works on the standard library. YAML needs PyYAML:

```bash
uvx --from 'create-mcp[yaml]' create-mcp my-server --from-openapi ./openapi.yaml
```

### Big specs

A 200-operation API makes a bad MCP server: long tool lists eat the model's context and
measurably hurt tool selection. Narrow it at generation time —

```bash
uvx create-mcp stripe-tools --from-openapi ./stripe.json --openapi-tag Invoice --openapi-tag Customer
```

— and delete what you don't need from `tools.py` afterwards. It's ordinary Python; the
generator hands you a starting point, not a binding.

> **Scope, honestly.** This is a pragmatic subset, not a full OpenAPI implementation.
> Local `$ref`s, scalars, arrays and enums are resolved; remote `$ref`s, `allOf` merging
> and polymorphic discriminators degrade to `dict[str, Any]` rather than failing.
> Deprecated operations are skipped. Responses are returned as decoded JSON, not modelled.

## What the generated project looks like

```
my-server/
├── src/my_server/
│   ├── server.py      # FastMCP instance (+ /health, + auth when enabled)
│   ├── tools.py       # your tools, resources, prompts
│   ├── settings.py    # typed config (pydantic-settings)
│   ├── app.py         # FastAPI host mounting MCP at /mcp
│   ├── auth.py        # OAuth 2.1 resource server  (only with --auth oauth)
│   └── __main__.py    # `uv run my_server`
├── tests/             # in-memory tests, green out of the box
├── Dockerfile         # uv-based image
├── .github/workflows/ci.yml
├── .pre-commit-config.yaml
├── .env.example
└── pyproject.toml
```

## Requirements

- [`uv`](https://docs.astral.sh/uv/) (for `uvx` and the generated projects)
- Python 3.11+

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every release runs the full matrix —
generating a project for **each preset × auth mode**, then installing, linting,
type-checking and testing it — so the templates can't rot silently.

## License

MIT © Shaxzodbek Qambaraliyev / [Blaze](https://blaze.uz)
