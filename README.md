# agent-ticket-orchestrator

A Claude Code **skill** plugin. Board-driven ecosystem orchestrator: bundles and clarifies Backlog tickets into released work packages (gatekeeper), then runs every Todo package unattended through agent-autonomous-developer to a merged, CI-green PR, moving board columns as the only status signal (run).

This plugin ships **only the skill content** — no binaries, no MCP server.

## Install

```
/plugin marketplace add Seretos/agent-marketplace
/plugin install agent-ticket-orchestrator@agent-marketplace
```

If the skill teaches Claude how to use a specific MCP, declare that MCP as a dependency in `.claude-plugin/plugin.json` (`dependencies` array). Claude Code will install/load it automatically.

## What the skill teaches

See `skills/run/SKILL.md` for the full content.
