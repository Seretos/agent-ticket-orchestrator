# agent-ticket-orchestrator

Pure skill + agents plugin — no binary, no MCP server. The **upper** layer of the Seretos ticket pipeline: it selects, bundles, clarifies, dispatches, moves board columns and merges. The **lower** layer, `agent-autonomous-developer`, turns one work package into one CI-green PR and knows nothing about this plugin. Three skills (`gatekeeper`, `run`, `ticket`), three subagents (`bundler`, `clarifier`, `triage`). README.md covers *what* it does and how to install; the skills and agents document their own rules. This file records only what you cannot reconstruct from any single file.

## Installed per project; the project id comes from the repo

This plugin is enabled in each project's own `.claude/settings.json`, next to `agent-autonomous-developer` and `agent-project-issues`, and is invoked from that project's main checkout. Both skills resolve the `project_id` from `git remote get-url origin` → `owner/repo` → the `list_projects` entry with that `path`; an explicit `project_id=` argument overrides. There is deliberately **no ecosystem-level orchestration here** (release chains, dependency bumps across repos, marketplace publishing) — that is a later, separate layer that would call these skills per project. Keep this plugin ignorant of other projects.

## Contracts an agent won't infer from the tree

### The lower plugins' entry points (the only thing this plugin knows about them)

the `run` skill starts, from its own main turn, with cwd = the prepared worktree:

```
claude -p "<entry> package=<id> project_id=<project> worktree_path=<abs> base_branch=<default>" \
  --permission-mode bypassPermissions --disallowedTools AskUserQuestion \
  --output-format stream-json --verbose --model <model> > <rundir>/stream.jsonl 2> <rundir>/stderr.txt
```

`<entry>` is `/agent-autonomous-developer:process-developer` for a `code` package and `/agent-autonomous-prompt-engineer:process-prompt-engineer` for a package labelled `lane:prose` — same parameters, same `adev:event v1` contract, and the entry is the **only** thing that differs (see "One package, one lane", below). Both skills were renamed from `process-ticket`/their scaffold names for this; a `run` released before both lower plugins ship the new names dispatches a skill that does not exist, which is why `plugin.json` pins the developer's minimum version (the prompt engineer is optional and detected per project instead).

A *work package* is a single ticket id or an epic id (= all its `list_hierarchy` children, one branch, one PR with one `Closes #<n>` per child). The lower plugin never creates worktrees or branches, never touches columns, never selects tickets, never asks a human. This plugin never writes code. Changing either side's half of that split is a contract change for both repositories.

### The comment-event contract (lower writes, this plugin reads)

The lower plugin posts comments on the **package ticket** (the epic when the package is an epic), each starting with a machine block, parsed as dumb `key: value` lines (empty = unknown, unknown keys ignored):

```
<!-- adev:event v1
event: <name>   package: <id>   attempt: <n>
generation: 1/2
rounds: plan-critic=1/3(1f,0i) test-critic=0/3 review=2/3(2f,0i) ci=1/3(0f,1i)
pr: <number or empty>   ci_run: <id or empty>
-->
```

`rounds`: `<gate>=<used>/<cap>(<f>f,<i>i)` — `f` rounds ended with real findings, `i` rounds were lost to infrastructure; both count toward the cap. As of the lower plugin's Phase R (rebase-and-repair), `rounds` also carries a `rebase=<u>/3(<f>f,<i>i)` sub-field, `0/3` on a session that never entered Phase R; `run` reads `rounds` as opaque prose and does not need to parse it. A separate `generation: <g>/2` field (2026-08-25) tracks the lower plugin's own replan mechanism — `1/2` unless a plan-critic/test-critic/review gate stagnated and triggered a fresh planner dispatch; `run` also reads this as opaque prose, purely informational. Events, exhaustive: `started`, `plan-committed`, `plan-critic-verdict`, `tests-red`, `test-critic-verdict`, `tests-green` (local pre-filter only — never success), `review-verdict`, `pr-opened`, `ci-red` (`ci_run` filled), `replan-triggered` (non-terminal — the lower plugin's own turn continues after it, see its `AGENTS.md`, "Round caps are progress-based, not just round-counted"), and the three **terminal** ones: `ci-green` (the only success signal), `blocked` (needs a human decision; text = question, options, recommendation, what was checked), `failed` (terminal non-decision failure; text distinguishes findings vs infra). A process that ended without a terminal event counts as `failed` (`replan-triggered` included — a process that dies mid-replan is exactly as unfinished as one that dies mid-review). The vocabulary is unchanged by Phase R — see the lower plugin's `AGENTS.md`, "Phase 0 orients on the branch instead of taking a parameter".

Reactions (`skills/run/SKILL.md` implements exactly this): `ci-green` → `merge_pr` (verify `merged: true`), → Done, remove worktree · merge fails on a **conflict** (`mergeable_state: dirty`/`behind` or the GitLab equivalent) → one rebase-and-retry dispatch, same script, `attempt+1`; merges after that → Done, still conflicted → Question · merge fails on branch protection / permission / an unresolved state → `add_comment` with the exact error, Question, `merge-failed`, human decides · `blocked` → triaged by a read-only subagent before it costs anything (answerable → answer posted, immediate re-dispatch; not answerable → Question right away) · `failed`/no terminal event → checked directly against the PR's actual CI/mergeability state first (a package that only died mid-CI-wait is not `failed`); only if that check does not resolve it, one fresh re-dispatch, still failed → Question with the failure summary. These are **independent** retry budgets (one `failed` retry, one rebase retry, one triage-driven re-dispatch), not a shared counter — see `skills/run/SKILL.md`, "Merge outcomes are classified, and a conflict is a retry". The run succeeds only if every package reached Done; partial is reported as partial; no "not included" list in any PR, ever.

`run` also runs a **Step 0 pre-flight**, before enumerating Todo: any open `pkg/*` PR left over from an earlier run gets the same `ci-green` reaction if that is its latest event, so a package finished by an earlier night's run does not sit unmerged forever, blocking every worktree cut after it. And **Step 2a gates on the previous package** — it verifies with `list_prs` that the previous package actually cleared before cutting the next worktree, rather than assuming the "never two open PRs" guarantee held. Neither check aborts the run when it finds a problem; both record it and let the merge-outcome classification absorb the consequence. This closes the gap a real incident found (2026-08-24, `agent-web-tester`): a stale `Question` card kept a `ci-green` package unmerged, the next package's worktree was cut from the stale base anyway, and the eventual conflict had no path back except a human. Full account in `skills/run/SKILL.md`, "Merge outcomes are classified, and a conflict is a retry".

`run` calls `get_pr` and `list_prs` for the merge-outcome classification **and** for the pre-retry CI check (see below). Both are read-only and need no permission beyond the project's existing read access; `merge_pr` still needs `pulls.merge`, unchanged.

### Waiting on CI is not a decision, and neither is a `blocked` event nobody tried to answer

Two follow-on fixes to the same escalation philosophy, both in `skills/run/SKILL.md` at their
point of use (`agent-ticket-orchestrator#7`, `#8`):

- **The pre-retry CI check.** A process that ends `failed` or on a non-terminal event can simply
  have died while its PR's gating CI was still running, or even after it had already finished
  green — two independent incidents (`agent-worktree` #165, `agent-project-issues` #268) escalated
  to Question on exactly that, one of them with both gating runs already green *before* the session
  exited. `run` now checks `get_pr`/`list_pipeline_runs` directly before treating `failed`/no
  terminal event as retry-worthy, and resolves the package (or waits one bounded extra look) instead
  of spending the retry on a package that was never actually failed.
- **`blocked` events are triaged, not always retried.** The old contract set every `blocked`
  package aside and gave it exactly one full retry session at the very end of the run, unconditional
  on whether a retry could plausibly change anything. A recorded incident (`agent-project-issues`
  package #265) showed the `blocked` event's own text predicting the retry would be wasted, and a
  human answered it from the ticket in seconds once they happened to look. The new `triage`
  subagent (read-only, same escalation test the `clarifier` applies before a run even starts) reads
  the question against ticket, code and comments: `ANSWERED` → the answer is posted as a comment
  and the package is re-dispatched immediately (no waiting for other packages first); `ESCALATE` →
  straight to Question, no retry spent. At most one triage attempt per package per run.

### Dependencies are relations, and "done" is a column, not a closed flag

A "wait for #X" comment is invisible to a human moving Planned → Todo and completely invisible to `run`. Incident: `agent-project-issues` epic #291 compiles only against `lib-python-projects` v0.3.14, which open ticket #289 was to introduce; the `clarifier` found it, the `gatekeeper` had nowhere to put it, and the only fix was a hand-written `blocked_by` relation applied after the fact (`agent-ticket-orchestrator#10`).

The fix is a three-level split: `bundler`/`clarifier` **report** raw ids (`depends_on` and `needed_by` in the bundler's JSON, `depends_on:` and `needed_by:` in the clarifier's `clarifier:frame` block — see below), `gatekeeper` **lifts and writes** (`blocked_by` on the package ticket, both ends lifted via the Step 2 package map and `list_hierarchy`), `run` **obeys** (`skills/run/SKILL.md` § 1a's topological order, plus a dispatch-time re-check). Neither `bundler` nor `clarifier` ever writes a relation, checks a column, or resolves an id to an epic — they have no write tools and no reason to guess at board state that changes between their pass and the gatekeeper's write.

**A reverse edge is written on the dependent, even a run-owned Question card.** `depends_on` only covers a ticket the pass is clarifying; it cannot say "a ticket *outside* this pass waits for this package". Incident (`agent-project-issues`): #375 introduced what #365 — a Question card `run` owned — was waiting for. Both finders saw it: the bundler wrote "That edge belongs on #365 … It isn't in this list" in its rationale, the clarifier called #375 "the enabler for #365" in prose, and neither had a key to put it in; the gatekeeper's "never touch a Question card you did not put there" left it nowhere to write, and a human had to add the `blocked_by` by hand, or `run` would have dispatched #365 before #375 merged (`agent-ticket-orchestrator#46`). Hence `needed_by`, same raw-id/no-lifting/no-column-check rules as `depends_on`, and a second Step 3.5 loop that writes `blocked_by` **from the dependent to the package**, verified by the same `relation-readback.py` against the dependent's relations; a persisting gap withholds the *package* from Planned, so the next pass re-writes the edge before the package can be released. The relation — plus, on GitLab, the one `## Dependency (gatekeeper)` comment that is its record — is the only write a run-owned card ever gets from the gatekeeper: it is the only thing `run`'s § 1a reads to hold a card, while any comment, label or column move would be the gatekeeper deciding on a card `run` and a human own.

**Epic lifting exists because only the package ticket travels the board.** A relation pointing at an epic child can never be observed satisfied: the child closes as a side effect of the epic's PR (`Closes #<n>`) and never has a column of its own. Lifting is the gatekeeper's job, not the clarifier's or `run`'s, because it is the only level that knows the epic↔child map at the moment the dependency is found — `run` only ever reads package tickets, and pushing lifting into it would break that invariant.

**Why "closed" alone is the wrong resolved-test, and why "column alone" is also wrong.** `run` treats a blocker as resolved when it is either in the **Done column** or **closed while never having queued through the board** (`skills/run/SKILL.md`, "When is a blocker resolved"). Both halves are load-bearing: an epic package ticket reaches **Done** by column but is *not* closed — only its children close, through `Closes #<n>` in the lower plugin's PR — so a status-only test would report every finished epic as still blocking and skip its dependents forever. Symmetrically, an epic *child* is closed but never enters a column, so a column-only test would report finished work as still blocking. This is unreconstructable from any one file, because it combines the lower plugin's `Closes #<n>` behaviour with this plugin's column semantics — that combination is exactly what the fact records.

**Blocked ≠ unplanned.** A `blocked_by` relation withholds a package from *execution*, never from Planned — the enforcement point is `run` alone (its dependency order and dispatch-time re-check), so that a human can still see and release a blocked card, and a package whose blocker is itself only a Backlog candidate of the same gatekeeper pass still reaches Planned rather than waiting on a human to notice and re-run.

**The provider branch is permanent.** No relation kind is portable across all three providers; which provider lacks which kind is in the agent-project-issues skill, "Relations: direction matters", which leaves the fallback convention to the caller. GitLab's record is `relates_to` plus a `<!-- gatekeeper:deps v1 -->` comment block, read by the same dumb `key: value` reader as `adev:event` — one parsing convention in this repo, not two.

A dependency cycle, or a package whose blocker never resolves, never aborts a `run` — Step 1a reports it (`dependency cycle: …`, `skipped: …`) and processes the cycle's members in board order at the end, the same "record it and continue" discipline as every other `run` failure mode.

### A ticket's own sequencing statement is not a collision, and a relation write is verified, not assumed

Incident: on `seretos-games/unity-fps-controls`, a `gatekeeper` pass bundled two large tickets (#9, a VR rig; #14, a teleport-anchor contract) as one `collision` epic, even though #14's own text sequenced the overlap behind #9 ("a second step after #9") — a dependency, not a cycle. The same pass then wrote only one of the five `depends_on` relations the `bundler` had reported for the epic, and nothing noticed; `run` could not order the rest. The epic (#16) did finish — `ci-green` on its first attempt, never escalated — but at 11 review rounds, 8 plan-critic verdicts and a forced generation-2 replan, and three defects a person would have seen (#35, #41, #43) escaped it into tickets of their own (`agent-ticket-orchestrator#25`; an earlier version of this paragraph recorded it as "6-plus hours, unfinished", which the ticket's own event history contradicts — corrected by `agent-ticket-orchestrator#41`, because a wrong reason here is what gets re-derived later).

**Sequencing is a dependency.** `agents/bundler.md` Step 3 now treats a ticket's own sequencing statement about another ticket — a non-goal, "a second step after #X", "only uses …" — as `depends_on`, never `collision`: a mutual reference where one side states the order is an order, not a cycle. The `## Worked cuts` section carries the #9/#14 case verbatim, the same shape as the clarifier's `## Worked frames`.

**A `collision` package is capped by size, and a pair the cap rejects is a Question with a proposed vertical split.** Each ticket in the bundler's JSON carries a required `size: small|medium|large` (a footprint estimate from Step 2). A `collision` package carries at most one `size: large` ticket — `effort` keeps its own, unrelated ~5-ticket cap. Until `agent-ticket-orchestrator#41` the bundler owed a `recut` for two overlapping large tickets it declined to bundle — a slice it invented, which Step 3.7 wrote onto both tickets with no confirmation round. An audit of all 34 configured projects (2026-09-21) found that hatch had never fired, that the only cuts that ever created tickets were the three bad ones `#40` removes, and that 35 of 38 bundles merged `ci-green` with none undone: merging is the gatekeeper's working half, cutting has no track record to protect. So the bundler now only **reports** an `oversized` entry (`{tickets, why, slices: [{slice, observable, covers}]}`); each slice must name what a *user of the software* can observe once it ships — #16's own plan (extract `RigMotionCore`, then `VrPlayerRig`, then `RigTeleport`) is the horizontal counter-example, "the rig stands, looks and moves" / "you teleport to an anchor" the vertical one — and a bundler that finds no two such slices emits `"slices": []` rather than fabricating. The gatekeeper **writes** one `## Clarification needed (gatekeeper)` comment on the lower ticket id (options: the proposed slices, run both as filed in dependency order, the owner cuts it) with a `gatekeeper:oversized v1` block, a pointer comment on the other card, and moves **both** cards to Question, neither to Planned; the existing answered-card pickup brings them back, with Step 1 reading the answer for the pointer card on the `proposal_on:` ticket. `run` is untouched. Step 3.7 survives with the lane split as its only `recut` source, so "no confirmation round" now describes only a split the architecture forces. A lone large ticket is never questioned for its size. If the bundler still emits an oversized `collision` package (its own cap is prose, not enforcement), the gatekeeper's Step 2 rejects it into `single` packages after the fact and still writes the `depends_on` edge the split needs.

**A returning ticket is bundled with its own history, and a changed verdict must be named.** For a candidate returning from Question, Step 2 reconstructs `previous_cut` — the prior package, its `depends_on`, and `prior_rationale` (the prior pass's actual reasoning, not just the `collision`/`effort`/`single` kind) — from the ticket's relations and its last `## Frame (gatekeeper)` comment, and passes it to the bundler. A verdict that diverges from `previous_cut` is accepted the moment its `changed_by` is **named** — an absent or empty `changed_by` leaves the previous cut standing. Whether that name can actually be *located* in `list_comments` or the ticket body is a separate, non-blocking confidence signal Step 5 reports (`verified` / `unverified: not found in comments/body`) — locatability was deliberately kept off the acceptance gate, because a real answer can arrive outside a ticket comment, or be paraphrased.

**A relation write is verified mechanically, the same discipline as the frame comment's half (b).** Step 3.5's write loop is LLM bookkeeping — the same mechanism that lost four of the bundler's five reported relations on the incident pass. A second round of prose telling the same LLM to check its own bookkeeping would fail the same way. `scripts/gatekeeper/relation-readback.py` is a pure stdin-JSON → stdout-verdict process outside the LLM: Step 3.5 pipes `{expected, relations, reasons}` into it and reads back `verdict: ok|gap` (exit 0/2) — a `gap` names the unresolved targets, is retried once, and, if it persists, becomes an **unexplained gap** that withholds the package from Planned (a new Hard rule, the one exception to *Blocked is not unplanned* — a blocker never withholds a package, a failed relation *write* does). The script also closes a narrower gap a plan review found: a relation of the wrong *kind* at the right target (anything other than `blocked_by`/`relates_to`) does not silently satisfy an expected dependency, because that is not the edge `run` can read.

### One package, one lane — decided from paths by a script, and a mixed ticket is split

Two lower plugins exist: `agent-autonomous-developer` (code, test-first) and `agent-autonomous-prompt-engineer` (prose a model executes — skills, agents, prompts — evidenced by blind tests and step replays). Sending prose to the developer deadlocks its critic gates: the test-critic calls a string check on a markdown file a `tautology`, the plan-critic calls its absence `untestable`, and the card lands in Question (`agent-autonomous-developer#122`/`#123`; this repo's `#35` patched triage's advice instead of the lane) (`agent-ticket-orchestrator#38`).

**The lane is a path table in `scripts/gatekeeper/classify-lane.py`, not a model's opinion.** The `bundler` reports each ticket's footprint as `paths` with a `role` (`deliverable`/`accompanying`) and never names a lane; the gatekeeper pipes them to the script (stdin JSON → `lane: code|prose|mixed`, same convention as `relation-readback.py`). The role hint is what keeps the rule usable: nearly every code PR in this ecosystem also touches `AGENTS.md`, so without "accompanying paths do not vote" nearly every ticket would be `mixed`. The table lives in the script and only there — reviewed as data, not scattered through skill prose.

**Why split and not chain two sessions on one ticket.** A package is one branch, one PR, one event stream: `ci-green` is the only success, `blocked`/`failed` are per package, and `run`'s three retry budgets are per package. Two sequential processes on one ticket would have to define whose `ci-green` counts, whose events `run` reads, and what "failed" means when the second half fails after the first merged. The gatekeeper already had the split machinery — `depends_on`, `recut` (Step 3.7), the relation read-back — and the natural order is the one #122 needed anyway: code first (a script with behaviour tests), prose second (the skill that calls the merged script), the prose ticket `blocked_by` the code ticket. The split happens in Step 2, before clarification, because it creates a ticket the clarifier must see. A `collision`/`effort` bundle that spans lanes is rejected the same way an oversized `collision` package is.

**The label is the whole interface to `run`.** `lane:prose` on the package ticket → `start-package-session.sh --lane prose`; no label → the developer, unchanged. The script takes a lane, never a skill name, so no free-form string reaches a `bypassPermissions` prompt, and the lane → entry table has one home. `run` branches on the lane nowhere else; `triage` receives it only so its test-evidence rule does not advise tests on a prose deliverable.

**The prose lane is optional, and its absence is a Question.** `agent-autonomous-prompt-engineer` is deliberately **not** in `plugin.json`'s dependencies: the developer is needed in every project, the prompt engineer only where model-executed prose is shipped, and a hard dependency would install it everywhere. `scripts/gatekeeper/prose-lane-available.py` reads the settings files a package session will actually see (user, then the project's committed `.claude/settings.json`; **not** `settings.local.json`, which is untracked and therefore absent from a worktree). Unavailable + a `prose`/`mixed` ticket → the gatekeeper posts a clarification and moves the ticket to Question, no label and no split. It is a real decision, not a retry: install a plugin into the project, or knowingly run prose through the developer ("code lane", recorded by the reply itself). Silently falling back to the code lane would reproduce exactly the #122 deadlock this routing exists to prevent. `run` applies the same script as a skip (card stays in Todo) for a `lane:prose` package whose project lost the plugin after it was planned.

**Open edge, deliberately unsolved:** an MCP tool's user-facing description that lives inside a code file is `code` for this classifier.

### `gatekeeper-ignore` is a human's "not now", filtered at enumeration and nowhere else

A maintainer parks a ticket in Backlog on purpose by labelling it `gatekeeper-ignore`; the gatekeeper's Step 1 passes `not_labels=["gatekeeper-ignore"]` on both `list_tickets` calls, so an ignored ticket never reaches the bundler, the clarifier, an epic or a column move — no per-step "skip if labelled" checks exist or should be added (`agent-ticket-orchestrator#37`). The gatekeeper never creates, adds or removes the label; how `not_labels` treats a label missing from the repository's catalog is in `list_tickets`' tool description (agent-project-issues#363). Two report-only `labels=` calls feed the `ignored (…)` line so a forgotten label stays visible. `run` deliberately does **not** look at the label: it only matters before Planned, and a human who moves an ignored ticket to Todo has decided.

### A criterion the PR run cannot prove is struck and recorded, never a ticket

An acceptance clause can demand evidence the package's own PR run will never produce — "a real run against the real `claude` CLI (no fake)", "verified in a real Windows shell", "a person confirms the released plugin loads on a fresh machine". These arrive from several ticket-writing agents, so the pipeline absorbs them rather than waiting for the authors to be fixed (`agent-ticket-orchestrator#40`).

**What the first remedy cost.** `agent-ticket-orchestrator#33` (from `agent-project-issues#347`, where a green PR was read as proof of a release-only procedure it never ran) promoted such a clause to a `pipeline-capability` ticket of its own — `auto:` with a `blocked_by` on the package, `manual:` as paperwork — in a gatekeeper Step 3.4 that no longer exists. Each spawned ticket was an ordinary candidate on the next pass and could spawn more; the cascade had no depth limit. Traced in `seretos-agents/lib-python-harness`: feature #24 ("a real run against the real CLI") → #27 (a PR-time live-CLI job, blocking #24) → #29 (provision a token), sibling #25 → #28. #27 was dispatched, merged PR #32 with a CI-only shim that defeated the library's own environment scrubbing, went `blocked` on a plan-critic critical, was escalated four times, and was finally undone by #33 — which then blocked #24 in turn. The feature the chain was about never moved.

**The rule that ships.** #33's problem was real; its lesson is *do not claim what was not shown*, and recording achieves that. Same three-level split as everything else: the `clarifier` **reports** (`unprovable_here: none | <clause>`, repeatable, both statuses, detected by Protocol step 1d's six cheap shapes — no `list_tickets` look-up, no `depends_on`) and writes the clause's **automatable residue** into `ac:` (the artifact that makes the check possible for whoever performs it later, never the performing of it; when nothing else is buildable the residue *is* the AC, and the status is `CLEAR`, never `NEEDS_INPUT`); the `gatekeeper` **writes** one `Not proven by this package:` line per clause into the `## Frame (gatekeeper)` comment it already posts; `run` is unchanged, and its `triage` answers a `blocked` event about such evidence from that line instead of escalating — deliberately the last line of defence, not the fix.

**The frame line is load-bearing.** The gatekeeper never edits a ticket body, so the struck clause still stands in the body the lower plugin's `context-extractor` reads. Without the line the planner tries to satisfy the clause and the plan-critic calls its absence a gap — exactly how #27's round 1 raised "the AC requires the live suite to execute in this PR's own run" as a blocking critical.

**Nothing waits, and nothing here is a gate.** No ticket, no relation, no label, no `recut`, no "human QA" column. A check performed outside the pipeline never holds a package, a merge or a release; the owner decides when to perform it, and infrastructure the owner actually wants is a ticket the owner files. The ecosystem-level release layer this repository anticipates must not read `unprovable_here` as a gate. `gatekeeper-ignore` remains the only mechanism that takes a ticket out of the process. The gatekeeper creates tickets in exactly two places — an epic, and the prose half of a lane split; the lane split is the one horizontal cut this plugin keeps, because the architecture forces it (one package is one branch, one PR, one event stream, and two lanes need two lower plugins), it is bounded to two halves planned in the same pass, and it blocks no unrelated work. `pipeline-capability` tickets already filed in other repositories are data, not contract; nothing scans for or migrates them.

### The frame comes before the questions — a precise answer inside a wrong frame is still wrong

Incident, compressed: `lib-python-worktree` #90 → #121 → #148 → #154. Four tickets, three weeks, one unchanged user symptom (`worktree_remove` hangs, the MCP server dies on Windows). Every one was framed as "thread leak", AC "thread count bounded". v0.3.12 bounded it: **AC met, symptom unchanged.** At #148 the `clarifier` asked four rounds of precise questions inside that frame and never questioned the frame itself. Root-cause account in `seretos-agents/lib-python-worktree#154` (`agent-ticket-orchestrator#11`).

Hence three mandatory frame questions — symptom, measurement, prior attempts — before any detail question, carried in the same `<!-- clarifier:frame v1 -->` machine block that also carries `depends_on` (one new parsing surface, not two — see above). Note that `ticket.acceptance_criteria` is often empty (on which providers: `get_ticket`'s tool description, agent-project-issues#361) and the AC is body prose under a heading like `## Acceptance` — a clarifier that reads only the field never sees the AC at all.

**The frame is repaired, not asked about (2026-08-29).** The first version of this rule gated `STATUS: CLEAR` on the AC measuring the symptom and made the clarifier *ask* whether to extend it — and likewise asked whether to reframe a regression chain as a root-cause task. The first real pass (`lib-python-worktree` #156, #157, #158) produced eight questions, of which none was a decision: three were "extend the AC with the symptom, or keep the proxy?" (nobody picks the proxy), one was "reframe as root cause, or repeat the point fix that failed four times?", two recommended the ticket's own literal reading, one invented scope (cross-repo repair of an already-published release), and the last was a fail-open/fail-closed design choice whose "bad" outcome was a beta-only migration edge that heals on reboot — phrased in `StopDetail.reason` and `_pid_alive` call sites for a human who had not written the ticket and could not act on it. Hence: the clarifier **writes** the symptom AC (`ac:` in the frame block, posted by the gatekeeper as `## Frame (gatekeeper)` — load-bearing, because the lower plugin's `context-extractor` only sees the ticket's comments) and **applies** the reframe (`reframe:` in the frame block, stated in the `## Regression chain (gatekeeper)` comment), both with "object by replying on the ticket". A question must pass five filters in `agents/clarifier.md` § 3a (not the ticket's literal reading, no added scope, not a reframe, wrong answer costs a user something durable, answerable without the code open) and must open with an `**About:**` sentence for a reader who has not opened the ticket. The only frame-driven `NEEDS_INPUT` left is a defect whose symptom cannot be named at all.

**A missing acceptance section is written, not asked about.** The same
2026-08-29 principle extends to a ticket that has no acceptance section at
all: when `ticket.acceptance_criteria` is empty or missing and the body
carries no `## Acceptance` heading, the clarifier writes the AC itself,
because the `## Frame (gatekeeper)` comment is the only channel through
which a written AC reaches the developer at all — there is no ticket comment
to reply to for something that was never asked. `premise:` exists for the
companion gap: a plan can rest on a capability nobody has verified on *this*
ticket — inherited, say, from an earlier clarification on a sibling ticket
in the same repo — and that unverified assumption needs a name in the frame
block, repeatable, one line per premise, or it silently becomes the plan's
foundation instead of a fact someone can check.

The GitHub issue forms under `templates/ISSUE_TEMPLATE/` and the `ticket`
skill both draw their section labels from the clarifier's own heading
vocabulary (`agents/clarifier.md`, "The heading vocabulary"), so a ticket
filed by hand, through the `ticket` skill, or through a web form all carry
headings the clarifier already knows how to read. Like `agents/`,
`templates/` is therefore a release artifact — the release workflow's stage
step must copy it onto the install tree, or a freshly installed project's
forms silently vanish.

**The escape hatch is narrow on purpose.** Most tickets in a prose/plugin repository like this one have no user-visible behaviour; a rule that turned them all into `NEEDS_INPUT` would be switched off within a week. `symptom: none:<category>` (`refactor, docs, ci, infra, test, chore, prose`) is the hatch; it is closed for anything labelled `bug`/`regression`/`defect` or describing a hang, crash, wrong result, slowness or leak.

**Chain detection lives in the `clarifier`, one dispatch.** It already has `list_tickets`, and "prior attempts" is one of its own three frame questions; the `gatekeeper` only *applies* the finding (`regression-chain` label + chain comment, Step 3.6), the same shape it already uses for `## Open Questions`. A two-phase design (gatekeeper searches, then dispatches the clarifier with a mandate) would ask the weaker level first and pay a second Opus dispatch to re-tell the clarifier what it had already found.

**The false-positive rule, and why cheap is correct.** A closed ticket only joins a chain when two of three signals hold (link/mention, same symptom verb, same module+symbol) — same-file-alone is never a chain. A wrong flag costs one label, one comment and one `NEEDS_INPUT` round a human clears in seconds; a missed chain costs three weeks and four tickets. Do not build a better detector.

**Why there is no CI fixture for any of this.** The `clarifier` is an LLM judgement dispatched inside a session, not a function: a fixture harness would need a live `claude -p`, an API key in CI and a live tracker, and would still be non-deterministic. The worked examples therefore live in `agents/clarifier.md` ("Worked frames") as prompt content — which changes behaviour — and `tests/test_pipeline_contract.py` asserts only that they are present and what outcome each states. **Do not "fix" this with a mock clarifier;** a test that asserts one hand-written string equals another tests nothing.

### Why state lives in the ticket, not in the return value

A headless `claude -p` returns "process ended" plus prose. Reconstructing state from that prose — or from a subagent's reply — is how silent report loss happened in the lower plugin's fleet era (#60, #88). So the **ticket is the state store**: the process exit code is a courtesy, and `run` re-reads `list_comments(order="desc", limit=3, body_max_chars=600)` for the latest `adev:event` before every decision. A crash anywhere in the chain loses nothing that matters; re-running `run` picks the card up from its column.

### Both skills request only what they read (`agent-ticket-orchestrator#26`)

A night's `run` was >90% MCP response echo the skills never read. Two cross-repo facts are not derivable from any single file here, and both are documented by agent-project-issues itself (`list_projects`' tool description; its skill's light-write-echo rule, `seretos-agents/agent-project-issues#314`): **light `list_projects` lacks fields both skills read from the resolved entry** (`path`, `permissions`, `local_path`), so resolution uses `search_projects(query="<owner/repo>", limit=5)` and light `list_projects` serves only the STOP message; and **the write tools answer with a light echo from that release on**, which is why `.claude-plugin/plugin.json` floors that dependency at `>=0.3.4` and the skills pass `response="light"` with no fallback branch. Every `list_comments(` call in either skill passes `body_max_chars`; only `gatekeeper` Step 2's `previous_cut` / `changed_by` look-ups read full comment bodies. The fallback wake-up while a package session runs is 3600 s, not a poll.

### Why the package session is a CLI process started by the skill itself — not an `Agent` subagent, not a wrapper

An `Agent` subagent inherits the **parent session's** MCP connections — this plugin's Serena project, this session's tool set — not the target project's. The lower plugin needs the target worktree's own `CLAUDE.md`, `.serena`, `.claude/settings.json`, plugin set and MCPs, which only a fresh `claude` process with cwd = worktree gets. And there is deliberately **no wrapper subagent** around that process either: a task-notification for a backgrounded Bash command reaches the **main** session, while a subagent that ends its turn "to wait" is terminated, not suspended (lower plugin #83/#88) — a wrapper that has to stay in-turn for hours is the exact shape that went silent before. So `run` starts the process with `Bash(run_in_background: true)` in its own turn — via `scripts/start-package-session.sh`, which owns the launch lock, the stream files and the exit marker so the skill never reproduces those mechanics from prose — and is woken by the harness when it ends. The orchestrator never reads `stream.jsonl`; state comes from the ticket. Retry = a fresh start with `attempt+1`, never a resume.

### No dollar budget on the package session; the model is pinned instead

`--max-budget-usd 15` was removed, and the two facts behind that removal are the kind that get re-litigated by anyone who sees an unbounded `claude -p` and reaches for a cap.

**The dollar figure is not the money.** `total_cost_usd` and `--max-budget-usd` are API *list price*, computed from token counts against the published price list, regardless of whether the account is billed per-token or by subscription. Over the 2026-08-23/24 runs — 32 sessions, 14 packages, five projects — the batch came to $251 list and consumed roughly 23 percentage points of a weekly subscription limit; the $15 cap was therefore ~1.4% of that week, not the third the number suggests. The cap also did not hold (one package finished at $18.40 against it, because the check only lands between turns) and it fired at the worst possible point: spend is front-loaded into context, planning and the critic gates, so aborting after the implementation and before the PR discards all of it. 61% of that $251 went on attempts that never reached `ci-green`. Cost control lives in the pipeline's shape — fewer dead attempts, work pushed so a retry resumes (lower plugin `#95`), the right model per role — not in a mid-flight ceiling. The platform's session limits are the runaway stop, and `skills/run/SKILL.md` → *Why there is no dollar budget* is the long form.

**`--model` is a correctness property, not a preference.** Without it the headless session inherits whatever `/model` the human last left set in an unrelated interactive session, so identical packages cost different amounts for reasons invisible to everyone in the run. In the same batch the main turn was **33% of a package's cost on Sonnet and 52% on Opus** — the split falls exactly on the day the human forgot to switch back. The lower plugin's six subagents already pin their models in frontmatter (`planner: opus`, the rest `sonnet`); the session's own turn was the last unpinned one. It sequences phases, counts rounds, posts events and drives git/PR/CI, delegating every judgement that needs a bigger model to a subagent that asks for one — hence the `sonnet` default, overridable per run with `ADEV_SESSION_MODEL`.

### The launch lock

Two `claude` processes starting at the same moment race on `~/.claude.json` and can corrupt it (upstream anthropics/claude-code #28813, #28847; observed here as `fix: serialize Claude session launches` in the meta-repo). `scripts/start-package-session.sh` takes `mkdir "$HOME/.claude/.launch-lock"` (atomic on NTFS and POSIX), writes its PID inside, breaks a lock whose owner is dead or older than 60 s, and holds it **only across the start** — until the stream shows the first `system` record or 25 s elapsed — never across the run. Inside a project everything is sequential for the same reason (and because parallel worktrees race on shared services).

### Two skills, neither blocks on `AskUserQuestion`

| skill | human | may `AskUserQuestion` | how it surfaces a question | writes |
|---|---|---|---|---|
| `gatekeeper` | starts the session, not needed at the keyboard while it runs | **no — never granted, never used** | posts `## Clarification needed (gatekeeper)` on the package ticket, moves it to Question, moves to the next package | epics, the prose half of a lane split, `parent` relations, `blocked_by`/`relates_to` relations, `epic`/`regression-chain` labels, the `lane:prose` label, clarification/frame/dependency/regression-chain/release-confirmation comments, Backlog → Planned, Backlog → Question, Question → Planned (own answered cards only) |
| `run` | absent, may run all night | no (tool not granted) | posts the question as a ticket comment, moves the card to Question | Todo → Doing → Done/Question, `merge_pr`, worktrees, the few comments the skill names; leaves a blocked package untouched in Todo |
| `ticket` | at the keyboard, answering three questions | **yes — this is where it lives** | asks the symptom/measurement/prior-attempts questions live, in chat | one `create_ticket` call, nothing else |

`AskUserQuestion` is forbidden for `gatekeeper` and `run` specifically — not
banned from the plugin as a whole. Both must complete an entire pass
unattended, and a skill that stops mid-list waiting for a reply defeats its
own purpose the moment nobody is watching at that exact moment. A bundling
decision is applied and reported, never confirmed first (see Step 2 of
`gatekeeper`), and a clarification question that cannot be answered from
ticket, comments and code is posted on the ticket rather than asked live, so
`gatekeeper` never blocks a run on somebody being present to answer
(`skills/gatekeeper/SKILL.md`, "Nothing in this skill blocks on a chat
answer"). `ticket` is the opposite case: invoked precisely because a human
is present and wants to file one ticket right now — its `AskUserQuestion`
calls are not a leftover the other two forgot to remove, they are the reason
the skill exists. A human still has to start the `gatekeeper` session by
hand — that has not changed, and there is no cron/headless trigger for it in
this plugin yet — but once started it runs every candidate to completion in
one pass instead of stalling on the first one that needs input.

Order inside `gatekeeper` is mandatory: **bundle, then clarify** — clarification comments are posted on the package ticket, and a ticket that becomes an epic child afterwards would carry comments the run never reads. `Planned → Todo` is human-only. `Question → anywhere` is human-only **except** for the one move `gatekeeper` makes on its own cards (2026-08-29): a Question card that carries a `## Clarification needed (gatekeeper)` comment, **no** `adev:event` comment (never dispatched, so not `run`'s), and a comment newer than the question goes back through bundle + clarify and, on CLEAR, straight to Planned. The ownership test is the `adev:event` comment, not the column — the ticket is the state store here too. Why Question and not Backlog for a gatekeeper question: across many projects the Backlog grew to the point where the handful of tickets actually waiting on a human were not findable; one column that means "needs me" is the whole point of the Question column, whichever skill asked. No skill here ever moves a card into Todo.

### Board model (shared GitHub Projects v2 board, logical names from `projects.yml`)

| column | meaning | who moves in |
|---|---|---|
| Backlog | everything new | anyone |
| Planned | bundled + clarified, no open questions | gatekeeper |
| Todo | released for the run — the only column `run` reads | **human only** — `run` may leave a card here untouched when its `blocked_by` blocker has not reached Done |
| Doing | package dispatched | run |
| Done | merged, CI green — an epic reaches Done as a **column**; it is not closed, only its children close, via `Closes #<n>` | run |
| Question | needs a human; the question is a ticket comment | run in (after dispatch), gatekeeper in (before dispatch); **human only** out, except gatekeeper → Planned for its own answered cards |

Logical names are the contract; native names (`"Frage offen"` vs `"Question"`) are never hardcoded — how a logical name is resolved and written: the agent-project-issues skill, "Board columns: resolve, then write". Reads: `list_tickets(column=<logical>)`. Comments are the log, columns are the signal; an empty Question column means no open questions.

### Escalation rule (ecosystem-wide, root `AGENTS.md`)

Escalate one level up until a level can answer; the human only when no level is left, and only for a **decision**, never a **retry**. Each level states what it checked and why that was not enough. A Question card whose only sensible reaction is "kick it again" is a bug in whichever level forwarded instead of trying — `clarifier` applies this before the run, the lower plugin's three-round caps during it, `run`'s second attempt after it. The `clarifier` now applies the same rule to a ticket's **frame** — its symptom, its measurement, whether it is the latest in a regression chain — not only to its open decisions (see "The frame comes before the questions", above).

### What a project needs in `~/.seretos/projects.yml`

- `permissions.issues.create` + `issues.modify` — epics, relations, comments, column writes (both skills).
- `permissions.pulls.create` + `pulls.modify` — the lower plugin's PR; `pulls.merge` — `run`'s `merge_pr`; `run` requires it and STOPs in Precondition 3 without it, before touching anything.
- `permissions.board.manage` — only for the one-time `ensure_board_column` when `Planned`/`Question` do not exist on the board yet.
- `board.columns` must list the logical names `Backlog`, `Planned`, `Todo`, `Doing`, `Done`, `Question` — `gatekeeper` STOPs without `Planned`/`Question`, `run` STOPs without `Todo`/`Doing`/`Done`/`Question`. An existing board's extra column is harmless: `run` neither reads nor requires it.
- `local_path` must point at a git checkout: `run` passes it as `repo_root` to `worktree_create`; both read-only subagents read code under it.

### Release mechanics

- **Release is orphan-branch + marketplace dispatch.** `release.yml` (manual: Actions → release → `version=X.Y.Z`) stamps the version into both manifests, force-pushes an orphan `release` branch holding only install-ready files and POSTs a dispatch (`category: skill`) to `seretos-agents/modular-software-factory`. `main` and `release` share no history. Clients install at the tag `agent-ticket-orchestrator--vX.Y.Z`.
- **`agents/` is a release artifact.** The stage step copies `agents/` next to `skills/`; drop that line and the released skills dispatch undefined subagent types.
- **Required secret:** `MARKETPLACE_DISPATCH_TOKEN` — fine-grained PAT, `Contents: RW` + `Pull requests: RW` on `seretos-agents/modular-software-factory` only.
- **The dispatch payload carries a `changelog` field** — the same notes body the release step already generated, read back via `gh release view <tag> --json body`, never recomputed a second time. `agent-marketplace#235` renders it into the opened PR under `## Changelog`; the field is optional on the consumer side and was silently ignored before this repo started sending it. Built by `.github/scripts/marketplace-payload.sh` with a single `jq -n` invocation, not spliced into the `curl -d @- <<EOF` heredoc the rest of the payload used to use raw — a changelog is multi-line markdown that can contain backticks/quotes/newlines, any of which would break an unquoted heredoc and silently drop the whole dispatch (the same class of bug that already hit `agent-marketplace`'s `tags` field once, see `agent-marketplace@89aa850`). This repo has no `dispatch.yml` to mirror the change into — only `lint.yml` and `release.yml` exist here.
- **A failed release never gets "fixed" in place.** There is no re-run, no re-dispatch, no editing an already-opened marketplace PR for a version that failed partway — the pre-flight step refuses to reuse a version number whose tag already exists, so the only way forward after a failure is the next version number, same as any other release. Nothing in this workflow tries to detect or special-case a retry.
- **`assets/icon.png` and `description.md` are release artifacts.** The dispatch payload points at `raw.githubusercontent.com/${repo}/${TAG}/assets/icon.png` and `…/description.md`, so both must live on the orphan `release` branch at the tagged commit — the stage step copies them for exactly that reason.
- **Dependencies** are declared in `.claude-plugin/plugin.json` (`agent-autonomous-developer`, `agent-project-issues`, `agent-worktree`); the developer's floor is the first release that carries the `process-developer` skill name, and `agent-autonomous-prompt-engineer` is deliberately absent — an optional lower plugin, detected per project (see "One package, one lane"); Claude Code installs/loads them with this plugin. The Codex manifest (`.codex-plugin/plugin.json`) carries no dependency field — Codex hosts install the MCPs separately.
- **LF only.** Claude Code silently ignores a `SKILL.md` or `agents/*.md` with CRLF line endings; `.gitattributes` forces LF and `lint.yml` fails on CRLF.

### Release notes are generated from `main` via `src/*` markers

The orphan `release` tag/branch shares no history with `main` or any previous release — `main` and `release` never merge — so `gh release create --generate-notes` / `--notes-start-tag`'s commit-graph walk from that tag found nothing to summarize; every release's notes were structurally empty (`agent-ticket-orchestrator#15`). The fix anchors note generation on `main` instead: `release.yml`'s new pre-flight step resolves the previous release's tag via `.github/scripts/prev-release-tag.sh` (real semver ordering — never `sort -V`, which gets release-vs-prerelease precedence backwards), the workflow then pushes a lightweight `src/<TAG>` tag at the `main` commit the release was cut from, and `gh api .../releases/generate-notes` diffs `src/<PREV_TAG>` → `src/<TAG>` — both markers live on `main`, where there is real history to walk. A `src/<TAG>` push is unconditional once pre-flight has proved its absence, and markers are never deleted, moved, or skip-guarded: a "skip if exists" guard here would only ever hide a bug, and once did — it let a stale marker from a failed run re-empty a later retry's notes (`agent-comfy` PR #55).

**`GITHUB_TOKEN` can never create a marker for a *past* release**, and that is permanent, not a gap to close: the historical `main` commit's `.github/workflows/*` tree differs from the default-branch tip (this very fix edits `release.yml`), and GitHub's workflow-tree-mismatch rule rejects the push — no `permissions:` scope lifts it. So the first release run after this change ships needs a one-time human bootstrap: read the `head_sha` of the previous release's own `release.yml` Actions run (`gh run list --workflow release.yml`), then

```
git tag  src/<PREV_TAG> <head_sha of PREV_TAG's release.yml run>
git push origin src/<PREV_TAG>
```

Concretely, in this repo today: the newest release tag is `agent-ticket-orchestrator--v0.1.4` and no `src/*` tag exists yet, so the very next release run will fail pre-flight, printing exactly the command pair above for `src/agent-ticket-orchestrator--v0.1.4`. That failure is free — no manifest stamp, no zip, no orphan push, no tag, no marketplace dispatch, no burned version number — and is the designed behaviour this change introduces, not a bug to fix.
