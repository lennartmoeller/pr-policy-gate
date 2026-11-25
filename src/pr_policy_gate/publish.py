import os

from pr_policy_gate.github import GitHub
from pr_policy_gate.report import MARKER

STATUS_CONTEXT = "pr-policy-gate"


def set_status(github: GitHub, sha: str, state: str, description: str, target_url: str) -> None:
    body = {"state": state, "context": STATUS_CONTEXT, "description": description, "target_url": target_url}
    github.send("POST", f"/repos/{github.repository}/statuses/{sha}", body)


def update_comment(github: GitHub, number: int, evaluated_sha: str, body: str | None) -> str | None:
    if github.get(f"/repos/{github.repository}/pulls/{number}")["head"]["sha"] != evaluated_sha:
        return None
    comments = github.get_all(f"/repos/{github.repository}/issues/{number}/comments?per_page=100")
    existing = next(
        (
            comment
            for comment in comments
            if comment["user"]["login"] == github.app_login and comment["body"].startswith(MARKER)
        ),
        None,
    )
    if existing is None:
        if body is None:
            return None
        return github.send("POST", f"/repos/{github.repository}/issues/{number}/comments", {"body": body})["html_url"]
    path = f"/repos/{github.repository}/issues/comments/{existing['id']}"
    if body is None:
        github.send("DELETE", path)
        return None
    return github.send("PATCH", path, {"body": body})["html_url"]


def append_summary(text: str) -> None:
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
        file.write(text)
