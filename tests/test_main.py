import ast
import json
import runpy
from pathlib import Path

import pytest
from fakes import API, INSTALLATIONS, FakeTransport, commit, contents, git, init_remote, responses

from pr_policy_gate.__main__ import main

REPO = "/repos/owner/repo"
CONFIG = """rules:
  - name: Baseline
    required_approvals:
      reviewers:
        - team: maintainers
    required_status_checks: [Lint]
"""
PACKAGE = Path(__file__).parents[1] / "src" / "pr_policy_gate"
CONFIG_PATH = ".github/pr-policy-gate.yml"


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / "server" / "owner" / "repo.git"
    repo.mkdir(parents=True)
    init_remote(repo)
    (repo / "README.md").write_text("readme\n")
    base = commit(repo, "base")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("app\n")
    return {"server": tmp_path / "server", "base": base, "head": commit(repo, "feature")}


def routes(source, config=CONFIG, pull=None, dismisses_stale_approvals=True, config_path=CONFIG_PATH):
    base, head = source["base"], source["head"]
    pull = {
        "number": 7,
        "state": "open",
        "user": {"login": "alice"},
        "base": {"ref": "main"},
        "head": {"sha": head, "ref": "feature", "repo": {"full_name": "owner/repo"}},
    } | (pull or {})
    rule = {"type": "pull_request", "parameters": {"dismiss_stale_reviews_on_push": dismisses_stale_approvals}}
    lint = {"name": "Lint", "status": "completed", "conclusion": "success"}
    reads = {
        "/app": {"slug": "gate-app"},
        f"{REPO}/pulls/7": pull,
        f"{REPO}/git/ref/heads/main": {"object": {"sha": base}},
        f"{REPO}/rules/branches/main?per_page=100": [rule],
        f"{REPO}/compare/{base}...{head}": {"merge_base_commit": {"sha": base}},
        f"{REPO}/pulls/7/reviews?per_page=100": [],
        f"{REPO}/activity?ref=refs/heads/feature&per_page=100": [{"after": head, "actor": {"login": "alice"}}],
        "/orgs/owner/teams/maintainers/members?per_page=100": [{"login": "alice"}],
        f"{REPO}/commits/{head}/check-runs?filter=latest&per_page=100": {"check_runs": [lint]},
        f"{REPO}/commits/{head}/status?per_page=100": {"statuses": []},
        f"{REPO}/issues/7/comments?per_page=100": [],
    }
    content = contents(config) if config is not None else (404, {"message": "Not Found"}, {})
    return (
        INSTALLATIONS
        | responses(reads)
        | {
            ("GET", f"{REPO}/contents/{config_path}?ref={base}"): content,
            ("POST", f"{REPO}/issues/7/comments"): (201, {"html_url": "https://comment"}, {}),
            ("POST", f"{REPO}/statuses/{head}"): (201, {}, {}),
        }
    )


@pytest.fixture
def environment(tmp_path, monkeypatch, key_pair, source):
    def configure(event_name, payload):
        event_file = tmp_path / "event.json"
        event_file.write_text(
            json.dumps(
                payload
                | {"repository": {"full_name": "owner/repo", "owner": {"login": "owner"}, "default_branch": "main"}}
            )
        )
        (tmp_path / "runner").mkdir(exist_ok=True)
        for name, value in {
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_EVENT_PATH": str(event_file),
            "GITHUB_SERVER_URL": f"file://{source['server']}",
            "GITHUB_API_URL": API,
            "GITHUB_RUN_ID": "1",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
            "RUNNER_TEMP": str(tmp_path / "runner"),
            "INPUT_CLIENT_ID": "Iv23test",
            "INPUT_APP_PRIVATE_KEY": key_pair[0],
            "INPUT_TEAM_MEMBERS": "",
            "INPUT_CONFIG_PATH": CONFIG_PATH,
        }.items():
            monkeypatch.setenv(name, value)
        return tmp_path / "summary.md"

    return configure


def opened(number=7):
    return {"action": "opened", "pull_request": {"number": number}}


def queued(source):
    return {"action": "checks_requested", "merge_group": {"head_sha": source["head"], "base_sha": source["base"]}}


def statuses(transport):
    return [
        (url.rsplit("/", 1)[1], body["state"], body["description"])
        for url, body in transport.sent("POST")
        if "/statuses/" in url
    ]


def test_a_pull_request_meeting_its_rules_gets_success_a_comment_and_a_summary(environment, source, capsys):
    summary = environment("pull_request_target", opened())
    transport = FakeTransport(routes(source))

    assert main(transport) == 0
    assert capsys.readouterr().out == f"::add-mask::t-owner\nEvaluating #7 at {source['head']}.\n"
    assert statuses(transport) == [(source["head"], "success", "All requirements met")]
    ((_, comment),) = [sent for sent in transport.sent("POST") if sent[0].endswith("/comments")]
    assert comment["body"].startswith(
        "<!-- pr-policy-gate -->\n\n## Pull Request Policy Gate\n\n"
        "### Required approvals\n- ✅ 1/1 `maintainers` approved\n\n### Required checks\n- ✅ `Lint` passed\n\n"
    )
    assert summary.read_text().startswith("## Pull Request Policy Gate · #7\n\n### Required approvals\n")
    assert list((summary.parent / "runner").iterdir()) == []


def test_passed_team_members_count_without_reading_the_team_from_github(environment, source, monkeypatch):
    environment("pull_request_target", opened())
    monkeypatch.setenv("INPUT_TEAM_MEMBERS", '[{"team": "Owner/Maintainers", "logins": ["Alice"]}]')
    reads = routes(source)
    del reads[("GET", "/orgs/owner/teams/maintainers/members?per_page=100")]
    transport = FakeTransport(reads)

    assert main(transport) == 0
    assert statuses(transport) == [(source["head"], "success", "All requirements met")]


def test_a_script_reads_the_merge_base_without_executing_head_or_comparison_code(environment, source):
    repo = source["server"] / "owner" / "repo.git"
    script = repo / ".github" / "probe.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\nexit 2\n")
    script.chmod(0o755)
    head = commit(repo, "untrusted head script")
    git(repo, "switch", "--quiet", "--detach", source["base"])
    script.parent.mkdir()
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'test -f "$PWD/trusted-only.txt"\n'
        'test ! -e "$PR_POLICY_GATE_DIFF_BASE_DIR/trusted-only.txt"\n'
        'test ! -e "$PR_POLICY_GATE_DIFF_BASE_DIR/src/app.py"\n'
        'test "$(cat "$PR_POLICY_GATE_DIFF_BASE_DIR/README.md")" = readme\n'
        'test -f "$PR_POLICY_GATE_HEAD_DIR/src/app.py"\n'
        'test ! -e "$PR_POLICY_GATE_HEAD_DIR/trusted-only.txt"\n'
    )
    script.chmod(0o755)
    (repo / "trusted-only.txt").write_text("trusted\n")
    trusted = commit(repo, "trusted control code")
    config = CONFIG.replace("name: Baseline\n", "name: Baseline\n    when:\n      script: .github/probe.sh\n")
    revisions = source | {"base": trusted, "head": head}
    environment("pull_request_target", opened())
    transport = FakeTransport(
        routes(revisions, config=config)
        | responses({f"{REPO}/compare/{trusted}...{head}": {"merge_base_commit": {"sha": source["base"]}}})
    )

    assert main(transport) == 0
    assert statuses(transport) == [(head, "success", "All requirements met")]


@pytest.mark.parametrize(
    ("overrides", "description", "comment_line"),
    [
        (
            {"config": "rules: []\n"},
            f"Error: invalid {CONFIG_PATH} on main",
            "- `rules`: is empty.",
        ),
        (
            {"config": None},
            f"Error: invalid {CONFIG_PATH} on main",
            "- The file does not exist.",
        ),
        (
            {"dismisses_stale_approvals": False},
            "Error: No ruleset on main dismisses stale approvals, which the gate relies on; "
            "set dismiss_stale_reviews_on_push in a pull_request rule.",
            "- No ruleset on `main` dismisses stale approvals",
        ),
        (
            {"pull": {"head": {"sha": "unused", "ref": "feature", "repo": {"full_name": "fork/repo"}}}},
            "Error: fork pull requests are not supported",
            "### ⚠️ Fork pull requests are not supported",
        ),
    ],
)
def test_an_evaluation_error_sets_error_and_explains_it_in_the_comment(
    environment, source, overrides, description, comment_line
):
    environment("pull_request_target", opened())
    transport = FakeTransport(routes(source, **overrides) | {("POST", f"{REPO}/statuses/unused"): (201, {}, {})})

    assert main(transport) == 1
    ((_, state, found),) = statuses(transport)
    assert (state, found) == ("error", description)
    ((_, comment),) = [sent for sent in transport.sent("POST") if sent[0].endswith("/comments")]
    assert comment_line in comment["body"]


@pytest.mark.parametrize(
    ("config", "description"),
    [(CONFIG, "All requirements met"), ("rules: []\n", "Error: invalid policy/gate.yml on main")],
)
def test_the_config_path_input_names_the_file_that_is_read_and_reported(
    environment, source, monkeypatch, config, description
):
    environment("pull_request_target", opened())
    monkeypatch.setenv("INPUT_CONFIG_PATH", "policy/gate.yml")
    transport = FakeTransport(routes(source, config=config, config_path="policy/gate.yml"))

    main(transport)

    assert [found for *_, found in statuses(transport)] == [description]


def test_a_closed_pull_request_gets_no_output(environment, source, capsys):
    environment("pull_request_target", opened())
    transport = FakeTransport(routes(source, pull={"state": "closed"}))

    assert main(transport) == 0
    assert transport.sent("POST") == [("/app/installations/1/access_tokens", None)]
    assert capsys.readouterr().out == "::add-mask::t-owner\n#7 is closed or targets another branch than main.\n"


def test_a_fork_review_run_which_gets_no_secrets_exits_without_a_request(environment, monkeypatch, capsys):
    fork = {"number": 7, "head": {"repo": {"full_name": "fork/repo"}}}
    environment("pull_request_review", {"action": "submitted", "pull_request": fork})
    monkeypatch.setenv("INPUT_APP_PRIVATE_KEY", "")
    transport = FakeTransport({})

    assert main(transport) == 0
    assert transport.requests == []
    assert capsys.readouterr().out == "This event needs no evaluation.\n"


def test_a_default_branch_push_skips_a_fork_pull_request_without_an_error(environment, source, capsys):
    environment("push", {"ref": "refs/heads/main"})
    fork = {"head": {"sha": "unused", "ref": "feature", "repo": {"full_name": "fork/repo"}}}
    open_pulls = responses({f"{REPO}/pulls?state=open&base=main&per_page=100": [{"number": 7}]})
    transport = FakeTransport(routes(source, pull=fork) | open_pulls)

    assert main(transport) == 0
    assert transport.sent("POST") == [("/app/installations/1/access_tokens", None)]
    assert capsys.readouterr().out == "::add-mask::t-owner\n#7 comes from a fork.\n"


def test_a_failing_git_command_is_reported_with_its_error_output(environment, source):
    environment("pull_request_target", opened())
    transport = FakeTransport(routes(source | {"head": "0" * 40}))

    assert main(transport) == 1
    ((_, comment),) = [sent for sent in transport.sent("POST") if sent[0].endswith("/comments")]
    assert "### ⚠️ Evaluation failed\n- Command '['git', 'fetch'," in comment["body"]
    assert "returned non-zero exit status 128. fatal:" in comment["body"]


def test_a_merge_group_gets_a_status_and_a_summary_but_no_comment(environment, source, capsys):
    head = source["head"]
    summary = environment("merge_group", queued(source))
    transport = FakeTransport(routes(source))

    assert main(transport) == 0
    assert capsys.readouterr().out == f"Evaluating the merge group at {head}.\n::add-mask::t-owner\n"
    assert statuses(transport) == [(head, "success", "All requirements met")]
    assert not [sent for sent in transport.sent("POST") if sent[0].endswith("/comments")]
    assert summary.read_text().startswith(
        f"## Pull Request Policy Gate · merge group `{head[:7]}`\n\n### Required checks\n- ✅ `Lint` passed\n"
    )


def test_a_merge_group_with_an_invalid_configuration_gets_the_error_status(environment, source):
    environment("merge_group", queued(source))
    transport = FakeTransport(routes(source, config="rules: []\n"))

    assert main(transport) == 1
    assert statuses(transport) == [(source["head"], "error", f"Error: invalid {CONFIG_PATH} on main")]


def test_a_merge_group_whose_checks_cannot_be_read_gets_the_error_status(environment, source):
    environment("merge_group", queued(source))
    checks = f"{REPO}/commits/{source['head']}/check-runs?filter=latest&per_page=100"
    transport = FakeTransport(routes(source) | {("GET", checks): (500, {"message": "Server Error"}, {})})

    assert main(transport) == 1
    ((_, state, description),) = statuses(transport)
    assert state == "error"
    assert description.startswith("Error: GET /repos/owner/repo/commits/")


def test_the_module_exits_with_the_result_of_main_when_it_runs_as_the_entrypoint(environment):
    environment("workflow_dispatch", {})

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(PACKAGE / "__main__.py"), run_name="__main__")

    assert raised.value.code == 0


def test_every_package_module_is_reachable_from_the_action_entrypoint():
    modules = {path.stem: path for path in PACKAGE.glob("*.py")}
    imports = {name: set() for name in modules}
    for name, path in modules.items():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imports[name] |= {
                    alias.name.removeprefix("pr_policy_gate.").split(".")[0]
                    for alias in node.names
                    if alias.name.startswith("pr_policy_gate.")
                }
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "pr_policy_gate":
                    imports[name] |= {alias.name for alias in node.names if alias.name in modules}
                elif node.module.startswith("pr_policy_gate."):
                    imports[name].add(node.module.removeprefix("pr_policy_gate.").split(".")[0])

    reachable = set()
    pending = ["__main__"]
    while pending:
        module = pending.pop()
        if module not in reachable:
            reachable.add(module)
            pending.extend(imports[module] - reachable)

    assert reachable == modules.keys()
