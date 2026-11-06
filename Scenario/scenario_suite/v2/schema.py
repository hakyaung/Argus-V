"""Standard-library JSON Schema subset (V-01 and E-01 share it).

Supported: type (incl. lists), const, enum, pattern, required, properties, additionalProperties
(bool or schema), items, minItems, maxItems, minLength, maxLength, minimum, maximum, local $ref.
Unknown keywords (description, $schema, $id, title, x-*) are ignored.
"""
import json
import math
import re
from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "design" / "scenario-template.v2.json"

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def _resolve(ref, root):
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref is supported: {ref}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def iter_errors(value, schema, root=None, path="$"):
    """Yield 'path: message' strings for every violation found (depth-first)."""
    root = schema if root is None else root
    if schema is True or schema == {}:
        return
    if schema is False:
        yield f"{path}: not allowed"
        return
    if "$ref" in schema:
        yield from iter_errors(value, _resolve(schema["$ref"], root), root, path)
    if "const" in schema and value != schema["const"]:
        yield f"{path}: must equal {schema['const']!r}"
    if "enum" in schema and value not in schema["enum"]:
        yield f"{path}: value not in enum"
    types = schema.get("type")
    if types is not None:
        names = types if isinstance(types, list) else [types]
        if not any(_TYPES.get(name, lambda v: False)(value) for name in names):
            yield f"{path}: wrong type (expected {types})"
            return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            yield f"{path}: non-finite number"
        if "minimum" in schema and value < schema["minimum"]:
            yield f"{path}: below minimum"
        if "maximum" in schema and value > schema["maximum"]:
            yield f"{path}: above maximum"
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            yield f"{path}: shorter than minLength"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            yield f"{path}: longer than maxLength"
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            yield f"{path}: does not match pattern"
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            yield f"{path}: fewer than minItems"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            yield f"{path}: more than maxItems"
        if "items" in schema:
            for index, item in enumerate(value):
                yield from iter_errors(item, schema["items"], root, f"{path}[{index}]")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                yield f"{path}: missing required '{key}'"
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                yield from iter_errors(item, properties[key], root, f"{path}.{key}")
            elif extra is False:
                yield f"{path}: unknown key '{key}'"
            elif isinstance(extra, dict):
                yield from iter_errors(item, extra, root, f"{path}.{key}")


def first_error(value, schema, root=None):
    return next(iter_errors(value, schema, root), None)


def schema_error_v2(arguments, schema):
    """E-01 runtime check of tool arguments: engine checks plus enum, const, pattern, minLength, minItems.

    Returns None or a short public message (no operator vocabulary)."""
    error = first_error(arguments, schema)
    if error is None:
        return None
    return error.replace("$.", "").replace("$", "arguments")


def load_template():
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def validate_spec(spec, template=None):
    """V-01 schema part: every error of the scenario JSON against the template schema."""
    template = template or load_template()
    schema = {key: value for key, value in template.items() if key not in ("examples", "x-translation-to-profile")}
    return list(iter_errors(spec, schema, schema))
