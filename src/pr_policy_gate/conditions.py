import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from pr_policy_gate.config import Reviewer, Rule
from pr_policy_gate.patterns import PLACEHOLDER, captured_values, matches


class ScriptFailedError(Exception):
    pass


@dataclass(frozen=True)
class Instance:
    rule: Rule
    value: str | None

    def reviewers(self) -> tuple[Reviewer, ...]:
        value = self.value
        if value is None:
            return self.rule.reviewers
        return tuple(
            replace(
                reviewer,
                name=reviewer.name.replace(PLACEHOLDER, value),
                organization=reviewer.organization and reviewer.organization.replace(PLACEHOLDER, value),
            )
            for reviewer in self.rule.reviewers
        )


@dataclass(frozen=True)
class ScriptContext:
    trusted_dir: Path
    diff_base_dir: Path
    head_dir: Path
    changed_files: Path
    trusted_sha: str
    head_sha: str
    environment: Mapping[str, str]


def matched_instances(
    rules: Sequence[Rule], changed_paths: Sequence[str], script_context: Callable[[], ScriptContext]
) -> list[Instance]:
    answers: dict[tuple[str, str | None], bool] = {}
    context: ScriptContext | None = None
    matched = []
    for rule in rules:
        captures = rule.paths is not None and any(PLACEHOLDER in pattern for pattern in rule.paths)
        for value in captured_values(rule.paths, changed_paths) if captures else [None]:
            if rule.paths is not None and not any(matches(rule.paths, path, value) for path in changed_paths):
                continue
            if rule.script is not None:
                key = (rule.script, value)
                if key not in answers:
                    context = context or script_context()
                    answers[key] = _run(rule, value, context)
                if not answers[key]:
                    continue
            matched.append(Instance(rule, value))
    return matched


def _run(rule: Rule, value: str | None, context: ScriptContext) -> bool:
    environment = {name: text for name, text in context.environment.items() if not name.startswith("INPUT_")} | {
        "PR_POLICY_GATE_BASE_SHA": context.trusted_sha,
        "PR_POLICY_GATE_HEAD_SHA": context.head_sha,
        "PR_POLICY_GATE_DIFF_BASE_DIR": str(context.diff_base_dir),
        "PR_POLICY_GATE_HEAD_DIR": str(context.head_dir),
        "PR_POLICY_GATE_CHANGED_FILES": str(context.changed_files),
        "PR_POLICY_GATE_MATCH": value or "",
    }
    instance = f"`{rule.name}`" + (f" (`{value}`)" if value is not None else "")
    try:
        code = subprocess.run(
            [context.trusted_dir / rule.script], cwd=context.trusted_dir, env=environment, check=False
        ).returncode
    except OSError as error:
        raise ScriptFailedError(
            f"Script `{rule.script}` of rule {instance} could not start: {error.strerror}."
        ) from error
    if code not in (0, 1):
        raise ScriptFailedError(f"Script `{rule.script}` of rule {instance} exited with {code}.")
    return code == 0
