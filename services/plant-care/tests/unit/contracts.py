"""Builds the OpenAPI contract from the running app and checks responses against it."""

import copy
from pathlib import Path
from urllib.parse import quote

import yaml
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import Response
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACTS_DIR = Path(__file__).parents[4] / "contracts"
PROBLEM_JSON = "application/problem+json"
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch"})
DOCUMENT_URI = "urn:growpath:openapi"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_SCHEMA = {
    "type": "object",
    "title": "Problem",
    "description": "Problem details (RFC 7807); members such as `code` or `errors` may follow.",
    "required": ["type", "title", "status"],
    "properties": {
        "type": {"type": "string"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "correlation_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "code": {"type": "string"},
        "errors": {"type": "array", "items": {"type": "object"}},
    },
}
VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")


def openapi_path(service: str) -> Path:
    return CONTRACTS_DIR / "openapi" / f"{service}.yaml"


def build_openapi(app: FastAPI, description: str, public_prefixes: tuple[str, ...]) -> dict:
    """The generated document plus gateway exposure and the problem responses FastAPI cannot see."""
    document = copy.deepcopy(app.openapi())
    document["info"]["description"] = description
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    for name in VALIDATION_SCHEMAS:
        schemas.pop(name, None)
    schemas["Problem"] = PROBLEM_SCHEMA
    problem = {
        "description": "Problem details",
        "content": {PROBLEM_JSON: {"schema": {"$ref": PROBLEM_REF}}},
    }
    for path, item in document["paths"].items():
        exposure = "public" if _is_public(path, public_prefixes) else "internal"
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            if exposure == "internal":
                operation.setdefault("tags", []).append("internal")
            operation["x-gateway"] = exposure
            responses = operation["responses"]
            if "422" in responses:
                responses["422"] = copy.deepcopy(problem)
            responses["default"] = copy.deepcopy(problem)
    return document


def render_yaml(document: dict) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)


def load_openapi(service: str) -> dict:
    return yaml.safe_load(openapi_path(service).read_text(encoding="utf-8"))


def operations_of(document: dict) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, item in document["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


def routes_of(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method, context.path)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute) and context.original_route.include_in_schema
        for method in context.methods
    }


def assert_conforms(document: dict, path: str, method: str, response: Response) -> None:
    """The body must satisfy the response schema declared for its status (or `default`)."""
    responses = document["paths"][path][method.lower()]["responses"]
    declared = str(response.status_code) if str(response.status_code) in responses else "default"
    media_type = response.headers["content-type"].split(";")[0]
    pointer = [
        "paths",
        path,
        method.lower(),
        "responses",
        declared,
        "content",
        media_type,
        "schema",
    ]
    validator = _validator(document, pointer)
    errors = sorted(validator.iter_errors(response.json()), key=lambda error: list(error.path))
    assert not errors, [f"{list(error.path)}: {error.message}" for error in errors]


def _is_public(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def _validator(document: dict, pointer: list[str]) -> Draft202012Validator:
    registry = Registry().with_resource(
        DOCUMENT_URI, Resource.from_contents(document, default_specification=DRAFT202012)
    )
    fragment = "/".join(
        quote(token.replace("~", "~0").replace("/", "~1"), safe="") for token in pointer
    )
    return Draft202012Validator(
        {"$ref": f"{DOCUMENT_URI}#/{fragment}"},
        registry=registry,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
