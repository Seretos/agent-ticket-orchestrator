"""
Behaviour tests for `scripts/package-branch.py`: whether a package branch
`pkg/<id>-<slug>` already exists (locally, or only on `origin`, materialised
locally at origin's tip) or is new -- decided against a real git checkout,
no mocked git.

Each test builds a temp repo world: a bare `origin.git`, a `seed` clone that
commits and pushes, and a `local` clone (of `origin`) that the script is run
against. Every git call in the fixture passes explicit user/committer config
and disables gpgsign so the developer's global git config cannot break the
setup.
"""

import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "package-branch.py"
BRANCH = "pkg/1-demo"

GIT_CFG = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def run_git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", str(cwd), *GIT_CFG, *args],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


@pytest.fixture
def world(tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    local = tmp_path / "local"

    run_git(tmp_path, "init", "--bare", str(origin))

    run_git(tmp_path, "init", "-b", "main", str(seed))
    (seed / "file.txt").write_text("seed\n", encoding="utf-8")
    run_git(seed, "add", "file.txt")
    run_git(seed, "commit", "-m", "seed commit")
    run_git(seed, "remote", "add", "origin", str(origin))
    run_git(seed, "push", "origin", "main")

    run_git(tmp_path, "clone", str(origin), str(local))
    run_git(local, "checkout", "main")

    return {"origin": origin, "seed": seed, "local": local}


def run_script(local_path, branch=BRANCH):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(local_path), branch],
        capture_output=True, text=True,
    )


def test_branch_only_local_is_existing(world):
    """Requirement 1: a branch existing only in `local` is reported existing."""
    run_git(world["local"], "checkout", "-b", BRANCH)
    run_git(world["local"], "checkout", "main")

    result = run_script(world["local"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["branch: existing"]
    # origin never saw this branch
    remote_check = subprocess.run(
        ["git", "ls-remote", "--heads", str(world["origin"]), BRANCH],
        capture_output=True, text=True,
    )
    assert remote_check.stdout.strip() == ""


def test_branch_only_on_origin_is_created_locally_at_origin_tip(world):
    """Requirement 2: an origin-only branch is materialised locally at origin's tip,
    without checking it out (HEAD stays on `main`)."""
    # local is cloned first (already done in the fixture), then the branch is
    # pushed from seed -- so local never saw it at clone time.
    run_git(world["seed"], "checkout", "-b", BRANCH)
    (world["seed"] / "extra.txt").write_text("extra\n", encoding="utf-8")
    run_git(world["seed"], "add", "extra.txt")
    run_git(world["seed"], "commit", "-m", "extra commit on branch")
    run_git(world["seed"], "push", "origin", BRANCH)

    result = run_script(world["local"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["branch: existing"]

    local_sha = run_git(world["local"], "rev-parse", f"refs/heads/{BRANCH}").stdout.strip()
    origin_sha = run_git(world["origin"], "rev-parse", f"refs/heads/{BRANCH}").stdout.strip()
    main_sha = run_git(world["local"], "rev-parse", "main").stdout.strip()

    assert local_sha == origin_sha
    assert local_sha != main_sha

    head = run_git(world["local"], "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert head == "main", "materialising the branch must not check it out"


def test_branch_nowhere_is_new(world):
    """Requirement 3: absent locally and on origin is reported new, and no
    local ref is created as a side effect."""
    result = run_script(world["local"])

    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["branch: new"]

    verify = subprocess.run(
        ["git", "-C", str(world["local"]), "rev-parse", "--verify", "--quiet",
         f"refs/heads/{BRANCH}"],
        capture_output=True, text=True,
    )
    assert verify.returncode == 1, "no local ref should have been created"


def test_git_error_is_not_new_and_has_its_own_exit_code(world):
    """Requirement 4: an unresolved git error (here: origin repointed at a
    nonexistent path) must never be reported as `branch: new` -- it gets its
    own exit code and an `error:` line, no `branch:` line at all."""
    missing = pathlib.Path(str(world["origin"])).parent / "missing.git"
    run_git(world["local"], "remote", "set-url", "origin", str(missing))

    result = run_script(world["local"])

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.returncode != 3
    assert "branch: new" not in result.stdout
    assert not any(line.startswith("branch:") for line in result.stdout.splitlines())
    assert any(line.startswith("error:") for line in result.stdout.splitlines()), result.stdout
