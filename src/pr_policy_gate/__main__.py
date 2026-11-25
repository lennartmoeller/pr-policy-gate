import json
import os
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pr_policy_gate import approvals, changes, checks, conditions, config, event, publish, report
from pr_policy_gate.github import GitHub, Transport, network_transport


@dataclass(frozen=True)
class Run:
    github: GitHub
    team_members: approvals.TeamMembers | None
    default_branch: str
    config_path: str
    scratch: Path
    git_url: str
    url: str


def main(transport: Transport = network_transport) -> int:
    payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    repository = payload["repository"]
    repository_url = f"{os.environ['GITHUB_SERVER_URL']}/{repository['full_name']}"
    with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as directory:
        scratch = Path(directory)
        github = GitHub(
            os.environ["GITHUB_API_URL"],
            repository["full_name"],
            repository["owner"]["login"],
            os.environ["INPUT_CLIENT_ID"],
            os.environ["INPUT_APP_PRIVATE_KEY"],
            scratch,
            transport,
        )
        run = Run(
            github,
            approvals.parse_team_members(os.environ["INPUT_TEAM_MEMBERS"]),
            repository["default_branch"],
            os.environ["INPUT_CONFIG_PATH"],
            scratch,
            f"{repository_url}.git",
            f"{repository_url}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        )
        target = event.targets(os.environ["GITHUB_EVENT_NAME"], payload, github, run.default_branch)
        if isinstance(target, event.MergeGroup):
            return 0 if evaluate_merge_group(run, target) else 1
        if isinstance(target, event.PullRequests):
            succeeded = [evaluate_pull_request(run, number, target.forks_are_errors) for number in target.numbers]
            return 0 if all(succeeded) else 1
        print("This event needs no evaluation.")
        return 0


def evaluate_pull_request(run: Run, number: int, forks_are_errors: bool) -> bool:
    github, subject, now = run.github, f"#{number}", datetime.now(UTC)
    pull = github.get(f"/repos/{github.repository}/pulls/{number}")
    head = pull["head"]["sha"]
    if pull["state"] != "open" or pull["base"]["ref"] != run.default_branch:
        print(f"{subject} is closed or targets another branch than {run.default_branch}.")
        return True
    if event.from_fork(pull, github.repository):
        if not forks_are_errors:
            print(f"{subject} comes from a fork.")
            return True
        failure = report.FORK_NOT_SUPPORTED
    else:
        print(f"Evaluating {subject} at {head}.")
        try:
            trusted = github.get(f"/repos/{github.repository}/git/ref/heads/{run.default_branch}")["object"]["sha"]
            approvals.require_stale_dismissal(github, run.default_branch)
            merge_base = changes.merge_base(github, trusted, head)
            instances = _matched_instances(run, trusted, merge_base, head, f"pull-{number}")
            approved, uncounted = approvals.evaluate(github, pull, instances, run.team_members)
            results = _results(github, head, instances, approved)
            _publish(run, head, report.evaluated(subject, results, uncounted, now), number)
            return True
        except config.InvalidConfigError as error:
            failure = report.invalid_config(run.config_path, error.violations, run.default_branch, trusted)
        except Exception as error:
            failure = _logged_failure(error)
    _publish(run, head, report.failed(subject, failure, now), number)
    return False


def evaluate_merge_group(run: Run, group: event.MergeGroup) -> bool:
    subject, now = f"merge group `{group.sha[:7]}`", datetime.now(UTC)
    print(f"Evaluating the merge group at {group.sha}.")
    try:
        instances = _matched_instances(run, group.base_sha, group.base_sha, group.sha, f"group-{group.sha}")
        results = _results(run.github, group.sha, instances, {})
        _publish(run, group.sha, report.evaluated(subject, results, [], now, merge_group=True))
        return True
    except config.InvalidConfigError as error:
        failure = report.invalid_config(run.config_path, error.violations, run.default_branch, group.base_sha)
    except Exception as error:
        failure = _logged_failure(error)
    _publish(run, group.sha, report.failed(subject, failure, now))
    return False


def _matched_instances(run: Run, trusted: str, merge_base: str, head: str, workspace: str) -> list[conditions.Instance]:
    text = run.github.content(run.config_path, trusted)
    if text is None:
        raise config.InvalidConfigError(["The file does not exist."])
    rules = config.load(text)
    directory = run.scratch / workspace
    directory.mkdir()
    clone = changes.Clone(directory / "git", run.git_url, run.github.token(run.github.owner))
    clone.fetch(*dict.fromkeys([trusted, merge_base, head]))
    changed = clone.changed_paths(merge_base, head)
    config.check_scripts(rules, clone.modes(trusted, [rule.script for rule in rules if rule.script]))
    return conditions.matched_instances(
        rules,
        changed,
        lambda: changes.script_context(clone, trusted, merge_base, head, changed, directory, os.environ),
    )


def _results(
    github: GitHub,
    sha: str,
    instances: list[conditions.Instance],
    approved: dict[conditions.Instance, approvals.Approvals],
) -> list[report.Result]:
    names = list(dict.fromkeys(name for instance in instances for name in instance.rule.checks))
    states = checks.states(github, sha, names)
    return [
        report.Result(instance, approved.get(instance), {name: states[name] for name in instance.rule.checks})
        for instance in instances
    ]


def _publish(run: Run, sha: str, rendered: report.Report, number: int | None = None) -> None:
    url = publish.update_comment(run.github, number, sha, rendered.comment) if number is not None else None
    publish.set_status(run.github, sha, rendered.state, rendered.description, url or run.url)
    publish.append_summary(rendered.summary)


def _logged_failure(error: Exception) -> report.Failure:
    traceback.print_exc()
    message = str(error)
    if isinstance(error, subprocess.CalledProcessError) and error.stderr:
        message = f"{message} {str(error.stderr).strip()}"
    return report.evaluation_failed(message)


if __name__ == "__main__":
    sys.exit(main())
