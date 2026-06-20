# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
