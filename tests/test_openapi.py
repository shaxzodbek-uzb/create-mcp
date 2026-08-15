"""Tests for OpenAPI parsing and the code it generates.

The load-bearing assertion is that the rendered ``tools.py`` compiles: a generator
that emits syntactically invalid Python fails at the worst possible moment — in
someone else's terminal, on their first command.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from create_mcp.generator import ProjectConfig, render_to_mapping
from create_mcp.openapi import (
    OpenAPIError,
    load_document,
    parse_document,
    parse_spec,
    python_type,
    to_identifier,
    to_snake,
)

SPEC: dict = {
    "openapi": "3.0.3",
    "info": {"title": "Pet Store", "version": "1.2.3"},
    "servers": [{"url": "https://api.example.com/v3/"}],
    "components": {
        "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        "schemas": {
            "Pet": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "The pet's name."},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string", "enum": ["available", "sold"]},
                    "age": {"type": "integer"},
                },
            }
        },
    },
    "paths": {
        "/pet/{petId}": {
            "parameters": [
                {"name": "petId", "in": "path", "schema": {"type": "integer"}},
            ],
            "get": {
                "operationId": "getPetById",
                "summary": "Find pet by ID.",
                "tags": ["pet"],
                "parameters": [
                    {
                        "name": "X-Trace-Id",
                        "in": "header",
                        "schema": {"type": "string"},
                        "description": "Correlation id.",
                    }
                ],
            },
            "delete": {"tags": ["pet"], "summary": "Remove a pet."},
            "patch": {"operationId": "deprecatedThing", "deprecated": True, "tags": ["pet"]},
        },
        "/pet": {
            "post": {
                "operationId": "addPet",
                "summary": "Add a new pet.",
                "tags": ["pet"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}
                    },
                },
            }
        },
        "/pet/findByStatus": {
            "get": {
                "operationId": "findPetsByStatus",
                "summary": 'Multi-line.\nSecond line with a "quote".',
                "tags": ["pet"],
                "parameters": [
                    {
                        "name": "status",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string", "enum": ["available", "sold"]},
                    },
                    {"name": "per-page", "in": "query", "schema": {"type": "integer"}},
                ],
            }
        },
        "/store/bulk": {
            "post": {
                "operationId": "bulkUpload",
                "tags": ["store"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"type": "array", "items": {"type": "string"}}
                        }
                    },
                },
            }
        },
    },
}


@pytest.fixture
def spec():
    return parse_spec(json.loads(json.dumps(SPEC)))


def _render_tools(spec_obj) -> str:
    files = render_to_mapping(ProjectConfig(project_name="pet-mcp", preset="openapi", api=spec_obj))
    return files["src/pet_mcp/tools.py"]


# -- naming ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("getPetById", "get_pet_by_id"),
        ("get-pet-by-id", "get_pet_by_id"),
        ("Find Pet", "find_pet"),
        ("HTTPServer", "http_server"),
        ("get_/pet/{petId}", "get_pet_pet_id"),
    ],
)
def test_to_snake(raw: str, expected: str) -> None:
    assert to_snake(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("per-page", "per_page"), ("X-Api-Key", "x_api_key"), ("class", "class_"), ("2fa", "_2fa")],
)
def test_to_identifier(raw: str, expected: str) -> None:
    assert to_identifier(raw) == expected


# -- type mapping ------------------------------------------------------------


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string"}, "str"),
        ({"type": "integer"}, "int"),
        ({"type": "number"}, "float"),
        ({"type": "boolean"}, "bool"),
        ({"type": "array", "items": {"type": "string"}}, "list[str]"),
        ({"type": "object"}, "dict[str, Any]"),
        ({"type": "string", "enum": ["a", "b"]}, 'Literal["a", "b"]'),
        ({"type": ["string", "null"]}, "str"),
        ({}, "Any"),
        ({"$ref": "#/nope/missing"}, "Any"),
        ({"$ref": "https://elsewhere/schema"}, "Any"),
    ],
)
def test_python_type(schema: dict, expected: str) -> None:
    assert python_type(schema, SPEC) == expected


def test_python_type_resolves_local_refs() -> None:
    assert python_type({"$ref": "#/components/schemas/Pet"}, SPEC) == "dict[str, Any]"


def test_cyclic_ref_terminates() -> None:
    document = {"components": {"schemas": {"Node": {"$ref": "#/components/schemas/Node"}}}}
    assert python_type({"$ref": "#/components/schemas/Node"}, document) == "Any"


# -- parsing -----------------------------------------------------------------


def test_parses_metadata(spec) -> None:
    assert spec.title == "Pet Store"
    assert spec.version == "1.2.3"
    assert spec.base_url == "https://api.example.com/v3"  # trailing slash stripped
    assert spec.auth_kind == "bearer"


def test_skips_deprecated_operations(spec) -> None:
    assert "deprecated_thing" not in {op.tool_name for op in spec.operations}


def test_names_operations_without_an_operation_id(spec) -> None:
    """DELETE /pet/{petId} has no operationId, so the name comes from method + path."""
    assert "delete_pet_pet_id" in {op.tool_name for op in spec.operations}


def test_path_parameters_are_always_required(spec) -> None:
    op = next(o for o in spec.operations if o.tool_name == "get_pet_by_id")
    pet_id = next(p for p in op.params if p.wire_name == "petId")
    assert pet_id.required is True
    assert pet_id.annotation == "int"
    assert pet_id.location == "path"


def test_shared_path_item_parameters_are_inherited(spec) -> None:
    """`petId` is declared on the path item, not the operation."""
    op = next(o for o in spec.operations if o.tool_name == "delete_pet_pet_id")
    assert [p.wire_name for p in op.params] == ["petId"]


def test_optional_parameters_become_nullable_with_a_default(spec) -> None:
    op = next(o for o in spec.operations if o.tool_name == "find_pets_by_status")
    per_page = next(p for p in op.params if p.wire_name == "per-page")
    assert per_page.name == "per_page"  # sanitised for Python
    assert per_page.annotation == "int | None"
    assert per_page.signature == "per_page: int | None = None"


def test_enum_parameters_become_literals(spec) -> None:
    op = next(o for o in spec.operations if o.tool_name == "find_pets_by_status")
    status = next(p for p in op.params if p.wire_name == "status")
    assert status.annotation == 'Literal["available", "sold"]'


def test_request_body_is_flattened_into_named_arguments(spec) -> None:
    op = next(o for o in spec.operations if o.tool_name == "add_pet")
    body = {p.wire_name: p for p in op.by_location("body")}
    assert set(body) == {"name", "tags", "status", "age"}
    assert body["name"].required is True  # in the schema's `required` list
    assert body["tags"].annotation == "list[str] | None"
    assert op.opaque_body is False


def test_non_object_request_body_falls_back_to_one_argument(spec) -> None:
    op = next(o for o in spec.operations if o.tool_name == "bulk_upload")
    assert op.opaque_body is True
    assert [(p.name, p.annotation) for p in op.by_location("body")] == [("body", "list[str]")]


def test_every_signature_is_valid_python(spec) -> None:
    """Python rejects a non-default argument after a defaulted one — so must we."""
    for op in spec.operations:
        ast.parse(f"async def {op.tool_name}({op.signature}) -> None: ...")


def test_required_arguments_sort_before_optional_ones(spec) -> None:
    op = next(o for o in spec.operations if o.tool_name == "add_pet")
    tree = ast.parse(f"def f({op.signature}): ...").body[0]
    names = [a.arg for a in tree.args.args]  # type: ignore[attr-defined]
    assert names[0] == "name"  # the only required body field
    assert len(tree.args.defaults) == len(names) - 1  # type: ignore[attr-defined]


def test_docstring_is_single_line_and_quote_safe(spec) -> None:
    op = next(o for o in spec.operations if o.tool_name == "find_pets_by_status")
    assert op.doc_lines == ["Multi-line."]  # second line and its quote are dropped


def test_docstring_carries_parameter_descriptions(spec) -> None:
    op = next(o for o in spec.operations if o.tool_name == "get_pet_by_id")
    assert op.doc_lines[0] == "Find pet by ID."
    assert "Args:" in op.doc_lines
    assert "    x_trace_id: Correlation id." in op.doc_lines


def test_a_summary_ending_in_a_quote_cannot_break_the_docstring() -> None:
    document = {
        "info": {"title": "Q"},
        "paths": {"/x": {"get": {"operationId": "q", "summary": 'Ends with a "quote"'}}},
    }
    op = parse_spec(document).operations[0]
    rendered = _render_tools(parse_spec(document))
    assert op.doc_lines[0].endswith(" ")
    ast.parse(rendered)


def test_tag_filter_selects_and_reports(spec) -> None:
    filtered = parse_spec(SPEC, tags=["store"])
    assert [op.tool_name for op in filtered.operations] == ["bulk_upload"]
    assert filtered.filtered_out == 4


def test_tag_filter_matching_nothing_is_an_error() -> None:
    with pytest.raises(OpenAPIError, match="no operations matched"):
        parse_spec(SPEC, tags=["nonexistent"])


def test_api_key_security_is_detected() -> None:
    document = dict(SPEC)
    document["components"] = {
        "securitySchemes": {"key": {"type": "apiKey", "name": "X-Api-Key", "in": "header"}}
    }
    spec = parse_spec(document)
    assert (spec.auth_kind, spec.api_key_name, spec.api_key_location) == (
        "api_key",
        "X-Api-Key",
        "header",
    )


def test_document_without_paths_is_rejected() -> None:
    with pytest.raises(OpenAPIError, match="no `paths`"):
        parse_spec({"openapi": "3.0.0", "info": {}})


def test_document_with_no_usable_operations_is_rejected() -> None:
    with pytest.raises(OpenAPIError, match="no usable operations"):
        parse_spec({"paths": {"/x": {"summary": "not a method"}}})


# -- loading -----------------------------------------------------------------


def test_loads_json_from_a_file(tmp_path) -> None:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(SPEC), encoding="utf-8")
    assert load_document(str(path))["info"]["title"] == "Pet Store"


def test_missing_file_is_reported_clearly(tmp_path) -> None:
    with pytest.raises(OpenAPIError, match="no such file"):
        load_document(str(tmp_path / "absent.json"))


def test_malformed_json_is_reported_clearly() -> None:
    with pytest.raises(OpenAPIError, match="not valid JSON"):
        parse_document('{"paths": ')


def test_yaml_is_parsed_when_pyyaml_is_available() -> None:
    pytest.importorskip("yaml")
    document = parse_document(
        textwrap.dedent(
            """
            openapi: 3.0.0
            info:
              title: Tiny
            paths:
              /ping:
                get:
                  operationId: ping
            """
        )
    )
    assert parse_spec(document).operations[0].tool_name == "ping"


def test_top_level_non_mapping_is_rejected() -> None:
    with pytest.raises(OpenAPIError, match="not an OpenAPI document"):
        parse_document("[1, 2, 3]")


# -- generated code ----------------------------------------------------------


def test_generated_tools_module_is_valid_python(spec) -> None:
    ast.parse(_render_tools(spec))


def test_generated_test_module_is_valid_python(spec) -> None:
    files = render_to_mapping(ProjectConfig(project_name="pet-mcp", preset="openapi", api=spec))
    ast.parse(files["tests/test_tools.py"])


def test_every_operation_becomes_a_function(spec) -> None:
    tree = ast.parse(_render_tools(spec))
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }
    for op in spec.operations:
        assert op.tool_name in defined


def test_generated_module_imports_literal_only_when_used(spec) -> None:
    assert "Literal" in _render_tools(spec)  # this spec has an enum

    plain = parse_spec(
        {
            "info": {"title": "Plain"},
            "servers": [{"url": "https://x.test"}],
            "paths": {"/ping": {"get": {"operationId": "ping"}}},
        }
    )
    rendered = _render_tools(plain)
    assert "Literal" not in rendered
    ast.parse(rendered)


def test_generated_module_carries_the_base_url(spec) -> None:
    assert '"https://api.example.com/v3"' in _render_tools(spec)


def test_bearer_auth_emits_an_authorization_header(spec) -> None:
    assert 'request_headers["Authorization"] = f"Bearer {API_TOKEN}"' in _render_tools(spec)


def test_api_key_in_query_is_sent_as_a_query_parameter() -> None:
    document = dict(SPEC)
    document["components"] = {
        "securitySchemes": {"key": {"type": "apiKey", "name": "api_key", "in": "query"}}
    }
    rendered = _render_tools(parse_spec(document))
    assert 'request_query["api_key"] = API_KEY' in rendered
    ast.parse(rendered)


def test_generated_module_passes_the_generated_projects_own_lint(spec, tmp_path) -> None:
    """The scaffold promises `ruff` green on the first run — hold the generator to it.

    Uses the same rules and line length the generated pyproject.toml configures, so an
    over-long signature or an unused import fails here rather than in a user's CI.
    """
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff is not installed")

    files = render_to_mapping(ProjectConfig(project_name="pet-mcp", preset="openapi", api=spec))
    for rel in ("src/pet_mcp/tools.py", "tests/test_tools.py"):
        written = tmp_path / Path(rel).name
        written.write_text(files[rel], encoding="utf-8")

    result = subprocess.run(
        [
            ruff,
            "check",
            "--isolated",
            "--line-length",
            "100",
            "--select",
            "E,F,I,UP,B,SIM,C4",
            "--target-version",
            "py311",
            # In a real project ruff infers this from the src layout; --isolated can't.
            "--config",
            'lint.isort.known-first-party = ["pet_mcp"]',
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout


def test_long_signatures_are_wrapped(spec) -> None:
    op = next(o for o in spec.operations if o.tool_name == "add_pet")
    assert "\n" in op.signature_rendered
    for line in _render_tools(spec).splitlines():
        assert len(line) <= 100, line


def test_short_signatures_stay_on_one_line(spec) -> None:
    op = next(o for o in spec.operations if o.tool_name == "delete_pet_pet_id")
    assert op.signature_rendered == "pet_id: int"


def test_openapi_preset_without_a_spec_is_rejected() -> None:
    from create_mcp.generator import GeneratorError

    with pytest.raises(GeneratorError, match="--from-openapi"):
        ProjectConfig(project_name="x", preset="openapi")


def test_openapi_preset_is_hidden_from_the_interactive_menu() -> None:
    from create_mcp.presets import preset_choices

    assert "openapi" not in preset_choices()
