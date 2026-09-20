"""
Behaviour tests for `scripts/start-package-session.sh`'s argument handling
(#38): the lane decides which lower plugin's entry skill the headless session
is started with, the default is the developer's, and nothing else about the
launch changes.

The real `claude` is never started: a stand-in executable named `claude` is
put first on PATH, records the arguments it was called with, and prints the
one `system` record the script's launch lock waits for. HOME points into the
test's tmp dir, so the launch lock taken here is never the machine's real one.

Bash resolution copied from tests/test_release_scripts.py: on win32 Git Bash
at its well-known absolute path, never a bare `bash` (which can resolve to
the WSL stub).
"""

import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
START_SCRIPT = REPO_ROOT / "scripts" / "start-package-session.sh"

if sys.platform == "win32":
    _BASH_PATH = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
    assert _BASH_PATH.is_file(), f"Git Bash not found at {_BASH_PATH}"
    BASH = str(_BASH_PATH)
else:
    BASH = "/bin/bash"

FAKE_CLAUDE = """#!/usr/bin/env bash
# stand-in for the claude CLI: record argv (one per line) and the cwd
printf '%s\\n' "$@" > "$FAKE_CLAUDE_ARGS"
pwd > "$FAKE_CLAUDE_CWD"
echo '{"type":"system","subtype":"init"}'
exit "${FAKE_CLAUDE_EXIT:-0}"
"""

DEVELOPER_ENTRY = "/agent-autonomous-developer:process-developer"
PROSE_ENTRY = "/agent-autonomous-prompt-engineer:process-prompt-engineer"


@pytest.fixture
def sandbox(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "claude"
    fake.write_bytes(FAKE_CLAUDE.encode("utf-8"))
    fake.chmod(0o755)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    worktree = tmp_path / "worktree"
    (worktree / ".git").mkdir(parents=True)
    runroot = tmp_path / "runs"
    runroot.mkdir()
    env = dict(os.environ)
    env.update({
        "PATH": str(bindir) + os.pathsep + env["PATH"],
        "HOME": str(home),
        "FAKE_CLAUDE_ARGS": str(tmp_path / "args.txt"),
        "FAKE_CLAUDE_CWD": str(tmp_path / "cwd.txt"),
    })
    env.pop("ADEV_SESSION_MODEL", None)
    return {"env": env, "worktree": worktree, "runroot": runroot, "tmp": tmp_path, "home": home}


def start(sandbox, *leading, extra_env=None, positional=None):
    env = dict(sandbox["env"], **(extra_env or {}))
    if positional is None:
        positional = ["proj-x", "42", str(sandbox["worktree"]), "main", "1", str(sandbox["runroot"])]
    return subprocess.run(
        [BASH, str(START_SCRIPT), *leading, *positional],
        capture_output=True, text=True, env=env, timeout=120,
    )


def recorded_args(sandbox):
    return (sandbox["tmp"] / "args.txt").read_text(encoding="utf-8").splitlines()


def prompt_of(sandbox):
    args = recorded_args(sandbox)
    return args[args.index("-p") + 1]


def test_without_lane_the_developer_entry_is_started(sandbox):
    result = start(sandbox)
    assert result.returncode == 0, result.stderr
    prompt = prompt_of(sandbox)
    assert prompt.startswith(DEVELOPER_ENTRY + " ")
    assert "process-prompt-engineer" not in prompt


def test_lane_code_is_the_same_as_no_lane(sandbox):
    assert start(sandbox, "--lane", "code").returncode == 0
    assert prompt_of(sandbox).startswith(DEVELOPER_ENTRY + " ")


def test_lane_prose_starts_the_prompt_engineer_entry(sandbox):
    result = start(sandbox, "--lane", "prose")
    assert result.returncode == 0, result.stderr
    prompt = prompt_of(sandbox)
    assert prompt.startswith(PROSE_ENTRY + " ")
    assert "agent-autonomous-developer" not in prompt


def test_only_the_entry_differs_between_the_lanes(sandbox):
    """Same parameters, same flags, same model pin -- the two argv lists are
    identical once the entry name is taken out."""
    start(sandbox, "--lane", "code")
    code_args = recorded_args(sandbox)
    start(sandbox, "--lane", "prose")
    prose_args = recorded_args(sandbox)
    assert [a.replace(DEVELOPER_ENTRY, "<entry>") for a in code_args] == \
           [a.replace(PROSE_ENTRY, "<entry>") for a in prose_args]
    prompt = prompt_of(sandbox)
    for param in ("package=42", "project_id=proj-x", "base_branch=main", "attempt=1", "worktree_path="):
        assert param in prompt
    assert prose_args[prose_args.index("--model") + 1] == "sonnet"
    assert "AskUserQuestion" in prose_args[prose_args.index("--disallowedTools") + 1]


def test_model_override_applies_in_the_prose_lane_too(sandbox):
    start(sandbox, "--lane", "prose", extra_env={"ADEV_SESSION_MODEL": "opus"})
    args = recorded_args(sandbox)
    assert args[args.index("--model") + 1] == "opus"


def test_unknown_lane_is_refused_before_anything_starts(sandbox):
    result = start(sandbox, "--lane", "mixed")
    assert result.returncode == 2
    assert "mixed" in result.stderr
    assert not (sandbox["tmp"] / "args.txt").exists(), "claude must not have been started"
    assert not any(sandbox["runroot"].iterdir()), "no run directory for a refused call"


def test_a_skill_name_is_not_accepted_as_a_lane(sandbox):
    """The caller passes a lane, never an entry: a free-form string must not
    reach the bypassPermissions prompt."""
    result = start(sandbox, "--lane", "/some-plugin:some-skill")
    assert result.returncode == 2
    assert not (sandbox["tmp"] / "args.txt").exists()


def test_lane_flag_without_a_value_is_a_usage_error(sandbox):
    result = start(sandbox, "--lane", positional=[])
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_too_few_positionals_is_still_a_usage_error_with_a_lane(sandbox):
    result = start(sandbox, "--lane", "prose", positional=["proj-x", "42"])
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_run_directory_exit_marker_and_lock_are_unchanged_by_the_lane(sandbox):
    result = start(sandbox, "--lane", "prose", extra_env={"FAKE_CLAUDE_EXIT": "7"})
    assert result.returncode == 7, "the script's exit code is the session's"
    lines = result.stdout.splitlines()
    assert lines[0].startswith("RUNDIR=")
    assert lines[-1] == "EXIT=7"
    rundirs = list(sandbox["runroot"].iterdir())
    assert len(rundirs) == 1 and rundirs[0].name.startswith("pkg-42-attempt-1-")
    assert (rundirs[0] / "exit_code").read_text().strip() == "7"
    assert '"type":"system"' in (rundirs[0] / "stream.jsonl").read_text()
    assert not (sandbox["home"] / ".claude" / ".launch-lock").exists(), "the lock is released after the start"
