"""CLI tests via Typer's CliRunner."""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from create_mcp import __version__
from create_mcp.cli import create

runner = CliRunner()


def _app() -> typer.Typer:
    app = typer.Typer()
    app.command()(create)
    return app


def test_version() -> None:
    result = runner.invoke(_app(), ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_generate_via_cli(tmp_path: object) -> None:
    result = runner.invoke(
        _app(),
        [
            "acme-mcp",
            "--preset",
            "minimal",
            "--yes",
            "--no-git",
            "--no-install",
            "--no-precommit",
            "-o",
            str(tmp_path),  # type: ignore[arg-type]
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "acme-mcp" / "pyproject.toml").is_file()  # type: ignore[operator]


def test_requires_name_with_yes() -> None:
    result = runner.invoke(_app(), ["--yes"])
    assert result.exit_code != 0
