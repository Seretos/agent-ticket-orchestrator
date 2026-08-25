# agent-ticket-orchestrator

A Claude Code **skill + agents** plugin. Board-driven ecosystem orchestrator: bundles and clarifies Backlog tickets into released work packages (`gatekeeper`), then runs every Todo package unattended through `agent-autonomous-developer` to a merged, CI-green PR, moving board columns as the only status signal (`run`).

This plugin ships **only skill and agent content** — no binaries, no MCP server. It is the upper layer over `agent-autonomous-developer` (one package → one green PR) and drives the `agent-project-issues` and `agent-worktree` MCPs.

## What it does

```
Backlog ──gatekeeper──▶ Planned ──human──▶ Todo ──run──▶ Doing ──▶ Review ──▶ Done
                                                            └──────────────▶ Question ──human──▶ Todo / Backlog
```

- **`/agent-ticket-orchestrator:gatekeeper`** — a human starts the session, but it does not block on one being at the keyboard while it runs. Reads the open Backlog, lets the `bundler` propose work packages (epics for tickets that collide in code, or effort batches of tiny tickets; everything else single) and applies the proposal directly — no confirmation round. Then lets the `clarifier` hunt every decision the night shift could not make on its own; a question it cannot answer itself is posted **as a comment on the package ticket**, the package stays in Backlog, and `gatekeeper` moves straight to the next package instead of waiting. Clear packages move to **Planned**. `AskUserQuestion` is not used anywhere in this plugin.
- **You** answer any open questions directly on their tickets, and move the packages you want processed from Planned to **Todo**. Nothing automated ever does either.
- **`/agent-ticket-orchestrator:run`** (from the project's main checkout; `project_id=<id>` overrides the repo-derived id) — unattended, may run all night. First finishes any `ci-green` package an earlier run left unmerged. Then, for each Todo package, sequentially: verify the previous package actually cleared, → Doing, create a worktree on `pkg/<id>-<slug>`, start `agent-autonomous-developer`'s `process-ticket` as a separate `claude -p` process in that worktree (from the skill's own turn, backgrounded) and wait for it to end. The lower plugin writes `adev:event` comments on the ticket and moves it to Review; `run` reads the latest event: `ci-green` → merge, → Done — and if the merge fails on a **conflict**, one rebase-and-retry round before it, too, escalates; branch protection / a missing permission / an unresolved mergeability state still → Review, human decides; `blocked` → triaged by a read-only subagent first (answerable → answered and re-dispatched immediately, not answerable → Question right away, no wasted retry); `failed`/no terminal event → checked directly against the PR's actual CI state first (a package that only died mid-CI-wait is not `failed`), then one fresh attempt if that does not resolve it, then → Question with the failure summary. Final report per package; SUCCESS only if everything reached Done.

Comments are the log, columns are the signal. An empty Question column means no open questions.

## Board model

The project's `~/.seretos/projects.yml` must bind a board with the logical columns `Backlog`, `Planned`, `Todo`, `Doing`, `Review`, `Done`, `Question` (native names are resolved live via `list_board_columns`; e.g. the native column may be called "Frage offen"). Required permissions: `issues.create/modify`, `pulls.create/modify/merge` (without `merge`, `run` leaves green packages in Review with a note), and `board.manage` once for creating missing columns with `ensure_board_column`.

## Install

```
/plugin marketplace add Seretos/agent-marketplace
/plugin install agent-ticket-orchestrator@agent-marketplace
```

Install it **per project** — enable it in the project's own `.claude/settings.json` (or `settings.local.json`) together with the plugins it drives, then run the skills from that project's main checkout:

```json
"enabledPlugins": {
  "agent-ticket-orchestrator@agent-marketplace": true,
  "agent-autonomous-developer@agent-marketplace": true,
  "agent-project-issues@agent-marketplace": true,
  "agent-worktree@agent-marketplace": true
}
```

The project must be registered in `~/.seretos/projects.yml` with its `path` (`owner/repo`) matching the repo's `origin` — that is how the skills find their `project_id` — and with `board.columns` listing `Backlog, Planned, Todo, Doing, Review, Done, Question` and `pulls.merge: true`. Fresh sessions may need `/reload-plugins` before the MCP tools are visible.

## Layout

- `skills/gatekeeper/SKILL.md`, `skills/run/SKILL.md` — the two entry points (both `disable-model-invocation: true`; invoke them explicitly).
- `agents/bundler.md`, `agents/clarifier.md` — read-only Opus subagents used by `gatekeeper`.
- `agents/triage.md` — read-only Opus subagent used by `run` to try to answer a `blocked` event before it costs a retry.
- `AGENTS.md` — the plugin's copy of the contract with `agent-autonomous-developer` (entry point, event table, reactions) and the design decisions behind it.

## Release

Manual: Actions → `release` → `version=X.Y.Z`. The workflow stamps the version into both manifests, pushes an orphan `release` branch with the install-ready tree (`skills/`, `agents/`, manifests, `assets/`, `description.md`), tags `agent-ticket-orchestrator--vX.Y.Z`, publishes a GitHub Release and dispatches to `Seretos/agent-marketplace` via the `MARKETPLACE_DISPATCH_TOKEN` secret.
