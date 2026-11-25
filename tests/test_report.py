from datetime import UTC, datetime
from pathlib import Path

import pytest

from pr_policy_gate.approvals import Approvals, UncountedApproval
from pr_policy_gate.checks import CheckState
from pr_policy_gate.conditions import Instance
from pr_policy_gate.config import Reviewer, Rule
from pr_policy_gate.report import FORK_NOT_SUPPORTED, Result, evaluated, evaluation_failed, failed, invalid_config

FIXTURES = Path(__file__).parent / "fixtures" / "report"
PASSED, FAILED, RUNNING, MISSING = CheckState.PASSED, CheckState.FAILED, CheckState.RUNNING, CheckState.MISSING


def at(time: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-15T{time}:00").replace(tzinfo=UTC)


def result(name, reviewers=(), minimum=1, counted=(), requested=(), checks=None, value=None, paths=None):
    reviewers = tuple(Reviewer(*reviewer) for reviewer in reviewers)
    rule = Rule(name, paths, None, minimum if reviewers else 0, reviewers, tuple(checks or {}))
    return Result(Instance(rule, value), Approvals(tuple(counted), tuple(requested)), dict(checks or {}))


BASELINE = {"reviewers": [("team", "maintainers")], "counted": ["carol"], "checks": {"Lint": PASSED}}
PLATFORM = {
    "reviewers": [("team", "ent:acme-platform-owner"), ("user", "alice"), ("app", "some-app[bot]")],
    "minimum": 2,
}
SECURITY = {"reviewers": [("team", "security")], "counted": ["dave"]}


def waiting_for_platform_approval():
    owners = [("team", "acme-{*}-owner", "acme-{*}")]
    checks = dict.fromkeys(["Configuration validation", "Quality", "Lint", "Analyze"], PASSED)
    results = [
        result("Organization owners", owners, counted=["alice"], value="web", paths=("organizations/{*}/**",)),
        result("Platform-sensitive organization changes", [("team", "ent:acme-platform-owner")], value="web"),
        result("Checks", checks=checks),
    ]
    return evaluated("#7", results, [], at("09:12"))


def all_requirements_met():
    results = [
        result("Baseline", **BASELINE),
        result("Platform files", **PLATFORM, counted=["bob", "carol"]),
        result("Security review of platform files", **SECURITY),
    ]
    return evaluated("#7", results, [], at("10:47"))


def blocked_by_request_for_changes():
    results = [
        result("Baseline", **BASELINE),
        result("Platform files", **PLATFORM, counted=["carol"], requested=["henry"]),
        result("Security review of platform files", **SECURITY),
    ]
    return evaluated("#7", results, [], at("13:05"))


def checks_failing_and_pending():
    services = {"paths": ("services/{*}/**",), "checks": {"Test": RUNNING}}
    results = [
        result("Baseline", **BASELINE | {"checks": {"Lint": FAILED, "SonarQube Code Analysis": MISSING}}),
        result("Service owners", [("team", "{*}-owners", "other-org")], counted=["olga"], value="billing", **services),
        result("Service owners", [("team", "{*}-owners", "other-org")], value="payments", **services),
    ]
    return evaluated("#7", results, [], at("14:31"))


def requirements_shared_by_several_rules():
    results = [
        result(
            "Platform files",
            [("team", "platform"), ("user", "alice")],
            minimum=2,
            counted=["bob", "carol"],
            checks={"Quality": PASSED, "Lint": PASSED},
        ),
        result(
            "Release files",
            [("user", "alice"), ("team", "platform")],
            minimum=3,
            counted=["bob", "carol"],
            checks={"Quality": PASSED},
        ),
    ]
    return evaluated("#7", results, [], at("15:20"))


def invalid_configuration():
    minimum = (
        "`rules[2]` (`Platform files`) `required_approvals.minimum`: `0` is less than 1. "
        + "How many distinct counted approvals the rule requires."
    )
    violations = [
        minimum,
        "`rules[3]` (`Platform files`) `name`: duplicate rule name. Names are unique within the file.",
    ]
    return failed(
        "#7",
        invalid_config(".github/pr-policy-gate.yml", violations, "main", "4b7d0e2c91a8f3d5e6b7c8d9e0f1a2b3c4d5e6f7"),
        at("16:58"),
    )


def fork_pull_request():
    return failed("#7", FORK_NOT_SUPPORTED, at("17:20"))


def evaluation_failed_report():
    message = (
        "Team `acme-gone/acme-gone-owner` is not readable: "
        + "GET /orgs/acme-gone/installation failed with HTTP 404 Not Found."
    )
    return failed("#7", evaluation_failed(message), at("18:02"))


EXAMPLES = {
    "01-waiting-for-platform-approval": (waiting_for_platform_approval, ("pending", "Waiting for 1 approval")),
    "02-all-requirements-met": (all_requirements_met, ("success", "All requirements met")),
    "03-blocked-by-request-for-changes": (
        blocked_by_request_for_changes,
        ("pending", "Waiting for 1 approval; changes requested by henry"),
    ),
    "04-checks-failing-and-pending": (
        checks_failing_and_pending,
        ("pending", "Waiting for 1 approval and 2 checks; 1 check failed"),
    ),
    "05-invalid-configuration": (
        invalid_configuration,
        ("error", "Error: invalid .github/pr-policy-gate.yml on main"),
    ),
    "06-requirements-shared-by-several-rules": (
        requirements_shared_by_several_rules,
        ("pending", "Waiting for 1 approval"),
    ),
    "07-fork-pull-request": (fork_pull_request, ("error", "Error: fork pull requests are not supported")),
    "08-evaluation-failed": (
        evaluation_failed_report,
        (
            "error",
            "Error: Team acme-gone/acme-gone-owner is not readable: "
            "GET /orgs/acme-gone/installation failed with HTTP 404 Not Found.",
        ),
    ),
}


@pytest.mark.parametrize("example", sorted(FIXTURES.glob("*.md")), ids=lambda path: path.stem)
def test_every_approved_example_is_the_comment_beside_its_status(example):
    render, status = EXAMPLES[example.stem]

    report = render()

    assert (report.state, report.description) == status
    assert report.comment == example.read_text()


def test_no_matched_rule_succeeds_without_a_comment():
    report = evaluated("#7", [], [], at("08:00"))

    assert (report.state, report.description, report.comment) == ("success", "No rule matched", None)


def test_the_description_fits_the_status_limit():
    blocked = result("Platform files", **PLATFORM, requested=[f"reviewer-{number:02}" for number in range(20)])

    report = evaluated("#7", [blocked], [], at("08:00"))

    assert report.state == "pending"
    assert len(report.description) == 140
    assert report.description.startswith("Waiting for 2 approvals; changes requested by reviewer-00, reviewer-01")
    assert report.description.endswith("…")


def test_the_summary_adds_a_title_and_the_approvals_that_count_for_no_matched_rule():
    uncounted = [
        UncountedApproval("eve", "not a reviewer of any matched rule"),
        UncountedApproval("frank", "dismissed"),
    ]

    assert evaluated("#12", [result("Security", **SECURITY)], uncounted, at("13:05")).summary == (
        "## PR Policy Gate · #12\n\n"
        "### Required approvals\n- ✅ 1/1 `security` approved\n\n"
        "### Approvals that count for no matched rule\n"
        "- `eve`: not a reviewer of any matched rule\n- `frank`: dismissed\n\n"
        "<sub>Last evaluated 2026-10-15 13:05 UTC</sub>\n"
    )


def test_merge_group_results_show_checks_only():
    group = Result(result("Baseline", **BASELINE).instance, None, {"Lint": RUNNING})

    report = evaluated("merge group `abc1234`", [group], [], at("08:00"), merge_group=True)

    assert (report.state, report.description) == ("pending", "Waiting for 1 check")
    assert report.summary == (
        "## PR Policy Gate · merge group `abc1234`\n\n### Required checks\n- ⏳ `Lint` running\n\n"
        "<sub>Last evaluated 2026-10-15 08:00 UTC</sub>\n"
    )


def test_reviewers_that_differ_only_in_case_are_one_requirement_with_the_strictest_minimum():
    results = [result("A", [("user", "alice")]), result("B", [("user", "ALICE")], minimum=2)]

    report = evaluated("#7", results, [], at("08:00"))

    assert report.description == "Waiting for 2 approvals"
    assert "### Required approvals\n- ⏳ 0/2 `alice` approved\n\n" in report.comment
    assert "ALICE" not in report.comment


def test_a_merge_group_whose_rules_require_no_checks_says_so():
    group = Result(result("Baseline", [("team", "maintainers")]).instance, None, {})

    report = evaluated("merge group `abc1234`", [group], [], at("08:00"), merge_group=True)

    assert (report.state, report.description) == ("success", "All requirements met")
    assert report.summary == (
        "## PR Policy Gate · merge group `abc1234`\n\nNo required checks.\n\n"
        "<sub>Last evaluated 2026-10-15 08:00 UTC</sub>\n"
    )


def test_a_failed_check_fails_a_merge_group_whose_commit_cannot_change():
    group = Result(result("Baseline", **BASELINE).instance, None, {"Lint": FAILED, "Test": RUNNING})

    report = evaluated("merge group `abc1234`", [group], [], at("08:00"), merge_group=True)

    assert (report.state, report.description) == ("failure", "Waiting for 1 check; 1 check failed")
