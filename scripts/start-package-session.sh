#!/usr/bin/env bash
#
# Starts one headless agent-autonomous-developer session for one work package and
# waits for it to end. This is the `run` skill's only way of dispatching work; the
# skill calls it with `Bash(run_in_background: true)` from its own turn and is woken
# by the harness when the process exits.
#
# Why a script and not an inline command in SKILL.md: the launch lock, the stream
# redirection and the exit marker are mechanics, not judgement. A skill that has to
# reproduce them from prose gets them subtly wrong; a script gets them right every
# time and can be tested on its own.
#
# Usage:
#   start-package-session.sh <project_id> <package> <worktree_path> <base_branch> <attempt> [<run_root>]
#
# Prints `RUNDIR=<dir>` first, then `EXIT=<code>` last. Exit code = the session's.
# Writes <rundir>/stream.jsonl, <rundir>/stderr.txt, <rundir>/exit_code.
#
# Model: pinned via --model, default `sonnet`, override with the environment
# variable ADEV_SESSION_MODEL. Pinning is not a preference, it is a correctness
# property: without --model the headless session inherits whatever `/model` the
# human last left set in their interactive session, so the same package costs a
# different amount depending on a setting nobody involved in the run can see.
# Measured over the 2026-08-23/24 runs, the main turn was 33% of a package's
# cost on Sonnet and 52% on Opus — same pipeline, same work, one forgotten
# toggle. The six subagents already pin their own model in their frontmatter
# (`planner: opus`, the rest `sonnet`); this line closes the last gap. The main
# turn sequences phases, counts rounds, posts events and drives git/PR/CI — it
# delegates every judgement that needs a bigger model to a subagent that names
# one.
#
# Budget: there is deliberately no `--max-budget-usd`. See AGENTS.md,
# "No dollar budget on the package session".
#
# Launch lock: concurrent `claude` starts corrupt ~/.claude.json (anthropics/claude-code
# #28813, #28847). The lock is a directory (`mkdir` is atomic on NTFS and POSIX), carries
# the owner PID, is broken if the owner is dead or it is older than 60 s, and is held
# only until the new process has read its config (first `system` line in the stream)
# or 25 s — never across the run.
set -uo pipefail

if [ $# -lt 5 ] || [ $# -gt 6 ]; then
  echo "usage: $0 <project_id> <package> <worktree_path> <base_branch> <attempt> [<run_root>]" >&2
  exit 2
fi

PROJECT="$1"; PACKAGE="$2"; WORKTREE="$3"; BASE="$4"; ATTEMPT="$5"
RUNROOT="${6:-${CLAUDE_SCRATCHPAD:-$(mktemp -d)}}"
MODEL="${ADEV_SESSION_MODEL:-sonnet}"

if [ ! -e "$WORKTREE/.git" ]; then
  echo "worktree not found or not a git checkout: $WORKTREE" >&2
  exit 3
fi

RUNDIR="$RUNROOT/pkg-$PACKAGE-attempt-$ATTEMPT-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUNDIR"
echo "RUNDIR=$RUNDIR"

# --- launch lock -------------------------------------------------------------------
LOCK="$HOME/.claude/.launch-lock"
for _ in $(seq 1 120); do
  if mkdir "$LOCK" 2>/dev/null; then echo $$ > "$LOCK/pid"; break; fi
  OWNER="$(cat "$LOCK/pid" 2>/dev/null || true)"
  AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
  if { [ -n "$OWNER" ] && ! kill -0 "$OWNER" 2>/dev/null; } || [ "$AGE" -gt 60 ]; then
    rm -rf "$LOCK"; continue
  fi
  sleep 0.5
done
if [ "$(cat "$LOCK/pid" 2>/dev/null)" != "$$" ]; then
  echo "could not take the launch lock at $LOCK" >&2
  exit 4
fi

# --- start: exactly the contract entry point, cwd = worktree --------------------------
(
  cd "$WORKTREE" && exec claude -p \
    "/agent-autonomous-developer:process-ticket package=$PACKAGE project_id=$PROJECT worktree_path=$WORKTREE base_branch=$BASE attempt=$ATTEMPT" \
    --permission-mode bypassPermissions \
    --disallowedTools AskUserQuestion \
    --output-format stream-json --verbose \
    --model "$MODEL"
) > "$RUNDIR/stream.jsonl" 2> "$RUNDIR/stderr.txt" &
PID=$!

for _ in $(seq 1 50); do
  grep -q '"type":"system"' "$RUNDIR/stream.jsonl" 2>/dev/null && break
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.5
done
rm -rf "$LOCK"

wait "$PID"; EXIT=$?
echo "$EXIT" > "$RUNDIR/exit_code"
echo "EXIT=$EXIT"
exit "$EXIT"
