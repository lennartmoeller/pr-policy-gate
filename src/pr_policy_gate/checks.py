from collections.abc import Collection
from enum import Enum, auto

from pr_policy_gate.github import GitHub

PASSING_CONCLUSIONS = {"success", "neutral", "skipped"}


class CheckState(Enum):
    PASSED = auto()
    FAILED = auto()
    RUNNING = auto()
    MISSING = auto()


def states(github: GitHub, sha: str, names: Collection[str]) -> dict[str, CheckState]:
    if not names:
        return {}
    commit = f"/repos/{github.repository}/commits/{sha}"
    found: dict[str, list[CheckState]] = {name: [] for name in names}
    for run in github.get_all(f"{commit}/check-runs?filter=latest&per_page=100", key="check_runs"):
        if run["name"] in found:
            found[run["name"]].append(_run_state(run))
    for status in github.get_all(f"{commit}/status?per_page=100", key="statuses"):
        if status["context"] in found:
            found[status["context"]].append(_status_state(status))
    return {name: _combined(results) for name, results in found.items()}


def _run_state(run: dict) -> CheckState:
    if run["status"] != "completed":
        return CheckState.RUNNING
    return CheckState.PASSED if run["conclusion"] in PASSING_CONCLUSIONS else CheckState.FAILED


def _status_state(status: dict) -> CheckState:
    return {"success": CheckState.PASSED, "pending": CheckState.RUNNING}.get(status["state"], CheckState.FAILED)


def _combined(results: list[CheckState]) -> CheckState:
    for state in (CheckState.FAILED, CheckState.RUNNING):
        if state in results:
            return state
    return CheckState.PASSED if results else CheckState.MISSING
