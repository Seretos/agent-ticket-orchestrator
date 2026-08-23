# agent-ticket-orchestrator

Board-driven ecosystem orchestrator: bundles and clarifies Backlog tickets into released work packages (gatekeeper), then runs every Todo package unattended through agent-autonomous-developer to a merged, CI-green PR, moving board columns as the only status signal (run).

## Key features

- **Gatekeeper (supervised):** bundles open Backlog tickets into work packages — epics for tickets that collide in code or batches of tiny tickets — and asks you every real design question up front, so the night shift never has to.
- **Run (unattended):** processes every Todo package one after another in its own worktree and its own `claude -p` process, merges the CI-green PR, moves the card to Done, and escalates genuine decisions to a Question column instead of waiting on anyone.
- **Board as the single status channel:** Backlog → Planned → Todo → Doing → Review → Done plus Question; comments are the log, columns are the signal, and only a human ever moves a card into Todo.
- **Clean layering:** depends on `agent-autonomous-developer`, `agent-project-issues` and `agent-worktree`; never writes code itself and never carries project content in its context.
