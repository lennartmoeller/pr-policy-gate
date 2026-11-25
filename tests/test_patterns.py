import pytest

from pr_policy_gate.patterns import captured_values, matches

CHEAT_SHEET = [
    (["*"], ["README.md", "server.rb"], ["docs/README.md"]),
    (["*.jsx?"], ["page.js", "page.jsx"], ["page.jsxx", "src/page.js"]),
    (["**"], ["all/the/files.md"], []),
    (["*.js"], ["app.js", "index.js"], ["src/app.js"]),
    (["**.js"], ["index.js", "js/index.js", "src/js/app.js"], ["index.jsx"]),
    (["docs/*"], ["docs/README.md", "docs/file.txt"], ["docs/mona/octocat.txt"]),
    (["docs/**"], ["docs/README.md", "docs/mona/octocat.txt"], ["README.md"]),
    (["docs/**/*.md"], ["docs/README.md", "docs/mona/hello-world.md", "docs/a/markdown/file.md"], ["docs/a.txt"]),
    (["**/docs/**"], ["docs/hello.md", "dir/docs/my-file.txt", "space/docs/plan/space.doc"], ["documents/a.md"]),
    (["**/README.md"], ["README.md", "js/README.md"], ["README.mdx"]),
    (["**/*src/**"], ["a/src/app.js", "my-src/code/js/app.js"], ["src.js"]),
    (["**/*-post.md"], ["my-post.md", "path/their-post.md"], ["post.md"]),
    (["**/migrate-*.sql"], ["migrate-10909.sql", "db/migrate-v1.0.sql", "db/sept/migrate-v1.sql"], ["db/migrate.sql"]),
    (["*.md", "!README.md"], ["hello.md"], ["README.md", "docs/hello.md"]),
    (["*.md", "!README.md", "README*"], ["hello.md", "README.md", "README.doc"], []),
]
SYNTAX = [
    (["a+.txt"], ["a.txt", "aaa.txt"], [".txt"]),
    (["v[0-9a-z].md"], ["v1.md", "vx.md"], ["vX.md", "v10.md"]),
    (["a.b"], ["a.b"], ["axb"]),
    (["a!b", "a[b"], ["a!b", "a[b"], ["ab"]),
    (["?a"], ["?a"], ["a"]),
    (["**", "!docs/**", "docs/keep.md"], ["src/a.py", "docs/keep.md"], ["docs/drop.md"]),
    (["**"], [".github/workflows/ci.yml", ".gitignore"], []),
    (["*", ".github/**"], [".gitignore", ".github/pr-policy-gate/config.yml"], ["src/.env"]),
]


@pytest.mark.parametrize(("patterns", "matching", "other"), CHEAT_SHEET + SYNTAX)
def test_matches_follows_the_actions_paths_filter(patterns, matching, other):
    assert [path for path in matching + other if matches(patterns, path)] == matching


def test_a_renamed_file_matches_through_its_old_path():
    assert any(matches(["old/**"], path) for path in ["old/a.txt", "new/a.txt"])


def test_captured_values_come_from_positive_patterns_of_any_changed_path():
    patterns = ["services/{*}/**", "!services/{*}/docs/**", "!shared/{*}/**"]
    paths = ["services/billing/a.py", "services/payments/docs/x.md", "shared/common/b.py", "README.md"]

    assert captured_values(patterns, paths) == ["billing", "payments"]


def test_a_value_matches_only_its_own_paths_including_negations():
    patterns = ["services/{*}/**", "!services/{*}/docs/**"]

    assert matches(patterns, "services/billing/a.py", "billing")
    assert not matches(patterns, "services/payments/a.py", "billing")
    assert not matches(patterns, "services/billing/docs/x.md", "billing")


def test_a_value_captures_one_path_segment_and_stays_literal():
    assert captured_values(["teams/{*}.yml", "services/{*}/**"], ["teams/alpha.yml", "services/a+b/c/d"]) == [
        "a+b",
        "alpha",
    ]
    assert matches(["services/{*}/**"], "services/a+b/x", "a+b")
    assert not matches(["services/{*}/**"], "services/aab/x", "a+b")
