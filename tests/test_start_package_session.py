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
# stand-in for the claude CLI: record argv (one per line), the cwd, whether
# the launch lock is held while it starts (FAKE_CLAUDE_LOCK), and touch a
# marker in its cwd -- the only portable way to observe lock state and cwd
# from Python (Git Bash `pwd` yields /c/... paths).
printf '%s\\n' "$@" > "$FAKE_CLAUDE_ARGS"
pwd > "$FAKE_CLAUDE_CWD"
if [ -e "$HOME/.claude/.launch-lock" ]; then
  echo held > "$FAKE_CLAUDE_LOCK"
else
  echo missing > "$FAKE_CLAUDE_LOCK"
fi
touch .claude-started-here
echo '{"type":"system","subtype":"init"}'
exit "${FAKE_CLAUDE_EXIT:-0}"
"""

DEVELOPER_ENTRY = "/agent-autonomous-developer:process-developer"
PROSE_ENTRY = "/agent-autonomous-prompt-engineer:process-prompt-engineer"
GATEKEEPER_ENTRY = "/agent-ticket-orchestrator:gatekeeper"


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
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    runroot = tmp_path / "runs"
    runroot.mkdir()
    env = dict(os.environ)
    env.update({
        "PATH": str(bindir) + os.pathsep + env["PATH"],
        "HOME": str(home),
        "FAKE_CLAUDE_ARGS": str(tmp_path / "args.txt"),
        "FAKE_CLAUDE_CWD": str(tmp_path / "cwd.txt"),
        "FAKE_CLAUDE_LOCK": str(tmp_path / "lock_status.txt"),
    })
    env.pop("ADEV_SESSION_MODEL", None)
    return {
        "env": env, "worktree": worktree, "checkout": checkout,
        "runroot": runroot, "tmp": tmp_path, "home": home,
    }


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


# --- gatekeeper-split mode (#53) ---------------------------------------------------
#
# `--gatekeeper-split <project_id> <ticket_id> <main_checkout> [<run_root>]` starts a
# fixed, literal gatekeeper prompt in the project's main checkout -- never a worktree,
# never a caller-supplied entry -- through the same lock/flag/RUNDIR plumbing as a
# package session. See .adev/53-1/plan.md, requirements R1-R5.

def _masked(args):
    """Argv with the `-p` prompt value replaced, so two argv lists that differ
    only in which prompt they carry can be compared for equality."""
    args = list(args)
    args[args.index("-p") + 1] = "<prompt>"
    return args


def test_gatekeeper_split_starts_the_fixed_gatekeeper_prompt_in_the_main_checkout(sandbox):
    result = start(
        sandbox, "--gatekeeper-split",
        positional=["proj-x", "53", str(sandbox["checkout"]), str(sandbox["runroot"])],
    )
    assert result.returncode == 0, result.stderr
    assert prompt_of(sandbox) == (
        f"{GATEKEEPER_ENTRY} single_ticket=53 advance_to_todo=true project_id=proj-x"
    )
    assert (sandbox["checkout"] / ".claude-started-here").exists()
    assert not (sandbox["worktree"] / ".claude-started-here").exists(), \
        "must not have started in the worktree"


def test_gatekeeper_split_uses_the_package_session_flags_and_lock(sandbox):
    code_result = start(sandbox, "--lane", "code")
    assert code_result.returncode == 0, code_result.stderr
    code_args = recorded_args(sandbox)

    split_result = start(
        sandbox, "--gatekeeper-split",
        positional=["proj-x", "53", str(sandbox["checkout"]), str(sandbox["runroot"])],
    )
    assert split_result.returncode == 0, split_result.stderr
    split_args = recorded_args(sandbox)

    assert _masked(code_args) == _masked(split_args)
    assert split_args[split_args.index("--model") + 1] == "sonnet"
    assert split_args[split_args.index("--permission-mode") + 1] == "bypassPermissions"
    assert "AskUserQuestion" in split_args[split_args.index("--disallowedTools") + 1]
    lock_status = (sandbox["tmp"] / "lock_status.txt").read_text().strip()
    assert lock_status == "held", "the lock must be held while claude starts"
    assert not (sandbox["home"] / ".claude" / ".launch-lock").exists(), \
        "the lock is released after the start"


def test_gatekeeper_split_model_override(sandbox):
    result = start(
        sandbox, "--gatekeeper-split",
        positional=["proj-x", "53", str(sandbox["checkout"]), str(sandbox["runroot"])],
        extra_env={"ADEV_SESSION_MODEL": "opus"},
    )
    assert result.returncode == 0, result.stderr
    args = recorded_args(sandbox)
    assert args[args.index("--model") + 1] == "opus"


def test_gatekeeper_split_run_directory_and_exit_marker(sandbox):
    result = start(
        sandbox, "--gatekeeper-split",
        positional=["proj-x", "53", str(sandbox["checkout"]), str(sandbox["runroot"])],
        extra_env={"FAKE_CLAUDE_EXIT": "7"},
    )
    assert result.returncode == 7, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("RUNDIR=")
    assert lines[-1] == "EXIT=7"
    rundirs = list(sandbox["runroot"].iterdir())
    assert len(rundirs) == 1 and rundirs[0].name.startswith("split-53-"), \
        [d.name for d in rundirs]
    assert (rundirs[0] / "exit_code").read_text().strip() == "7"
    assert '"type":"system"' in (rundirs[0] / "stream.jsonl").read_text()
    assert (rundirs[0] / "stderr.txt").exists()


GATEKEEPER_SPLIT_BAD_ARGS = [
    ("no_args", lambda checkout, runroot: []),
    ("missing_ticket_id", lambda checkout, runroot: ["proj-x"]),
    ("ticket_id_abc", lambda checkout, runroot: ["proj-x", "abc", str(checkout), str(runroot)]),
    ("ticket_id_53a", lambda checkout, runroot: ["proj-x", "53a", str(checkout), str(runroot)]),
    ("ticket_id_hash53", lambda checkout, runroot: ["proj-x", "#53", str(checkout), str(runroot)]),
    ("ticket_id_empty", lambda checkout, runroot: ["proj-x", "", str(checkout), str(runroot)]),
    ("extra_trailing_arg", lambda checkout, runroot: ["proj-x", "53", str(checkout), str(runroot), "extra"]),
    ("extra_trailing_skill_name", lambda checkout, runroot: [
        "proj-x", "53", str(checkout), str(runroot), "/some-plugin:some-skill",
    ]),
    ("project_id_is_a_skill_name", lambda checkout, runroot: [
        "/some-plugin:some-skill", "53", str(checkout), str(runroot),
    ]),
    ("project_id_has_whitespace", lambda checkout, runroot: ["proj x", "53", str(checkout), str(runroot)]),
]


@pytest.mark.parametrize(
    "build_positional", [c[1] for c in GATEKEEPER_SPLIT_BAD_ARGS],
    ids=[c[0] for c in GATEKEEPER_SPLIT_BAD_ARGS],
)
def test_gatekeeper_split_refuses_bad_arguments_before_starting(sandbox, build_positional):
    positional = build_positional(sandbox["checkout"], sandbox["runroot"])
    result = start(sandbox, "--gatekeeper-split", positional=positional)
    assert result.returncode == 2, result.stderr
    assert "usage:" in result.stderr
    assert not (sandbox["tmp"] / "args.txt").exists(), "claude must not have been started"
    assert not any(sandbox["runroot"].iterdir()), "no run directory for a refused call"


def test_gatekeeper_split_missing_checkout_is_refused(sandbox):
    not_a_checkout = sandbox["tmp"] / "not-a-checkout"
    not_a_checkout.mkdir()
    result = start(
        sandbox, "--gatekeeper-split",
        positional=["proj-x", "53", str(not_a_checkout), str(sandbox["runroot"])],
    )
    assert result.returncode == 3, result.stderr
    assert not (sandbox["tmp"] / "args.txt").exists()


def test_gatekeeper_entry_is_not_reachable_as_a_lane(sandbox):
    result = start(sandbox, "--lane", "gatekeeper")
    assert result.returncode == 2
    assert not (sandbox["tmp"] / "args.txt").exists()
