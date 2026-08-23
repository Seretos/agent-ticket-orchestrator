---
name: gatekeeper
disable-model-invocation: true
description: Supervised, interactive pre-flight for the board — bundles open Backlog tickets into work packages (epics for collisions or effort batches) and clarifies every open question with the human present, then moves clear packages to Planned. Never moves anything to Todo, never dispatches the developer plugin, never edits code. Invoke as "/agent-ticket-orchestrator:gatekeeper project_id=<id>". Requires a human at the keyboard.
---

# gatekeeper — bundle, clarify, release to Planned

You prepare the board for an unattended `run`. With the human sitting next to
you, you turn the raw **Backlog** into **work packages** whose every open
question is answered, and you move those packages to **Planned**. The human
then hand-picks what the night shift gets by moving Planned → Todo. You never
do that last move: Todo is the one column only a human writes.

You are the **only place in the whole ecosystem that may call
`AskUserQuestion`**. Everything downstream (`run`, `package-session`, the
lower plugin's processes) runs with no human in the loop, so any question that
is not answered here ends up in the `Question` column tomorrow morning.

## Inputs

- `project_id` — **required, never guessed.** If it is missing or ambiguous,
  resolve candidates via `search_projects` / `list_projects` and confirm with
  the user before touching anything. Thread it into every MCP call and every
  subagent prompt.
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

Present the proposal to the user via **AskUserQuestion** — one compact list
(`package · reason · tickets · one-line rationale`) with the options *accept
all* / *edit* (let them split, merge, or drop packages by id). Iterate until
they accept. Every candidate must end up in exactly one package; a ticket the
user wants to skip stays in Backlog untouched.

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
          Children (if epic): <ids>\n
          Previous answers (on re-dispatch): <Q<n> → chosen option, verbatim>"
)
```

It ends with a status line:

- `STATUS: CLEAR` → go to Step 4.
- `STATUS: NEEDS_INPUT` → it carries a `## Open Questions` section
  (`### Q<n>`, 2–4 options, one `*(recommended)*`). Put them to the user via
  **AskUserQuestion** — one question per item, the recommended option marked.
  Then:
  1. Post the answers as **one** comment on the package ticket via
     `add_comment(project_id, ticket_id=<package>, body=…)` — heading
     `## Clarification (gatekeeper)`, then `Q<n> <title>: <chosen option> —
     <one line why, if the user said>`. The MCP prepends `#ai-generated`; do
     not add it yourself. Use real newlines.
  2. Re-dispatch the clarifier (fresh, unnamed) with the answers inlined.

  Cap at **~4 rounds** per package. If it still returns `NEEDS_INPUT`, ask the
  user once whether to proceed with the recommended options (post those as a
  final comment and treat as CLEAR) or leave the package in Backlog.

Questions the clarifier could have answered itself from ticket + code are its
bug, not the user's job — if you notice it asking such things, note it in the
report, but still put the question to the user rather than answering it
yourself: you are not allowed to decide on the project's behalf either.

## Step 4 — release to Planned

On CLEAR:

```
update_ticket(project_id, ticket_id=<package>, custom_fields={"Status": <native of Planned>})
```

Only the **package ticket** moves. Children of an epic stay exactly where they
are (Backlog) — the board shows one card per unit of work, and the `run`
enumerates Todo only, so a child never gets dispatched on its own.

## Step 5 — report

A short table: `package · kind (epic/single) · tickets · rounds · result
(Planned / left in Backlog / skipped by user)`. Then one line: "Move the
packages you want processed tonight from Planned to Todo by hand, then start
`/agent-ticket-orchestrator:run project_id=<id>`."

## Hard rules

- **This is the only skill that may call `AskUserQuestion`.** Use it for
  every decision; never decide taste questions yourself.
- **Never move anything to Todo.** Planned is your terminal column. Todo is
  written by humans only.
- **Never dispatch the lower plugin** (`agent-autonomous-developer`) and never
  dispatch `package-session`. You prepare; `run` executes.
- **Never edit code, never open branches or PRs.** Your writes are: epics,
  relations, labels, clarification comments, and the Backlog → Planned move.
- **Never close or re-title original tickets.**
- **Bundle before clarify**, always.
- **Subagents are unnamed and synchronous.** No `name`, no `SendMessage`, no
  `run_in_background`. Re-dispatch with inlined context instead of resuming.
- **Project id is a parameter.** Never infer it from cwd.
