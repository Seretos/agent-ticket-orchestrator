#!/usr/bin/env python3
"""
Is the prose lane installed for this project? (#38 follow-up)

`agent-autonomous-prompt-engineer` is deliberately NOT a dependency of this
plugin: the developer is needed in every project, the prompt engineer only
in projects that actually ship model-executed prose. So its presence is a
per-project fact somebody has to check before a package is routed to it --
and "somebody" is this script, not a model looking at its own skill list.

A package session is a fresh `claude -p` with cwd = a git worktree of the
project. The plugins it gets are the ones enabled in the settings files that
process will read:

  1. <home>/.claude/settings.json            (user)
  2. <local_path>/.claude/settings.json      (project, committed)

in that order, the later file overriding the earlier one per plugin key --
Claude Code's own precedence. `<local_path>/.claude/settings.local.json` is
deliberately NOT read: it is untracked, so a worktree cut from the branch
does not contain it, and a plugin enabled only there is not there for the
package session.

A plugin key is `<plugin>@<marketplace>`; any marketplace counts.

Usage: prose-lane-available.py <local_path>

stdout:
  prose_lane: available | unavailable
  source: <the settings file that decided, or "none">

exit code: 0 available, 2 unavailable, 1 unusable input (no argument, or
`local_path` is not a directory). A settings file that is missing or not
valid JSON is skipped, not an error.
"""

import json
import pathlib
import sys

PLUGIN = "agent-autonomous-prompt-engineer"


def enabled_in(path: pathlib.Path):
    """True / False when the file states it for PLUGIN, None when it is silent."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return None
    states = [bool(v) for k, v in plugins.items() if k.split("@", 1)[0] == PLUGIN]
    if not states:
        return None
    return any(states)


def main() -> int:
    if len(sys.argv) != 2:
        print("unusable input: usage: prose-lane-available.py <local_path>")
        return 1
    local_path = pathlib.Path(sys.argv[1])
    if not local_path.is_dir():
        print(f"unusable input: not a directory: {local_path}")
        return 1

    verdict, source = False, "none"
    for settings in (
        pathlib.Path.home() / ".claude" / "settings.json",
        local_path / ".claude" / "settings.json",
    ):
        state = enabled_in(settings)
        if state is not None:
            verdict, source = state, str(settings)

    print(f"prose_lane: {'available' if verdict else 'unavailable'}")
    print(f"source: {source}")
    return 0 if verdict else 2


if __name__ == "__main__":
    sys.exit(main())
