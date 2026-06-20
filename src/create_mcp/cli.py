"""The ``create-mcp`` command-line interface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

from . import FASTMCP_TARGET, __version__
from .generator import GeneratorError, ProjectConfig, generate, to_package_name
from .presets import DEFAULT_PRESET, PRESETS, preset_choices

console = Console()
err_console = Console(stderr=True)

TRANSPORTS = ["streamable-http", "stdio"]
AUTH_MODES = ["none", "oauth"]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"create-mcp {__version__} [dim](targets FastMCP {FASTMCP_TARGET}.x)[/dim]")
        raise typer.Exit()


def _run(cmd: list[str], cwd: Path) -> bool:
    """Run a command, streaming nothing; return True on success, False otherwise."""
    try:
        subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _maybe_git_init(target: Path) -> None:
    if shutil.which("git") is None:
        console.print("  [yellow]›[/yellow] git not found — skipping repository init")
        return
    if _run(["git", "init", "-q"], target) and _run(["git", "add", "-A"], target):
        _run(["git", "commit", "-q", "-m", "Initial commit (create-mcp)"], target)
        console.print("  [green]✓[/green] initialised git repository")
    else:
        console.print("  [yellow]›[/yellow] could not initialise git repository")


def _maybe_uv_sync(target: Path) -> None:
    if shutil.which("uv") is None:
        console.print("  [yellow]›[/yellow] uv not found — skipping dependency install")
        return
    console.print("  [dim]…[/dim] installing dependencies with uv")
    if _run(["uv", "sync"], target):
        console.print("  [green]✓[/green] installed dependencies (uv sync)")
    else:
        console.print("  [yellow]›[/yellow] uv sync failed — run it yourself later")


def _maybe_precommit(target: Path) -> None:
    if shutil.which("git") is None or not (target / ".git").exists():
        return
    if shutil.which("uv") is not None and _run(["uv", "run", "pre-commit", "install"], target):
        console.print("  [green]✓[/green] installed pre-commit hooks")


def _prompt_choice(label: str, choices: list[str], default: str) -> str:
    return Prompt.ask(f"[bold]{label}[/bold]", choices=choices, default=default)


def create(  # noqa: C901 - the CLI orchestration is intentionally linear
    project_name: str = typer.Argument(None, help="Name of the project / directory to create."),
    preset: str = typer.Option(
        None, "--preset", "-p", help=f"Preset: {', '.join(preset_choices())}."
    ),
    transport: str = typer.Option(
        None, "--transport", "-t", help="Transport: streamable-http or stdio."
    ),
    auth: str = typer.Option(
        None, "--auth", "-a", help="Auth mode: none or oauth (OAuth 2.1 resource server)."
    ),
    package_name: str = typer.Option(
        None, "--package-name", help="Override the derived Python package name."
    ),
    description: str = typer.Option(None, "--description", help="One-line project description."),
    output_dir: Path = typer.Option(
        None, "--output-dir", "-o", help="Directory to create the project in (default: cwd)."
    ),
    do_git: bool = typer.Option(True, "--git/--no-git", help="Initialise a git repository."),
    do_install: bool = typer.Option(
        True, "--install/--no-install", help="Run `uv sync` after scaffolding."
    ),
    do_precommit: bool = typer.Option(
        True, "--precommit/--no-precommit", help="Install pre-commit hooks."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite a non-empty target directory."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Accept all defaults; never prompt (CI / scripting)."
    ),
    _version: bool = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    """Scaffold a production-ready Python MCP server."""
    interactive = not yes
    output_dir = output_dir or Path.cwd()

    def choose(label: str, choices: list[str], default: str, current: str | None) -> str:
        if current is not None:
            return current
        return _prompt_choice(label, choices, default) if interactive else default

    if not project_name:
        if interactive:
            project_name = Prompt.ask("[bold]Project name[/bold]", default="my-mcp-server")
        else:
            err_console.print("[red]error:[/red] project name is required with --yes")
            raise typer.Exit(code=2)

    preset = choose("Preset", preset_choices(), DEFAULT_PRESET, preset)
    transport = choose("Transport", TRANSPORTS, "streamable-http", transport)
    auth = choose("Auth", AUTH_MODES, "none", auth)

    if interactive:
        do_git = Confirm.ask("Initialise a git repository?", default=do_git)
        do_install = Confirm.ask("Install dependencies now (uv sync)?", default=do_install)
        if do_git:
            do_precommit = Confirm.ask("Install pre-commit hooks?", default=do_precommit)

    try:
        config = ProjectConfig(
            project_name=project_name,
            preset=preset,
            transport=transport,
            auth=auth,
            description=description or "",
        )
        if package_name:
            # Validate + override the derived package name.
            config.package_name = to_package_name(package_name)
        target = output_dir / project_name
        generate(config, target, force=force)
    except GeneratorError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print()
    console.print(
        f"[green]✓[/green] Scaffolded [bold]{project_name}[/bold] "
        f"[dim]({PRESETS[preset].title} · {transport} · auth: {auth})[/dim]"
    )

    if do_git:
        _maybe_git_init(target)
    if do_install:
        _maybe_uv_sync(target)
    if do_precommit:
        _maybe_precommit(target)

    _print_next_steps(config, target, output_dir)


def _print_next_steps(config: ProjectConfig, target: Path, output_dir: Path) -> None:
    rel = target.relative_to(output_dir) if target.is_relative_to(output_dir) else target
    pkg = config.package_name
    run_cmd = f"uv run {pkg}"
    body = Text()
    body.append("Next steps\n\n", style="bold")
    body.append(f"  cd {rel}\n")
    body.append("  uv sync                  ", style="cyan")
    body.append("# install dependencies\n", style="dim")
    body.append(f"  {run_cmd}{' ' * max(1, 17 - len(run_cmd))}", style="cyan")
    body.append(f"# run the server ({config.transport})\n", style="dim")
    body.append("  uv run pytest            ", style="cyan")
    body.append("# run the test suite\n\n", style="dim")
    body.append("Inspect it with the MCP Inspector:\n")
    body.append(f"  npx @modelcontextprotocol/inspector uv run {pkg}\n", style="cyan")
    if config.auth_enabled:
        body.append("\nAuth is on. ", style="bold yellow")
        body.append("Set OAUTH_* vars in .env (see .env.example) and point them at\n")
        body.append("your identity provider (Keycloak / WorkOS / Auth0 / Azure).")
    console.print(Panel(body, border_style="green", expand=False))


def main() -> None:
    typer.run(create)


if __name__ == "__main__":
    main()
