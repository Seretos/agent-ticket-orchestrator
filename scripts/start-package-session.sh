#!/usr/bin/env bash
#
# Starts one headless lower-plugin session for one work package and waits for it to
# end. Which lower plugin is decided by the package's lane (see --lane below). This is the `run` skill's only way of dispatching work; the
# skill calls it with `Bash(run_in_background: true)` from its own turn and is woken
# by the harness when the process exits.
#
# Why a script and not an inline command in SKILL.md: the launch lock, the stream
# redirection and the exit marker are mechanics, not judgement. A skill that has to
# reproduce them from prose gets them subtly wrong; a script gets them right every
# time and can be tested on its own.
#
# Usage:
#   start-package-session.sh [--lane code|prose] <project_id> <package> <worktree_path> <base_branch> <attempt> [<run_root>]
#   start-package-session.sh --gatekeeper-split <project_id> <ticket_id> <main_checkout> [<run_root>]
#
# Lane: `code` (the default -- a call without --lane behaves exactly as before) starts
# /agent-autonomous-developer:process-developer; `prose` (a package ticket carrying the
# `lane:prose` label) starts /agent-autonomous-prompt-engineer:process-prompt-engineer.
# The lane -> entry table lives here and nowhere else: the caller passes a lane, never
# a skill name, so no free-form string ever reaches a bypassPermissions prompt. Both
# entries take the same parameters and speak the same adev:event v1 contract; nothing
# else in this script depends on the lane.
#
# Gatekeeper-split mode (#53): `--gatekeeper-split <project_id> <ticket_id>
# <main_checkout> [<run_root>]` starts one headless gatekeeper pass for exactly one
# named ticket, cwd = the project's *main checkout* (never a worktree -- there is no
# base branch, no attempt, nothing to review). The entry is a fixed literal
# (GATEKEEPER_SPLIT_ENTRY below), never caller-supplied, and it is deliberately kept
# out of the `--lane` case so `--lane gatekeeper` can never reach it. `ticket_id` and
# `project_id` are validated before anything is created or started, the same
# lock/RUNDIR/flag plumbing as a package session runs the process, and the run
# directory is named `split-<ticket>-...` (not `pkg-...`) so it is never mistaken for
# a package session's run dir. See .adev/53-1/plan.md; the flow that calls this mode
# (triage marker, gatekeeper/run wiring) is #57, out of scope here.
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
# cost on Sonnet and 52% on Opus -- same pipeline, same work, one forgotten
# toggle. The six subagents already pin their own model in their frontmatter
# (`planner: opus`, the rest `sonnet`); this line closes the last gap. The main
# turn sequences phases, counts rounds, posts events and drives git/PR/CI -- it
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
# or 25 s -- never across the run.
set -uo pipefail

USAGE="usage: $0 [--lane code|prose] <project_id> <package> <worktree_path> <base_branch> <attempt> [<run_root>]
       $0 --gatekeeper-split <project_id> <ticket_id> <main_checkout> [<run_root>]"

GATEKEEPER_SPLIT_ENTRY="/agent-ticket-orchestrator:gatekeeper"

if [ "${1:-}" = "--gatekeeper-split" ]; then
  shift
  if [ $# -lt 3 ] || [ $# -gt 4 ]; then
    echo "$USAGE" >&2
    exit 2
  fi
  PROJECT="$1"; TICKET="$2"; CHECKOUT="$3"
  RUNROOT="${4:-${CLAUDE_SCRATCHPAD:-$(mktemp -d)}}"
  MODEL="${ADEV_SESSION_MODEL:-sonnet}"

  if ! [[ "$TICKET" =~ ^[1-9][0-9]*$ ]]; then
    echo "$USAGE" >&2
    exit 2
  fi
  if ! [[ "$PROJECT" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]]; then
    echo "$USAGE" >&2
    exit 2
  fi
  if [ ! -e "$CHECKOUT/.git" ]; then
    echo "main checkout not found or not a git checkout: $CHECKOUT" >&2
    exit 3
  fi

  CWD="$CHECKOUT"
  PROMPT="$GATEKEEPER_SPLIT_ENTRY single_ticket=$TICKET advance_to_todo=true project_id=$PROJECT"
  RUNDIR_NAME="split-$TICKET"
else
  LANE="code"
  if [ "${1:-}" = "--lane" ]; then
    if [ $# -lt 2 ]; then echo "$USAGE" >&2; exit 2; fi
    LANE="$2"; shift 2
  fi
  case "$LANE" in
    code)  ENTRY="/agent-autonomous-developer:process-developer" ;;
    prose) ENTRY="/agent-autonomous-prompt-engineer:process-prompt-engineer" ;;
    *) echo "unknown lane: $LANE (valid: code, prose)" >&2; echo "$USAGE" >&2; exit 2 ;;
  esac

  if [ $# -lt 5 ] || [ $# -gt 6 ]; then
    echo "$USAGE" >&2
    exit 2
  fi

  PROJECT="$1"; PACKAGE="$2"; WORKTREE="$3"; BASE="$4"; ATTEMPT="$5"
  RUNROOT="${6:-${CLAUDE_SCRATCHPAD:-$(mktemp -d)}}"
  MODEL="${ADEV_SESSION_MODEL:-sonnet}"

  if [ ! -e "$WORKTREE/.git" ]; then
    echo "worktree not found or not a git checkout: $WORKTREE" >&2
    exit 3
  fi

  CWD="$WORKTREE"
  PROMPT="$ENTRY package=$PACKAGE project_id=$PROJECT worktree_path=$WORKTREE base_branch=$BASE attempt=$ATTEMPT"
  RUNDIR_NAME="pkg-$PACKAGE-attempt-$ATTEMPT"
fi

RUNDIR="$RUNROOT/$RUNDIR_NAME-$(date +%Y%m%d-%H%M%S)"
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

# --- start: exactly the contract entry point, cwd = worktree (or main checkout) -----
(
  cd "$CWD" && exec claude -p \
    "$PROMPT" \
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
