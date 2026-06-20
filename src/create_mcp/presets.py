"""Preset definitions for the kind of MCP server to scaffold.

Each preset maps to a directory under ``templates/presets/<key>/`` that is
overlaid on top of ``templates/base/`` to provide a purpose-built starting set
of tools/resources/prompts. The default project layout is identical across
presets — only the example ``tools.py`` (and its declared extra dependencies)
differ.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preset:
    key: str
    title: str
    description: str
    # Extra runtime dependencies the generated project needs for this preset.
    extra_dependencies: tuple[str, ...] = field(default_factory=tuple)


PRESETS: dict[str, Preset] = {
    "minimal": Preset(
        key="minimal",
        title="Minimal",
        description="A clean, typed server with one example tool, resource and prompt.",
    ),
    "api-wrapper": Preset(
        key="api-wrapper",
        title="API wrapper",
        description="Wrap an existing HTTP/JSON API as MCP tools, with typed models.",
        extra_dependencies=("httpx>=0.27",),
    ),
    "db": Preset(
        key="db",
        title="Database",
        description="Expose a SQLite-backed store as typed MCP tools (CRUD example).",
    ),
    "agent-tools": Preset(
        key="agent-tools",
        title="Agent tools",
        description="A toolbox for autonomous agents: calculator, scratchpad memory, clock.",
    ),
}

DEFAULT_PRESET = "minimal"


def preset_dirname(key: str) -> str:
    """Template directory name for a preset key (hyphens -> underscores)."""
    return key.replace("-", "_")


def preset_choices() -> list[str]:
    return list(PRESETS)
