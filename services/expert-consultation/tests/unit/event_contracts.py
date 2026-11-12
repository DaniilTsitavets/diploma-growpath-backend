"""Loads the AsyncAPI contract and validates event payloads and attributes against it."""

import copy
from collections.abc import Mapping
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ASYNCAPI_PATH = Path(__file__).parents[4] / "contracts" / "asyncapi" / "growpath-events.yaml"
DOCUMENT_URI = "urn:growpath:asyncapi"


def load_asyncapi() -> dict:
    return yaml.safe_load(ASYNCAPI_PATH.read_text(encoding="utf-8"))


def example_payloads(message: str) -> dict[str, dict]:
    examples = load_asyncapi()["components"]["messages"][message]["examples"]
    return {example["name"]: copy.deepcopy(example["payload"]) for example in examples}


def required_fields(message: str) -> set[str]:
    document = load_asyncapi()
    reference = document["components"]["messages"][message]["payload"]["$ref"]
    return set(document["components"]["schemas"][reference.rsplit("/", 1)[1]]["required"])


def payload_errors(message: str, payload: object) -> list[str]:
    return _errors(message, "payload", payload)


def attribute_errors(message: str, attributes: Mapping[str, Mapping[str, str]]) -> list[str]:
    """Checks SNS-style `{name: {"DataType": ..., "StringValue": ...}}` message attributes."""
    plain = {name: value["StringValue"] for name, value in attributes.items()}
    return _errors(message, "headers", plain)


def _errors(message: str, part: str, instance: object) -> list[str]:
    document = load_asyncapi()
    reference = document["components"]["messages"][message][part]["$ref"]
    registry = Registry().with_resource(
        DOCUMENT_URI, Resource.from_contents(document, default_specification=DRAFT202012)
    )
    validator = Draft202012Validator(
        {"$ref": f"{DOCUMENT_URI}{reference}"},
        registry=registry,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    return [
        f"{list(error.path)}: {error.message}"
        for error in sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    ]
