# agent-ticket-orchestrator

A Claude Code **skill + agents** plugin. Board-driven ecosystem orchestrator: bundles and clarifies Backlog tickets into released work packages (`gatekeeper`), then runs every Todo package unattended through `agent-autonomous-developer` to a merged, CI-green PR, tracking state with board columns (or status labels, on a project without a board binding) and closing the ticket when its PR has merged (`run`).

This plugin ships **only skill and agent content** — no binaries, no MCP server. It is the upper layer over `agent-autonomous-developer` (one package → one green PR) and drives the `agent-project-issues` and `agent-worktree` MCPs.

## What it does

```
Backlog ──gatekeeper──▶ Planned ──human──▶ Todo ──run──▶ Doing ──▶ closed
                                                            ├────────────────▶ Question ──human──▶ Todo / Backlog
                                                            └──split session──▶ Todo (new code half + the prose original)
```

- **`/agent-ticket-orchestrator:gatekeeper`** — a human starts the session, but it does not block on one being at the keyboard while it runs. Reads the open Backlog — skipping every ticket that carries the `gatekeeper-ignore` label — lets the `bundler` propose work packages (epics for tickets that collide in code, or effort batches of tiny tickets; everything else single) and applies the proposal directly — no confirmation round. A ticket that itself sequences its overlap behind another ("a second step after #X") is read as `depends_on`, never a `collision`; a `collision` package is capped at one `size: large` ticket, and two overlapping large tickets the cap rejects are not cut by the gatekeeper — it posts one question with a proposed split into user-observable slices and moves both cards to Question; a candidate returning from Question is bundled with its own `previous_cut` and a changed verdict must be named to be accepted. Every ticket's **lane** — `code` or `prose` (files a model executes: skills, agents, prompts) — is derived from the bundler's footprint paths by `scripts/gatekeeper/classify-lane.py`, never by a model: when the project has the optional `agent-autonomous-prompt-engineer` enabled, a prose package is labelled `lane:prose`, a bundle never spans lanes, and a ticket whose change is both is split into a code ticket and a prose ticket that is `blocked_by` it; when the project does not have it, such a ticket goes to Question instead. Then lets the `clarifier` answer the ticket's problem frame (which user-visible symptom, whether the acceptance criterion actually measures it, prior attempts on the same symptom) before hunting every decision the night shift could not make on its own; a defect whose AC only measures an internal quantity gets a symptom-level AC written for it (posted as a `## Frame (gatekeeper)` comment), an acceptance clause the package's own PR run cannot prove is struck and recorded in that same comment rather than turned into a ticket or a blocker, and a ticket that is the latest in a closed-ticket regression chain gets a `regression-chain` label and a comment stating how it is reframed as a root-cause task — both applied and reported, not asked. What is still asked has to survive a five-test filter (not the ticket's own literal reading, no added scope, not a reframe, a wrong answer must cost a user something durable, answerable without the code open) and is phrased for someone who has not opened the ticket. A question it cannot answer itself is posted **as a comment on the package ticket**, the package moves to the **Question** column (one place for everything that needs a human, across every project), and `gatekeeper` moves straight to the next package instead of waiting; the next pass picks answered cards up from Question and moves them on to Planned. An inter-package dependency (this package needs a capability another ticket introduces) becomes a `blocked_by` relation on the package ticket, and Step 3.5 verifies the write mechanically — via `scripts/gatekeeper/relation-readback.py`, not prose — before the package moves on; the package **still** moves to **Planned** once otherwise clear, unless that read-back finds an unexplained gap; a package moved to Planned also gets a short `## Released (gatekeeper)` comment recording what was checked and the move. `AskUserQuestion` is forbidden here and in `run`, both of which must complete a whole pass unattended; the `ticket` skill below is the one place in this plugin that uses it.
- **`/agent-ticket-orchestrator:ticket`** — files one ticket, interactively, in the same frame shape the `clarifier` expects: a user-visible symptom, an acceptance criterion that measures it with a real call, prior attempts on the same symptom. Asks three questions with `AskUserQuestion` (the one skill in this plugin that does), then makes exactly one `create_ticket` call.
- **You** answer any open questions directly on their tickets, and move the packages you want processed from Planned to **Todo**. Nothing automated answers for you, and nothing automated releases a package to Todo on its own decision. The one automated Todo move is the split session: when a prose-lane package you already released finds, after dispatch, requirements that need code, `run` starts the `gatekeeper` on that one ticket, which files the code half as its own ticket, blocks the original on it and moves both back to Todo — your release of that scope, carried forward. A `gatekeeper` pass you start never writes Todo.
- **`/agent-ticket-orchestrator:run`** (from the project's main checkout; `project_id=<id>` overrides the repo-derived id) — unattended, may run all night. First finishes any `ci-green` package an earlier run left unmerged. Then orders every Todo package by its `blocked_by` relations — a blocker also in Todo is processed first; a package whose blocker is still open elsewhere is left untouched in Todo and reported as skipped; a dependency cycle is reported and processed in board order rather than aborting the night. For each package in that order, sequentially: verify the previous package actually cleared, → Doing, create a worktree on `pkg/<id>-<slug>`, start the package's lower plugin — `agent-autonomous-developer`'s `process-developer`, or `agent-autonomous-prompt-engineer`'s `process-prompt-engineer` for a package labelled `lane:prose` — as a separate `claude -p` process in that worktree (from the skill's own turn, backgrounded) and wait for it to end. The lower plugin writes `adev:event` comments on the ticket and opens the PR; `run` reads the latest event: `ci-green` → merge, then close the package ticket (an epic included) — and if the merge fails on a **conflict**, one rebase-and-retry round before it, too, escalates; branch protection / a missing permission / an unresolved mergeability state → comment on the ticket and Question, human decides; `blocked` → triaged by a read-only subagent first (answerable → answered and re-dispatched immediately; a prose-lane package that reports requirements it may not build → the split session above instead of a re-dispatch; not answerable → Question right away, no wasted retry); `failed`/no terminal event → checked directly against the PR's actual CI state first (a package that only died mid-CI-wait is not `failed`), then one fresh attempt if that does not resolve it, then → Question with the failure summary. Final report per package; SUCCESS only if every package was merged and closed.

Comments are the log, columns are the signal; a finished ticket is a closed one. An empty Question column means no open questions.

## Board model

Register the project in `~/.seretos/projects.yml` with `board.columns` listing the logical columns `Backlog` (first), `Planned`, `Todo`, `Doing`, `Question`. A board binding is **optional**:

- **With `board.binding`** the columns live on a real board (e.g. GitHub Projects v2); how logical names map to it: the agent-project-issues skill, "Board columns: resolve, then write".
- **Without a binding** (label mode, agent-project-issues ≥ 0.3.11 — the usual case on GitLab) each column is a `status:*` label on the ticket. The first column carries no label, so a ticket with no status label is in Backlog — that is why `Backlog` must be listed first.

Both skills make exactly the same calls either way. There is no Done column: **finished means closed** — `run` closes the package ticket once its PR has merged. A board that still has a Done column keeps it; nothing reads it.

**Sending a ticket back to Backlog by hand:** on a bound board, move the card to Backlog; without a binding, remove every `status:*` label from the ticket. If a ticket ever carries two `status:*` labels at once, the next `gatekeeper` pass keeps the one furthest along the column order and removes the others.

Minimal entry without a binding:

```yaml
board:
  columns: [Backlog, Planned, Todo, Doing, Question]
```

Required permissions: `issues.create/modify`, `pulls.create/modify/merge` (`run` requires `merge` and STOPs before touching anything without it; use `/agent-autonomous-developer:process-developer` for single tickets instead), and `board.manage` once for creating missing columns with `ensure_board_column`.

## Labels

| label | set by | meaning |
|---|---|---|
| `gatekeeper-ignore` | **human only** — the gatekeeper never creates, adds or removes it | "Not now": a Backlog ticket (or a gatekeeper Question card) carrying it is not a candidate — not bundled, not clarified, not commented on, not moved, never folded into an epic. Remove the label and the next pass picks the ticket up unchanged. The pass reports how many tickets it skipped this way. `run` does not look at it: it only matters before Planned. |
| `lane:prose` | gatekeeper | The package's deliverables are files a model executes; `run` starts `agent-autonomous-prompt-engineer` for it. No label means the code lane (`agent-autonomous-developer`). |
| `epic` | gatekeeper | A multi-ticket package; the epic is the card that travels the board. |
| `regression-chain` | gatekeeper | The ticket is the latest in a chain of closed tickets on the same symptom and was reframed as a root-cause task. |

Suggested description for `gatekeeper-ignore` when you create it: *"gatekeeper skips this ticket until the label is removed"*.

## Adopting the forms in a project

To make a project's own hand-filed tickets follow this same frame shape:

1. Copy `templates/ISSUE_TEMPLATE/*.yml` from this plugin into the project's
   own `.github/ISSUE_TEMPLATE/` directory.
2. Set `tickets.templates: enforce` in the project's `~/.seretos/projects.yml`
   entry — inert until `agent-project-issues#307` ships the enforcement
   check on the MCP side, but safe to set now.
3. What changes, per audience: a **human** filing through the GitHub web UI
   is presented with the form's fields instead of a blank body; an **agent**
   calling `create_ticket` directly has the ticket **refused** when a
   required section (per the heading vocabulary `agents/clarifier.md`
   documents) is missing.

Ensure the `bug` and `epic` labels exist in the target project before adopting `bug.yml`/`epic.yml` — see the agent-project-issues skill, "Labels: create the catalog entry first".

## Install

```
/plugin marketplace add seretos-agents/modular-software-factory
/plugin install agent-ticket-orchestrator@modular-software-factory
```

Install it **per project** — enable it in the project's own `.claude/settings.json` (or `settings.local.json`) together with the plugins it drives, then run the skills from that project's main checkout:

```json
"enabledPlugins": {
  "agent-ticket-orchestrator@modular-software-factory": true,
  "agent-autonomous-developer@modular-software-factory": true,
  "agent-project-issues@modular-software-factory": true,
  "agent-worktree@modular-software-factory": true
}
```

`agent-autonomous-prompt-engineer` is **optional** — add `"agent-autonomous-prompt-engineer@modular-software-factory": true` to the project's committed `.claude/settings.json` (not `settings.local.json`: package sessions run in a worktree, which does not contain untracked files) only in projects that ship model-executed prose (skills, agents, prompts). Without it, the gatekeeper moves a ticket that changes such files to Question and asks whether to install the plugin or run the ticket through the developer anyway.

The project must be registered in `~/.seretos/projects.yml` with its `path` (`owner/repo`) matching the repo's `origin` — that is how the skills find their `project_id` — and with `board.columns` listing `Backlog, Planned, Todo, Doing, Question` (`Backlog` first; a board binding is optional, see "Board model") and with `pulls.merge: true`. Fresh sessions may need `/reload-plugins` before the MCP tools are visible.

## Verifying on a bound and an unbound project

A live check that both backends go through the pipeline the same way. It needs two projects you can file throwaway tickets in, and is run by hand — no CI job does it.

1. Register two projects in `~/.seretos/projects.yml`: a GitHub project **with** `board.binding` (a Projects v2 board carrying the five columns) and a GitLab project **without** a binding, `board.columns: [Backlog, Planned, Todo, Doing, Question]`. Give both the permissions listed in "Board model" and commit the plugin set in each project's `.claude/settings.json`.
2. In each project, file one small ticket with no `status:*` label and no board column set (it is in Backlog).
3. From each project's main checkout, run `/agent-ticket-orchestrator:gatekeeper`. Confirm the ticket reached Planned — the card on the bound board, the `status:planned` label on the GitLab ticket (or the label your `label_map` names).
4. Move it to Todo by hand: drag the card on the bound board; on GitLab, replace its status label with the Todo one.
5. Run `/agent-ticket-orchestrator:run` in each project. While the package session runs, confirm the ticket is in Doing; when it ends, confirm the PR/MR merged and the ticket is **closed**, and that the report says `Done`.
6. Compare the two sessions' tool calls to `list_tickets`, `update_ticket`, `list_board_columns` and `get_ticket`: they must be the same calls with the same logical column names. The only difference between the two runs is the `projects.yml` entry.

## Layout

- `skills/gatekeeper/SKILL.md`, `skills/run/SKILL.md`, `skills/ticket/SKILL.md` — the three entry points (all `disable-model-invocation: true`; invoke them explicitly). `ticket` is the one attended skill, filing a single ticket interactively.
- `agents/bundler.md`, `agents/clarifier.md` — read-only Opus subagents used by `gatekeeper`. The `bundler` sizes every ticket, caps a `collision` package at one `size: large` ticket, and reports an overlapping large pair that cap rejects as `oversized`, with a proposed vertical split the `gatekeeper` puts to the owner as a question — it never cuts a ticket. The `clarifier` also interrogates the ticket's problem frame, writes a missing acceptance criterion, records unverified premises, detects regression chains, and reports an acceptance clause the package's own PR run cannot prove (`unprovable_here`) — a real external run, another OS, a person's check — which the `gatekeeper` records in the frame comment as not proven by this package; the clarifier writes the buildable residue as the acceptance criterion, and no ticket is ever created for such a clause.
- `agents/triage.md` — read-only Opus subagent used by `run` to try to answer a `blocked` event before it costs a retry.
- `scripts/gatekeeper/relation-readback.py` — the deterministic stdin-JSON → stdout-verdict helper `gatekeeper`'s Step 3.5 pipes its written relations through, so a relation write is verified mechanically instead of by LLM prose re-checking its own bookkeeping.
- `scripts/gatekeeper/classify-lane.py` — the path → lane table: stdin-JSON footprint paths in, `lane: code|prose|mixed` out. The only place that decides which lower plugin a package runs in.
- `scripts/gatekeeper/state-repair.py` — stdin-JSON → stdout-verdict repair for a ticket carrying two or more `status:*` labels: the state furthest along the configured column order is kept. `gatekeeper` Step 0 pipes such tickets through it.
- `scripts/gatekeeper/prose-lane-available.py` — whether the optional `agent-autonomous-prompt-engineer` is enabled for a project, read from the settings files a package session will see.
- `scripts/start-package-session.sh` — starts one package session (`--lane prose` selects the prompt engineer's entry; the default is the developer's), or with `--gatekeeper-split` the one-ticket `gatekeeper` split session, and owns the launch lock, stream files and exit marker.
- `templates/ISSUE_TEMPLATE/*.yml` — GitHub issue forms carrying the same heading vocabulary the `clarifier` and `ticket` skill use, for tickets filed by hand through the web UI.
- `AGENTS.md` — the plugin's copy of the contract with the two lower plugins (entry points, event table, reactions) and the design decisions behind it.

## Release

Manual: Actions → `release` → `version=X.Y.Z`. The workflow stamps the version into both manifests, pushes an orphan `release` branch with the install-ready tree (`skills/`, `agents/`, manifests, `assets/`, `description.md`), tags `agent-ticket-orchestrator--vX.Y.Z`, publishes a GitHub Release and dispatches to `seretos-agents/modular-software-factory` via the `MARKETPLACE_DISPATCH_TOKEN` secret.
