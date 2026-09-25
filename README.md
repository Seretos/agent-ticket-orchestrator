# agent-ticket-orchestrator

A Claude Code **skill + agents** plugin. Board-driven ecosystem orchestrator: bundles and clarifies Backlog tickets into released work packages (`gatekeeper`), then runs every Todo package unattended through `agent-autonomous-developer` to a merged, CI-green PR, moving board columns as the only status signal (`run`).

This plugin ships **only skill and agent content** — no binaries, no MCP server. It is the upper layer over `agent-autonomous-developer` (one package → one green PR) and drives the `agent-project-issues` and `agent-worktree` MCPs.

## What it does

```
Backlog ──gatekeeper──▶ Planned ──human──▶ Todo ──run──▶ Doing ──▶ Done
                                                            └──────────────▶ Question ──human──▶ Todo / Backlog
```

- **`/agent-ticket-orchestrator:gatekeeper`** — a human starts the session, but it does not block on one being at the keyboard while it runs. Reads the open Backlog — skipping every ticket that carries the `gatekeeper-ignore` label — lets the `bundler` propose work packages (epics for tickets that collide in code, or effort batches of tiny tickets; everything else single) and applies the proposal directly — no confirmation round. A ticket that itself sequences its overlap behind another ("a second step after #X") is read as `depends_on`, never a `collision`; a `collision` package is capped at one `size: large` ticket, and two overlapping large tickets the cap rejects are not cut by the gatekeeper — it posts one question with a proposed split into user-observable slices and moves both cards to Question; a candidate returning from Question is bundled with its own `previous_cut` and a changed verdict must be named to be accepted. Every ticket's **lane** — `code` or `prose` (files a model executes: skills, agents, prompts) — is derived from the bundler's footprint paths by `scripts/gatekeeper/classify-lane.py`, never by a model: when the project has the optional `agent-autonomous-prompt-engineer` enabled, a prose package is labelled `lane:prose`, a bundle never spans lanes, and a ticket whose change is both is split into a code ticket and a prose ticket that is `blocked_by` it; when the project does not have it, such a ticket goes to Question instead. Then lets the `clarifier` answer the ticket's problem frame (which user-visible symptom, whether the acceptance criterion actually measures it, prior attempts on the same symptom) before hunting every decision the night shift could not make on its own; a defect whose AC only measures an internal quantity gets a symptom-level AC written for it (posted as a `## Frame (gatekeeper)` comment), an acceptance clause the package's own PR run cannot prove is struck and recorded in that same comment rather than turned into a ticket or a blocker, and a ticket that is the latest in a closed-ticket regression chain gets a `regression-chain` label and a comment stating how it is reframed as a root-cause task — both applied and reported, not asked. What is still asked has to survive a five-test filter (not the ticket's own literal reading, no added scope, not a reframe, a wrong answer must cost a user something durable, answerable without the code open) and is phrased for someone who has not opened the ticket. A question it cannot answer itself is posted **as a comment on the package ticket**, the package moves to the **Question** column (one place for everything that needs a human, across every project), and `gatekeeper` moves straight to the next package instead of waiting; the next pass picks answered cards up from Question and moves them on to Planned. An inter-package dependency (this package needs a capability another ticket introduces) becomes a `blocked_by` relation on the package ticket, and Step 3.5 verifies the write mechanically — via `scripts/gatekeeper/relation-readback.py`, not prose — before the package moves on; the package **still** moves to **Planned** once otherwise clear, unless that read-back finds an unexplained gap; a package moved to Planned also gets a short `## Released (gatekeeper)` comment recording what was checked and the move. `AskUserQuestion` is forbidden here and in `run`, both of which must complete a whole pass unattended; the `ticket` skill below is the one place in this plugin that uses it.
- **`/agent-ticket-orchestrator:ticket`** — files one ticket, interactively, in the same frame shape the `clarifier` expects: a user-visible symptom, an acceptance criterion that measures it with a real call, prior attempts on the same symptom. Asks three questions with `AskUserQuestion` (the one skill in this plugin that does), then makes exactly one `create_ticket` call.
- **You** answer any open questions directly on their tickets, and move the packages you want processed from Planned to **Todo**. Nothing automated ever does either.
- **`/agent-ticket-orchestrator:run`** (from the project's main checkout; `project_id=<id>` overrides the repo-derived id) — unattended, may run all night. First finishes any `ci-green` package an earlier run left unmerged. Then orders every Todo package by its `blocked_by` relations — a blocker also in Todo is processed first; a package whose blocker is still open elsewhere is left untouched in Todo and reported as skipped; a dependency cycle is reported and processed in board order rather than aborting the night. For each package in that order, sequentially: verify the previous package actually cleared, → Doing, create a worktree on `pkg/<id>-<slug>`, start the package's lower plugin — `agent-autonomous-developer`'s `process-developer`, or `agent-autonomous-prompt-engineer`'s `process-prompt-engineer` for a package labelled `lane:prose` — as a separate `claude -p` process in that worktree (from the skill's own turn, backgrounded) and wait for it to end. The lower plugin writes `adev:event` comments on the ticket and opens the PR; `run` reads the latest event: `ci-green` → merge, → Done — and if the merge fails on a **conflict**, one rebase-and-retry round before it, too, escalates; branch protection / a missing permission / an unresolved mergeability state → comment on the ticket and Question, human decides; `blocked` → triaged by a read-only subagent first (answerable → answered and re-dispatched immediately, not answerable → Question right away, no wasted retry); `failed`/no terminal event → checked directly against the PR's actual CI state first (a package that only died mid-CI-wait is not `failed`), then one fresh attempt if that does not resolve it, then → Question with the failure summary. Final report per package; SUCCESS only if everything reached Done.

Comments are the log, columns are the signal. An empty Question column means no open questions.

## Board model

The project's `~/.seretos/projects.yml` must bind a board with the logical columns `Backlog`, `Planned`, `Todo`, `Doing`, `Done`, `Question` (native names are resolved live via `list_board_columns`; e.g. the native column may be called "Frage offen"). Required permissions: `issues.create/modify`, `pulls.create/modify/merge` (`run` requires `merge` and STOPs before touching anything without it; use `/agent-autonomous-developer:process-developer` for single tickets instead), and `board.manage` once for creating missing columns with `ensure_board_column`.

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

Ensure the `bug` and `epic` labels exist in the target project before adopting
`bug.yml`/`epic.yml` (create them via the project-issues MCP's `create_label`
or the GitHub UI) — the form's default label is not created automatically,
and GitHub 404s on an unknown label at ticket-creation time.

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

The project must be registered in `~/.seretos/projects.yml` with its `path` (`owner/repo`) matching the repo's `origin` — that is how the skills find their `project_id` — and with `board.columns` listing `Backlog, Planned, Todo, Doing, Done, Question` and `pulls.merge: true`. Fresh sessions may need `/reload-plugins` before the MCP tools are visible.

## Layout

- `skills/gatekeeper/SKILL.md`, `skills/run/SKILL.md`, `skills/ticket/SKILL.md` — the three entry points (all `disable-model-invocation: true`; invoke them explicitly). `ticket` is the one attended skill, filing a single ticket interactively.
- `agents/bundler.md`, `agents/clarifier.md` — read-only Opus subagents used by `gatekeeper`. The `bundler` sizes every ticket, caps a `collision` package at one `size: large` ticket, and reports an overlapping large pair that cap rejects as `oversized`, with a proposed vertical split the `gatekeeper` puts to the owner as a question — it never cuts a ticket. The `clarifier` also interrogates the ticket's problem frame, writes a missing acceptance criterion, records unverified premises, detects regression chains, and reports an acceptance clause the package's own PR run cannot prove (`unprovable_here`) — a real external run, another OS, a person's check — which the `gatekeeper` records in the frame comment as not proven by this package; the clarifier writes the buildable residue as the acceptance criterion, and no ticket is ever created for such a clause.
- `agents/triage.md` — read-only Opus subagent used by `run` to try to answer a `blocked` event before it costs a retry.
- `scripts/gatekeeper/relation-readback.py` — the deterministic stdin-JSON → stdout-verdict helper `gatekeeper`'s Step 3.5 pipes its written relations through, so a relation write is verified mechanically instead of by LLM prose re-checking its own bookkeeping.
- `scripts/gatekeeper/classify-lane.py` — the path → lane table: stdin-JSON footprint paths in, `lane: code|prose|mixed` out. The only place that decides which lower plugin a package runs in.
- `scripts/gatekeeper/prose-lane-available.py` — whether the optional `agent-autonomous-prompt-engineer` is enabled for a project, read from the settings files a package session will see.
- `scripts/start-package-session.sh` — starts one package session (`--lane prose` selects the prompt engineer's entry; the default is the developer's) and owns the launch lock, stream files and exit marker.
- `templates/ISSUE_TEMPLATE/*.yml` — GitHub issue forms carrying the same heading vocabulary the `clarifier` and `ticket` skill use, for tickets filed by hand through the web UI.
- `AGENTS.md` — the plugin's copy of the contract with the two lower plugins (entry points, event table, reactions) and the design decisions behind it.

## Release

Manual: Actions → `release` → `version=X.Y.Z`. The workflow stamps the version into both manifests, pushes an orphan `release` branch with the install-ready tree (`skills/`, `agents/`, manifests, `assets/`, `description.md`), tags `agent-ticket-orchestrator--vX.Y.Z`, publishes a GitHub Release and dispatches to `seretos-agents/modular-software-factory` via the `MARKETPLACE_DISPATCH_TOKEN` secret.
