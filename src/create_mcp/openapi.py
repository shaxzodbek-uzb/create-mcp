"""Read an OpenAPI document and reduce it to what a generator needs.

The output is a small, JSON-serialisable intermediate representation
(:class:`ApiSpec` / :class:`Operation` / :class:`Param`) that the ``openapi``
preset template walks to emit one typed MCP tool per operation. Nothing here
knows about Jinja, and nothing here touches the network except
:func:`load_document`, so parsing is trivially unit-testable.

Deliberately *not* a full OpenAPI implementation: local ``$ref`` resolution,
scalar/array/enum types, and JSON request bodies cover the operations that make
sense as MCP tools. Anything richer degrades to ``dict[str, Any]`` rather than
failing — a usable tool beats a perfect type.
"""

from __future__ import annotations

import json
import keyword
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_REF_DEPTH = 10
#: Beyond this many tools an MCP client's tool list becomes unusable, and most
#: models start mis-selecting. We still generate, but the CLI warns.
CROWDED_TOOL_COUNT = 40

_HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


class OpenAPIError(Exception):
    """Raised when a document can't be read or contains no usable operations."""


# -- naming ------------------------------------------------------------------


def to_snake(text: str) -> str:
    """``getPetById`` / ``get-pet-by-id`` / ``Get Pet`` -> ``get_pet_by_id``.

    Acronyms split before the word they precede, so ``HTTPServer`` becomes
    ``http_server`` rather than ``httpserver`` — operationIds in real specs are full
    of them (``getURLInfo``, ``listVPCs``).
    """
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^0-9a-zA-Z]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_").lower()


def to_identifier(text: str, *, fallback: str = "arg") -> str:
    """Coerce an arbitrary wire name (``X-Api-Key``, ``per_page``) to a Python name."""
    name = to_snake(text) or fallback
    if name[0].isdigit():
        name = f"_{name}"
    if keyword.iskeyword(name) or name in {"self", "mcp"}:
        name = f"{name}_"
    return name


def _unique(name: str, taken: set[str]) -> str:
    if name not in taken:
        taken.add(name)
        return name
    for n in range(2, 1000):
        candidate = f"{name}_{n}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise OpenAPIError(f"could not find a unique name for {name!r}")


# -- IR ----------------------------------------------------------------------


def _safe_doc(text: str) -> str:
    """Make spec prose safe to paste inside a ``\"\"\"`` docstring."""
    text = " ".join(text.split())
    text = text.replace("\\", "\\\\").replace('"""', "'''")
    # A docstring may not end on a quote or a backslash — it would escape the closer.
    return text + " " if text.endswith(('"', "\\")) else text


@dataclass(frozen=True)
class Param:
    """One argument of a generated tool."""

    name: str  # Python identifier
    wire_name: str  # name the API expects
    location: str  # "path" | "query" | "header" | "body"
    annotation: str  # rendered Python type, e.g. 'int | None'
    required: bool
    description: str = ""
    sample: str = "None"  # Python literal used in the generated test

    @property
    def signature(self) -> str:
        return f"{self.name}: {self.annotation}" + ("" if self.required else " = None")


@dataclass
class Operation:
    """One HTTP operation, as an MCP tool."""

    tool_name: str
    method: str
    path: str
    summary: str
    params: list[Param] = field(default_factory=list)
    #: True when the request body could not be flattened into named arguments.
    opaque_body: bool = False

    #: Matches the generated project's own `line-length = 100` ruff setting, which its
    #: CI enforces — a generator that emits code failing its own lint is a bad gift.
    LINE_LENGTH = 100

    @property
    def ordered_params(self) -> list[Param]:
        """Required arguments first — Python forbids a non-default after a default."""
        return sorted(self.params, key=lambda p: not p.required)

    @property
    def signature(self) -> str:
        return ", ".join(p.signature for p in self.ordered_params)

    @property
    def signature_rendered(self) -> str:
        """The argument list as it goes between the parentheses, wrapped if long.

        Returned with the surrounding newlines and indentation baked in, so the
        template stays a single ``async def name(<here>) -> Any:`` line either way.
        """
        inline = self.signature
        # 4 indent + "async def " + name + "(" + args + ") -> Any:"
        if 4 + 10 + len(self.tool_name) + 1 + len(inline) + 9 <= self.LINE_LENGTH:
            return inline
        body = "".join(f"\n        {p.signature}," for p in self.ordered_params)
        return f"{body}\n    "

    def by_location(self, location: str) -> list[Param]:
        return [p for p in self.params if p.location == location]

    @property
    def sample_arguments(self) -> str:
        """A ``{"arg": value}`` literal covering the required params, for the test."""
        required = [p for p in self.params if p.required]
        if not required:
            return "{}"
        return "{" + ", ".join(f'"{p.name}": {p.sample}' for p in required) + "}"

    @property
    def doc_lines(self) -> list[str]:
        """Docstring body: the summary, then an ``Args:`` block when we have one.

        The docstring is what an MCP client shows the model when it picks a tool, so
        parameter descriptions from the spec are worth carrying through.
        """
        lines = [_safe_doc(self.summary)]
        described = [p for p in self.params if p.description]
        if described:
            lines.extend(["", "Args:"])
            lines.extend(f"    {p.name}: {_safe_doc(p.description)}" for p in described)
        return lines


@dataclass
class ApiSpec:
    title: str
    version: str
    base_url: str
    operations: list[Operation]
    #: "bearer", "api_key", or "" when the document declares no security.
    auth_kind: str = ""
    api_key_name: str = ""
    api_key_location: str = "header"
    #: Operations dropped because their tag didn't match the requested filter.
    filtered_out: int = 0

    @property
    def uses_literal(self) -> bool:
        """Whether any annotation needs ``typing.Literal`` imported.

        The generated project is ruff-clean out of the box, so an unused import
        would fail its own CI on the first run.
        """
        return any(
            "Literal[" in p.annotation for operation in self.operations for p in operation.params
        )


# -- loading -----------------------------------------------------------------


def load_document(source: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """Read an OpenAPI document from a file path or an ``http(s)`` URL.

    JSON is parsed with the stdlib. YAML needs PyYAML, which is an optional extra
    — the error says so rather than dying on a JSON syntax error 400 lines in.
    """
    if source.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(source, timeout=timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
            raise OpenAPIError(f"could not fetch {source}: {exc}") from exc
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise OpenAPIError(f"no such file: {source}")
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise OpenAPIError(f"could not read {source}: {exc}") from exc

    return parse_document(raw, source=source)


def parse_document(raw: str, *, source: str = "<string>") -> dict[str, Any]:
    """Parse JSON, falling back to YAML when PyYAML is available."""
    stripped = raw.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAPIError(f"{source} is not valid JSON: {exc}") from exc
    else:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise OpenAPIError(
                f"{source} looks like YAML. Install the extra to read YAML specs:\n"
                "    uvx 'create-mcp[yaml]' ...    (or: pip install pyyaml)\n"
                "Alternatively, point --from-openapi at a .json document."
            ) from exc
        try:
            document = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise OpenAPIError(f"{source} is not valid YAML: {exc}") from exc

    if not isinstance(document, dict):
        raise OpenAPIError(f"{source} is not an OpenAPI document (expected a mapping at the top)")
    return document


# -- schema -> Python types --------------------------------------------------


def _resolve(schema: Any, document: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Follow local ``$ref`` pointers. Remote refs and cycles degrade to ``{}``."""
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return schema
    if not ref.startswith("#/") or depth >= MAX_REF_DEPTH:
        return {}
    target: Any = document
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or part not in target:
            return {}
        target = target[part]
    return _resolve(target, document, depth + 1)


def _literal(values: list[Any]) -> str | None:
    """Render a string/int enum as ``Literal[...]``; anything mixed returns None."""
    if not values or len(values) > 20:
        return None
    if all(isinstance(v, str) for v in values):
        return "Literal[" + ", ".join(json.dumps(v) for v in values) + "]"
    if all(isinstance(v, bool) for v in values):
        return None
    if all(isinstance(v, int) for v in values):
        return "Literal[" + ", ".join(str(v) for v in values) + "]"
    return None


def python_type(schema: Any, document: dict[str, Any], depth: int = 0) -> str:
    """Render an OpenAPI schema as a Python annotation."""
    schema = _resolve(schema, document, depth)
    if not schema or depth >= MAX_REF_DEPTH:
        return "Any"

    for combinator in ("anyOf", "oneOf"):
        options = schema.get(combinator)
        if isinstance(options, list) and options:
            branches = {python_type(opt, document, depth + 1) for opt in options}
            branches.discard("Any")
            return " | ".join(sorted(branches)) if branches else "Any"

    enum = schema.get("enum")
    if isinstance(enum, list):
        literal = _literal(enum)
        if literal:
            return literal

    declared = schema.get("type")
    if isinstance(declared, list):  # OpenAPI 3.1 allows a list of types
        declared = next((t for t in declared if t != "null"), None)

    if declared == "array":
        return f"list[{python_type(schema.get('items', {}), document, depth + 1)}]"
    if declared == "object" or "properties" in schema:
        return "dict[str, Any]"
    return {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
    }.get(declared, "Any")


def sample_value(annotation: str, name: str) -> str:
    """A Python literal of the given type, for the generated smoke test."""
    if annotation.startswith("Literal["):
        return annotation[len("Literal[") : -1].split(",")[0].strip()
    base = annotation.split(" | ")[0]
    if base.startswith("list["):
        return "[]"
    return {
        "str": json.dumps(name),
        "int": "1",
        "float": "1.0",
        "bool": "True",
        "dict[str, Any]": "{}",
    }.get(base, "None")


# -- parsing -----------------------------------------------------------------


def _base_url(document: dict[str, Any]) -> str:
    servers = document.get("servers")
    if isinstance(servers, list):
        for server in servers:
            url = server.get("url") if isinstance(server, dict) else None
            if isinstance(url, str) and url.strip():
                # A relative server URL ("/api/v3") is legal but useless standalone;
                # the generated project reads API_BASE_URL from the environment anyway.
                return url.strip().rstrip("/")
    return ""


def _security(document: dict[str, Any]) -> tuple[str, str, str]:
    """Return ``(auth_kind, api_key_name, api_key_location)`` from the first scheme."""
    schemes = (document.get("components") or {}).get("securitySchemes")
    if not isinstance(schemes, dict):
        return "", "", "header"
    for scheme in schemes.values():
        if not isinstance(scheme, dict):
            continue
        kind = str(scheme.get("type", "")).lower()
        if kind == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
            return "bearer", "", "header"
        if kind == "oauth2":
            return "bearer", "", "header"
        if kind == "apikey":
            name = scheme.get("name")
            if isinstance(name, str) and name:
                location = str(scheme.get("in", "header")).lower()
                return "api_key", name, location if location in ("header", "query") else "header"
    return "", "", "header"


def _collect_params(
    raw_params: list[Any],
    document: dict[str, Any],
    taken: set[str],
) -> list[Param]:
    params: list[Param] = []
    for entry in raw_params:
        entry = _resolve(entry, document)
        wire_name = entry.get("name")
        location = entry.get("in")
        if not isinstance(wire_name, str) or location not in ("path", "query", "header"):
            continue  # cookie params and malformed entries are not worth a tool argument
        annotation = python_type(entry.get("schema", {}), document)
        required = bool(entry.get("required")) or location == "path"
        name = _unique(to_identifier(wire_name), taken)
        params.append(
            Param(
                name=name,
                wire_name=wire_name,
                location=location,
                annotation=annotation if required else f"{annotation} | None",
                required=required,
                description=str(entry.get("description") or "").strip(),
                sample=sample_value(annotation, wire_name),
            )
        )
    return params


def _body_params(
    body: Any,
    document: dict[str, Any],
    taken: set[str],
) -> tuple[list[Param], bool]:
    """Flatten a JSON request body into named arguments.

    Named arguments make a far better tool than one opaque ``dict`` — the model
    can see what the endpoint wants. When the body isn't a flat object (an array
    payload, a ``$ref`` we can't resolve), fall back to a single ``body`` dict.
    """
    body = _resolve(body, document)
    content = body.get("content")
    if not isinstance(content, dict):
        return [], False
    media = next(
        (v for k, v in content.items() if isinstance(k, str) and "json" in k.lower()),
        None,
    )
    if not isinstance(media, dict):
        return [], False

    schema = _resolve(media.get("schema", {}), document)
    properties = schema.get("properties")
    body_required = bool(body.get("required"))
    if not isinstance(properties, dict) or not properties:
        name = _unique("body", taken)
        annotation = python_type(schema, document)
        if annotation == "Any":
            annotation = "dict[str, Any]"
        return (
            [
                Param(
                    name=name,
                    wire_name=name,
                    location="body",
                    annotation=annotation if body_required else f"{annotation} | None",
                    required=body_required,
                    description="Request body.",
                    sample=sample_value(annotation, "body"),
                )
            ],
            True,
        )

    required_fields = schema.get("required")
    required_fields = set(required_fields) if isinstance(required_fields, list) else set()
    params: list[Param] = []
    for wire_name, prop in properties.items():
        if not isinstance(wire_name, str):
            continue
        annotation = python_type(prop, document)
        required = body_required and wire_name in required_fields
        name = _unique(to_identifier(wire_name), taken)
        prop_resolved = _resolve(prop, document)
        params.append(
            Param(
                name=name,
                wire_name=wire_name,
                location="body",
                annotation=annotation if required else f"{annotation} | None",
                required=required,
                description=str(prop_resolved.get("description") or "").strip(),
                sample=sample_value(annotation, wire_name),
            )
        )
    return params, False


def _summary(operation: dict[str, Any], method: str, path: str) -> str:
    for key in ("summary", "description"):
        value = operation.get(key)
        if isinstance(value, str) and value.strip():
            # Docstrings are one line; a multi-paragraph description would break out.
            first = value.strip().splitlines()[0].strip()
            if first:
                return first
    return f"{method.upper()} {path}"


def parse_spec(document: dict[str, Any], *, tags: list[str] | None = None) -> ApiSpec:
    """Reduce an OpenAPI document to the operations worth exposing as MCP tools."""
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise OpenAPIError("document has no `paths` — is it an OpenAPI specification?")

    wanted = {t.lower() for t in tags} if tags else None
    info = document.get("info")
    info = info if isinstance(info, dict) else {}

    operations: list[Operation] = []
    tool_names: set[str] = set()
    filtered_out = 0

    for path, item in paths.items():
        if not isinstance(path, str) or not isinstance(item, dict):
            continue
        shared = item.get("parameters")
        shared = shared if isinstance(shared, list) else []
        for method in _HTTP_METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            if operation.get("deprecated") is True:
                continue

            if wanted is not None:
                op_tags = operation.get("tags")
                op_tags = op_tags if isinstance(op_tags, list) else []
                if not any(str(t).lower() in wanted for t in op_tags):
                    filtered_out += 1
                    continue

            operation_id = operation.get("operationId")
            base_name = (
                to_snake(operation_id)
                if isinstance(operation_id, str) and operation_id.strip()
                else to_snake(f"{method}_{path}")
            )
            tool_name = _unique(to_identifier(base_name, fallback="operation"), tool_names)

            taken: set[str] = set()
            own = operation.get("parameters")
            own = own if isinstance(own, list) else []
            params = _collect_params([*shared, *own], document, taken)
            body, opaque = _body_params(operation.get("requestBody"), document, taken)

            operations.append(
                Operation(
                    tool_name=tool_name,
                    method=method.upper(),
                    path=path,
                    summary=_summary(operation, method, path),
                    params=[*params, *body],
                    opaque_body=opaque,
                )
            )

    if not operations:
        if filtered_out:
            raise OpenAPIError(
                f"no operations matched the requested tag(s); {filtered_out} were filtered out"
            )
        raise OpenAPIError("document declares no usable operations")

    auth_kind, api_key_name, api_key_location = _security(document)
    return ApiSpec(
        title=str(info.get("title") or "API").strip() or "API",
        version=str(info.get("version") or "").strip(),
        base_url=_base_url(document),
        operations=operations,
        auth_kind=auth_kind,
        api_key_name=api_key_name,
        api_key_location=api_key_location,
        filtered_out=filtered_out,
    )


def load_spec(source: str, *, tags: list[str] | None = None) -> ApiSpec:
    """Load and parse in one step — the path the CLI takes."""
    return parse_spec(load_document(source), tags=tags)


__all__ = [
    "ApiSpec",
    "Operation",
    "OpenAPIError",
    "Param",
    "CROWDED_TOOL_COUNT",
    "load_document",
    "load_spec",
    "parse_document",
    "parse_spec",
    "python_type",
    "to_identifier",
    "to_snake",
]
