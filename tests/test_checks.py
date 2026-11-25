import pytest
from fakes import responses

from pr_policy_gate.checks import CheckState, states


def run(name, status="completed", conclusion=None):
    return {"name": name, "status": status, "conclusion": conclusion}


def status(context, state):
    return {"context": context, "state": state}


@pytest.mark.parametrize(
    ("runs", "statuses", "state"),
    [
        ([run("Lint", conclusion="success")], [], CheckState.PASSED),
        ([run("Lint", conclusion="neutral")], [], CheckState.PASSED),
        ([run("Lint", conclusion="skipped")], [], CheckState.PASSED),
        ([run("Lint", conclusion="cancelled")], [], CheckState.FAILED),
        ([run("Lint", conclusion="timed_out")], [], CheckState.FAILED),
        ([run("Lint", status="in_progress")], [], CheckState.RUNNING),
        ([], [status("Lint", "success")], CheckState.PASSED),
        ([], [status("Lint", "error")], CheckState.FAILED),
        ([], [status("Lint", "pending")], CheckState.RUNNING),
        ([run("Lint", conclusion="success")], [status("Lint", "failure")], CheckState.FAILED),
        ([run("Lint", status="queued"), run("Lint", conclusion="success")], [], CheckState.RUNNING),
        ([run("Lint", status="queued")], [status("Lint", "failure")], CheckState.FAILED),
        ([run("Other", conclusion="success")], [status("Other", "success")], CheckState.MISSING),
    ],
)
def test_every_result_of_a_name_decides_its_state(client, runs, statuses, state):
    github, _ = client(
        responses(
            {
                "/repos/owner/repo/commits/sha/check-runs?filter=latest&per_page=100": {"check_runs": runs},
                "/repos/owner/repo/commits/sha/status?per_page=100": {"statuses": statuses},
            }
        )
    )

    assert states(github, "sha", ["Lint"]) == {"Lint": state}


def test_no_required_name_reads_nothing(client):
    github, transport = client({})

    assert states(github, "sha", []) == {}
    assert transport.requests == []
