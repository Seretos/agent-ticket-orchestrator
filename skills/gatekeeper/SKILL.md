---
name: gatekeeper
disable-model-invocation: true
description: Board pre-flight — bundles open Backlog tickets into work packages (epics for collisions or effort batches) without asking for confirmation, then clarifies every open question against ticket, comments and code. A question it cannot answer itself is posted as a ticket comment, not asked in chat — the package moves to the board's Question column and the gatekeeper moves straight on to the next one, so one hard-to-clarify package never blocks the rest of a run and every card waiting on a human sits in one column. Clear packages move to Planned; on a later pass, an answered Question card of its own goes straight from Question to Planned. Never moves anything to Todo, never dispatches the developer plugin, never edits code. Installed per project; invoke as "/agent-ticket-orchestrator:gatekeeper" from the project's main checkout (project_id=<id> overrides the repo-derived id). A human starts the session, but is not needed at the keyboard while it runs — open questions wait in ticket comments until the next invocation.
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
and the package moves to **Question** — you move on to the next candidate
immediately. The human answers in the ticket, at their own pace, and the
next gatekeeper run picks the answer up from the Question column. Question
is the one place a human looks for "what needs me" across every project;
a card waiting on an answer in Backlog is invisible among a hundred others
(the user's own words, 2026-08-29, after the Backlog had grown across
projects to the point where finding the asked-about tickets was work).

## Inputs

- `project_id` — **optional; resolved from the repository you are in.** This
  plugin is installed per project and runs from that project's main checkout.
  Resolution, in this order: an explicit `project_id=<id>` argument wins;
  otherwise run `git remote get-url origin`, reduce it to `owner/repo`
  (`git@github.com:owner/repo.git` → `owner/repo`; `https://…/owner/repo.git`
  → `owner/repo`), call `search_projects(query="<owner/repo>", limit=5)` and
  take the single result whose `path` equals it exactly. Pass its `id` on
  **verbatim** (search matches case-insensitively, every other tool is
  case-sensitive). No match or more than one → STOP and say which repo you
  resolved and which configured ids `list_projects(fields="light")` returned
  — never pick one. Thread the resolved id into every MCP call and every
  subagent prompt. (Light `list_projects` returns only `{id, provider}`,
  which is why it serves the STOP message but not the resolution: this skill
  reads `path`, `permissions`, `local_path` and `provider` from the resolved
  entry.)
- The project's `local_path` (read from the resolved project entry) — handed to the subagents
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
3. **Write permission.** From the resolved project entry read `permissions.issues.create` and
   `issues.modify` must be `true` (you create epics, add relations, post
   comments, move cards). Otherwise STOP and say which flag is missing.
4. **Relation vocabulary.** `list_relation_kinds()` is called once, at the
   start of Step 2, and its result is kept for the rest of the pass:
   `provider_support` for this project's `provider` (read from the resolved project entry)
   decides the dependency-writing path in Step 3.5. A provider without
   `blocked_by` (GitLab) is **not** a stop condition — see Step 3.5's
   fallback.

## Step 1 — enumerate the Backlog, and your own answered Question cards

```
list_tickets(project_id, column="Backlog", status="open", limit=100, omit_body=True, not_labels=["gatekeeper-ignore"])
list_tickets(project_id, column="Question", status="open", limit=100, omit_body=True, not_labels=["gatekeeper-ignore"])
```

**A ticket carrying the `gatekeeper-ignore` label is not a candidate.** The
label is a human's "not now": they set it, they remove it, and removing it
puts the ticket back into the next pass unchanged. The exclusion happens
here, in the two calls above, and nowhere else — an ignored ticket never
reaches the bundler, the clarifier, an epic, a comment or a column move, so
no later step checks for the label. That covers your own answered Question
cards too: an ignored one is not re-bundled. The filter needs no label in the
repository's catalog (verified on GitHub, 2026-09-21: `not_labels` naming a
label that does not exist returns the unfiltered list, no error) — you never
create the label, never add it and never remove it.

Two more calls, for the report only — their result feeds Step 5's `ignored`
line and nothing else:

```
list_tickets(project_id, column="Backlog", status="open", limit=100, omit_body=True, omit_nulls=True, labels=["gatekeeper-ignore"])
list_tickets(project_id, column="Question", status="open", limit=100, omit_body=True, omit_nulls=True, labels=["gatekeeper-ignore"])
```

A candidate whose `depends_on` names an ignored ticket is nothing new: the
ignored ticket is a blocker outside this pass, the relation is written as
for any other (Step 3.5), and `run` withholds the dependent until the
blocker reaches Done.

Then drop every ticket that is **already a child of an epic**: for each
candidate call `list_hierarchy(project_id, ticket_id)` and exclude it when
`parent` is non-null (a previous gatekeeper pass already packaged it — the
epic, not the child, is what moves). Keep epics themselves in the list; the
bundler may fold further tickets into them or leave them as-is.

**Question cards are yours only when all three hold**, checked per card via
`list_comments(project_id, ticket_id, order="desc", limit=20, body_max_chars=200)`
(a heading scan needs no bodies; only Step 2's `previous_cut` / `changed_by` look-ups read comment content, and they read it in full):

1. it carries a `## Clarification needed (gatekeeper)` comment — you put it
   there;
2. it carries **no** `<!-- adev:event` comment — it was never dispatched, so
   it is not a card `run` escalated (those are `run`'s and a human's, never
   yours, even if they also carry an older clarification comment — except
   for the one relation Step 3.5 writes onto such a card when a package of
   this pass is what it waits for);
3. at least one comment is **newer** than your latest clarification comment —
   somebody answered. A card with your question and nothing after it is still
   waiting; skip it silently, nothing changed. One exception, for the second
   card of an oversized pair (Step 2): when your latest clarification comment
   carries a `gatekeeper:oversized` block whose `proposal_on:` names another
   ticket, the owner answers *there* — apply this test to that ticket's
   comments instead, so both cards of the pair return in the same pass.

Cards that pass go into the candidate list like any Backlog ticket — they
are re-bundled and re-clarified the same way, and on `CLEAR` they move
Question → Planned (Step 4). Cards that fail 2 or 3 are left exactly where
they are and are not mentioned in the report except by count ("<n> Question
cards still waiting, <m> belong to run").

0 candidates → report "Backlog is empty / fully packaged, no answered
Question cards" — plus the `ignored` line of Step 5 when any ticket was
skipped for its label — and stop.

## Step 2 — bundle (before clarifying — the order is mandatory)

**Reconstruct `previous_cut` for a candidate returning from Question.** `list_hierarchy` for `package`; `get_ticket(project_id, ticket_id, include_relations=True)` for `depends_on`; the latest `## Frame (gatekeeper)` / `## Dependency (gatekeeper)` / `## Re-cut (gatekeeper)` comment (`list_comments(order="desc")`) for `reason` (the prior package kind: `collision`/`effort`/`single`) and `prior_rationale`.
`prior_rationale` carries the prior pass's actual reasoning, distinct from the reason kind above — not just the kind — with `source` naming which comment it came from.
`reason: "unknown"` and empty `prior_rationale` when nothing is recoverable. A first-generation candidate (never through Question before) gets no `previous_cut` at all.

**Collect `oversized answered` for a returning oversized pair.** A candidate that came back from Question with a `gatekeeper:oversized` block in its clarification comment (Step 1) was asked about together with the other ticket of its `pair:`. Read the owner's reply — the comments newer than the question on the `proposal_on:` ticket, in full — and pass it to the bundler verbatim, so the pair is not reported a second time.

Dispatch the `bundler` **once**, unnamed, synchronous, fresh:

```
Agent(
  subagent_type="bundler",
  description="bundle Backlog of <project_id>",
  prompt="project_id=<project_id> local_path=<local_path>\n
          Candidates (id · title · labels):\n<the full list>\n
          previous_cut (for returning candidates only): <the reconstructed
          object per candidate, omitted for a first-generation ticket>\n
          oversized answered (for a returning oversized pair only): #a/#b —
          <the owner's reply, verbatim>\n
          Return the packages JSON block."
)
```

It returns a JSON block:

```json
{ "packages": [
  { "title": "...", "reason": "collision" | "effort" | "single",
    "tickets": [{ "id": <id>, "size": "small" | "medium" | "large",
                  "paths": [{ "path": "...", "role": "deliverable" | "accompanying" }, ...] }, ...],
    "rationale": "...",
    "depends_on": [ { "ticket": <id>, "why": "...", "evidence": "..." } ],
    "needed_by":  [ { "ticket": <id>, "why": "...", "evidence": "..." } ],
    "changed_from_previous": { "ticket": <id>, "was": "...", "now": "...",
                                "changed_by": "..." } }
  ],
  "oversized": [
  { "tickets": [<id>, <id>], "why": "...",
    "slices": [ { "slice": "...", "observable": "...", "covers": [<id>, ...] } ] }
] }
```

**A changed verdict is accepted when it is named, not when it is locatable.** For a candidate carrying `previous_cut`, compare this pass's package/`reason` against it.
Absent or empty `changed_by` in `changed_from_previous` means the named change never arrived: the previous cut stands, and this pass keeps the prior package/`reason` for that ticket instead of the bundler's new one.
A non-empty `changed_by` accepts the new cut outright.
Then *try* to locate the named answer/change in `list_comments` or the ticket body, and record the outcome in Step 5 as a confidence signal only: verified when found, unverified: not found in comments/body when not.
Locatability never rejects a cut — an answer can arrive outside a ticket comment, or be paraphrased; only an absent or empty `changed_by` keeps the previous cut standing.

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

### Every ticket gets a lane, from a script

Two lower plugins exist, and a package runs in exactly one: `code`
(`agent-autonomous-developer`, test-first) or `prose`
(`agent-autonomous-prompt-engineer`, for files a model executes — skills,
agents, prompts). A prose change sent to the developer deadlocks its critic
gates and lands in Question (`agent-autonomous-developer#122`, this repo's
`#35`). The lane is **derived from paths by a program** — never asked of a
human, the bundler or the clarifier, and never your own judgement.

For every ticket in the bundler's JSON, **before** anything is materialised,
pipe its `paths` list, unchanged, as JSON on **stdin** to
`scripts/gatekeeper/classify-lane.py` (`python`, or `python3` if `python` is
not on PATH) and read stdout:

```
lane: code | prose | mixed
deliverables: <n> | none
path: <lane> <role> <path>
```

`exit 0` is a decided verdict. `exit 2` is unusable input — in practice an
empty `paths`: treat the ticket as `code` (today's behaviour) and record
`lane undecided: #<id> — no footprint, treated as code` for Step 5. The
path → lane table lives in that script and only there; do not restate or
second-guess it.

**A ticket an earlier pass already split keeps the lane that split gave
it.** `list_comments(project_id, ticket_id, order="desc", limit=20, body_max_chars=600)`
— a `## Lane split (gatekeeper)` comment's `gatekeeper:lane` block carries
`lane:` for this ticket; take it instead of the verdict, and never split the
same ticket twice. (Its body still describes both halves, so the bundler will
report both again; the `Non-goal (re-cut to #<n>)` line is what took the
prose half out.)

### The prose lane is optional — check that this project has it

`agent-autonomous-prompt-engineer` is not a dependency of this plugin: the
developer is needed in every project, the prompt engineer only where
model-executed prose is actually shipped. So before any ticket is routed to
it, check — **once per pass, and only when at least one ticket came back
`prose` or `mixed`**:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/gatekeeper/prose-lane-available.py" "<local_path>"
```

stdout `prose_lane: available | unavailable` plus the `source:` settings file
that decided; `exit 0` available, `exit 2` unavailable. It reads the settings
files a package session will actually see; do not substitute your own view
of which skills this session has loaded.

**Available** → carry on below. **Unavailable** → every `prose` and every
`mixed` ticket of this pass is a question for a human, because both answers
cost something and neither is yours to pick: installing a plugin into the
project, or knowingly sending prose through the developer, whose critic gates
are known to deadlock on it. For each such ticket — no split, no
`lane:prose` label, no clarifier dispatch this pass — post

```
## Clarification needed (gatekeeper)

### Q1
**About:** this ticket changes files a model executes (skills, agents,
prompts), and this project does not have the plugin that builds and verifies
those.

<the classifier's output, verbatim>

- **Enable `agent-autonomous-prompt-engineer` in this project's
  `.claude/settings.json`, then reply here** *(recommended)* — the ticket is
  routed (and, when it also changes code, split) on the next pass.
- **Reply "code lane"** — the ticket runs through `agent-autonomous-developer`
  as it is, unsplit; expect its test gates to object to prose and the package
  to possibly come back as a Question.
```

and move it to Question (Step 3's calls). A bundle containing such a ticket
is rejected first, as below, so the other members are not held up.

**When such a card returns answered:** run the script again. Available →
the ticket is handled as if the question had never been asked. Still
unavailable and a reply after your question says `code lane` → its lane is
`code` for this and every later pass (the reply is the record; no label, no
split), reported as `lane forced to code by reply: #<id>`. Still unavailable
and no such reply → leave the card in Question, post nothing, and report
`prose lane not installed: #<id> still waiting`.

### A bundle never spans lanes

A `collision` or `effort` package whose members do not all share one lane is
rejected, the same way an oversized `collision` package is (below): members
of the same decided lane stay together as one package of the original
`reason` when two or more remain, a lone member becomes `single`, and every
`mixed` member becomes `single` so it can be split. Each member's
`depends_on` entries are kept and written through Step 3.5. Report it in
Step 5 as `bundle rejected (spans lanes): #a, #b code · #c prose`.

### A mixed ticket is split into a code ticket and a prose ticket

One package is one branch, one PR, one event stream and one set of retry
budgets, so a `mixed` ticket is never run as two processes on one ticket —
it becomes two tickets with a dependency. Code first (a script with real
behaviour tests), prose second (the skill that calls the merged script).
For each ticket whose verdict is `lane: mixed` with `deliverables:` a
number:

1. **Label.** `list_labels(project_id)`, then
   `create_label(project_id, "lane:prose")` if absent — GitHub 404s on an
   unknown label at `create_ticket` time.
2. **Create the prose ticket.** No `custom_fields`, so it lands in Backlog.
   The body is exactly the two headings `templates/ISSUE_TEMPLATE/task.yml`
   requires:

   ```
   create_ticket(project_id, title="<the original title> — prose half", labels=["lane:prose"], template="task", body="### Goal
   The model-executed files of #<original>, changed as #<original> describes, once its code half has merged: <the deliverable paths the classifier listed as prose>

   ### Acceptance
   <the original ticket's acceptance lines that concern those files, verbatim; when none can be told apart: "The files above carry the change #<original> describes for them and refer to what #<original> merged.">
   Evidence is the prose lane's own (blind tests, step replays) — never a string-presence test on these files.")
   ```

3. **The original keeps the code half** — its lane is `code` from here on —
   and the new ticket joins this pass as a `single` package of lane `prose`:
   it is clarified in Step 3 like any other package, which is why the split
   happens here and not after clarification.
4. **Order.** Add the original's id to the new ticket's `deps`, so Step 3.5
   writes `blocked_by` from the prose ticket to the code ticket and verifies
   it with `relation-readback.py`.
5. **Move the slice.** Emit a `recut` entry `{from: <original>, to: <new
   ticket>, slice: <the prose paths and what the ticket asks of them>, why:
   "model-executed prose runs in the prose lane, after the code it calls has
   merged"}` for Step 3.7 — `## Frame (gatekeeper)` on both, the slice as an
   additional requirement on the new ticket and a non-goal on the original,
   no epic, no confirmation round.
6. **Record it**, once, on the original:

   ```
   add_comment(project_id, ticket_id=<original>, body="## Lane split (gatekeeper)

   Code half: this ticket.
   Prose half: #<new ticket> — blocked_by this ticket.
   Paths: <every `path:` line of the classifier, verbatim>

   <!-- gatekeeper:lane v1
   lane: code
   prose_ticket: #<new ticket>
   -->

   Object by replying on this ticket.")
   ```

   The MCP prepends `#ai-generated`; do not add it yourself. The block is
   read by the same dumb `key: value` reader as `adev:event`.

**`lane: mixed` with `deliverables: none`** is the one shape nobody can
argue from the paths — both halves were reported as accompanying, so there
is no deliverable to cut along. Do not split and do not clarify: post
`## Clarification needed (gatekeeper)` with the classifier's output verbatim
and the one question "Which of these files is this ticket for — the code, the
model-executed prose, or both?", move the ticket to Question (Step 3's
calls), and go on. **Once:** when the ticket returns answered and the verdict
is still `mixed` / `deliverables: none`, treat it as `code` and record
`lane undecided: #<id> — treated as code` for Step 5.

### Materialise multi-ticket packages as epics

For each accepted package with **two or more** tickets:

1. `list_labels(project_id)` — if no `epic` label exists, `create_label`
   (GitHub 404s on an unknown label at `create_ticket` time).
2. `create_ticket(project_id, title=<bundler title>, labels=["epic"], template="epic", body=…)`
   where the body has exactly two headings, matching the required fields of
   `templates/ISSUE_TEMPLATE/epic.yml`:

   ```
   ### Children
   - #<id> <title>
   - #<id> <title>

   ### Rationale
   <the bundler's rationale, verbatim>
   ```

   Omit `custom_fields` so the epic lands in Backlog like any new ticket.
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

### An oversized collision package is split, not materialised

When a `collision` package carries two or more `size: large` tickets, the gatekeeper rejects it into `single` packages, one package per ticket.
No epic is materialised for it.
Each member's `depends_on` entries, including the kept large↔large edge, are written per member through the ordinary Step 3.5 path.
Report it in Step 5 as `collision package rejected (2 large tickets): #a, #b are now single`.

### An oversized pair is a Question, not a cut

The bundler reports two overlapping `size: large` tickets it declined to
bundle as an `oversized` entry (absent on an ordinary pass): the pair, what
overlaps, and a **proposed vertical split** — slices that each name
something a user of the software can observe — or `"slices": []` when it
found no honest one. You notice the pair and you ask; you never cut. A size
judgement is not a reason to move scope between two tickets without the
owner seeing it. For each entry, with `#a` the **lower ticket id** of the
pair (a deterministic tie-break, not a judgement) and `#b` the other:

1. **Idempotency first.** `list_comments(project_id, ticket_id=#a, order="desc", limit=20, body_max_chars=600)`
   — a `gatekeeper:oversized` block naming this pair with no comment newer
   than it means the question is already posted and still waiting: post
   nothing and move nothing for this pair. With a newer comment, the pair
   came back through Step 1 as answered and the bundler was told so
   (`oversized answered:`, above): post nothing either — should it report
   the pair again anyway, ignore the entry — and treat both tickets as the
   ordinary `single` packages they are. The reply reaches the clarifier as
   an ordinary comment.
   You do not split a ticket or edit a body on the owner's behalf; an
   accepted split is the owner's write.
2. **Post the proposal, once, on `#a`:**

   ```
   add_comment(project_id, ticket_id=#a, body="## Clarification needed (gatekeeper)

   ### Q1 Two large overlapping tickets — cut them differently before they run?
   **About:** #a (<title>) and #b (<title>) are both large and overlap in <the entry's `why`>.
   **Decision:** whether the two run as filed, one after the other, or are re-cut first into slices that each ship something a user can see.
   - (a) Re-cut into these slices *(recommended)* — 1. <slice> — a user can then: <observable> (serves #<covers>) · 2. <slice> — …
   - (b) Run both as filed, in dependency order — the overlap is built once in the first and re-touched by the second.
   - (c) I cut them myself — reply here once the tickets are edited.

   <!-- gatekeeper:oversized v1
   pair: #a,#b
   proposal_on: #a
   -->")
   ```

   With `"slices": []`, option (a) is absent, (b) carries *(recommended)*,
   and the `**About:**` line adds that no split into user-visible slices
   was found. The block is read by the same dumb `key: value` reader as
   `adev:event`. The MCP prepends `#ai-generated`; do not add it yourself.
3. **Point the other card at it:** one comment on `#b`, heading
   `## Clarification needed (gatekeeper)`, the single line "This ticket and
   #a are asked about together — the question and its proposal are on #a;
   answer there." and the same `gatekeeper:oversized` block.
4. **Both cards go to Question** — `update_ticket(project_id, ticket_id=<each>, custom_fields={"Status": <native of Question>}, response="light")`
   for `#a` and for `#b`. Neither is clarified and neither is released this
   pass: releasing one half while the pair's boundary is under review would
   dispatch a package whose scope may still change. Step 3.5 still writes
   the bundler's `depends_on` for both — an order between them is a fact
   whichever way the owner decides.

No new answer path exists for this: Step 1's test for your own answered
Question cards is what brings the pair back.

From here on, *package ticket* means the epic, or the single ticket.

**Record the lane on the package ticket.** A package's lane is its members'
shared lane (a bundle never spans lanes, above). A `prose` package ticket
carries the label `lane:prose` — `list_labels` / `create_label` if absent,
then `update_ticket(project_id, ticket_id=<package>, labels_add=["lane:prose"], response="light")`,
skipped when it already carries it. A `code` package carries **no** label;
when a returning ticket carries `lane:prose` but is `code` on this pass,
`labels_remove=["lane:prose"]`. The label is the whole interface to `run`:
it starts the prose lane's entry skill for a package that carries it and the
developer's for every other, and looks at the lane for nothing else.

**Build the package map while materialising.** Keep, for the rest of this
pass, every candidate ticket id → the id of the package ticket it now belongs
to (its epic, or itself). Every dependency written in Step 3.5 is resolved
through this map first, so a dependency naming a ticket that became an epic
child in this same pass lands on the epic, not on the child. Also fold the
bundler's own `depends_on` entries into a per-package `deps` list, and its
`needed_by` entries into a per-package `needed_by` list, here — both are
written in Step 3.5 together with the clarifier's.

## Step 3 — clarify each package

**Dispatch the `clarifier` in waves.** A clarifier only reads, so the calls
of packages that do not depend on each other go out together; everything
that writes stays one package at a time.

1. **Clarify set.** Every package ticket from Step 2, in the bundler's order,
   except the ones Step 2 already moved to Question (both cards of an
   oversized pair, a ticket held for the missing prose lane, a `mixed`
   ticket with `deliverables: none`).
2. **Same-pass edge.** A package waits for another when one of the bundler's
   `depends_on` targets for it, lifted through the Step 2 package map, is
   another package of the clarify set. A target that lifts to the package
   itself, or to a ticket outside the clarify set, is not an edge and never
   holds a dispatch back (Step 3.5 still writes it). A `needed_by` entry
   never creates an edge either: it names a ticket outside the candidate
   list.
3. **Waves.** Wave 1 is every package with no same-pass edge. Each later wave
   is the packages whose same-pass blockers were all in earlier waves. The
   bundler's order decides nothing else: a package listed first still waits
   for its blocker. A wave holds at most **4** packages; the ones beyond 4
   move to the next wave, in bundler order. The cap exists because each
   clarifier can make up to two `list_tickets` searches and GitHub's Search
   API allows 30 requests a minute; whether a wave's concurrent reads stay
   under the provider's secondary rate limits is **unverified**. The cap
   bounds that risk, it does not remove it — keeping the writes sequential
   removes only the write-side risk.
4. **One wave is one assistant message** carrying one `Agent` call per
   package in it — the call below. No `run_in_background`: these are
   ordinary synchronous calls, and the turn waits until every one of them
   has returned.
5. **Handle the results one at a time**, in the main turn, in dispatch order.
   For one package: parse its frame block, then Step 3.5, Step 3.6, Step 3.7,
   then its status (the `NEEDS_INPUT` post and the move to Question, or
   Step 4) — all against that package's own clarifier result. Finish that
   package before you start on the next result. Never issue two packages'
   writes in one message.
6. **Dispatch the next wave only after every package of this wave is fully
   handled.** A dependent's clarifier then reads its blocker's frame comment
   and column as this pass left them. The package map is already complete
   from Step 2, so lifting never waits for a wave.
7. **Cycle.** When packages remain and none of them can be dispatched —
   their same-pass edges form a cycle — send the rest one per wave, in
   bundler order, and report `clarify order cycle: #a, #b` in Step 5.

The call, one per package in the wave:

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

Each result ends with a status line:

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

  Then move the package ticket to **Question**:

  ```
  update_ticket(project_id, ticket_id=<package>, custom_fields={"Status": <native of Question>}, response="light")
  ```

  and move on to the next result immediately — do not wait here. Record
  it in Step 5's report as "needs answer — see ticket #<id>". Only the
  package ticket moves; an epic's children stay in Backlog, as in Step 4.

  A **repeat pass** (this package came in through Step 1's Question branch —
  it already carries an earlier `## Clarification needed (gatekeeper)`
  comment and the human has since replied to it) re-dispatches the
  `clarifier` exactly as above; it reads the reply itself. If it comes back
  `NEEDS_INPUT` again, the new questions are posted and the card simply
  stays in Question — nothing to move. Count the `## Clarification needed (gatekeeper)`
  comments on the ticket (`list_comments`); at **4 or more**, add one line to
  Step 5's report flagging the package as unusually hard to clarify — not a
  cap, not a block, just a signal that it may need a different kind of
  attention than another clarifier round.

Questions the clarifier could have answered itself from ticket + code are its
bug, not something to post — if you notice it asking such things, note it in
the report, but still post the question rather than answering it yourself:
you are not allowed to decide on the project's behalf either. The same goes
for a question without an `**About:**` line, or one whose options read as
code identifiers rather than user-visible behaviour: post it, flag it in the
report as "clarifier question below the bar" — the human decides whether to
answer or to send it back, and the flag is how the `clarifier` prompt gets
fixed.

**Parse the frame block.** The clarifier's report begins with a
`<!-- clarifier:frame v1 … -->` block on **both** statuses. Parse it as dumb
`key: value` lines — the same reader `run` applies to `adev:event`: empty
value = unknown, unknown keys ignored. You need `symptom`, `measurement`,
`ac`, `premise`, `unprovable_here`, `depends_on` and `needed_by` for every package, `chain`
and `reframe` for Step 3.6, and `ac`, `premise` and `unprovable_here` again for
Step 4's frame comment. `premise` may appear more than once — collect every
occurrence, in the order they appear. `unprovable_here` is read the same way:
every occurrence, `none` when absent.

**Render the premises line, once, here — Step 3.6 and Step 4 both reuse this
exact rendering as defined in Step 3, rather than each inventing their own.**
When the collected `premise` values are not `none`, render one line:

  Premises to verify before planning: <p1>; <p2>; …

joining every collected value with `; ` — two premises `browser_install` and
`schema_v2_migrated` render as `Premises to verify before planning:
browser_install; schema_v2_migrated`. A single premise still uses the same
prefix, with one item and no separator. Omit the line entirely when
`premise` is `none`.

**Render the struck clauses the same way, once, here.** An `unprovable_here`
value is an acceptance clause this package's own PR run cannot produce
evidence for — a real run against an external service, another OS, a
release-only job, an installed artifact, a real shell, a person's check. For
every collected value other than `none`, render one line each:

  Not proven by this package: <clause> — this package's own PR run cannot produce that evidence; do not plan for it, and its absence is not a gap.

Step 3.6, Step 3.7 and Step 4 reuse these lines verbatim. That is the whole
reaction: you create no ticket for such a clause, write no relation, apply no
label and emit no `recut` — the clause is struck and recorded, and the
package is processed like any other. The clarifier has already written what
*can* be built (the artifact that makes the check possible for whoever
performs it later) into `ac:`.

If the block is missing or unparseable, record `frame block missing` in
Step 5's report and continue on the `STATUS:` line alone — never abort a
pass for a malformed block.

## Step 3.5 — link dependencies

Runs per package, in the main turn, first thing once that package's result
is handled (Step 3's waves), **on both
statuses** (CLEAR and NEEDS_INPUT) — a dependency is a fact, not a decision,
and a package that goes to Question does not make it false.

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
  6. Read back and verify. Build `expected` (every target `deps` resolved
     to, after lifting), `relations` (a fresh `get_ticket(project_id,
     <this package>, include_relations=True)`, `{kind, target}` per entry —
     matched against `expected` only when `kind` is `blocked_by` or
     `relates_to`; any other kind, even at the same target, does not
     satisfy an expected dependency) and `reasons` (the `not found` /
     `closed` / `self-edge` reason recorded per target in steps 2-3 above,
     keyed by target).
```

Pipe `{"expected": [...], "relations": [...], "reasons": {...}}` as JSON on **stdin** to `scripts/gatekeeper/relation-readback.py` (`python`, or `python3` if `python` is not on PATH), and read its `verdict: ok|gap` line from stdout — `exit 0` on `ok`, `exit 2` on `gap` (naming the missing target(s)).
A `gap` verdict: re-write the missing relation once (step 4 above) and re-run the read-back; still `gap` → record it as an **unexplained gap** in Step 5's report.
A `gap` verdict does not move the package to Planned (Step 4); it stays in its current column until the next pass's write succeeds, and Step 5 records the unexplained gap.

**Reverse edges — a ticket that waits for this package.** Then, for the same
package, a second loop. `needed_by` names tickets outside the candidate list
whose own text says they need what this package introduces; the relation is
written **on that dependent**, pointing at this package, because `run` reads
blockers only from the card it is about to dispatch.

```
needed_by = bundler's needed_by for this package  ∪  clarifier frame's needed_by
for each raw dependent #d:
  1. Lift #d exactly as step 1 above (package map, then list_hierarchy,
     at most 3 hops, topmost) -> dependent
  2. dependent == this package -> record "reverse dependency absorbed",
     write nothing, skip steps 3-5, next #d.
     get_ticket(project_id, dependent):
     - not found     -> record "#d not found", write nothing,
                        skip steps 3-5, next #d
     - status closed -> record "#d closed", write nothing,
                        skip steps 3-5, next #d
  3. Write, from the DEPENDENT side:
     - "blocked_by" in provider_support[<provider>] (github, azuredevops):
         add_relation(project_id, ticket_id=<dependent>,
                       kind="blocked_by", target="#<this package>")
     - otherwise (GitLab):
         add_relation(project_id, ticket_id=<dependent>,
                       kind="relates_to", target="#<this package>")
       plus the `## Dependency (gatekeeper)` comment of step 4 above, posted
       on the DEPENDENT, with `Blocked by: #<this package>` and
       `blocked_by: #<this package>` in its `gatekeeper:deps v1` block.
  4. Idempotency, as step 5 above: skip a relation the dependent already
     carries, and on GitLab a comment it already carries.
  5. Read back per dependent: pipe {"expected": ["#<this package>"],
     "relations": <a fresh get_ticket(project_id, <dependent>,
     include_relations=True), {kind, target} per entry>, "reasons": {}}
     to relation-readback.py, exactly as above.
```

A `gap` gets one re-write and one re-read. Still `gap` → it is an
**unexplained gap**, and it withholds **this package** — not the
dependent — from Planned, so the next pass finds the entry again and
re-writes it before anyone can release the package to Todo.

The dependent may be a card `run` owns — in Question, carrying an
`adev:event` comment — and the relation is written there all the same: a
dependency is a fact, not a decision, and the relation is the only write
that makes `run` hold the card until this package reaches Done; a report
line would rely on a human remembering it when they move both cards to
Todo. It is also the only write such a card receives: no other comment, no
label, no frame comment, no column move. On GitLab the one
`## Dependency (gatekeeper)` comment is part of that relation's record and
is the single exception; on GitHub and Azure DevOps the dependent gets the
relation and nothing else.

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

1. **Idempotency first.** `list_comments(project_id, ticket_id=<package>, order="desc", limit=20, body_max_chars=200)` (a heading scan) — if a
   `## Regression chain (gatekeeper)` comment already exists **and** the
   package already carries the `regression-chain` label, do nothing here; the
   chain was recorded on an earlier pass and re-posting it is noise on
   exactly the ticket that already has too much history.
2. **Label.** (Every `update_ticket` here passes `response="light"` — the write tools return a light echo by default since `seretos-agents/agent-project-issues#314`, and this skill reads nothing from them.) `list_labels(project_id)` — `create_label(project_id,
   "regression-chain")` if absent (GitHub 404s on an unknown label at write
   time) — then `update_ticket(project_id, ticket_id=<package>,
   labels_add=["regression-chain"], response="light")`.
3. **Comment.** `add_comment(project_id, ticket_id=<package>, body=…)`:

   ```
   ## Regression chain (gatekeeper)

   | ticket | acceptance criterion | outcome |
   |---|---|---|
   | #<a> | <its AC> | closed <date> — symptom persisted |
   | #<b> | <its AC> | closed <date> — symptom persisted |

   Symptom: <frame symptom>
   Measurement of this ticket's AC: <frame measurement>
   Acceptance criterion: <frame ac, or "as filed">
   Implemented as: <frame reframe, or "as filed — the ticket already has a symptom AC and a non-goal">
   Premises to verify before planning: <p1>; <p2>; … — rendered exactly as Step 3 defines; omit this line when `premise` is `none`
   Not proven by this package: <clause> — … — one line per `unprovable_here` value, rendered exactly as Step 3 defines; omit when `none`

   Object by replying on this ticket; otherwise the package is built this way.
   ```

   Content comes from the clarifier's `### Frame` lines, verbatim — you have
   no code access and must not re-derive it. The MCP prepends
   `#ai-generated`; do not add it yourself.
4. The clarifier's root-cause mandate is already discharged: it detected the
   chain and its own protocol obliged it to **reframe and stay CLEAR** —
   the reframe is applied and reported through this comment, never asked as
   a question (until 2026-08-29 it was, and the alternative "proceed as
   another point fix although #a and #b already did that" was never once
   chosen). **There is no second dispatch.** Step 3's normal status handling
   then acts on the status unchanged; only a `## Clarification needed
   (gatekeeper)` comment counts toward the existing "4+ rounds" heuristic.

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

## Step 3.7 — apply a recut
Runs on **both** clarifier statuses (`CLEAR` and `NEEDS_INPUT`), immediately after Step 3.5, for every `recut` entry of this pass. Step 2's lane split is the only source of one: its `from` is the ticket that was split and its `to` the prose ticket created there. The bundler emits none — a size judgement is never applied as a cut (Step 2, *An oversized pair is a Question, not a cut*) — so the absence of a confirmation round below describes only a split the architecture forces, two lanes needing two lower plugins.

For each `recut` entry, on **both** endpoints, in this order:

1. Post a `## Frame (gatekeeper)` comment — the same body Step 4 defines, rendered from the frame block Step 3 already parsed for that package:

```
add_comment(project_id, ticket_id=<endpoint>, body=…)
```

```
## Frame (gatekeeper)

Symptom: <frame symptom>
Acceptance criterion: <frame ac>
Premises to verify before planning: <p1>; <p2>; … — omit when `premise` is `none`
Not proven by this package: <clause> — … — one line per `unprovable_here` value, as Step 3 renders it; omit when `none`

<the labelled re-cut line for this endpoint — see below>

<closing sentence — pick by trigger, never more than one>

Object by replying on this ticket.
```

On the **target** (`to`) endpoint, this line renders:

Additional requirement (re-cut from #<from>): <slice>

The closing sentence: when neither `ac:` nor `premise:` nor `unprovable_here:` fired for this package, use the third variant, verbatim — "The ticket's own acceptance criterion is unchanged; the re-cut line above is part of this package's frame." Otherwise Step 4's variants apply unchanged, chosen the same way Step 4 chooses them.

On the **source** (`from`) endpoint, this line renders:

Non-goal (re-cut to #<to>): <slice>

2. Post a `## Re-cut (gatekeeper)` comment:

```
add_comment(project_id, ticket_id=<endpoint>, body=…)
```

```
## Re-cut (gatekeeper)

From: #<from>
To: #<to>
Slice: <slice>
Why: <why>

Object by replying on this ticket.
```

No epic is created for a `recut` pair, on either endpoint, and neither endpoint waits for a reply.

## Step 4 — release to Planned

On CLEAR, first the frame comment.
Post the frame comment when `ac:` is anything other than `as-filed` — either for that reason, or because `premise:` is not `none`, or because `unprovable_here:` is not `none` — unless Step 3.6 already posted a `## Regression chain (gatekeeper)` comment carrying the same content, or Step 3.7 already posted a `## Frame (gatekeeper)` comment for this same endpoint this pass: skip Step 4's post in either case, so no endpoint ever carries two frame comments.

```
add_comment(project_id, ticket_id=<package>, body=…)
```

```
## Frame (gatekeeper)

Symptom: <frame symptom>
Acceptance criterion: <frame ac>
<the clarifier's "Acceptance criterion" line's helper-measurement note, verbatim>
Premises to verify before planning: <p1>; <p2>; … — rendered exactly as Step 3 defines; omit this line when `premise` is `none`
Not proven by this package: <clause> — this package's own PR run cannot produce that evidence; do not plan for it, and its absence is not a gap.

<closing sentence — pick by trigger, never both>
```

The `Not proven by this package:` line appears once per `unprovable_here`
value, rendered exactly as Step 3 defines, and is omitted when the key is
`none`.

The closing sentence depends on which trigger fired the comment: when
`ac:` differs from `as-filed`, use "The ticket's own finish line measured an
internal quantity; the package is built and reviewed against the symptom
above." — when the comment is posted solely because `premise:` is not `none`
(the AC itself is `as-filed`, unchanged), that sentence is false and must be
replaced with "The ticket's own acceptance criterion is unchanged; verify the
premise(s) above before planning." When `unprovable_here:` is not `none`,
neither of those is true and this one takes precedence over both: "The
clause(s) named above stay in the ticket body but are not proven by this
package; it is built and reviewed against the acceptance criterion above."
Every variant ends with the same final line: "Object by replying on this
ticket."

This comment is load-bearing, not decoration: the lower plugin's
`context-extractor` reads the package ticket's comments, and this is the only
way a rewritten AC reaches the developer and the reviewer. The struck-clause
line is load-bearing for the same reason, doubled: you never edit a ticket
body, so the clause still stands there, and without the line the planner
tries to satisfy it and the plan-critic calls its absence a gap — that is how
`lib-python-harness#27` reached `blocked` ("the AC requires the live suite to
execute in this PR's own run"). `run`'s `triage` answers such a `blocked`
event from this line. Idempotent like
Step 3.6 — skip it when an identical `## Frame (gatekeeper)` comment already
exists. Then:

```
update_ticket(project_id, ticket_id=<package>, custom_fields={"Status": <native of Planned>}, response="light")
```

This is the same call whether the package came from Backlog or from your own
answered Question card (Step 1) — Question → Planned is the one move out of
Question a skill makes, and only for a card the gatekeeper itself put there
and a human has since replied on. `run`'s Question cards are never moved or
commented on; Step 3.5's reverse-edge relation is the one write they receive.

Then leave the release confirmation, always, once the move above has succeeded — the comment asserts a move that happened, so a failed move leaves nothing behind. Write `Question` in place of `Backlog` on the `Moved:` line for a card reclaimed from Question; the body has no other variable part:

```
add_comment(project_id, ticket_id=<package>, body=…)
```

```
## Released (gatekeeper)

Package: <single #<id> | epic #<id> (children #a, #b)> — <bundler reason>
Checked: bundling against the open Backlog, then clarification against ticket, comments and code — no open questions.
Moved: Backlog → Planned. Planned → Todo stays a human move.
```

The MCP prepends the `#ai-generated` marker; do not write it yourself. Pass no `response=` argument, and read no comments back to verify the post.

Only the **package ticket** moves. Children of an epic stay exactly where they
are (Backlog) — the board shows one card per unit of work, and the `run`
enumerates Todo only, so a child never gets dispatched on its own.

A package carrying a `blocked_by` (or its GitLab `relates_to` fallback)
relation moves to Planned like any other CLEAR package — see Step 3.5.

## Step 5 — report

A table: `package · kind (epic/single) · lane (code/prose) · tickets · reason (bundler) ·
depends on (#ids, or —) · result (Planned / needs answer — see ticket #<id>)`.
Under each row, two indented lines from the frame block: `symptom: <…>` and
`measurement: <…>`.

Also report, each named where it is produced: `regression-chain: #a → #b →
this` for every chained package; `Planned but blocked: #<pkg> waits on #<b>,
which is still in Backlog` for a blocker that has not itself reached Planned
(Step 3.5); `dependency absorbed into the package`, `dependency #t already
closed`, `dependency #t not found`, `frame block missing` (Step 3.5/3);
`clarify order cycle: #a, #b` (Step 3);
`collision package rejected (2 large tickets): #a, #b are now single` (Step
2); `recut applied: #<from> → #<to>` (Step 3.7, lane split only); `oversized pair → Question: #a, #b (proposal on #a | no vertical split found)` and `oversized pair still waiting: #a, #b` (Step 2); `struck (unprovable here): #<pkg> — <clause>` for every `unprovable_here` value (Step 3); `unexplained relation gap:
#<pkg> — #<ids>` for a package withheld from Planned (Step 3.5);
`reverse dependency: #<d> blocked_by #<pkg> (written | already present |
#<d> closed | not found | absorbed)` for every `needed_by` entry, and
`unexplained relation gap (reverse): #<d> — #<pkg>` for a reverse edge whose
read-back failed twice; the withheld package is the second id, `#<pkg>`, not
the dependent `#<d>` (Step 3.5);
`lane split: #<original> (code) → #<new> (prose, blocked_by #<original>)`,
`bundle rejected (spans lanes): …`, `lane undecided: #<id> — …`,
`prose lane not installed: #<ids> → Question`, `lane forced to code by reply:
#<id>` and `prose lane not installed: #<id> still waiting` (Step 2).

Next to the "<n> Question cards still waiting, <m> belong to run" count,
always when it is not zero: `ignored (gatekeeper-ignore): <n> — #<id>, #<id>`
from Step 1's two report-only calls — a forgotten label is only visible
here.

For a returning candidate whose verdict changed from `previous_cut`, report
the `changed_by` confidence signal — `changed_from_previous: #<id> —
changed_by named, verified` when the named change was located, or
`changed_from_previous: #<id> — changed_by named, unverified: not found in
comments/body` when it was not (Step 2).

Flag any package at 4+ `## Clarification needed (gatekeeper)` comments as
unusually hard to clarify (see Step 3). Then one line: "Move the packages you
want processed tonight from Planned to Todo by hand, then start
`/agent-ticket-orchestrator:run project_id=<id>`." — plus, if any package
needs an answer, "Answer the open questions directly on their tickets — they
are all in the Question column — then run
`/agent-ticket-orchestrator:gatekeeper` again to pick them up."

## Hard rules

- **No `AskUserQuestion`, anywhere in this skill.** A bundling decision is
  applied and reported, not confirmed. A clarification question is posted on
  the ticket, not asked in chat. Nothing here waits on a live reply.
- **Never block on one package.** A package that needs a human answer goes
  to Question and you move straight to the next candidate — see Step 3.
- **Never touch a Question card you did not put there.** The one exception
  is Step 3.5's reverse edge: when a card with an `adev:event` comment waits
  for a package of this pass, it receives that `blocked_by` relation (on
  GitLab, `relates_to` plus its one `## Dependency (gatekeeper)` comment) and
  nothing else — no other comment, no label, no column move. Otherwise it
  belongs to `run` and the human — see Step 1.
- **Never move anything to Todo.** Planned is your terminal column. Todo is
  written by humans only.
- **Never dispatch the lower plugin** (`agent-autonomous-developer`) and never
  start a package session. You prepare; `run` executes.
- **Never edit code, never open branches or PRs.** Your writes are: epics,
  the prose half of a lane split, `blocked_by`/`relates_to`
  relations, labels (including `regression-chain` and
  `lane:prose`), clarification comments,
  dependency comments, frame comments, regression-chain comments,
  lane-split comments, release-confirmation comments, and the Backlog → Planned, Backlog → Question
  and Question → Planned moves.
- **Never close or re-title original tickets.** A reframe is a proposal in a
  comment; the human edits the ticket body.
- **An unprovable criterion is struck and recorded, never a ticket.** An `unprovable_here` value produces one line in the frame comment and one in the report — no ticket, no relation, no label, no `recut`, and nothing waits on it. You create tickets in exactly two places: the prose half of a lane split and an epic (both Step 2).
- **You never apply a size-driven cut.** Two overlapping large tickets become one question with a proposed vertical split, and both cards go to Question (Step 2); the only `recut` you apply is the lane split's.
- **Bundle before clarify**, always.
- **The lane comes from `scripts/gatekeeper/classify-lane.py`, never from a
  model.** One package, one lane; a `mixed` ticket is split, a bundle that
  spans lanes is rejected, and a ticket is split at most once (Step 2).
- **No prose routing without the prose lane.** Whether
  `agent-autonomous-prompt-engineer` is installed comes from
  `scripts/gatekeeper/prose-lane-available.py`; when it is not, a `prose` or
  `mixed` ticket goes to Question — never labelled, never split, and never
  quietly sent down the code lane (Step 2).
- **`gatekeeper-ignore` is the human's label.** It is filtered in Step 1's
  `list_tickets` calls and checked nowhere else; you never create, add or
  remove it.
- **Blocked is not unplanned.** A `blocked_by` relation never keeps a CLEAR
  package out of Planned (Step 3.5).
- **An unexplained relation gap withholds Planned.** Unlike `blocked_by`, a relation write that `scripts/gatekeeper/relation-readback.py` cannot verify keeps the package out of Planned until the write succeeds, including a reverse edge written on another ticket (Step 3.5).
- **Never write a dependency relation from the child side.** It is written on
  the package ticket, on both ends, lifted through the Step 2 package map and
  `list_hierarchy` (Step 3.5).
- **The `regression-chain` label and its comment are written once.** Check
  for the existing comment and label before posting (Step 3.6).
- **Subagents are unnamed and synchronous.** No `name`, no `SendMessage`, no
  `run_in_background`. Several `clarifier` calls in one message (Step 3's
  waves) are still synchronous calls; the turn waits for all of them.
  Re-dispatch fresh instead of resuming; the `clarifier`
  reads its own answers back from the ticket, so nothing needs to be inlined
  by hand on a repeat pass.
- **Project id is a parameter.** Never infer it from cwd.
