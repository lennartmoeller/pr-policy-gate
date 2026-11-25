import json
import re
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

type Location = tuple[str | int, ...]

SCHEMA = json.loads((Path(__file__).parents[2] / "schema" / "pr-policy-gate.schema.json").read_text())
_NON_ASSERTIONS = {"$defs", "$id", "$schema", "default", "description", "examples", "title"}
_TYPE_NAMES = {"object": "a mapping", "array": "a list", "string": "a string", "integer": "a whole number"}


@dataclass(frozen=True)
class Violation:
    location: Location
    message: str
    description: str


def validate(instance: Any, schema: dict) -> list[Violation]:
    return _errors(instance, schema, schema, (), "")


def _errors(instance: Any, node: dict, root: dict, location: Location, description: str) -> list[Violation]:
    description = node.get("description", description)
    if "type" in node and not _has_type(instance, node["type"]):
        return [Violation(location, f"{_show(instance)} is not {_TYPE_NAMES[node['type']]}", description)]
    errors: list[Violation] = []
    for keyword, value in node.items():
        if keyword in _NON_ASSERTIONS or keyword in ("type", "then", "else"):
            continue
        if keyword not in _KEYWORDS:
            raise ValueError(f"The built-in validator does not support the schema keyword {keyword}.")
        instance_type, check = _KEYWORDS[keyword]
        if instance_type is None or _has_type(instance, instance_type):
            errors += check(instance, value, node, root, location, description)
    return errors


def _ref(instance, value, _node, root, location, description):
    target = root
    for part in value.removeprefix("#/").split("/"):
        target = target[part]
    return _errors(instance, target, root, location, description)


def _required(instance, value, node, _root, location, description):
    properties = node.get("properties", {})
    return [
        Violation((*location, key), "missing", properties.get(key, {}).get("description", description))
        for key in value
        if key not in instance
    ]


def _properties(instance, value, _node, root, location, description):
    return [
        error
        for key, subschema in value.items()
        if key in instance
        for error in _errors(instance[key], subschema, root, (*location, key), description)
    ]


def _additional_properties(instance, value, node, root, location, description):
    additional = [key for key in instance if key not in node.get("properties", {})]
    if value is False:
        return [Violation((*location, key), "unknown key", description) for key in additional]
    return [error for key in additional for error in _errors(instance[key], value, root, (*location, key), description)]


def _min_size(noun, instance, value, _node, _root, location, description):
    if len(instance) < value:
        return [Violation(location, "is empty" if value == 1 else f"needs at least {value} {noun}", description)]
    return []


def _max_properties(instance, value, _node, _root, location, description):
    if len(instance) > value:
        return [Violation(location, f"has {len(instance)} keys but allows only {value}", description)]
    return []


def _items(instance, value, _node, root, location, description):
    return [
        error for i, item in enumerate(instance) for error in _errors(item, value, root, (*location, i), description)
    ]


def _unique_items(instance, _value, _node, _root, location, description):
    seen: set[str] = set()
    for item in instance:
        key = json.dumps(item, sort_keys=True)
        if key in seen:
            return [Violation(location, f"repeats {_show(item)}", description)]
        seen.add(key)
    return []


def _contains(instance, value, _node, root, location, description):
    if all(_errors(item, value, root, location, description) for item in instance):
        return [Violation(location, "has no fitting item", description)]
    return []


def _pattern(instance, value, _node, _root, location, description):
    if not re.search(value, instance):
        return [Violation(location, f"{_show(instance)} has an invalid form", description)]
    return []


def _minimum(instance, value, _node, _root, location, description):
    if instance < value:
        return [Violation(location, f"{_show(instance)} is less than {value}", description)]
    return []


def _not(instance, value, _node, root, location, description):
    if _errors(instance, value, root, location, description):
        return []
    return [Violation(location, f"{_show(instance)} is not allowed", description)]


def _any_of(instance, value, _node, root, location, description):
    if any(not _errors(instance, alternative, root, location, description) for alternative in value):
        return []
    if all(alternative.keys() == {"required"} for alternative in value):
        keys = [f"`{key}`" for alternative in value for key in alternative["required"]]
        return [Violation(location, f"needs {' or '.join(keys)}", description)]
    return [Violation(location, f"{_show(instance)} is not allowed", description)]


def _if(instance, value, node, root, location, description):
    branch = node.get("else" if _errors(instance, value, root, location, description) else "then")
    return _errors(instance, branch, root, location, description) if branch else []


_KEYWORDS = {
    "$ref": (None, _ref),
    "required": ("object", _required),
    "properties": ("object", _properties),
    "additionalProperties": ("object", _additional_properties),
    "minProperties": ("object", partial(_min_size, "keys")),
    "maxProperties": ("object", _max_properties),
    "items": ("array", _items),
    "minItems": ("array", partial(_min_size, "items")),
    "uniqueItems": ("array", _unique_items),
    "contains": ("array", _contains),
    "pattern": ("string", _pattern),
    "minimum": ("integer", _minimum),
    "not": (None, _not),
    "anyOf": (None, _any_of),
    "if": (None, _if),
}


def _has_type(instance: Any, name: str) -> bool:
    if name == "integer":
        return (isinstance(instance, int) and not isinstance(instance, bool)) or (
            isinstance(instance, float) and instance.is_integer()
        )
    return isinstance(instance, {"object": dict, "array": list, "string": str}[name])


def _show(instance: Any) -> str:
    text = instance if isinstance(instance, str) else json.dumps(instance, separators=(",", ":"))
    return f"`{text}`"
