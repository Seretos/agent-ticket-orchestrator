#!/usr/bin/env python3
"""
Does a package branch `pkg/<id>-<slug>` already exist, or is it new? (#62,
code prerequisite for #55)

Decided against a real git checkout of the project's main checkout
(`local_path`) -- never against a worktree, and never by checking the branch
out. `run` needs this before it decides whether `worktree_create` should cut
a fresh branch or reuse one that already exists (locally, or only on
`origin`, in which case it is materialised locally at origin's tip so a
later `worktree_create` can check it out there).

Decision order:

  1. `git -C <local_path> rev-parse --verify --quiet refs/heads/<branch>` --
     exit 0 means the branch already exists locally.
  2. `git -C <local_path> ls-remote --exit-code --heads origin
     refs/heads/<branch>` -- exit 2 means absent on origin too, so the
     branch is new. Exit 0 means it exists only on origin.
  3. When step 2 found it only on origin: `fetch` that one ref into
     `refs/remotes/origin/<branch>`, then `git branch --track <branch>
     refs/remotes/origin/<branch>` to create the local branch at origin's
     tip -- without checking it out. `local_path`'s HEAD never moves: moving
     it would affect the human's checkout and would stop `worktree_create`
     from checking the branch out in a worktree of its own.

Any git call that fails outside the expected exit codes above (a missing
`git` binary, a missing/unreachable `origin`, an auth failure, wrong argc, or
`local_path` not being a directory) is an unresolved error -- it is never
reported as "new", because a caller acting on a false "new" would create a
branch that silently diverges from one that already exists somewhere.

Usage: package-branch.py <local_path> <branch>

stdout: exactly one line --
  branch: existing   (the branch exists locally, or was just materialised
                       locally at origin's tip)
  branch: new        (the branch exists in neither place)
  error: <git command> -> <exit code>: <stderr, first line>
                      (an unresolved error; no `branch:` line is printed)

exit code: 0 existing, 3 new, 1 error (unusable input or unresolved git
error).
"""

import os
import pathlib
import subprocess
import sys

GIT_ENV_OVERRIDES = {"GIT_TERMINAL_PROMPT": "0"}


def run_git(local_path, *args):
    """Run one git subprocess against local_path's checkout, non-interactively.

    Returns the CompletedProcess, or None if `git` itself could not be
    launched (e.g. not installed) -- callers treat None as an error.
    """
    env = dict(os.environ)
    env.update(GIT_ENV_OVERRIDES)
    try:
        return subprocess.run(
            ["git", "-C", str(local_path), *args],
            capture_output=True, text=True, env=env,
        )
    except FileNotFoundError:
        return None


def error_line(command, result):
    if result is None:
        return f"error: git {' '.join(command)} -> not found: git binary is missing"
    stderr_first = (result.stderr or "").strip().splitlines()
    detail = stderr_first[0] if stderr_first else "(no stderr)"
    return f"error: git {' '.join(command)} -> {result.returncode}: {detail}"


def main() -> int:
    if len(sys.argv) != 3:
        print("error: usage: package-branch.py <local_path> <branch>")
        return 1

    local_path = pathlib.Path(sys.argv[1])
    branch = sys.argv[2]

    if not local_path.is_dir():
        print(f"error: not a directory: {local_path}")
        return 1

    # Step 1: local branch?
    local_check_args = ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]
    result = run_git(local_path, *local_check_args)
    if result is not None and result.returncode == 0:
        print("branch: existing")
        return 0
    if result is not None and result.returncode == 1:
        pass  # not local -- fall through to the origin check
    else:
        print(error_line(local_check_args, result))
        return 1

    # Step 2: branch on origin?
    remote_check_args = ["ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}"]
    result = run_git(local_path, *remote_check_args)
    if result is not None and result.returncode == 2:
        print("branch: new")
        return 3
    if result is not None and result.returncode == 0:
        pass  # present on origin only -- materialise it locally
    else:
        print(error_line(remote_check_args, result))
        return 1

    # Step 3: materialise the origin-only branch locally, at origin's tip.
    fetch_args = ["fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"]
    result = run_git(local_path, *fetch_args)
    if result is None or result.returncode != 0:
        print(error_line(fetch_args, result))
        return 1

    track_args = ["branch", "--track", branch, f"refs/remotes/origin/{branch}"]
    result = run_git(local_path, *track_args)
    if result is None or result.returncode != 0:
        print(error_line(track_args, result))
        return 1

    print("branch: existing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
