from dataclasses import dataclass
from typing import Any

from pr_policy_gate.github import GitHub


@dataclass(frozen=True)
class PullRequests:
    numbers: tuple[int, ...]
    forks_are_errors: bool


@dataclass(frozen=True)
class MergeGroup:
    sha: str
    base_sha: str


def targets(
    event_name: str, payload: dict[str, Any], github: GitHub, default_branch: str
) -> PullRequests | MergeGroup | None:
    match event_name:
        case "pull_request_target":
            return PullRequests((payload["pull_request"]["number"],), forks_are_errors=True)
        case "pull_request_review" if not from_fork(payload["pull_request"], github.repository):
            return PullRequests((payload["pull_request"]["number"],), forks_are_errors=False)
        case "merge_group":
            return MergeGroup(payload["merge_group"]["head_sha"], payload["merge_group"]["base_sha"])
        case "workflow_run" | "check_run" | "status":
            sha, branches = _commit(event_name, payload)
            if any(branch.startswith(f"gh-readonly-queue/{default_branch}/") for branch in branches):
                return MergeGroup(sha, github.get(f"/repos/{github.repository}/commits/{sha}")["parents"][0]["sha"])
            pulls = github.get_all(f"/repos/{github.repository}/commits/{sha}/pulls?per_page=100")
            return _pull_requests(pull for pull in pulls if pull["state"] == "open" and pull["head"]["sha"] == sha)
        case "push" if payload["ref"] == f"refs/heads/{default_branch}":
            return _pull_requests(
                github.get_all(f"/repos/{github.repository}/pulls?state=open&base={default_branch}&per_page=100")
            )
    return None


def from_fork(pull: dict[str, Any], repository: str) -> bool:
    return pull["head"]["repo"] is None or pull["head"]["repo"]["full_name"] != repository


def _commit(event_name: str, payload: dict[str, Any]) -> tuple[str, list[str]]:
    if event_name == "workflow_run":
        return payload["workflow_run"]["head_sha"], [payload["workflow_run"]["head_branch"] or ""]
    if event_name == "check_run":
        return payload["check_run"]["head_sha"], [payload["check_run"]["check_suite"]["head_branch"] or ""]
    return payload["sha"], [branch["name"] for branch in payload["branches"]]


def _pull_requests(pulls: Any) -> PullRequests | None:
    numbers = tuple(sorted(pull["number"] for pull in pulls))
    return PullRequests(numbers, forks_are_errors=False) if numbers else None
