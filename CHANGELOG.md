# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-08-16

### Added
- **`--from-openapi`** — generate a working MCP server from an OpenAPI 3.x
  document (a file or a URL, JSON or YAML). Every operation becomes a typed
  `@mcp.tool` with a real Python signature, a docstring carrying the operation's
  own summary and description, and a generated test.
- `--openapi-tag` to scaffold only the operations under one tag, so a 300-endpoint
  spec doesn't have to become a 300-tool server.
- New `openapi` preset.

### Notes on the generated code
- Path, query, header and JSON-body parameters are flattened into a single
  signature; required parameters come first, optional ones get their spec default.
- `$ref`, `enum` and `anyOf` schemas resolve to real annotations (`Literal[...]`,
  `X | None`) rather than a bare `dict`.
- Operation IDs are converted acronym-aware, so `getHTTPStatus` becomes
  `get_http_status`, not `get_h_t_t_p_status`.
- Long signatures are wrapped to 100 columns. The generated project is checked
  against its own ruff configuration by this package's test suite, so what you get
  is lint-clean on the first run.

### Changed
- New optional extra `create-mcp[yaml]`, needed only to read YAML OpenAPI
  documents. JSON specs work on the standard library alone, so the default
  install stays dependency-light.

## [0.1.2] — 2026-06-20

### Fixed
- Corrected the package author email in project metadata to `shaxzodbek@blaze.uz`.

## [0.1.1] — 2026-06-20

### Fixed
- Corrected repository URLs (PyPI project metadata, README CI badge, the
  `CONTRIBUTING` clone command, and the generated-project README template) to
  point at `github.com/shaxzodbek-uzb/create-mcp`.

## [0.1.0] — 2026-06-20

### Added
- Initial release: `create-mcp` scaffolder.
- Presets: `minimal`, `api-wrapper`, `db`, `agent-tools`.
- Transports: `streamable-http` (default) and `stdio`.
- `--auth oauth`: OAuth 2.1 resource server (RFC 9728 Protected Resource
  Metadata + `401`/`WWW-Authenticate` discovery + JWT validation) via FastMCP's
  `RemoteAuthProvider`.
- Generated projects ship tests (in-memory FastMCP client), GitHub Actions CI,
  a uv-based Dockerfile, ruff + mypy + pre-commit, `.env.example`, and a README.
- `uvx create-mcp` zero-install usage; interactive prompts and a `--yes` path.
