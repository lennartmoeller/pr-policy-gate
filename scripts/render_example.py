#!/usr/bin/env python3
"""Print the YAML example of a JSON Schema on standard output.

The example holds every key of the schema: its comments are the schema's `description` text and rules,
and its values are the schema's `examples`. So the example is corrected by correcting the schema.

Usage: render_example.py SCHEMA
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Final


def resolve(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        target = root
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
    return node


def constraints(node: dict[str, Any]) -> list[str]:
    out = []
    if node.get("minItems"):
        out.append(f"at least {node['minItems']} entry" + ("" if node["minItems"] == 1 else "s"))
    if node.get("minProperties"):
        out.append(f"at least {node['minProperties']} field" + ("" if node["minProperties"] == 1 else "s"))
    if "pattern" in node and "\\x" not in node["pattern"]:
        out.append(f"matching `{node['pattern']}`")
    return out


def sentence(parts: list[str]) -> str:
    if not parts:
        return ""
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


def defaults(node: dict[str, Any]) -> list[str]:
    return [f"Defaults to `{json.dumps(node['default'])}`."] if "default" in node else []


def prose(node: dict[str, Any], root: dict[str, Any]) -> list[str]:
    """One sentence per line, so rewording a sentence changes exactly one line of the output.

    An array states its rules across itself and its `items`, so both are read here.
    """
    entry = resolve(node["items"], root) if "items" in node else {}
    text = " ".join(
        filter(
            None,
            [
                node.get("description", ""),
                sentence(constraints(node) + constraints(entry)),
                *defaults(node),
                *defaults(entry),
            ],
        )
    )
    return re.split(r"(?<=[A-Za-z0-9`)/>]\.)\s+(?=[A-Z])", text) if text else []


def fields(node: dict[str, Any], root: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    """An array declares its fields on its entries."""
    if "items" in node:
        node = resolve(node["items"], root)
    return dict(node.get("properties") or {}), set(node.get("required", []))


# YAML reads a plain scalar back as the string it was written from unless it opens with an indicator,
# carries a flow separator or a comment, or spells a word YAML gives a meaning of its own.
INDICATORS: Final = set("-?:,[]{}#&*!|>'\"%@`")
SEPARATORS: Final = set(",[]{}")
RESERVED: Final = {"y", "n", "yes", "no", "true", "false", "on", "off", "null", "~"}


def numeric(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def plain(text: str) -> bool:
    return bool(
        text
        and text == text.strip()
        and text[0] not in INDICATORS
        and not SEPARATORS & set(text)
        and ": " not in text
        and " #" not in text
        and not text.endswith(":")
        and text.lower() not in RESERVED
        and not numeric(text)
    )


def scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(scalar(item) for item in value) + "]"
    text = str(value)
    if plain(text):
        return text
    return "'" + text.replace("'", "''") + "'"


def commented(node: dict[str, Any], root: dict[str, Any], indent: int, required: bool) -> list[str]:
    """Every key here is present, so what a reader needs is which ones they may delete."""
    pad = " " * indent
    lines = [] if required else ["Optional."]
    return [f"{pad}# {line.replace('`', '')}" for line in lines + prose(node, root)]


def example(node: dict[str, Any], root: dict[str, Any], name: str, path: str, indent: int, required: bool) -> list[str]:
    """A sequence dash sits at its key's column."""
    node = resolve(node, root)
    pad = " " * indent
    lines = commented(node, root, indent, required)
    given = next(iter(node.get("examples", [])), None)
    children, needed = fields(node, root)
    if "items" in node and children:
        lines += [f"{pad}{name}:", ""]
        per_entry = resolve(node["items"], root).get("maxProperties", len(children))
        for index, (child_name, child) in enumerate(children.items()):
            block = example(child, root, child_name, f"{path}.{child_name}", indent + 2, child_name in needed)
            if index:
                lines.append("")
            if index % per_entry:
                lines += block
            else:
                # The dash carries the first field of an entry, and its comments stay with the fields they
                # describe rather than following the dash out to its column.
                body = [line for line in block if not line.strip().startswith("#")]
                lines += [line for line in block if line.strip().startswith("#")]
                lines.append(f"{pad}- {body[0].strip()}")
                lines += body[1:]
        return lines
    if children:
        lines += [f"{pad}{name}:", ""]
        for index, (child_name, child) in enumerate(children.items()):
            if index:
                lines.append("")
            lines += example(child, root, child_name, f"{path}.{child_name}", indent + 2, child_name in needed)
        return lines
    if given is None:
        raise SystemExit(f"The schema declares no `examples` for '{path}'. Every key it declares is shown.")
    lines.append(f"{pad}{name}: {scalar(given)}")
    return lines


def main(schema_path: str) -> None:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    lines: list[str] = []
    for index, (name, node) in enumerate(schema["properties"].items()):
        if index:
            lines.append("")
        lines += example(node, schema, name, name, 0, name in schema.get("required", []))
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1])
