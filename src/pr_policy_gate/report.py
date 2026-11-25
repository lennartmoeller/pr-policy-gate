from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pr_policy_gate.approvals import Approvals, UncountedApproval
from pr_policy_gate.checks import CheckState
from pr_policy_gate.conditions import Instance
from pr_policy_gate.config import Reviewer

TITLE = "PR Policy Gate"
MARKER = "<!-- pr-policy-gate -->"
DESCRIPTION_LIMIT = 140
CHECK_LINES = {
    CheckState.PASSED: ("✅", "passed"),
    CheckState.FAILED: ("❌", "failed"),
    CheckState.RUNNING: ("⏳", "running"),
    CheckState.MISSING: ("⏳", "not reported yet"),
}
WAITING = {CheckState.RUNNING, CheckState.MISSING}


@dataclass(frozen=True)
class Result:
    instance: Instance
    approvals: Approvals | None
    checks: dict[str, CheckState]


@dataclass(frozen=True)
class ApprovalRequirement:
    reviewers: tuple[Reviewer, ...]
    minimum: int
    counted: tuple[str, ...]

    def missing(self) -> int:
        return max(0, self.minimum - len(self.counted))


@dataclass(frozen=True)
class Requirements:
    approvals: list[ApprovalRequirement]
    blockers: list[str]
    checks: dict[str, CheckState]


@dataclass(frozen=True)
class Failure:
    title: str
    details: tuple[str, ...]
    closing: str | None
    reason: str


@dataclass(frozen=True)
class Report:
    state: str
    description: str
    summary: str
    comment: str | None


FORK_NOT_SUPPORTED = Failure("Fork pull requests are not supported", (), None, "fork pull requests are not supported")


def invalid_config(path: str, violations: Sequence[str], branch: str, trusted: str) -> Failure:
    return Failure(
        f"Invalid `{path}` on `{branch}`",
        tuple(violations),
        f"No rule was evaluated; fix the configuration on `{branch}` (`{trusted[:7]}`).",
        f"invalid {path} on {branch}",
    )


def evaluation_failed(message: str) -> Failure:
    return Failure("Evaluation failed", (message,), None, message.replace("`", ""))


def evaluated(
    subject: str,
    results: Sequence[Result],
    uncounted: Sequence[UncountedApproval],
    evaluated_at: datetime,
    *,
    merge_group: bool = False,
) -> Report:
    if results:
        requirements = _requirements(results)
        content = _groups(requirements) or "No required checks."
        status, comment = _status(requirements, merge_group), _marked(content, evaluated_at)
    else:
        content, status, comment = "No rule matched.", ("success", "No rule matched"), None
    if uncounted:
        lines = "\n".join(f"- `{approval.login}`: {approval.reason}" for approval in uncounted)
        content += f"\n\n### Approvals that count for no matched rule\n{lines}"
    summary = _frame(f"{TITLE} · {subject}", content, evaluated_at)
    return Report(*status, summary, comment)


def failed(subject: str, failure: Failure, evaluated_at: datetime) -> Report:
    details = "".join(f"\n- {detail}" for detail in failure.details)
    closing = f"\n\n{failure.closing}" if failure.closing else ""
    section = f"### ⚠️ {failure.title}{details}{closing}"
    summary = _frame(f"{TITLE} · {subject}", section, evaluated_at)
    return Report("error", _fit(f"Error: {failure.reason}"), summary, _marked(section, evaluated_at))


def _status(requirements: Requirements, merge_group: bool) -> tuple[str, str]:
    missing = sum(approval.missing() for approval in requirements.approvals)
    waiting = [name for name, state in requirements.checks.items() if state in WAITING]
    failing = [name for name, state in requirements.checks.items() if state is CheckState.FAILED]
    waits = ([_count(missing, "approval")] if missing else []) + ([_count(len(waiting), "check")] if waiting else [])
    parts = (
        ([f"Waiting for {' and '.join(waits)}"] if waits else [])
        + ([f"{_count(len(failing), 'check')} failed"] if failing else [])
        + ([f"changes requested by {', '.join(requirements.blockers)}"] if requirements.blockers else [])
    )
    if not parts:
        return "success", "All requirements met"
    description = "; ".join(parts)
    state = "failure" if merge_group and failing else "pending"
    return state, _fit(description[0].upper() + description[1:])


def _requirements(results: Sequence[Result]) -> Requirements:
    shared: dict[frozenset[tuple[str, str]], list[Result]] = {}
    for result in results:
        if result.approvals is not None and result.instance.rule.minimum:
            same = frozenset((reviewer.kind, reviewer.label.casefold()) for reviewer in result.instance.reviewers())
            shared.setdefault(same, []).append(result)
    approvals = [
        ApprovalRequirement(
            group[0].instance.reviewers(),
            max(result.instance.rule.minimum for result in group),
            group[0].approvals.counted,
        )
        for group in shared.values()
    ]
    blockers = {
        login for result in results if result.approvals is not None for login in result.approvals.requested_changes
    }
    checks = {name: state for result in results for name, state in result.checks.items()}
    return Requirements(
        sorted(approvals, key=lambda approval: [reviewer.label.casefold() for reviewer in approval.reviewers]),
        sorted(blockers, key=str.casefold),
        dict(sorted(checks.items(), key=lambda check: check[0].casefold())),
    )


def _groups(requirements: Requirements) -> str:
    groups = {
        "Required approvals": [_approval_line(approval) for approval in requirements.approvals]
        + [f"- 🛑 `{login}` requested changes" for login in requirements.blockers],
        "Required checks": [_check_line(name, state) for name, state in requirements.checks.items()],
    }
    return "\n\n".join(f"### {title}\n" + "\n".join(lines) for title, lines in groups.items() if lines)


def _approval_line(approval: ApprovalRequirement) -> str:
    counted = len(approval.counted)
    reviewers = "/".join(f"`{reviewer.label}`" for reviewer in approval.reviewers)
    return f"- {'✅' if counted >= approval.minimum else '⏳'} {counted}/{approval.minimum} {reviewers} approved"


def _check_line(name: str, state: CheckState) -> str:
    icon, text = CHECK_LINES[state]
    return f"- {icon} `{name}` {text}"


def _marked(content: str, evaluated_at: datetime) -> str:
    return f"{MARKER}\n\n{_frame(TITLE, content, evaluated_at)}"


def _frame(title: str, content: str, evaluated_at: datetime) -> str:
    return f"## {title}\n\n{content}\n\n<sub>Last evaluated {evaluated_at:%Y-%m-%d %H:%M} UTC</sub>\n"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _fit(text: str) -> str:
    return text if len(text) <= DESCRIPTION_LIMIT else f"{text[: DESCRIPTION_LIMIT - 1]}…"
