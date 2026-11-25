from pathlib import Path

import pytest

from pr_policy_gate.conditions import Instance, ScriptContext, ScriptFailedError, matched_instances
from pr_policy_gate.config import Reviewer, Rule


def rule(name, paths=None, script=None, reviewers=()):
    return Rule(name, tuple(paths) if paths else None, script, 1 if reviewers else 0, tuple(reviewers), ())


def script(trusted: Path, name: str, body: str) -> str:
    path = trusted / ".github" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)
    return f".github/{name}"


class Contexts:
    def __init__(self, tmp_path: Path):
        self.trusted = tmp_path / "trusted"
        self.diff_base = tmp_path / "diff-base"
        self.head = tmp_path / "head"
        self.changed = tmp_path / "changed-files"
        self.output = tmp_path / "output"
        self.trusted.mkdir()
        self.diff_base.mkdir()
        self.head.mkdir()
        self.created = 0

    def __call__(self) -> ScriptContext:
        self.created += 1
        environment = {
            "KEEP": "kept",
            "OUTPUT": str(self.output),
            "INPUT_APP_PRIVATE_KEY": "secret",
            "PATH": "/usr/bin:/bin",
        }
        return ScriptContext(self.trusted, self.diff_base, self.head, self.changed, "t" * 40, "h" * 40, environment)


@pytest.fixture
def contexts(tmp_path):
    return Contexts(tmp_path)


def test_a_placeholder_creates_one_instance_per_value_with_its_own_reviewers(contexts):
    services = rule(
        "Service owners",
        ["services/{*}/**", "!services/{*}/docs/**"],
        reviewers=[Reviewer("team", "{*}-owners", "org")],
    )
    changed = ["services/billing/a.py", "services/payments/docs/x.md", "services/shipping/b.py"]

    instances = matched_instances([services], changed, contexts)

    assert instances == [Instance(services, "billing"), Instance(services, "shipping")]
    assert [instance.reviewers() for instance in instances] == [
        (Reviewer("team", "billing-owners", "org"),),
        (Reviewer("team", "shipping-owners", "org"),),
    ]
    assert contexts.created == 0


def test_a_rule_without_paths_always_matches_and_a_path_rule_needs_a_changed_path(contexts):
    baseline, docs = rule("Baseline"), rule("Docs", ["docs/**"])

    assert matched_instances([baseline, docs], ["src/a.py"], contexts) == [Instance(baseline, None)]


def test_a_script_decides_by_exit_code_and_runs_only_when_its_paths_match(contexts):
    rules = [
        rule("True", ["src/**"], script(contexts.trusted, "true.sh", "exit 0")),
        rule("False", ["src/**"], script(contexts.trusted, "false.sh", "exit 1")),
        rule("Unmatched", ["docs/**"], script(contexts.trusted, "broken.sh", "exit 2")),
    ]

    assert [instance.rule.name for instance in matched_instances(rules, ["src/a.py"], contexts)] == ["True"]
    assert contexts.created == 1


def test_a_script_runs_in_the_trusted_checkout_with_the_contract_environment(contexts):
    body = (
        'printf "%s\\n" "$PWD" "$PR_POLICY_GATE_BASE_SHA" "$PR_POLICY_GATE_HEAD_SHA" "$PR_POLICY_GATE_HEAD_DIR" '
        '"$PR_POLICY_GATE_DIFF_BASE_DIR" "$PR_POLICY_GATE_CHANGED_FILES" "$PR_POLICY_GATE_MATCH" "$KEEP" '
        '"${INPUT_APP_PRIVATE_KEY:-removed}" >"$OUTPUT"'
    )
    services = rule("Services", ["services/{*}/**"], script(contexts.trusted, "record.sh", body))

    matched_instances([services], ["services/billing/a.py"], contexts)

    pwd, *rest = contexts.output.read_text().splitlines()
    assert Path(pwd).resolve() == contexts.trusted.resolve()
    assert rest == [
        "t" * 40,
        "h" * 40,
        str(contexts.head),
        str(contexts.diff_base),
        str(contexts.changed),
        "billing",
        "kept",
        "removed",
    ]


def test_a_script_runs_once_per_value_however_many_rules_use_it(contexts):
    counting = script(contexts.trusted, "count.sh", 'echo run >>"$OUTPUT"')
    rules = [rule("A", ["src/**"], counting), rule("B", ["src/**"], counting)]

    assert len(matched_instances(rules, ["src/a.py"], contexts)) == 2
    assert contexts.output.read_text() == "run\n"


def test_other_exit_codes_and_scripts_that_cannot_start_are_errors(contexts):
    failing = rule("Services", ["services/{*}/**"], script(contexts.trusted, "fail.sh", "exit 2"))
    no_shebang = contexts.trusted / "plain.sh"
    no_shebang.write_text("exit 0\n")
    no_shebang.chmod(0o755)

    with pytest.raises(
        ScriptFailedError, match=r"^Script `.github/fail.sh` of rule `Services` \(`billing`\) exited with 2\.$"
    ):
        matched_instances([failing], ["services/billing/a.py"], contexts)
    with pytest.raises(
        ScriptFailedError, match=r"^Script `plain.sh` of rule `Plain` could not start: Exec format error\.$"
    ):
        matched_instances([rule("Plain", None, "plain.sh")], ["a"], contexts)
