# agent-ticket-orchestrator

A Claude Code **skill + agents** plugin. Board-driven ecosystem orchestrator: bundles and clarifies Backlog tickets into released work packages (`gatekeeper`), then runs every Todo package unattended through `agent-autonomous-developer` to a merged, CI-green PR, moving board columns as the only status signal (`run`).

This plugin ships **only skill and agent content** — no binaries, no MCP server. It is the upper layer over `agent-autonomous-developer` (one package → one green PR) and drives the `agent-project-issues` and `agent-worktree` MCPs.

## What it does

```
Backlog ──gatekeeper──▶ Planned ──human──▶ Todo ──run──▶ Doing ──▶ Review ──▶ Done
                                                            └──────────────▶ Question ──human──▶ Todo / Backlog
```

- **`/agent-ticket-orchestrator:gatekeeper project_id=<id>`** — supervised, you at the keyboard. Reads the open Backlog, lets the `bundler` propose work packages (epics for tickets that collide in code, or effort batches of tiny tickets; everything else single), creates the epics after you accept, then lets the `clarifier` hunt every decision the night shift could not make on its own and asks you — answers land as a ticket comment. Clear packages move to **Planned**. It is the only place in the ecosystem that asks a human anything.
- **You** move the packages you want processed from Planned to **Todo**. Nothing automated ever does that.
- **`/agent-ticket-orchestrator:run project_id=<id>`** (or `project_id=all`) — unattended, may run all night. For each Todo package, sequentially: → Doing, create a worktree on `pkg/<id>-<slug>`, dispatch `package-session`, which runs `agent-autonomous-developer`'s `process-ticket` as a separate `claude -p` process in that worktree and waits for it to end. The lower plugin writes `adev:event` comments on the ticket and moves it to Review; `run` reads the latest event: `ci-green` → merge, → Done; `blocked` → one more attempt at the end of the run, then → Question; `failed` → one fresh attempt, then → Question with the failure summary. Final report per package; SUCCESS only if everything reached Done.

Comments are the log, columns are the signal. An empty Question column means no open questions.

## Board model

The project's `~/.seretos/projects.yml` must bind a board with the logical columns `Backlog`, `Planned`, `Todo`, `Doing`, `Review`, `Done`, `Question` (native names are resolved live via `list_board_columns`; e.g. the native column may be called "Frage offen"). Required permissions: `issues.create/modify`, `pulls.create/modify/merge` (without `merge`, `run` leaves green packages in Review with a note), and `board.manage` once for creating missing columns with `ensure_board_column`.

## Install

```
/plugin marketplace add Seretos/agent-marketplace
/plugin install agent-ticket-orchestrator@agent-marketplace
```

Declared dependencies (installed automatically): `agent-autonomous-developer`, `agent-project-issues`, `agent-worktree`. Fresh sessions may need `/reload-plugins` before the MCP tools are visible.

## Layout

- `skills/gatekeeper/SKILL.md`, `skills/run/SKILL.md` — the two entry points (both `disable-model-invocation: true`; invoke them explicitly).
- `agents/bundler.md`, `agents/clarifier.md` — read-only Opus subagents used by `gatekeeper`.
- `agents/package-session.md` — the thin Sonnet wrapper that owns one `claude -p` process per attempt for `run`.
- `AGENTS.md` — the plugin's copy of the contract with `agent-autonomous-developer` (entry point, event table, reactions) and the design decisions behind it.

## Release

Manual: Actions → `release` → `version=X.Y.Z`. The workflow stamps the version into both manifests, pushes an orphan `release` branch with the install-ready tree (`skills/`, `agents/`, manifests, `assets/`, `description.md`), tags `agent-ticket-orchestrator--vX.Y.Z`, publishes a GitHub Release and dispatches to `Seretos/agent-marketplace` via the `MARKETPLACE_DISPATCH_TOKEN` secret.
