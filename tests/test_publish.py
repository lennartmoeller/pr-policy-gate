import pytest
from fakes import responses

from pr_policy_gate.publish import append_summary, set_status, update_comment
from pr_policy_gate.report import MARKER

WRITES = {
    ("POST", "/repos/owner/repo/statuses/sha"): (201, {}, {}),
    ("POST", "/repos/owner/repo/issues/7/comments"): (201, {"html_url": "https://comment"}, {}),
    ("PATCH", "/repos/owner/repo/issues/comments/3"): (200, {"html_url": "https://comment"}, {}),
    ("DELETE", "/repos/owner/repo/issues/comments/3"): (204, None, {}),
}


def pull_request(client, head="evaluated", comments=()):
    reads = {
        "/repos/owner/repo/pulls/7": {"head": {"sha": head}},
        "/repos/owner/repo/issues/7/comments?per_page=100": list(comments),
        "/app": {"slug": "gate-app"},
    }
    return client(responses(reads) | WRITES)


def writes(transport):
    return [
        (method, path, body)
        for method in ("POST", "PATCH", "DELETE")
        for path, body in transport.sent(method)
        if not path.endswith("/access_tokens")
    ]


def own(comment_id, body=f"{MARKER}\nold"):
    return {"id": comment_id, "user": {"login": "gate-app[bot]"}, "body": body}


def test_the_status_carries_the_gate_context(client):
    github, transport = client(WRITES)

    set_status(github, "sha", "pending", "Waiting for 1 approval", "https://comment")

    assert writes(transport) == [
        (
            "POST",
            "/repos/owner/repo/statuses/sha",
            {
                "state": "pending",
                "context": "pr-policy-gate",
                "description": "Waiting for 1 approval",
                "target_url": "https://comment",
            },
        )
    ]


def test_a_moved_head_leaves_the_comment_unchanged(client):
    github, transport = pull_request(client, head="newer", comments=[own(3)])

    assert update_comment(github, 7, "evaluated", f"{MARKER}\nnew") is None
    assert writes(transport) == []


@pytest.mark.parametrize(
    ("comments", "body", "sent", "url"),
    [
        (
            [],
            f"{MARKER}\nnew",
            [("POST", "/repos/owner/repo/issues/7/comments", {"body": f"{MARKER}\nnew"})],
            "https://comment",
        ),
        (
            [{"id": 1, "user": {"login": "someone"}, "body": f"{MARKER}\nfake"}, own(2, "unmarked"), own(3)],
            f"{MARKER}\nnew",
            [("PATCH", "/repos/owner/repo/issues/comments/3", {"body": f"{MARKER}\nnew"})],
            "https://comment",
        ),
        ([own(3)], None, [("DELETE", "/repos/owner/repo/issues/comments/3", None)], None),
        ([], None, [], None),
    ],
)
def test_the_app_creates_updates_or_deletes_only_its_own_marked_comment(client, comments, body, sent, url):
    github, transport = pull_request(client, comments=comments)

    assert update_comment(github, 7, "evaluated", body) == url
    assert writes(transport) == sent


def test_append_summary_adds_to_the_step_summary(tmp_path, monkeypatch):
    summary_file = tmp_path / "summary.md"
    summary_file.write_text("before\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    append_summary("## Pull Request Policy Gate · #7\n")

    assert summary_file.read_text() == "before\n## Pull Request Policy Gate · #7\n"
