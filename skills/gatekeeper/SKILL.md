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
    "tickets": [<ids>], "rationale": "..." }
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

## Step 4 — release to Planned

On CLEAR:

```
update_ticket(project_id, ticket_id=<package>, custom_fields={"Status": <native of Planned>})
```

Only the **package ticket** moves. Children of an epic stay exactly where they
are (Backlog) — the board shows one card per unit of work, and the `run`
enumerates Todo only, so a child never gets dispatched on its own.

## Step 5 — report

A short table: `package · kind (epic/single) · tickets · reason (bundler) ·
result (Planned / needs answer — see ticket #<id>)`. Flag
any package at 4+ `## Clarification needed (gatekeeper)` comments as
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
  relations, labels, clarification comments, and the Backlog → Planned move.
- **Never close or re-title original tickets.**
- **Bundle before clarify**, always.
- **Subagents are unnamed and synchronous.** No `name`, no `SendMessage`, no
  `run_in_background`. Re-dispatch fresh instead of resuming; the `clarifier`
  reads its own answers back from the ticket, so nothing needs to be inlined
  by hand on a repeat pass.
- **Project id is a parameter.** Never infer it from cwd.
