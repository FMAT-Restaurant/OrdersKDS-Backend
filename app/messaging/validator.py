"""Validation of event payloads against JSON Schema contracts.

Satisfies: RNF-13, CT-23
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import jsonschema
from jsonschema.exceptions import ValidationError


class EventContractError(RuntimeError):
    """Raised when an event payload violates its contract schema."""


_DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "contracts" / "events"
_SCHEMA_CACHE: dict[str, dict[str, object]] = {}


def _resolve_schema_path(event_type: str, schema_dir: Path) -> Path:
    """Find the JSON schema file for the given event type.

    Args:
        event_type: Broker routing key (e.g. ordenes.orden.creada).
        schema_dir: Directory containing contract schemas.

    Returns:
        Path to the found schema file.

    Raises:
        EventContractError: If no matching schema file is found.
    """
    versioned = schema_dir / f"{event_type}.v1.json"
    if versioned.is_file():
        return versioned
    plain = schema_dir / f"{event_type}.json"
    if plain.is_file():
        return plain
    raise EventContractError(f"No schema contract found for event type: '{event_type}'")


def load_schema(event_type: str, schema_dir: Path | None = None) -> dict[str, object]:
    """Load and cache the JSON schema for an event type.

    Args:
        event_type: Event routing key name.
        schema_dir: Optional custom directory path for schemas.

    Returns:
        Loaded JSON schema as a dictionary.

    Raises:
        EventContractError: If schema file cannot be read or parsed.
    """
    effective_dir = schema_dir or _DEFAULT_SCHEMA_DIR
    cache_key = f"{effective_dir}:{event_type}"
    if cache_key in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[cache_key]

    path = _resolve_schema_path(event_type, effective_dir)
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise EventContractError(f"Schema at {path} is not a valid JSON object")
        _SCHEMA_CACHE[cache_key] = content
        return content
    except (json.JSONDecodeError, OSError) as exc:
        raise EventContractError(f"Failed to read schema at {path}: {exc}") from exc


def validate_event_payload(
    event_type: str,
    payload: Mapping[str, object],
    schema_dir: Path | None = None,
) -> None:
    """Validate an event payload against its versioned JSON Schema contract.

    Args:
        event_type: Event routing key identifier.
        payload: Event payload dictionary to validate.
        schema_dir: Optional directory to search for schemas.

    Raises:
        EventContractError: If the payload is invalid according to the schema.
    """
    schema = load_schema(event_type, schema_dir=schema_dir)
    try:
        jsonschema.validate(instance=dict(payload), schema=schema)
    except ValidationError as err:
        raise EventContractError(
            f"Payload failed contract validation for '{event_type}': {err.message}"
        ) from err
