"""The scaffolding engine: turn a :class:`ProjectConfig` into a project on disk.

Templates live under ``templates/{base,presets/<preset>,auth}`` and are rendered
with Jinja using *non-default* delimiters (``[[ ]]`` / ``[% %]``) so the template
files can freely contain ``{{ }}`` and GitHub Actions ``${{ }}`` expressions
verbatim. Path segments are de-tokenised:

* ``__pkg__``   -> the Python package name
* ``dot-foo``   -> ``.foo``           (ship dotfiles without leading dots)
* trailing ``.jinja`` is stripped from the output filename
"""

from __future__ import annotations

import datetime
import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path

import jinja2

from . import FASTMCP_TARGET, __version__
from . import templates as _templates
from .presets import PRESETS, preset_dirname

TEMPLATES_DIR = Path(_templates.__file__).parent


class GeneratorError(Exception):
    """Raised when a project cannot be generated (bad name, target exists, ...)."""


@dataclass
class ProjectConfig:
    """Everything needed to render a project."""

    project_name: str
    preset: str = "minimal"
    transport: str = "streamable-http"
    auth: str = "none"  # "none" | "oauth"
    description: str = ""
    author: str = ""
    python_version: str = "3.11"

    # Derived / filled in __post_init__.
    package_name: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.package_name = to_package_name(self.project_name)
        if self.preset not in PRESETS:
            raise GeneratorError(
                f"Unknown preset {self.preset!r}. Choose from: {', '.join(PRESETS)}"
            )
        if self.transport not in ("streamable-http", "stdio"):
            raise GeneratorError(f"Unknown transport {self.transport!r}.")
        if self.auth not in ("none", "oauth"):
            raise GeneratorError(f"Unknown auth mode {self.auth!r}.")
        if not self.description:
            self.description = "A Model Context Protocol server, scaffolded with create-mcp."

    @property
    def auth_enabled(self) -> bool:
        return self.auth == "oauth"

    @property
    def http(self) -> bool:
        return self.transport == "streamable-http"

    @property
    def context(self) -> dict[str, object]:
        """The variables exposed to templates."""
        return {
            "project_name": self.project_name,
            "package_name": self.package_name,
            "preset": self.preset,
            "transport": self.transport,
            "http": self.http,
            "auth": self.auth,
            "auth_enabled": self.auth_enabled,
            "description": self.description,
            "author": self.author or "your name",
            "python_version": self.python_version,
            "fastmcp_target": FASTMCP_TARGET,
            "create_mcp_version": __version__,
            "year": datetime.date.today().year,
            "extra_dependencies": list(PRESETS[self.preset].extra_dependencies),
        }


def to_package_name(project_name: str) -> str:
    """Turn an arbitrary project name into a valid, importable package name."""
    slug = project_name.strip().lower()
    slug = re.sub(r"[^0-9a-z]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        raise GeneratorError(f"Cannot derive a package name from {project_name!r}.")
    if slug[0].isdigit():
        slug = f"_{slug}"
    if keyword.iskeyword(slug):
        slug = f"{slug}_"
    return slug


def _layers(config: ProjectConfig) -> list[Path]:
    """The template directories to overlay, in order (later wins)."""
    layers = [TEMPLATES_DIR / "base", TEMPLATES_DIR / "presets" / preset_dirname(config.preset)]
    if config.auth_enabled:
        layers.append(TEMPLATES_DIR / "auth")
    return layers


def _detokenise(rel: Path, package_name: str) -> Path:
    parts: list[str] = []
    for seg in rel.parts:
        if seg == "__pkg__":
            seg = package_name
        elif seg.startswith("dot-"):
            seg = "." + seg[len("dot-") :]
        if seg.endswith(".jinja"):
            seg = seg[: -len(".jinja")]
        parts.append(seg)
    return Path(*parts)


def _make_env(searchpath: Path) -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(searchpath)),
        variable_start_string="[[",
        variable_end_string="]]",
        block_start_string="[%",
        block_end_string="%]",
        comment_start_string="[#",
        comment_end_string="#]",
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )


def render_to_mapping(config: ProjectConfig) -> dict[str, str]:
    """Render every template to an in-memory ``{relative_path: content}`` mapping.

    Kept separate from disk I/O so the generator is trivially unit-testable.
    """
    context = config.context
    files: dict[str, str] = {}
    for layer in _layers(config):
        if not layer.is_dir():
            raise GeneratorError(f"Missing template layer: {layer}")
        env = _make_env(layer)
        for path in sorted(layer.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(layer)
            template = env.get_template(rel.as_posix())
            rendered = template.render(**context)
            out_rel = _detokenise(rel, config.package_name).as_posix()
            files[out_rel] = rendered
    return files


def generate(config: ProjectConfig, target_dir: Path, *, force: bool = False) -> Path:
    """Write the rendered project to ``target_dir`` and return the project root."""
    target_dir = target_dir.resolve()
    if target_dir.exists() and any(target_dir.iterdir()) and not force:
        raise GeneratorError(
            f"Target directory {target_dir} already exists and is not empty. "
            "Use --force to overwrite."
        )
    files = render_to_mapping(config)
    for rel, content in files.items():
        dest = target_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    return target_dir
