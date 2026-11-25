import json
from pathlib import Path

import pytest
from fakes import responses

from pr_policy_gate.event import MergeGroup, PullRequests, targets

FIXTURES = {
    path.stem: json.loads(path.read_text())
    for path in sorted((Path(__file__).parent / "fixtures" / "event").glob("*.json"))
}


def expected_targets(expected):
    if expected is None:
        return None
    if "merge_group" in expected:
        return MergeGroup(**expected["merge_group"])
    return PullRequests(tuple(expected["pull_requests"]), expected["forks_are_errors"])


@pytest.mark.parametrize("name", FIXTURES)
def test_targets_follow_the_event_table(name, client):
    fixture = FIXTURES[name]
    github, _ = client(responses(fixture["api"]))

    assert targets(fixture["event"], fixture["payload"], github, "main") == expected_targets(fixture["expected"])


@pytest.mark.parametrize("modified", [[], ["README.md"], [".github/pr-policy-gate/platform-approval.jq"]])
def test_every_default_branch_push_reevaluates_open_pulls_regardless_of_changed_files(modified, client):
    fixture = FIXTURES["push-to-default-branch"]
    payload = fixture["payload"] | {"commits": [{"modified": modified}]}
    github, _ = client(responses(fixture["api"]))

    assert targets("push", payload, github, "main") == PullRequests((4, 9), forks_are_errors=False)
