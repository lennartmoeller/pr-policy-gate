import pytest

from pr_policy_gate.config import InvalidConfigError, Reviewer, Rule, check_scripts, load


def violations(text: str) -> list[str]:
    with pytest.raises(InvalidConfigError) as raised:
        load(text)
    return raised.value.violations


def test_load_reads_team_user_and_app_reviewers_with_the_default_minimum():
    text = """
rules:
  - name: Platform files
    required_approvals:
      reviewers:
        - team: ent:acme-platform-owner
        - team: other/owners
        - user: alice
        - app: some-app
"""

    assert load(text) == [
        Rule(
            "Platform files",
            None,
            None,
            1,
            (
                Reviewer("team", "ent:acme-platform-owner"),
                Reviewer("team", "owners", "other"),
                Reviewer("user", "alice"),
                Reviewer("app", "some-app[bot]"),
            ),
            (),
        )
    ]


def test_a_rule_without_required_approvals_requires_none():
    assert load("rules:\n  - name: CI\n    required_status_checks: [Lint]\n") == [
        Rule("CI", None, None, 0, (), ("Lint",))
    ]


def test_violations_name_the_rule_the_location_and_the_schema_description():
    text = """
rules:
  - name: Baseline
    required_status_checks: [Lint]
  - name: Docs
    required_status_checks: [Lint]
  - name: Platform files
    when:
      paths: [".github/**"]
    required_approvals:
      minimum: 0
      reviewers:
        - team: maintainers
  - name: Platform files
    required_status_checks: [Lint]
"""

    minimum = (
        "`rules[2]` (`Platform files`) `required_approvals.minimum`: `0` is less than 1. "
        + "How many distinct counted approvals the rule requires."
    )

    assert violations(text) == [
        minimum,
        "`rules[3]` (`Platform files`) `name`: duplicate rule name. Names are unique within the file.",
    ]


def test_violations_explain_unknown_keys_missing_requirements_and_a_misplaced_placeholder():
    text = """
rules:
  - name: A
    reason: x
    required_status_checks: [Lint]
  - name: B
  - name: C
    required_approvals:
      reviewers:
        - team: "x-{*}"
"""
    rule = (
        "One rule with what it requires when it matches, through `required_approvals`, `required_status_checks`, "
        "or both. Several groups that must all approve are separate rules with the same `when`."
    )

    assert violations(text) == [
        f"`rules[0]` (`A`) `reason`: unknown key. {rule}",
        f"`rules[1]` (`B`): needs `required_approvals` or `required_status_checks`. {rule}",
        "`rules[2]` (`C`) `required_approvals.reviewers[0].team`: `x-{*}` is not allowed. "
        "Reviewer values contain `{*}` only when the rule's `when.paths` do.",
    ]


@pytest.mark.parametrize(
    ("text", "violation"),
    [
        ("rules: [a, b", "Not valid YAML: go-yaml load error in parser"),
        ("rules: [services/{*}/**]", "Not valid YAML: go-yaml load error in scanner"),
        ("a: 1\n---\nb: 2\n", "Holds more than one YAML document."),
        ("", "`null` is not a mapping. The contract for the configuration file, `.github/pr-policy-gate.yml`"),
    ],
)
def test_a_file_that_is_no_single_mapping_is_one_violation(text, violation):
    (found,) = violations(text)

    assert found.startswith(violation)


def test_merge_keys_follow_the_yaml_spec():
    text = """
rules:
  - name: Platform files
    <<: {when: {paths: [".github/**"]}}
    required_status_checks: [Lint]
"""

    assert load(text)[0].paths == (".github/**",)


def test_check_scripts_requires_an_executable_file_in_the_trusted_commit():
    rules = load(
        "rules:\n"
        + "".join(
            f"  - name: {name}\n    when:\n      script: {name}.sh\n    required_status_checks: [Lint]\n"
            for name in ("a", "b", "c")
        )
    )

    with pytest.raises(InvalidConfigError) as raised:
        check_scripts(rules, {"a.sh": "100755", "b.sh": "100644"})

    explanation = "A script is an executable file in the default branch's current head."
    assert raised.value.violations == [
        f"`rules[1]` (`b`) `when.script`: `b.sh` is not executable. {explanation}",
        f"`rules[2]` (`c`) `when.script`: `c.sh` does not exist. {explanation}",
    ]
