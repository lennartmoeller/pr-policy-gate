import pytest
from fakes import API, responses

from pr_policy_gate.approvals import (
    Approvals,
    TeamNotReadableError,
    UncountedApproval,
    evaluate,
    parse_team_members,
)
from pr_policy_gate.conditions import Instance
from pr_policy_gate.config import Reviewer, Rule

PULL = {"number": 7, "user": {"login": "alice"}, "head": {"ref": "feature/x", "sha": "head"}}
MEMBERS = {
    "/orgs/owner/teams/maintainers/members?per_page=100": ["alice", "Bob", "carol", "erin", "frank"],
    "/orgs/other/teams/owners/members?per_page=100": ["carol"],
    "/orgs/owner/teams/ent:acme-platform-owner/members?per_page=100": ["dave"],
}


def reviewed(client, reviews, pusher="alice", routes=None):
    pushes = [{"after": "head", "actor": {"login": pusher}}] if pusher else []
    bodies = {
        "/repos/owner/repo/pulls/7/reviews?per_page=100": [
            {"user": {"login": login}, "state": state} for login, state in reviews
        ],
        "/repos/owner/repo/activity?ref=refs/heads/feature/x&per_page=100": [
            *pushes,
            {"after": "older", "actor": {"login": "zoe"}},
        ],
    } | {path: [{"login": login} for login in logins] for path, logins in MEMBERS.items()}
    return client(responses(bodies) | (routes or {}))


def team_reads(transport):
    return [
        (request.full_url.removeprefix(API), request.get_header("Authorization"))
        for request in transport.requests
        if "/teams/" in request.full_url
    ]


def instance(*reviewers, name="Rule"):
    return Instance(Rule(name, None, None, 1, tuple(Reviewer(*reviewer) for reviewer in reviewers), ()), None)


MAINTAINERS = instance(("team", "maintainers"))


def test_each_reviewer_counts_with_the_latest_review_that_decides(client):
    reviews = [
        ("Bob", "APPROVED"),
        ("Bob", "COMMENTED"),
        ("carol", "CHANGES_REQUESTED"),
        ("carol", "APPROVED"),
        ("erin", "APPROVED"),
        ("erin", "CHANGES_REQUESTED"),
        ("frank", "DISMISSED"),
    ]
    github, _ = reviewed(client, reviews)

    results, uncounted = evaluate(github, PULL, [MAINTAINERS])

    assert results == {MAINTAINERS: Approvals(counted=("alice", "Bob", "carol"), requested_changes=("erin",))}
    assert uncounted == [UncountedApproval("frank", "dismissed")]


@pytest.mark.parametrize(
    ("pusher", "reason"),
    [("Bob", "someone else made the latest push"), (None, "the latest push is not on record")],
)
def test_the_author_counts_only_while_they_made_the_latest_push(client, pusher, reason):
    github, _ = reviewed(client, [("Bob", "APPROVED")], pusher=pusher)

    results, uncounted = evaluate(github, PULL, [MAINTAINERS])

    assert results[MAINTAINERS].counted == ("Bob",)
    assert uncounted == [UncountedApproval("alice", reason)]


def test_one_approval_counts_for_every_rule_and_outsiders_are_listed(client):
    by_user = instance(("user", "BOB"), name="User")
    github, transport = reviewed(client, [("Bob", "APPROVED"), ("gina", "APPROVED")])

    results, uncounted = evaluate(github, PULL, [MAINTAINERS, by_user, instance(("team", "maintainers"), name="Again")])

    assert results[MAINTAINERS].counted == ("alice", "Bob")
    assert results[by_user].counted == ("Bob",)
    assert uncounted == [UncountedApproval("gina", "not a reviewer of any matched rule")]
    assert team_reads(transport) == [("/orgs/owner/teams/maintainers/members?per_page=100", "Bearer t-owner")]


def test_apps_and_teams_of_other_organizations_count(client):
    mixed = instance(("app", "some-app[bot]"), ("team", "owners", "other"))
    github, transport = reviewed(client, [("some-app[bot]", "APPROVED"), ("carol", "APPROVED")])

    results, _ = evaluate(github, PULL, [mixed])

    assert results[mixed].counted == ("carol", "some-app[bot]")
    assert team_reads(transport) == [("/orgs/other/teams/owners/members?per_page=100", "Bearer t-other")]


def test_passed_team_members_count_and_no_team_or_foreign_organization_is_read(client):
    teams = instance(("team", "owners", "other"), ("team", "maintainers"), ("team", "empty"))
    members = parse_team_members(
        '[{"team": "OTHER/owners", "logins": ["Carol"]}, {"team": "owner/maintainers", "logins": ["alice"]},'
        ' {"team": "owner/empty", "logins": []}, {"team": "Owner/Maintainers", "logins": ["BOB"]}]'
    )
    github, transport = reviewed(client, [("bob", "APPROVED"), ("carol", "APPROVED"), ("dave", "APPROVED")])

    results, uncounted = evaluate(github, PULL, [teams], members)

    assert results[teams].counted == ("alice", "bob", "carol")
    assert uncounted == [UncountedApproval("dave", "not a reviewer of any matched rule")]
    assert team_reads(transport) == []
    assert not [request for request in transport.requests if "/orgs/other/installation" in request.full_url]


def test_a_team_the_passed_members_do_not_name_is_an_error_naming_it(client):
    github, _ = reviewed(client, [])

    with pytest.raises(TeamNotReadableError) as raised:
        evaluate(
            github,
            PULL,
            [instance(("team", "maintainers"))],
            parse_team_members('[{"team": "owner/other", "logins": []}]'),
        )

    assert str(raised.value) == (
        "Team `maintainers` is not readable: the input `team-members` has no entry for `owner/maintainers`."
    )


@pytest.mark.parametrize(
    "text",
    [
        "{}",
        '["owner/maintainers"]',
        '[{"logins": ["alice"]}]',
        '[{"team": "owner/maintainers", "logins": "alice"}]',
        '[{"team": "owner/maintainers", "logins": [1]}]',
    ],
)
def test_passed_team_members_must_be_teams_with_logins(text):
    with pytest.raises(ValueError, match="array of objects, each with a `team` as `<organization>/<team>`"):
        parse_team_members(text)


def test_an_empty_input_passes_no_team_members():
    assert parse_team_members("") is None


def test_an_enterprise_team_is_read_through_the_repository_organization_with_an_unencoded_colon(client):
    enterprise = instance(("team", "ent:acme-platform-owner"))
    github, transport = reviewed(client, [("dave", "APPROVED")])

    results, _ = evaluate(github, PULL, [enterprise])

    assert results[enterprise].counted == ("dave",)
    assert team_reads(transport) == [
        ("/orgs/owner/teams/ent:acme-platform-owner/members?per_page=100", "Bearer t-owner")
    ]


def test_an_unreadable_team_is_an_error_naming_it(client):
    github, _ = reviewed(
        client, [], routes={("GET", "/orgs/acme-gone/installation"): (404, {"message": "Not Found"}, {})}
    )

    with pytest.raises(TeamNotReadableError) as raised:
        evaluate(github, PULL, [instance(("team", "acme-gone-owner", "acme-gone"))])

    assert str(raised.value) == (
        "Team `acme-gone/acme-gone-owner` is not readable: "
        + "GET /orgs/acme-gone/installation failed with HTTP 404 Not Found."
    )
