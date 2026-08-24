---
name: run
disable-model-invocation: true
description: Unattended night-shift runner — takes every open package in the board's Todo column, gives each its own worktree, hands it to agent-autonomous-developer in a separate claude -p process started from this skill's own turn, then merges the CI-green PR and moves the card to Done (or escalates to Question). Sequential, no human in the loop, may run for hours. Installed per project; invoke as "/agent-ticket-orchestrator:run" from the project's main checkout (project_id=<id> overrides the repo-derived id).
---

# run — process every Todo package to a merged, CI-green PR

You are the unattended executor. Nobody is watching; you may run all night.
You pull packages from **Todo**, drive each one through the lower plugin
(`agent-autonomous-developer`) in its own worktree and its own `claude -p`
process, and move the board card as the single status signal:
`Todo → Doing → (Review, written by the lower plugin) → Done`, or `→ Question`
when a human decision is genuinely needed.

**Comments are the log, columns are the signal.** You never read a subagent's
prose to learn what happened — you read the latest `adev:event` comment on
the package ticket. You never produce code, plans, or diffs, and you never
carry any project content in your context.

## Inputs

- `project_id` — **optional; resolved from the repository you are in.** This
  plugin is installed per project and runs from that project's main checkout.
  Resolution, in this order: an explicit `project_id=<id>` argument wins;
  otherwise run `git remote get-url origin`, reduce it to `owner/repo`
  (`git@github.com:owner/repo.git` → `owner/repo`; `https://…/owner/repo.git`
  → `owner/repo`), and take the single `list_projects()` entry whose `path`
  equals it. No match or more than one → STOP and say which repo you resolved
  and what `list_projects` returned — never pick one. Thread the resolved id
  into every MCP call and every subagent prompt.
- **Inside a project there is no parallelism**: one package, one worktree,
  one process at a time. Parallel `claude` starts race on `~/.claude.json`
  and parallel worktrees race on shared services; the night is long enough.
  Orchestration *across* projects (release chains, version bumps) is not this
  plugin's job — it runs one project's board.
- Optional `model` for the package process (default `sonnet`), passed as
  `ADEV_SESSION_MODEL=<model>` in front of the script call. The subagents inside
  the package session pin their own models; this is only the session's own
  orchestrating turn, which sequences phases, counts rounds, posts events and
  drives git/PR/CI. Pinning it at all is the point — an unpinned session
  inherits whatever `/model` the human last left set.
- **No dollar budget.** There is no `budget_usd` and no `--max-budget-usd`; the
  platform's own session limits are the only ceiling. See *Why there is no
  dollar budget* below.

## Preconditions (per project)

1. **MCPs loaded.** `agent-project-issues` and `agent-worktree` tools must be
   available. If not, **STOP** and tell the user to `/reload-plugins`.
2. **Board columns.** `list_board_columns(project_id)` must contain the logical
   columns `Todo`, `Doing`, `Review`, `Done`, `Question`. Keep the
   `logical → native` map; every board write uses the *native* value. Missing
   column → STOP for this project with the missing name.
3. **Merge permission.** From `list_projects` read `permissions.pulls.merge`.
   If `false`, still run — but instead of merging, leave the package in
   `Review` and append a final comment *"CI green, merge not permitted for
   this project — merge manually"*. Say so in the report up front.
4. **Repo root.** `local_path` from `list_projects` must exist on disk and be
   a git checkout; the default branch is `git -C <local_path> symbolic-ref
   --short refs/remotes/origin/HEAD` (fallback: `main`). `worktree_create`
   fetches `origin` itself.

## Flow per project

### 1. Enumerate

```
packages = list_tickets(project_id, column="Todo", status="open", limit=100)
```

**Only Todo.** Never read Backlog or Planned as candidates — those columns are
the human's staging area and the gatekeeper's output; what is in Todo is what
the human released. Process in board order (oldest first).

### 2. Per package, sequentially

**Why strictly one at a time, merge before next:** every package's PR is
merged by you on `ci-green` *before* the next package's worktree is created,
and that worktree is cut from the **post-merge** default branch
(`worktree_create` fetches `origin` first). So at no point do two open PRs
coexist, and a later package can never conflict with an earlier one — the
conflict that a human would otherwise inherit after merging PR 1 of 3 cannot
arise. This guarantee only holds while `run` merges itself; with
`pulls.merge: false` the packages pile up in **Review** and the human who
merges them by hand also inherits the conflicts. That trade is the human's,
not this skill's — it is stated here so nobody "fixes" it by parallelising.


**a. Claim + worktree.**

- `update_ticket(project_id, ticket_id, custom_fields={"Status": <native Doing>})`.
- Branch name: `pkg/<id>-<slug>` (slug = title, lower-case, `[^a-z0-9]+` → `-`,
  trimmed, max 40 chars).
- `environment_list()` first: if a worktree for that branch already exists
  (a retry after a crash), reuse its `path`; otherwise
  `worktree_create(repo_root=<local_path>, branch="pkg/<id>-<slug>", base=<default branch>)`
  and take `path` from the returned record. Remember the `id` for removal,
  but re-fetch it via `environment_list` if you ever removed and re-created.

**b. Start the package session — yourself, from this turn.** No subagent
wraps the process: a task-notification for a backgrounded Bash command is
delivered to the **main** session, and a subagent that ends its turn to wait
is terminated, not suspended (lower plugin #83/#88). So **you** start the
process, with `Bash(run_in_background: true)`, through the bundled script —
never by typing the `claude` command yourself:

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/start-package-session.sh" <project_id> <id> "<worktree_path>" <default branch> <attempt>
```

The script owns the mechanics (run directory, launch lock around the start,
stream/stderr files, exit marker — see its header) and prints `RUNDIR=…`
first and `EXIT=<code>` last. Then **stop and wait for the completion
notification**. Do not poll the ticket, do not read the stream, do not start
a second package. CI rounds of up to 45 minutes and three review rounds all
happen inside that process; hours are normal. `EXIT` and `RUNDIR` are
informational; you never read `stream.jsonl` into your context (the
orchestrator stays free of project content) — `tail -n 3 "<RUNDIR>/stderr.txt"`
is allowed to classify a crash.

**c. Read the ticket, react.** The exit code is informational; the truth is
the ticket. Call

```
list_comments(project_id, ticket_id=<package>, order="desc", limit=20)
```

and take the **first** comment whose body contains `<!-- adev:event`. Parse
the block as dumb `key: value` lines (`event`, `package`, `attempt`,
`rounds`, `pr`, `ci_run`; empty = unknown; unknown keys ignored). Then:

| latest event | you do |
|---|---|
| `ci-green` | `merge_pr(project_id, pr_id=<pr>)` with defaults (the project's default merge method; do not pass `merge_method`). Children of an epic close through `Closes #<n>` in the PR body — you do not close them. Then → `Done`, then `worktree_remove(environment_id=<id>)`. If merge is not permitted (Precondition 3): leave in Review, comment, remove worktree. |
| `blocked` | Set aside: append to `blocked_list`, leave the card in **Doing**, `worktree_remove` (the branch is pushed if a PR exists; if not, the retry starts fresh anyway). Continue with the next package. |
| `failed`, or no terminal event (non-zero exit, or the latest event is a non-terminal one like `pr-opened`/`ci-red`/`review-verdict` — the process died mid-pipeline) | **One** fresh start (step b, same script) with `attempt+1`, same worktree. If that ends `ci-green` → handle as above. If still `failed`/none → `add_comment` summarising both attempts (event, `rounds` with the findings-vs-infra split, `pr`, both `RUNDIR`s), → **Question**, `worktree_remove`. |

A `pr-opened` or `ci-red` event seen *while the process is still alive* is
not terminal — but you never see that state, because you only act after
the process has ended. CI waiting is the lower plugin's job,
inside its own process. If the latest event after the process ended is
`pr-opened` or `ci-red`, the process died mid-CI-loop: that is the "none"
row above.

A merge that fails (conflict, branch protection, permission) is **not** a
reason to retry the developer: comment the error, leave the card in
**Review**, remove the worktree, record it as `merge-failed` in the report.

**d. Worktree removal.** Always `worktree_remove(environment_id=…)`; on a
Windows directory lock retry once with `kill_blocking_processes=true`; if it
still fails, record the path under *manual cleanup* in the report and
continue. A stuck worktree never blocks the next package.

### 3. Second pass for set-aside packages

After every Todo package has had its turn, re-dispatch each entry of
`blocked_list` **once** with `attempt+1` and a **fresh** worktree (same
branch name; `worktree_create` again — the branch still exists remotely if
a PR was pushed, so omit `base`). React as in 2c. If the result is `blocked`
again: the question is already on the ticket as the lower plugin's `blocked`
comment; add exactly one line —

> Escalated: needs a human decision — see the blocked event above.

— then → **Question**, `worktree_remove`.

### 4. Final report

One table: `package · result (Done / Question / Review / merge-failed) · PR ·
rounds (from the last event's `rounds`) · attempts`.

The run is **SUCCESS only if every package reached Done**. Anything else is
**PARTIAL** with the list of what is not Done and where it sits. Never
silently drop a package, and never write a "not included" list into any PR —
the PR belongs to the lower plugin and describes one package only.

## Waiting rule

This skill never waits on a human and never polls CI. The only thing it ever
waits on is the completion notification of the `claude -p` process it
started in step 2b. Everything slower than that (CI rounds of up to 45
minutes, three review rounds) happens *inside* that process. So a single
package can occupy you for hours; that is fine. Do not start a second
package to "use the time".

## Why there is no dollar budget

`--max-budget-usd` used to cap each package session at $15. It is gone, and it
should not come back in that shape. Four reasons, all measured on the
2026-08-23/24 runs (32 sessions, 14 packages, five projects):

1. **It regulated the wrong quantity.** The figure Claude Code reports is API
   *list price*, which is not what a subscription is billed. Those 32 sessions
   came to $251 list and consumed roughly 23 percentage points of a weekly
   subscription limit — so the $15 cap was about 1.4% of that week's budget,
   not the third it reads like. A ceiling denominated in a currency nobody in
   the loop is actually spending cannot be set correctly by anyone.
2. **It did not hold.** The check only lands between turns: one package
   finished at **$18.40 against a $15 cap** (+23%), while exactly one of the
   32 sessions ever tripped the abort. A ceiling that is 23% porous in one
   direction and near-inert in the other buys no safety.
3. **It aborted at the most expensive possible moment.** The spend is front-
   loaded — context, planning, two critic gates, test-first implementation —
   so an abort after the implementation and before the PR discards everything
   already paid for, and the retry pays for orientation again. Of the $251,
   **$152 (61%) went on attempts that never reached `ci-green`**; the cheapest
   successful attempts were the ones that inherited a worktree with committed
   work in it. Forcing an abort there is the most costly behaviour available.
4. **It manufactured Questions the section below forbids.** A card whose
   comment says "budget was too small" leaves a human exactly one sensible
   move — raise it and put the card back in Todo. That is a retry wearing a
   decision's clothes.

The platform's own session limits already stop a runaway, at a boundary the
platform owns and enforces consistently. Cost control belongs in the pipeline's
shape — fewer dead attempts, work committed so a retry resumes instead of
restarting, the session's own turn on a model that fits what it does — not in a
second ceiling that fires mid-flight. If a package genuinely never terminates,
that is a bug in the lower plugin's round caps, and it is fixed there.

## Why "retry" is never a valid Question

The escalation rule of the ecosystem says a human is asked only for a
**decision**, never for a **retry**. Every level below you — the lower plugin's
plan-critic, test-critic, review and CI gates with their three-round caps, and
this skill's own second attempt — already does the retrying. By the time a
card reaches `Question`, the ticket comment must state a real fork: a
trade-off the ticket and the code do not settle, or an error with two attempts'
worth of evidence that it is not transient. If a human opens a Question card
and the only sensible reaction is "move it back to Todo and kick it again",
then one of the levels skipped its own attempt — that is a **bug in this
system**, to be fixed in the level that forwarded instead of trying, not a
workflow for the human.

## Hard rules

- **No `AskUserQuestion`.** The tool is not granted to this skill and there is
  nobody to answer. A question becomes a ticket comment plus a `Question`
  card.
- **Unnamed dispatches only.** Every `Agent` call is synchronous and without
  `name`; never `SendMessage`, never resume. Retry = fresh dispatch with
  `attempt+1`.
- **One writer per ticket during a session.** While a package session is
  running, you do not comment on or move that ticket. The lower plugin writes
  the events; you react afterwards.
- **Never edit code**, never run tests, never open or push branches yourself.
  `Edit`/`Write` are not part of this skill's job even if available.
- **Never touch Backlog or Planned.** Never move anything *out of* Question —
  that direction is human-only (→ Todo or → Backlog).
- **Board writes are the status channel; comments only where this skill says**
  (failure summary before → Question, the one-line escalation, the
  merge-not-permitted / merge-failed notes).
- **Project id comes from the repo's `origin`** (or an explicit argument) and is
  threaded into every call; it is never guessed from a name.
- **Sequential.** One package at a time, one process at a time.
