"""Unit tests for the generator (no network, no subprocess)."""

from __future__ import annotations

import pytest

from create_mcp.generator import (
    GeneratorError,
    ProjectConfig,
    generate,
    render_to_mapping,
    to_package_name,
)
from create_mcp.presets import PRESETS


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("my-server", "my_server"),
        ("My Cool Server", "my_cool_server"),
        ("pay.tools", "pay_tools"),
        ("123abc", "_123abc"),
        ("class", "class_"),
    ],
)
def test_to_package_name(raw: str, expected: str) -> None:
    assert to_package_name(raw) == expected


def test_to_package_name_rejects_empty() -> None:
    with pytest.raises(GeneratorError):
        to_package_name("---")


def test_minimal_mapping_has_core_files() -> None:
    files = render_to_mapping(ProjectConfig(project_name="acme-mcp"))
    assert "pyproject.toml" in files
    assert "README.md" in files
    assert ".gitignore" in files
    assert ".github/workflows/ci.yml" in files
    assert "src/acme_mcp/server.py" in files
    assert "src/acme_mcp/tools.py" in files
    assert "tests/test_server.py" in files
    assert "tests/test_tools.py" in files


def test_no_unrendered_tokens_anywhere() -> None:
    files = render_to_mapping(ProjectConfig(project_name="acme-mcp", preset="db", auth="oauth"))
    blob = "\n".join(files) + "\n" + "\n".join(files.values())
    for token in ("[[", "]]", "[%", "%]", "__pkg__", "dot-"):
        assert token not in blob, f"leaked template token: {token!r}"


def test_auth_files_only_present_with_oauth() -> None:
    without = render_to_mapping(ProjectConfig(project_name="x", auth="none"))
    assert "src/x/auth.py" not in without
    assert "tests/test_auth.py" not in without

    with_auth = render_to_mapping(ProjectConfig(project_name="x", auth="oauth"))
    assert "src/x/auth.py" in with_auth
    assert "tests/test_auth.py" in with_auth
    assert "pyjwt" in with_auth["pyproject.toml"]


@pytest.mark.parametrize("preset", list(PRESETS))
def test_every_preset_renders(preset: str) -> None:
    files = render_to_mapping(ProjectConfig(project_name="demo", preset=preset))
    assert "src/demo/tools.py" in files
    assert "def register(" in files["src/demo/tools.py"]


def test_stdio_transport_in_settings() -> None:
    files = render_to_mapping(ProjectConfig(project_name="x", transport="stdio"))
    assert 'transport: str = "stdio"' in files["src/x/settings.py"]


def test_generate_writes_to_disk(tmp_path: object) -> None:
    target = tmp_path / "out"  # type: ignore[operator]
    root = generate(ProjectConfig(project_name="acme-mcp"), target)
    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "acme_mcp" / "server.py").is_file()


def test_generate_refuses_nonempty_dir(tmp_path: object) -> None:
    target = tmp_path / "out"  # type: ignore[operator]
    target.mkdir()
    (target / "keep.txt").write_text("hi")
    with pytest.raises(GeneratorError):
        generate(ProjectConfig(project_name="x"), target)


def test_bad_preset_rejected() -> None:
    with pytest.raises(GeneratorError):
        ProjectConfig(project_name="x", preset="nope")
