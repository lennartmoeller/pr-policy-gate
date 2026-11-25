import base64
import os
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from pr_policy_gate.conditions import ScriptContext
from pr_policy_gate.github import GitHub


def merge_base(github: GitHub, trusted: str, head: str) -> str:
    return github.get(f"/repos/{github.repository}/compare/{trusted}...{head}")["merge_base_commit"]["sha"]


class Clone:
    def __init__(self, directory: Path, url: str, token: str):
        credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        self._environment = os.environ | {
            "GIT_DIR": str(directory),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {credentials}",
        }
        self._git("init", "--bare", "--quiet")
        self._git("remote", "add", "origin", url)

    def fetch(self, *shas: str, blobs: bool = False) -> None:
        self._git("fetch", "--quiet", "--depth=1", "--no-filter" if blobs else "--filter=blob:none", "origin", *shas)

    def changed_paths(self, merge_base: str, head: str) -> list[str]:
        return self._git("diff", "--no-renames", "--name-only", "-z", merge_base, head).split("\0")[:-1]

    def modes(self, sha: str, paths: Iterable[str]) -> dict[str, str]:
        entries = self._git("ls-tree", "-z", sha, "--", *paths).split("\0")[:-1]
        return {entry.split("\t", 1)[1]: entry.split(" ", 1)[0] for entry in entries}

    def export(self, sha: str, target: Path) -> None:
        target.mkdir(parents=True)
        with subprocess.Popen(["git", "archive", sha], env=self._environment, stdout=subprocess.PIPE) as archive:
            extracted = subprocess.run(["tar", "-x", "-C", target], stdin=archive.stdout, check=False)
        if archive.returncode != 0:
            raise subprocess.CalledProcessError(archive.returncode, ["git", "archive", sha])
        extracted.check_returncode()

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", *args], env=self._environment, capture_output=True, text=True, check=True).stdout


def script_context(
    clone: Clone,
    trusted: str,
    diff_base: str,
    head: str,
    changed_paths: Sequence[str],
    scratch: Path,
    environment: Mapping[str, str],
) -> ScriptContext:
    clone.fetch(*dict.fromkeys([trusted, diff_base, head]), blobs=True)
    trusted_dir, diff_base_dir, head_dir = scratch / "trusted", scratch / "diff-base", scratch / "head"
    changed_files = scratch / "changed-files"
    clone.export(trusted, trusted_dir)
    clone.export(diff_base, diff_base_dir)
    clone.export(head, head_dir)
    for root in [diff_base_dir, head_dir]:
        for directory, _, files in os.walk(root):
            for path in [Path(directory, name) for name in files] + [Path(directory)]:
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode & ~0o222)
    changed_files.write_text("".join(f"{path}\n" for path in changed_paths))
    return ScriptContext(trusted_dir, diff_base_dir, head_dir, changed_files, trusted, head, environment)
