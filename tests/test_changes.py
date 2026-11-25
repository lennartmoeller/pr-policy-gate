import os
import subprocess

import pytest
from fakes import commit, git, init_remote

from pr_policy_gate.changes import Clone, script_context


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    repo = tmp_path_factory.mktemp("source")
    init_remote(repo)
    for name in ["keep.txt", "old.txt", "gone.txt", "plain.sh", "owner.sh"]:
        (repo / name).write_text(f"{name}\n")
    os.symlink("../outside", repo / "outside-link")
    (repo / "owner.sh").chmod(0o755)
    base = commit(repo, "base")
    git(repo, "switch", "--quiet", "-c", "feature")
    git(repo, "mv", "old.txt", "new.txt")
    git(repo, "rm", "--quiet", "gone.txt")
    (repo / "many").mkdir()
    for number in range(3100):
        (repo / "many" / f"{number}.txt").write_text(f"{number}\n")
    head = commit(repo, "feature")
    git(repo, "switch", "--quiet", "main")
    (repo / "later.txt").write_text("later\n")
    trusted = commit(repo, "later on main")
    return {"url": f"file://{repo}", "base": base, "head": head, "trusted": trusted}


@pytest.fixture
def clone(tmp_path, source):
    return Clone(tmp_path / "clone", source["url"], "the-token")


@pytest.fixture
def scratch(tmp_path):
    yield tmp_path
    for directory, _, _ in os.walk(tmp_path):
        os.chmod(directory, os.stat(directory).st_mode | 0o200)


def test_changed_paths_list_both_rename_paths_deletions_and_more_files_than_the_api_lists(clone, source):
    clone.fetch(source["base"], source["head"])

    changed = clone.changed_paths(source["base"], source["head"])

    assert len(changed) == 3103
    assert {"old.txt", "new.txt", "gone.txt", "many/0.txt", "many/3099.txt"} <= set(changed)
    assert "later.txt" not in changed


def test_modes_report_the_file_modes_of_existing_paths(clone, source):
    clone.fetch(source["trusted"])

    assert clone.modes(source["trusted"], ["owner.sh", "plain.sh", "missing.sh"]) == {
        "owner.sh": "100755",
        "plain.sh": "100644",
    }


def test_export_fails_when_git_cannot_archive_the_commit(clone, tmp_path):
    with pytest.raises(subprocess.CalledProcessError) as raised:
        clone.export("0" * 40, tmp_path / "export")

    assert raised.value.cmd == ["git", "archive", "0" * 40]


@pytest.mark.usefixtures("clone")
def test_the_token_reaches_git_only_through_the_environment(tmp_path):
    assert "the-token" not in (tmp_path / "clone" / "config").read_text()


def test_a_blob_fetch_after_a_blobless_one_brings_every_blob(clone, source, tmp_path):
    commits = source["trusted"], source["head"]
    clone.fetch(*commits)
    clone.fetch(*commits, blobs=True)

    objects = git(tmp_path, "--git-dir=clone", "rev-list", "--objects", "--missing=print", *commits)
    assert [line for line in objects.splitlines() if line.startswith("?")] == []


@pytest.mark.parametrize("diff_base", ["base", "trusted"])
def test_script_context_keeps_trusted_code_separate_from_read_only_comparison_data(clone, source, scratch, diff_base):
    context = script_context(
        clone, source["trusted"], source[diff_base], source["head"], ["new.txt", "old.txt"], scratch, {"X": "1"}
    )

    assert (context.trusted_dir / "later.txt").exists()
    assert os.access(context.trusted_dir / "owner.sh", os.X_OK)
    assert (context.diff_base_dir / "old.txt").read_text() == "old.txt\n"
    assert (context.diff_base_dir / "later.txt").exists() == (diff_base == "trusted")
    assert not (context.diff_base_dir / "new.txt").exists()
    assert not os.access(context.diff_base_dir / "old.txt", os.W_OK)
    assert not os.access(context.diff_base_dir, os.W_OK)
    assert (context.head_dir / "new.txt").read_text() == "old.txt\n"
    assert not (context.head_dir / "old.txt").exists()
    assert not os.access(context.head_dir / "new.txt", os.W_OK)
    assert not os.access(context.head_dir / "many", os.W_OK)
    assert context.changed_files.read_text() == "new.txt\nold.txt\n"
    assert (context.trusted_sha, context.head_sha, context.environment) == (
        source["trusted"],
        source["head"],
        {"X": "1"},
    )


def test_script_context_does_not_change_a_symlink_target_outside_the_head_checkout(clone, source, scratch):
    outside = scratch / "outside"
    outside.write_text("writable\n")
    original_mode = outside.stat().st_mode

    context = script_context(clone, source["trusted"], source["base"], source["head"], [], scratch, {})

    assert (context.head_dir / "outside-link").is_symlink()
    assert (context.diff_base_dir / "outside-link").is_symlink()
    assert outside.stat().st_mode == original_mode
