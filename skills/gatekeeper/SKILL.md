---
name: gatekeeper
disable-model-invocation: true
description: Board pre-flight — bundles open Backlog tickets into work packages (epics for collisions or effort batches) without asking for confirmation, then clarifies every open question against ticket, comments and code. A question it cannot answer itself is posted as a ticket comment, not asked in chat — the package stays in Backlog and the gatekeeper moves straight on to the next one, so one hard-to-clarify package never blocks the rest of a run. Clear packages move to Planned. Never moves anything to Todo, never dispatches the developer plugin, never edits code. Installed per project; invoke as "/agent-ticket-orchestrator:gatekeeper" from the project's main checkout (project_id=<id> overrides the repo-derived id). A human starts the session, but is not needed at the keyboard while it runs — open questions wait in ticket comments until the next invocation.
---

# gatekeeper — bundle, clarify, release to Planned

You prepare the board for an unattended `run`. You turn the raw **Backlog**
into **work packages** and move every package whose questions are settled to
**Planned**. The human then hand-picks what the night shift gets by moving
Planned → Todo. You never do that last move: Todo is the one column only a
human writes.

**Nothing in this skill blocks on a chat answer.** `AskUserQuestion` is not
part of this flow, for the same reason it is not granted to `run` or the
lower plugin: everything downstream runs unattended, and a skill that stops
mid-list waiting for a reply defeats its own purpose the moment nobody is
watching at that exact moment. A bundling decision is applied and reported,
never confirmed first (see Step 2). A clarification question that cannot be
answered from ticket, comments and code is posted **on the package ticket**
and the package stays in Backlog — you move on to the next candidate
immediately. The human answers in the ticket, at their own pace, and the
next gatekeeper run picks the answer up.

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
- The project's `local_path` (from `list_projects`) — handed to the subagents
  so they can look at the code.

## Preconditions

1. **agent-project-issues MCP loaded.** If its tools are not available (fresh
   sessions do not auto-load plugin MCPs — anthropics/claude-code#61866),
   **STOP** and tell the user to `/reload-plugins`, then re-invoke.
2. **Board columns.** Call `list_board_columns(project_id)` and keep the
   `logical → native` map. `Backlog`, `Planned` and `Question` must all be
   present as *logical* names. If `Planned` or `Question` is missing, **STOP**
   with a clear message: the project's `board.columns` in
   `~/.seretos/projects.yml` must list them, and the native column must exist
   on the board — `ensure_board_column` can create it but needs
   `permissions.board.manage`. Never hardcode a native name ("Frage offen" vs
   "Question" is a per-board choice; the logical name is the contract).
3. **Write permission.** `list_projects` → `permissions.issues.create` and
   `issues.modify` must be `true` (you create epics, add relations, post
   comments, move cards). Otherwise STOP and say which flag is missing.
4. **Relation vocabulary.** `list_relation_kinds()` is called once, at the
   start of Step 2, and its result is kept for the rest of the pass:
   `provider_support` for this project's `provider` (from `list_projects`)
   decides the dependency-writing path in Step 3.5. A provider without
   `blocked_by` (GitLab) is **not** a stop condition — see Step 3.5's
   fallback.

## Step 1 — enumerate the Backlog

```
list_tickets(project_id, column="Backlog", status="open", limit=100)
```

Then drop every ticket that is **already a child of an epic**: for each
candidate call `list_hierarchy(project_id, ticket_id)` and exclude it when
`parent` is non-null (a previous gatekeeper pass already packaged it — the
epic, not the child, is what moves). Keep epics themselves in the list; the
bundler may fold further tickets into them or leave them as-is.

0 candidates → report "Backlog is empty / fully packaged" and stop.

## Step 2 — bundle (before clarifying — the order is mandatory)

Dispatch the `bundler` **once**, unnamed, synchronous, fresh:

```
Agent(
  subagent_type="bundler",
  description="bundle Backlog of <project_id>",
  prompt="project_id=<project_id> local_path=<local_path>\n
          Candidates (id · title · labels):\n<the full list>\n
          Return the packages JSON block."
)
```

It returns a JSON block:

```json
{ "packages": [
  { "title": "...", "reason": "collision" | "effort" | "single",
    "tickets": [<ids>], "rationale": "...",
    "depends_on": [ { "ticket": <id>, "why": "...", "evidence": "..." } ] }
] }
```

**Apply the proposal directly — no confirmation round.** Every candidate the
bundler placed in a package is materialised as that package (see
*Materialise multi-ticket packages as epics*, below); nothing here is held
for a human's accept/edit. Step 5's report is where the
cut becomes visible, after the fact, not before it. If a package's cut turns
out to be wrong once a human looks, that is a Backlog-time fix, not a reason
to make every run wait on a confirmation it almost always accepts anyway: an
epic can be split back apart by hand (remove the `parent` relations, move
the children back) before it ever leaves Backlog or Planned — the epic and
its children are ordinary tickets, not a one-way transformation.

**Why bundle first:** clarification answers are posted on the *package*
ticket. If you clarified first and bundled afterwards, the answers would sit
on tickets that then become children, and the epic the run actually processes
would carry none of them.

### Materialise multi-ticket packages as epics

For each accepted package with **two or more** tickets:

1. `list_labels(project_id)` — if no `epic` label exists, `create_label`
   (GitHub 404s on an unknown label at `create_ticket` time).
2. `create_ticket(project_id, title=<bundler title>, labels=["epic"], body=…)`
   where the body lists the children (`- #<id> <title>`) and the bundler's
   rationale. Omit `custom_fields` so the epic lands in Backlog like any new
   ticket.
3. `list_relation_kinds()` once, then link the epic to each child with
   `add_relation(project_id, ticket_id=<epic>, kind="parent", target="#<child>")`
   — `ticket_id` is always the *from* end, so `kind="parent"` on the epic
   makes the epic the parent. (If the provider matrix lists only `child`, call
   it from the child side instead: `ticket_id=<child>, kind="child",
   target="#<epic>"`.)
4. **Never close the originals.** They stay open in their column; only the
   epic moves from now on. The lower plugin closes them via `Closes #<n>` in
   the PR when the epic is done.

A **single-ticket** package is the ticket itself — no epic, nothing created.

From here on, *package ticket* means the epic, or the single ticket.

**Build the package map while materialising.** Keep, for the rest of this
pass, every candidate ticket id → the id of the package ticket it now belongs
to (its epic, or itself). Every dependency written in Step 3.5 is resolved
through this map first, so a dependency naming a ticket that became an epic
child in this same pass lands on the epic, not on the child. Also fold the
bundler's own `depends_on` entries into a per-package `deps` list here, to be
written in Step 3.5 together with the clarifier's.

## Step 3 — clarify each package

For each package, in order, dispatch the `clarifier` unnamed, synchronous,
fresh:

```
Agent(
  subagent_type="clarifier",
  description="clarify package #<id>",
  prompt="project_id=<project_id> local_path=<local_path> package=#<id>\n
          Children (if epic): <ids>"
)
```

No answers are inlined into the prompt on this call — the `clarifier`'s own
protocol already reads `list_comments` and treats an earlier
`## Clarification needed (gatekeeper)` comment's replies as settled answers
(`agents/clarifier.md`, "earlier clarification comments count as answers"),
so a human's reply left on the ticket between gatekeeper runs is picked up
without you doing anything special here.

It ends with a status line:

- `STATUS: CLEAR` → go to Step 4.
- `STATUS: NEEDS_INPUT` → it carries a `## Open Questions` section
  (`### Q<n>`, 2–4 options, one `*(recommended)*`). **Post it to the
  ticket, do not ask in chat:**

  ```
  add_comment(project_id, ticket_id=<package>, body=…)
  ```

  heading `## Clarification needed (gatekeeper)`, then the `clarifier`'s
  `## Open Questions` section verbatim. The MCP prepends `#ai-generated`; do
  not add it yourself.

  Leave the package in **Backlog** and move on to the **next** package
  immediately — do not wait here. Record it in Step 5's report as "needs
  answer — see ticket #<id>".

  A **repeat pass** (this package already carries an earlier
  `## Clarification needed (gatekeeper)` comment, and the human has since
  replied to it) re-dispatches the `clarifier` exactly as above; it reads the
  reply itself. If it comes back `NEEDS_INPUT` again with the *same*
  questions unanswered, treat it as still Backlog and move on — nothing
  changed, nothing to redo. Count the `## Clarification needed (gatekeeper)`
  comments on the ticket (`list_comments`); at **4 or more**, add one line to
  Step 5's report flagging the package as unusually hard to clarify — not a
  cap, not a block, just a signal that it may need a different kind of
  attention than another clarifier round.

Questions the clarifier could have answered itself from ticket + code are its
bug, not something to post — if you notice it asking such things, note it in
the report, but still post the question rather than answering it yourself:
you are not allowed to decide on the project's behalf either.

**Parse the frame block.** The clarifier's report begins with a
`<!-- clarifier:frame v1 … -->` block on **both** statuses. Parse it as dumb
`key: value` lines — the same reader `run` applies to `adev:event`: empty
value = unknown, unknown keys ignored. You need `symptom`, `measurement` and
`depends_on` for every package, and `chain` for Step 3.6. If the block is
missing or unparseable, record `frame block missing` in Step 5's report and
continue on the `STATUS:` line alone — never abort a pass for a malformed
block.

## Step 3.5 — link dependencies

Runs per package, immediately after its clarifier call returns, **on both
statuses** (CLEAR and NEEDS_INPUT) — a dependency is a fact, not a decision,
and a package that stays in Backlog does not make it false.

```
deps = bundler's depends_on for this package  ∪  clarifier frame's depends_on
for each raw target #t:
  1. Lift. #t in the Step 2 package map -> target = map[#t]
     else list_hierarchy(project_id, #t); parent non-null -> walk up
       (at most 3 hops, take the topmost)
     else target = #t
     Why: only the package ticket travels the board and reaches Done. A
     child closes as a side effect of its epic's PR and never has a column,
     so a relation pointing at a child is a relation `run` can never see
     satisfied.
  2. Drop a self-edge. target == this package -> record "dependency absorbed
     into the package", write nothing. Normal outcome when the bundler
     bundled the pair.
  3. Validate. get_ticket(project_id, target):
     - not found     -> record "dependency #t not found", write nothing
     - status closed -> record "dependency #t already closed", write nothing
  4. Write, from the DEPENDENT side (ticket_id is always the "from" end):
     - "blocked_by" in provider_support[<this project's provider>]
       (github, azuredevops):
         add_relation(project_id, ticket_id=<this package>,
                       kind="blocked_by", target="#<target>")
     - otherwise (GitLab supports neither blocked_by nor blocks; GitHub in
       turn has no relates_to, so there is no portable kind and this branch
       is permanent):
         add_relation(project_id, ticket_id=<this package>,
                       kind="relates_to", target="#<target>")
       plus one comment on this package:

         ## Dependency (gatekeeper)

         Blocked by: #<target> — <why, from the clarifier/bundler>

         <!-- gatekeeper:deps v1
         blocked_by: #<target>
         -->

       `run` reads this block on providers without `blocked_by`. Do **not**
       post this comment on github/azuredevops — there the relation is the
       record and a duplicate comment is noise.
  5. Idempotency. Skip a relation the package already carries (from this
     step's own get_ticket, or an earlier pass's). A second identical
     relation is harmless; a second identical comment is not.
```

**Being blocked never withholds a package from Planned.** A package whose
questions are settled moves to Planned in Step 4 exactly as it would without
the relation. Blocking is a reason to withhold from **execution**, and the
only place that is enforced is `run`'s dependency ordering — the human still
hand-picks Planned → Todo, and `run` still refuses to dispatch out of order.
Withholding it from Planned instead would put the whole point of the relation
back in a human's head.

A blocker that is itself only a **Backlog candidate of this same pass**, and
whose own clarifier came back `NEEDS_INPUT`, needs no special case: the
relation is written, this package still reaches Planned, and a human who
moves it to Todo will see `run` skip it until the blocker has been clarified,
released and processed. That is a Planned package that is temporarily
un-runnable — say so in Step 5's report, because it is the surprising outcome
and the report is the only place it is visible.

## Step 3.6 — regression chains

Runs only when the frame block has `chain: regression-chain:#a,#b[,…]`.

1. **Idempotency first.** `list_comments` — if a
   `## Regression chain (gatekeeper)` comment already exists **and** the
   package already carries the `regression-chain` label, do nothing here; the
   chain was recorded on an earlier pass and re-posting it is noise on
   exactly the ticket that already has too much history.
2. **Label.** `list_labels(project_id)` — `create_label(project_id,
   "regression-chain")` if absent (GitHub 404s on an unknown label at write
   time) — then `update_ticket(project_id, ticket_id=<package>,
   labels_add=["regression-chain"])`.
3. **Comment.** `add_comment(project_id, ticket_id=<package>, body=…)`:

   ```
   ## Regression chain (gatekeeper)

   | ticket | acceptance criterion | outcome |
   |---|---|---|
   | #<a> | <its AC> | closed <date> — symptom persisted |
   | #<b> | <its AC> | closed <date> — symptom persisted |

   Symptom: <frame symptom>
   Measurement of this ticket's AC: <frame measurement>
   ```

   Content comes from the clarifier's `### Frame` → *Prior attempts* lines,
   verbatim — you have no code access and must not re-derive it. The MCP
   prepends `#ai-generated`; do not add it yourself.
4. The clarifier's root-cause mandate is already discharged: it detected the
   chain and its own protocol obliged it either to reframe (staying CLEAR
   only if the ticket already carries a symptom AC and a non-goal) or to
   return `NEEDS_INPUT` with exactly the reframe question. **There is no
   second dispatch.** Step 3's normal status handling then acts on that
   status unchanged — two separate comments with intent: only the
   `## Clarification needed (gatekeeper)` comment counts toward the existing
   "4+ rounds" heuristic.

Chain detection lives in the `clarifier`, not in a gatekeeper pre-pass: it
already has `list_tickets`, "prior attempts" is one of its own three frame
questions, and it is the only level in this flow with the code and the ticket
history in context — you have neither, deliberately. A two-phase design would
ask the same question twice, in the weaker place first, and pay for a second
Opus dispatch per chained package to tell the clarifier something it had
already found. What you do here is only what the clarifier cannot: apply a
label and post a comment — exactly the same shape as
`## Clarification needed (gatekeeper)`, which is already how this skill turns
the clarifier's read-only output into board state.

## Step 4 — release to Planned

On CLEAR:

```
update_ticket(project_id, ticket_id=<package>, custom_fields={"Status": <native of Planned>})
```

Only the **package ticket** moves. Children of an epic stay exactly where they
are (Backlog) — the board shows one card per unit of work, and the `run`
enumerates Todo only, so a child never gets dispatched on its own.

A package carrying a `blocked_by` (or its GitLab `relates_to` fallback)
relation moves to Planned like any other CLEAR package — see Step 3.5.

## Step 5 — report

A table: `package · kind (epic/single) · tickets · reason (bundler) ·
depends on (#ids, or —) · result (Planned / needs answer — see ticket #<id>)`.
Under each row, two indented lines from the frame block: `symptom: <…>` and
`measurement: <…>`.

Also report, each named where it is produced: `regression-chain: #a → #b →
this` for every chained package; `Planned but blocked: #<pkg> waits on #<b>,
which is still in Backlog` for a blocker that has not itself reached Planned
(Step 3.5); `dependency absorbed into the package`, `dependency #t already
closed`, `dependency #t not found`, `frame block missing` (Step 3.5/3).

Flag any package at 4+ `## Clarification needed (gatekeeper)` comments as
unusually hard to clarify (see Step 3). Then one line: "Move the packages you
want processed tonight from Planned to Todo by hand, then start
`/agent-ticket-orchestrator:run project_id=<id>`." — plus, if any package
needs an answer, "Answer the open questions above directly on their tickets,
then run `/agent-ticket-orchestrator:gatekeeper` again to pick them up."

## Hard rules

- **No `AskUserQuestion`, anywhere in this skill.** A bundling decision is
  applied and reported, not confirmed. A clarification question is posted on
  the ticket, not asked in chat. Nothing here waits on a live reply.
- **Never block on one package.** A package that needs a human answer stays
  in Backlog and you move straight to the next candidate — see Step 3.
- **Never move anything to Todo.** Planned is your terminal column. Todo is
  written by humans only.
- **Never dispatch the lower plugin** (`agent-autonomous-developer`) and never
  start a package session. You prepare; `run` executes.
- **Never edit code, never open branches or PRs.** Your writes are: epics,
  `blocked_by`/`relates_to` relations, labels (including `regression-chain`),
  clarification comments, dependency comments, regression-chain comments, and
  the Backlog → Planned move.
- **Never close or re-title original tickets.** A reframe is a proposal in a
  comment; the human edits the ticket body.
- **Bundle before clarify**, always.
- **Blocked is not unplanned.** A `blocked_by` relation never keeps a CLEAR
  package out of Planned (Step 3.5).
- **Never write a dependency relation from the child side.** It is written on
  the package ticket, on both ends, lifted through the Step 2 package map and
  `list_hierarchy` (Step 3.5).
- **The `regression-chain` label and its comment are written once.** Check
  for the existing comment and label before posting (Step 3.6).
- **Subagents are unnamed and synchronous.** No `name`, no `SendMessage`, no
  `run_in_background`. Re-dispatch fresh instead of resuming; the `clarifier`
  reads its own answers back from the ticket, so nothing needs to be inlined
  by hand on a repeat pass.
- **Project id is a parameter.** Never infer it from cwd.
