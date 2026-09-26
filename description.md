# agent-ticket-orchestrator

Board-driven ecosystem orchestrator: bundles and clarifies Backlog tickets into released work packages (gatekeeper), then runs every Todo package unattended through agent-autonomous-developer to a merged, CI-green PR and closes the ticket (run).

## Key features

- **Gatekeeper (supervised):** bundles open Backlog tickets into work packages — epics for tickets that collide in code or batches of tiny tickets — and asks you every real design question up front, so the night shift never has to.
- **Run (unattended):** processes every Todo package one after another in its own worktree and its own `claude -p` process, merges the CI-green PR, closes the ticket, and escalates genuine decisions to a Question column instead of waiting on anyone.
- **Works with or without a board:** state is tracked by board columns, or by `status:*` labels on a project without a board binding (GitLab included) — Backlog → Planned → Todo → Doing plus Question, and a finished ticket is simply closed, not moved to a Done column. Comments are the log, columns are the signal, and only a human ever moves a card into Todo.
- **Clean layering:** depends on `agent-autonomous-developer`, `agent-project-issues` and `agent-worktree`; never writes code itself and never carries project content in its context.
