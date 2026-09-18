---
name: run
disable-model-invocation: true
description: Unattended night-shift runner — before enumerating, finishes any CI-green PR an earlier run left unmerged; then orders every open package in the board's Todo column by its blocked_by relations (a blocker also in Todo is processed first; a package whose blocker is still open elsewhere is skipped, left untouched in Todo, and reported), gives each its own worktree, hands it to agent-autonomous-developer in a separate claude -p process started from this skill's own turn, and merges the CI-green PR. A merge conflict gets one rebase-and-retry round (mechanical, absorbed here) before it escalates; a package that only died mid-CI-wait is checked directly (get_pr/list_pipeline_runs) before its retry is spent, never escalated for waiting alone; a blocked event is triaged (a read-only subagent tries to answer it from ticket and code) before it costs a retry; branch protection, a missing permission, and an unresolved mergeability state still move the card to Done or escalate to Question as before. Sequential, no human in the loop, may run for hours. Installed per project; invoke as "/agent-ticket-orchestrator:run" from the project's main checkout (project_id=<id> overrides the repo-derived id).
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

### 0. Pre-flight — finish what an earlier run left open

```
open_prs = list_prs(project_id, status="open", omit_body=True, omit_nulls=True, limit=50)
```

Keep the entries whose head branch starts with `pkg/`. Each one is an earlier
run's package that never merged, and each one is a base this run's worktrees
will **not** contain. For each, in board order: recover the package id from
`pkg/<id>-<slug>` and read its latest `adev:event`
(`list_comments(project_id, ticket_id=<package>, order="desc", limit=20)`).

- **Latest event is `ci-green`** → finish it now: run the full **2c
  `ci-green`** reaction below, classification and rebase retry included.
  This is not a decision, it is yesterday's unfinished job — and it is
  exactly the state the 2026-08-24 incident got stuck in (see *Merge outcomes
  are classified, and a conflict is a retry* below).
- **Anything else** (`blocked`, `failed`, `pr-opened`, `ci-red`, or the card
  already sits in **Question**) → leave it alone. Record
  `carried over: PR #<n>, package <id>, latest event <e>` in the report.

**Never abort the run because a carried-over PR could not be closed out.** An
unattended night that stops for one stuck card is worth less than a night
that finishes the other seven. Instead, state the consequence once, here and
in the report: every open `pkg/*` PR left standing is a base this run's own
packages do not contain, so conflicts against it are *expected* downstream —
and the conflict retry in 2c is exactly what absorbs them. That is why the
two halves of this change belong together.

### 1. Enumerate

```
packages = list_tickets(project_id, column="Todo", status="open", limit=100)
```

**Only Todo.** Never read Backlog or Planned as candidates — those columns are
the human's staging area and the gatekeeper's output; what is in Todo is what
the human released. Process in board order (oldest first), reordered by
dependency as described next.

### 1a. Order Todo by dependency

```
1. board_order = the Todo tickets from Step 1, oldest first (unchanged).
2. For each package p, one call:
     get_ticket(project_id, p, include_relations=True, include_comments=False)
   blockers[p] = [r.ticket_id for r in relations if r.kind == "blocked_by"]
   On a provider whose list_relation_kinds provider_support lacks blocked_by
   (GitLab), ALSO read the newest "## Dependency (gatekeeper)" comment's
   <!-- gatekeeper:deps v1 ... --> block via list_comments and take its
   blocked_by: line — same dumb key: value reader as adev:event, one more
   block, no new mechanism. Skip that call entirely on github/azuredevops.
3. Classify every blocker b (memoise per b for the whole run — see "When is
   a blocker resolved" below):
     - b resolved            -> drop the edge
     - b in board_order      -> INTERNAL edge, orderable inside this run
     - otherwise             -> EXTERNAL-OPEN
4. Every package with an EXTERNAL-OPEN blocker is SKIPPED. Remove it from the
   graph; do not move its card; report
   `skipped: blocked by #<b> (<its column, or "closed elsewhere: no">)`.
5. Topological order over what remains, INTERNAL edges only — Kahn with a
   board-order tie-break, and no other heuristic:
     ready = packages with no unsatisfied blocker, in board order
     repeat: emit the FIRST of ready (board order); re-evaluate the packages
             it unblocked; merge them back into ready keeping board order
   Deterministic, and the only reordering this skill ever performs. There is
   no priority field, no "smallest first", nothing else.
6. Transitive skip: a package whose only blocker was itself SKIPPED is
   SKIPPED too — `skipped: blocker #<b> skipped`.
7. Cycle. Anything still unemitted when `ready` runs empty is in a cycle or
   downstream of one. Emit those at the very END, in board order, and record
   one line: `dependency cycle: #a -> #b -> #a, processed in board order`.
   NEVER drop a package and NEVER abort the run for a cycle: a cycle is a
   human's ten-second fix on the board, and losing a night's other seven
   packages to it is the failure mode this skill exists to avoid.
```

Reading a blocker's ticket by id is not "touching Backlog or Planned". That
rule forbids *selecting candidates from* and *writing to* those columns; it
has never forbidden looking at one ticket you were pointed at. `run` still
enumerates Todo only and still writes nothing outside Todo → Doing →
Done/Question.

### When is a blocker resolved

A blocker `#b` counts as **resolved** when **any** of:

1. `run` itself moved `#b` to **Done** earlier in this very run. Keep a
   `done_this_run` set; it is authoritative and needs no re-read.
2. `get_ticket(project_id, #b, include_custom_fields=True,
   include_comments=False, include_relations=False)` returns
   `custom_fields["Status"]` equal to the **native name of the logical `Done`**
   column, from the map Precondition 2 already built.
3. `#b` is `status: closed` **and** its board column is not one of `Backlog`,
   `Planned`, `Todo`, `Doing` — a ticket closed without ever having been
   queued (a duplicate, a wontfix, a hand-closed ticket, or an epic child
   closed by a `Closes #<n>`). A ticket that is closed while sitting in
   `Todo` is a contradiction: treat it as **not** resolved and record it,
   because it is far more likely a mis-close than finished work.

Everything else is **not** resolved: `Review` (PR open, CI running, or merge
not permitted), `Question`, `Doing`, and no board item at all.

**Why "closed" alone is not the test.** An epic package ticket is moved to
the **Done column** by this skill and is *not* closed — only its children
close, through `Closes #<n>` in the lower plugin's PR body. A `status`-only
test would therefore report every finished epic as still blocking and skip
its dependents forever. Symmetrically, an epic *child* is closed but never
enters a column, so a column-only test would report finished work as still
blocking. The column is the primary signal — comments are the log, columns
are the signal — and `closed` is the fallback for tickets that never travel
the board.

One `get_ticket` per distinct blocker per run, memoised. Nothing here polls.

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

The guarantee is only worth something if it is **verified**, not assumed —
step **a** below checks the previous package actually cleared before cutting
the next worktree. When it did not, `run` does not stop the night for it: it
proceeds and records the violation, because the merge-outcome classification
in 2c is exactly what absorbs the resulting conflicts. See *Merge outcomes
are classified, and a conflict is a retry* below.

**a. Claim + worktree.**

**Re-check blockers at dispatch time.** Re-read `blockers[p]` one last time,
against the same resolved test as 1a. Step 1a's order was computed before any
package ran; the run's own outcomes are the only new information, and they
arrive too late for the ordering pass.

- Every blocker resolved → proceed exactly as below.
- A blocker that was in Todo did **not** reach Done — it ended in
  `Question`, `Review` or `Doing` → **skip this package**. Leave the card in
  **Todo**, do not move it to Doing, do not cut a worktree, do not start a
  session, and record `skipped: blocker #<b> ended in <column>`. Then
  continue with the next package. This is the
  blocker-in-Todo-that-did-not-land case, and it degrades to the ordinary
  skip on purpose: a package whose precondition did not land is exactly as
  un-runnable as one whose blocker was never in Todo at all.

A skip is never an abort and never a Question. Record it and continue — the
same rule as every other failure mode in this skill.

**Gate on the previous package — verify, do not assume.** Here "previous
package" means the last package this run actually *processed*, in the
dependency order from Step 1a — not necessarily the one immediately before it
in board order. Skip this for the first package processed in the run (Step
0's pre-flight already swept every carried-over `pkg/*` PR).

```
still_open = list_prs(project_id, status="open", head="pkg/<prev id>-<prev slug>", limit=5)
```

- **empty** → proceed; either the previous package merged, or it never opened
  a PR (`blocked`/`failed` before Phase 5) — either way this worktree's base
  is honest.
- **not empty**, and the previous package's latest event is `ci-green` → one
  more merge attempt, the full 2c classification below, before continuing.
  If it merges, proceed normally.
- **not empty** and it still will not merge → **continue anyway**, and record
  `sequencing: package <n-1> left PR #<x> open` in the report exactly once.
  From here the run knowingly cuts this worktree from a base that lacks
  `<n-1>`; the conflict retry in 2c handles the fallout. Do not stop the run,
  do not skip the remaining packages, and do not "fix" this by parallelising.

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
| `ci-green` | `merge_pr(project_id, pr_id=<pr>)` with defaults (the project's default merge method; do not pass `merge_method`). Children of an epic close through `Closes #<n>` in the PR body — you do not close them. **Verify `pull_request.merged == true` in the response** before treating it as merged — a populated `merge_commit_sha` alone is a speculative pre-merge preview, not proof. Then → `Done`, then `worktree_remove(environment_id=<id>)`. If merge is not permitted (Precondition 3): leave in Review, comment, remove worktree. If the call errors or returns `merged: false`: **classify before reacting** — see *When the merge fails* below. |
| `blocked` | Triage before you retry or escalate — see *Blocked events are triaged before they cost a retry* below. |
| `failed`, or no terminal event (non-zero exit, or the latest event is a non-terminal one like `pr-opened`/`ci-red`/`review-verdict` — the process died mid-pipeline) | **First**, if a PR already exists for this package, run *The pre-retry CI check* below — it can resolve the package (straight to the `ci-green` reaction) without spending the retry. Only when that check does not resolve it: **one** fresh start (step b, same script) with `attempt+1`, same worktree. If that ends `ci-green` → handle as above. If still `failed`/none → `add_comment` summarising both attempts (event, `rounds` with the findings-vs-infra split, `pr`, both `RUNDIR`s), → **Question**, `worktree_remove`. |

A `pr-opened` or `ci-red` event seen *while the process is still alive* is
not terminal — but you never see that state, because you only act after
the process has ended. CI waiting is the lower plugin's job,
inside its own process. If the latest event after the process ended is
`pr-opened` or `ci-red`, the process died mid-CI-loop: that is the "none"
row above.

**Blocked events are triaged before they cost a retry.** A `blocked` event means the lower plugin
genuinely could not decide something — but "genuinely could not decide" and "a retry would change
nothing" are not the same fact, and the old unconditional path (set aside, one full retry session
at the end of the run, no matter what) spent a whole session on cases that told us in their own
text that a retry was pointless. Incident, `agent-project-issues` package #265
(`agent-ticket-orchestrator#7`): the `blocked` event's own text predicted a Codex re-review would
"very likely just exhaust the cap without new information" — a human who happened to be watching
answered it from the ticket comment alone, no retry session needed.

So instead of setting the package aside:

1. Dispatch the **triage** subagent (fresh, unnamed, synchronous) with the `blocked` event's
   question, options, recommendation, and what was already checked, plus `project_id`, `package`,
   `local_path`. It reads ticket, comments, siblings and code — the same test the `clarifier`
   already applies to Backlog questions — and ends `STATUS: ANSWERED` (a chosen option plus
   reasoning) or `STATUS: ESCALATE` (why it is not answerable from context).
2. **`ANSWERED`** → `add_comment(project_id, ticket_id=<package>, body=…)` with heading
   `## Blocked triage (run)`, the question, the chosen option, and the reasoning — then
   immediately re-dispatch (step 2b, same script, `attempt+1`, same worktree). The lower plugin's
   `context-extractor` re-reads the ticket transcript on the next attempt and picks the answer up;
   no change to its contract. Do **not** wait for every other Todo package to have its turn first —
   the whole point is that nothing about this answer changes by waiting.
3. **`ESCALATE`** → `add_comment` with the original question plus one line — *"Escalated: not
   answerable from ticket, comments or code — see the blocked event above."* — → **Question**,
   `worktree_remove`. Do not spend a retry session on a question triage already told you a retry
   cannot resolve.
4. **Triage once per package per run.** Before dispatching triage, check whether this package's
   ticket already carries a `## Blocked triage (run)` comment from earlier in this run
   (`list_comments`, search for the heading). If it does, a second `blocked` event goes straight to
   the `ESCALATE` reaction above — a triage-answered redispatch that blocks again means the answer
   did not hold or a materially different question surfaced, and either way a second guess is not
   this system's to make alone.

This replaces the old two-stage design entirely: there is no more "second pass at the end of the
run" for `blocked` packages, and `blocked_list` does not exist. A `blocked` event is triaged the
moment it is read, in board order, exactly like every other reaction in this step.

**When the merge fails — classify before reacting.**

A merge failure is not one thing. Call `get_pr(project_id, pr_id=<pr>)`
**once** and read `pull_request`. If `mergeable_state` is `null` or
`"unknown"`, `Bash("sleep 20")` once and fetch a **second and last** time —
never a third. On Azure DevOps `mergeable_state` is permanently `null`; do
not fetch a second time there, go straight to classifying from the error
text. Then, in this order:

| what you see | what it is | you do |
|---|---|---|
| `merged: true` (or `status: "merged"`) | already merged — a race, or a human merged it by hand | treat as a successful merge: → **Done**, `worktree_remove`. Note `merged externally` in the report. |
| GitHub `mergeable_state: "dirty"`, or GitLab `detailed_merge_status` in {`conflict`, `need_rebase`}, or (any provider) the merge error text names a conflict | **conflict** — the base moved | **the rebase retry** below. Not a Question. |
| GitHub `mergeable_state: "behind"` | base moved, no textual conflict, but the branch is not up to date | **the rebase retry** below — the session finds a clean rebase and goes straight to push + CI. |
| GitHub `mergeable_state` in {`blocked`, `draft`, `unstable`, `has_hooks`}, or GitLab `detailed_merge_status` in {`ci_must_pass`, `ci_still_running`, `blocked_status`, `discussions_not_resolved`, `not_approved`, `draft_status`, `broken_status`, `not_open`} | branch protection, a required review, a required check | **today's behaviour**: `add_comment` with the exact error and the `mergeable_state`, leave the card in **Review**, `worktree_remove`, record `merge-failed` in the report. A human decides. |
| a permission error (`pulls.merge`, 403, "not permitted") | permission | as above, with the note *"CI green, merge not permitted — merge manually"*. |
| `mergeable_state` still uncomputed after the second fetch, and the error text names nothing | unknown | **today's behaviour** (Review, `merge-failed (state unknown)`). Never guess a conflict from silence — a wrong guess costs a whole session. |

A **conflict is mechanical** and belongs to this system. Everything else on
this table is a decision or a configuration, and belongs to a human. See
*Why "retry" is never a valid Question*.

**The pre-retry CI check — waiting is not failing.** A process can end on `failed` or a
non-terminal event for a reason that has nothing to do with the package: it can die while a gating
CI run is still `in_progress`, or even after that run has already finished green, simply because
the session ended before it read the result. Two independent incidents escalated to Question on
exactly this (`agent-ticket-orchestrator#8`): `agent-worktree` package #165 (one CI run green,
the other still running when the session exited) and `agent-project-issues` package #268 (**both**
gating runs had already completed successfully before the session exited — there was nothing left
to wait for, let alone decide). Before spending the `failed`/no-terminal-event retry:

1. If the latest event carries a `pr:` value, or `list_prs(project_id, head="pkg/<id>-<slug>",
   status="open", limit=5)` finds one, call `get_pr(project_id, pr_id=<pr>)` once and
   `list_pipeline_runs(project_id, commit_sha=<pr.head.sha>, limit=20)`.
2. **Every run `conclusion == "success"`**, and `mergeable_state` does not read as a conflict per
   the table above → the package is finished in every way that matters even though the process
   never said so. Go straight to the `ci-green` reaction (merge, verify `merged: true`, → Done)
   **without spending the retry**.
3. **At least one run is still `status != "completed"`** → the package is only waiting.
   `Bash("sleep 60")` once and re-check — the same one-more-look pattern the merge classification
   above already uses, never a third check here either. Still not finished → *now* the ordinary
   retry applies (step b, `attempt+1`); this is one extra look, not an unbounded wait, and it does
   not conflict with *Waiting rule* below (that rule is about never polling CI in place of the
   lower plugin's own Phase 6 loop — this is a single, bounded recheck of a process that has
   already ended, not a wait *inside* a running process).
4. **Any run has failed** → this is a genuine `failed`; the ordinary retry applies unchanged.
5. **No PR exists yet for this package** → nothing to check; the ordinary retry applies unchanged.

**The rebase retry.** One attempt, once per package per run — a budget
independent of the `failed`-retry budget above and the triage-driven
re-dispatch a `blocked` event can trigger (see *Merge outcomes are classified,
and a conflict is a retry* below for why they do not share a counter).

1. **Reuse the worktree.** You have not removed it yet at this point in 2c,
   and it is on `pkg/<id>-<slug>` with the package's commits. Do **not**
   remove and re-create it: a worktree `id` is not stable across remove +
   create. If it is genuinely gone (this `run` resumed after a crash),
   `environment_list()` and reuse the entry for that branch; only if there is
   none, `worktree_create(repo_root=<local_path>, branch="pkg/<id>-<slug>")` —
   **omit `base`**, the branch already exists remotely — then re-read the
   `id` from a fresh `environment_list()`. Never re-cut the branch from the
   default branch: that discards the package's commits.
2. **Dispatch, exactly as in step 2b**, same script, `attempt+1`. No extra
   argument to the script or the lower plugin: the lower plugin orients
   itself on the branch (an open PR plus green CI on this exact HEAD plus a
   base that moved means "repair only" to it — see the lower plugin's
   `AGENTS.md`, "Phase 0 orients on the branch instead of taking a
   parameter"). Then stop and wait for the completion notification, exactly
   as step 2b. A repair session is short but still runs the CI gate — budget
   the same 45-minute rounds.
3. **React to the new latest event.**
   - `ci-green` → back to the top of the `ci-green` row: `merge_pr`, verify
     `merged: true`, → **Done**, `worktree_remove`. Note `merged after
     rebase` in the report.
   - `ci-green` and the merge fails **again** → stop. `add_comment` naming
     both merge attempts, both `mergeable_state` values and both `RUNDIR`s →
     **Question**, `worktree_remove`, note `merge-conflict` in the report.
   - `blocked` → the resolution needs a product decision (two packages
     implemented incompatible behaviour) — the rebase retry's own single
     attempt is already spent, so this does not also draw on the triage
     mechanism below (that budget is for the *primary* `blocked` reaction,
     not a second one inside an already-spent repair attempt). `add_comment`
     with one line — *"Escalated after a rebase attempt: the conflict
     resolution is a decision — see the blocked event above."* → **Question**,
     `worktree_remove`.
   - `failed`, or no terminal event → `add_comment` with the failure summary
     and both `RUNDIR`s → **Question**, `worktree_remove`. **Do not** spend
     the `failed`-retry budget on a repair session — it already had its own
     budget, in step 2.

**`ci-green` outranks everything.** Before moving any card to **Question**
for any reason, re-read its latest `adev:event`. If it is `ci-green`, the
package's *work* is finished and only the merge is left — the only reactions
available to you are exactly this classification table and nothing else.
Never write Question for a reason unrelated to the merge, never leave a
`ci-green` package in **Doing**, and never carry a stale escalation from an
earlier attempt forward past a later `ci-green`. Only the **latest** event
counts — that is why you always read `list_comments(order="desc")` and take
the *first* `adev:event`, before every decision, including a triage-driven
re-dispatch and every escalation.

**d. Worktree removal.** Always `worktree_remove(environment_id=…)`; on a
Windows directory lock retry once with `kill_blocking_processes=true`; if it
still fails, record the path under *manual cleanup* in the report and
continue. A stuck worktree never blocks the next package.

### 3. Final report

One table: `package · result (Done / Question / Review / Skipped) · note · PR
· rounds (from the last event's `rounds`) · attempts`. `note` is empty for a
clean Done, and otherwise one of: `merged after rebase`, `merged externally`,
`merge-conflict`, `merge-failed`, `merge-not-permitted`, `blocked-escalated`,
`manual cleanup: <path>`, `skipped: blocked by #<b> (<column>)`,
`skipped: blocker #<b> ended in <column>`, `skipped: blocker #<b> skipped`.
Above the table, one line per carried-over PR found by the Step 0 pre-flight,
one line per sequencing violation observed during the run, and one line per
dependency cycle found in Step 1a (`dependency cycle: #a -> #b -> #a,
processed in board order`) — all named above.

The run is **SUCCESS only if every package reached Done**. Anything else is
**PARTIAL** with the list of what is not Done and where it sits. A skipped
package makes the run PARTIAL, correctly — it is not Done. But it is a
**benign** partial that names its own blocker and its own next step, unlike a
failure; do not blur the two in the report. Never silently drop a package,
and never write a "not included" list into any PR — the PR belongs to the
lower plugin and describes one package only.

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

A merge conflict is the canonical case this section was written for. The base
moved because another package merged first — nobody made a choice, nothing
about the ticket is in question, and the fix (rebase, resolve, re-verify,
re-push, re-merge) is exactly as mechanical as a red CI run. Treating it as a
Question, as this skill used to, put "move it back to Todo and kick it again"
in a human's hands for a problem this system was always capable of absorbing
itself. See *Merge outcomes are classified, and a conflict is a retry* below.

## Merge outcomes are classified, and a conflict is a retry

**Incident, 2026-08-24, `agent-web-tester`.** Package #4 reached `ci-green`
(PR #14) but sat behind a stale `Question` escalation left over from before
`--max-budget-usd` was removed, so it never merged. Because #4 never merged,
package #5's worktree was cut from the **old** default branch and ran its
full cycle in parallel with #4's still-open PR — the "never two open PRs"
guarantee (see § 2's rationale) silently did not hold, because nothing checked
it. A human merged #14 by hand; #5 then also reached `ci-green` (PR #16), but
by then the base had moved and PR #16 came back `mergeable_state: dirty`. The
only reaction available under the old contract was to leave #16 in Review and
report `merge-failed` — a dead end that needed a human to rebase it by hand,
for a conflict that carried no decision at all.

Three changes close that hole, all documented at their point of use above:

- **Step 0's pre-flight** finishes any `ci-green` package an earlier run left
  unmerged, *before* enumerating Todo — the exact situation package #4 was
  stuck in.
- **Step 2a's gate** verifies the previous package actually cleared before
  cutting the next worktree, instead of assuming the sequencing guarantee
  held.
- **Step 2c's merge-outcome classification** tells a conflict (mechanical,
  this system's job) apart from branch protection, a missing permission, or
  an unresolved state (all still a human's job), and gives the conflict case
  exactly one rebase retry before it, too, becomes a Question.

**Independent retry budgets, not a shared counter:** one `failed`/
no-terminal-event retry (2c, now preceded by the pre-retry CI check, which
does not itself spend the budget), one rebase retry (the conflict path), and
one triage-driven re-dispatch when a `blocked` event turns out to be
`ANSWERED` (also 2c — see *Blocked events are triaged before they cost a
retry*, above). A package can legitimately reach `attempt=3` — failed once,
`ci-green` on the second try, conflicted and rebased on the third — and
`attempt` stays a monotonically increasing session counter across all of it,
exactly as it already was. **Hard ceiling: at most three sessions per package
per run, at most one of which is a rebase session, at most one of which is a
triage-driven re-dispatch.** A shared counter would reproduce the exact dead
end this incident describes: a package that spent its one retry on an earlier
crash, then reached `ci-green`, then had nothing left for a purely mechanical
conflict.

The lower plugin (`agent-autonomous-developer`) makes the rebase retry
possible: its `process-ticket` skill orients on the branch itself (an open
PR, green CI on the exact current HEAD, and a base that moved means "repair
only" to it) rather than taking a parameter from this skill — see its
`AGENTS.md`, "Phase 0 orients on the branch instead of taking a parameter".
That is why step 2c's rebase retry dispatches with the **same script call**
as an ordinary retry, just `attempt+1`.

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
  merge-not-permitted / merge-failed notes, and the merge-outcome
  classification's own comments: both merge attempts on a persisted conflict,
  the one-line escalation after a `blocked` rebase).
- **Project id comes from the repo's `origin`** (or an explicit argument) and is
  threaded into every call; it is never guessed from a name.
- **Sequential.** One package at a time, one process at a time.
- **`ci-green` outranks everything.** A package whose latest event is
  `ci-green` may only be reacted to via the 2c merge-outcome classification —
  never moved to Question for an unrelated reason, never left in Doing, never
  judged by a stale earlier event. See *Merge outcomes are classified, and a
  conflict is a retry*.
- **A merge conflict is a retry, not a Question.** One rebase attempt, same
  script, `attempt+1`, before it escalates. Branch protection, a missing
  permission, and an unresolved mergeability state remain human-only.
- **A blocked package is skipped, never reordered past its blocker and never
  escalated.** It stays in Todo; the next run picks it up once the blocker
  reaches Done. See *1a. Order Todo by dependency*.
- **A dependency cycle never stops the night.** Report it, process the
  cycle's members in board order at the end, continue.
