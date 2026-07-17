"""
validator.py — Validate an io_list JSON payload against schema and business rules.
"""

import json
import os
import jsonschema

_SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "knowledge", "IOList-config-schema.json"
)

ANALOG_TYPES = {"AI_Physical", "AO_Physical"}


def _load_schema() -> dict:
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def validate(payload: dict) -> list:
    errors: list = []

    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path)):
        path = " > ".join(str(p) for p in err.absolute_path) or "root"
        errors.append(f"{path}: {err.message}")

    if errors:
        return errors

    for i, point in enumerate(payload.get("io_list", [])):
        io_type = point.get("io_type", "")
        tag = point.get("equip_proc_loc", f"point[{i}]")
        if io_type in ANALOG_TYPES:
            for field in ("range_min", "range_max", "units"):
                if point.get(field) is None:
                    errors.append(f"{tag}: '{field}' is required for io_type '{io_type}'")

    return errors
