import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pr_policy_gate import schema
from pr_policy_gate.github import bot_login

DEFAULT_MINIMUM = schema.SCHEMA["properties"]["rules"]["items"]["properties"]["required_approvals"]["properties"][
    "minimum"
]["default"]
EXECUTABLE_MODE = "100755"


class InvalidConfigError(Exception):
    def __init__(self, violations: list[str]):
        super().__init__(" ".join(violations))
        self.violations = violations


@dataclass(frozen=True)
class Reviewer:
    kind: str
    name: str
    organization: str | None = None

    @property
    def label(self) -> str:
        return f"{self.organization}/{self.name}" if self.organization else self.name


@dataclass(frozen=True)
class Rule:
    name: str
    paths: tuple[str, ...] | None
    script: str | None
    minimum: int
    reviewers: tuple[Reviewer, ...]
    checks: tuple[str, ...]


def load(text: str) -> list[Rule]:
    document = parse_yaml(text)
    names = _rule_names(document)
    violations = schema.validate(document, schema.SCHEMA) + [
        schema.Violation(("rules", index, "name"), "duplicate rule name", "Names are unique within the file.")
        for index, name in enumerate(names)
        if name is not None and name in names[:index]
    ]
    if violations:
        raise InvalidConfigError([_sentence(violation, names) for violation in _sorted(violations)])
    return [_rule(item) for item in document["rules"]]


def check_scripts(rules: Sequence[Rule], modes: Mapping[str, str]) -> None:
    names = [rule.name for rule in rules]
    violations = [
        _sentence(
            schema.Violation(
                ("rules", index, "when", "script"),
                f"`{rule.script}` {'is not executable' if rule.script in modes else 'does not exist'}",
                "A script is an executable file in the default branch's current head.",
            ),
            names,
        )
        for index, rule in enumerate(rules)
        if rule.script is not None and modes.get(rule.script) != EXECUTABLE_MODE
    ]
    if violations:
        raise InvalidConfigError(violations)


def parse_yaml(text: str) -> Any:
    result = subprocess.run(
        ["yq", "--yaml-fix-merge-anchor-to-spec", "--output-format=json", "--indent=0", "."],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise InvalidConfigError([f"Not valid YAML: {result.stderr.strip().removeprefix("Error: bad file '-': ")}."])
    documents = result.stdout.splitlines()
    if len(documents) > 1:
        raise InvalidConfigError(["Holds more than one YAML document."])
    return json.loads(documents[0] if documents else "null")


def _rule_names(document: Any) -> list[str | None]:
    rules = document.get("rules") if isinstance(document, dict) else None
    if not isinstance(rules, list):
        return []
    return [item["name"] if isinstance(item, dict) and isinstance(item.get("name"), str) else None for item in rules]


def _sorted(violations: list[schema.Violation]) -> list[schema.Violation]:
    return sorted(violations, key=lambda violation: [(isinstance(part, str), part) for part in violation.location])


def _sentence(violation: schema.Violation, names: Sequence[str | None]) -> str:
    location, parts = violation.location, []
    if location[:1] == ("rules",) and len(location) >= 2:
        name = names[location[1]]
        parts.append(f"`rules[{location[1]}]`" + (f" (`{name}`)" if name is not None else ""))
        location = location[2:]
    if location:
        parts.append(f"`{_path(location)}`")
    text = f"{violation.message}. {violation.description}"
    return f"{' '.join(parts)}: {text}" if parts else text


def _path(location: schema.Location) -> str:
    return "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in location).removeprefix(".")


def _rule(item: dict) -> Rule:
    when = item.get("when", {})
    approvals = item.get("required_approvals")
    return Rule(
        name=item["name"],
        paths=tuple(when["paths"]) if "paths" in when else None,
        script=when.get("script"),
        minimum=int(approvals.get("minimum", DEFAULT_MINIMUM)) if approvals else 0,
        reviewers=tuple(_reviewer(entry) for entry in approvals["reviewers"]) if approvals else (),
        checks=tuple(item.get("required_status_checks", ())),
    )


def _reviewer(entry: dict) -> Reviewer:
    ((kind, name),) = entry.items()
    if kind == "team" and "/" in name:
        organization, slug = name.split("/")
        return Reviewer(kind, slug, organization)
    return Reviewer(kind, bot_login(name) if kind == "app" else name)
