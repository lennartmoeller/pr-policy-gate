import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from pr_policy_gate.conditions import Instance
from pr_policy_gate.config import Reviewer
from pr_policy_gate.github import GitHub, GitHubError

DECISIVE_STATES = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}

type TeamMembers = dict[str, set[str]]


class TeamNotReadableError(Exception):
    pass


class StaleApprovalsKeptError(Exception):
    pass


def require_stale_dismissal(github: GitHub, branch: str) -> None:
    rules = github.get_all(f"/repos/{github.repository}/rules/branches/{branch}?per_page=100")
    if not any(
        rule["type"] == "pull_request" and rule["parameters"]["dismiss_stale_reviews_on_push"] for rule in rules
    ):
        raise StaleApprovalsKeptError(
            f"No ruleset on `{branch}` dismisses stale approvals, which the gate relies on; "
            "set `dismiss_stale_reviews_on_push` in a `pull_request` rule."
        )


@dataclass(frozen=True)
class Approvals:
    counted: tuple[str, ...]
    requested_changes: tuple[str, ...]


@dataclass(frozen=True)
class UncountedApproval:
    login: str
    reason: str


def parse_team_members(text: str) -> TeamMembers | None:
    if not text:
        return None
    entries = json.loads(text)
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict)
        and isinstance(entry.get("team"), str)
        and isinstance(entry.get("logins"), list)
        and all(isinstance(login, str) for login in entry["logins"])
        for entry in entries
    ):
        raise ValueError(
            "The input `team-members` must be a JSON array of objects, each with a `team` as `<organization>/<team>` "
            "and a list of `logins`."
        )
    members: TeamMembers = {}
    for entry in entries:
        members.setdefault(entry["team"].casefold(), set()).update(login.casefold() for login in entry["logins"])
    return members


def evaluate(
    github: GitHub, pull: dict[str, Any], instances: Sequence[Instance], team_members: TeamMembers | None = None
) -> tuple[dict[Instance, Approvals], list[UncountedApproval]]:
    reviews = github.get_all(f"/repos/{github.repository}/pulls/{pull['number']}/reviews?per_page=100")
    latest = {review["user"]["login"]: review["state"] for review in reviews if review["state"] in DECISIVE_STATES}
    author = pull["user"]["login"]
    pusher = _latest_pusher(github, pull)
    author_pushed_last = pusher is not None and pusher.casefold() == author.casefold()
    members: dict[str, set[str]] = {}

    def eligible(reviewer: Reviewer, login: str) -> bool:
        if reviewer.kind == "team":
            if reviewer.label not in members:
                members[reviewer.label] = _team_members(github, reviewer, team_members)
            return login.casefold() in members[reviewer.label]
        return login.casefold() == reviewer.name.casefold()

    def reviewed(state: str, reviewers: tuple[Reviewer, ...]) -> list[str]:
        return [
            login
            for login, latest_state in latest.items()
            if latest_state == state and any(eligible(reviewer, login) for reviewer in reviewers)
        ]

    approved = {}
    author_in_a_team = False
    for instance in instances:
        reviewers = instance.reviewers()
        author_counts = any(reviewer.kind == "team" and eligible(reviewer, author) for reviewer in reviewers)
        author_in_a_team |= author_counts
        counted = reviewed("APPROVED", reviewers) + ([author] if author_counts and author_pushed_last else [])
        approved[instance] = Approvals(
            counted=tuple(sorted(set(counted), key=str.casefold)),
            requested_changes=tuple(sorted(reviewed("CHANGES_REQUESTED", reviewers), key=str.casefold)),
        )

    counted_anywhere = {login for approvals in approved.values() for login in approvals.counted}
    uncounted = [
        UncountedApproval(login, "dismissed" if state == "DISMISSED" else "not a reviewer of any matched rule")
        for login, state in latest.items()
        if state == "DISMISSED" or (state == "APPROVED" and login not in counted_anywhere)
    ]
    if author_in_a_team and not author_pushed_last:
        reason = "someone else made the latest push" if pusher else "the latest push is not on record"
        uncounted.append(UncountedApproval(author, reason))
    return approved, sorted(uncounted, key=lambda approval: approval.login.casefold())


def _latest_pusher(github: GitHub, pull: dict[str, Any]) -> str | None:
    ref = quote(f"refs/heads/{pull['head']['ref']}", safe="/")
    activity = github.get(f"/repos/{github.repository}/activity?ref={ref}&per_page=100")
    return next((entry["actor"]["login"] for entry in activity if entry["after"] == pull["head"]["sha"]), None)


def _team_members(github: GitHub, team: Reviewer, passed: TeamMembers | None) -> set[str]:
    org = team.organization or github.owner
    if passed is not None:
        key = f"{org}/{team.name}".casefold()
        if key not in passed:
            raise TeamNotReadableError(
                f"Team `{team.label}` is not readable: the input `team-members` has no entry for `{key}`."
            )
        return passed[key]
    try:
        members = github.get_all(f"/orgs/{org}/teams/{team.name}/members?per_page=100", org=org)
    except GitHubError as error:
        raise TeamNotReadableError(f"Team `{team.label}` is not readable: {error}.") from error
    return {member["login"].casefold() for member in members}
