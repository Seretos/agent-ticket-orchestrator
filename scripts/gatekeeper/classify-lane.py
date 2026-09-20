#!/usr/bin/env python3
"""
Deterministic lane verdict for the gatekeeper's Step 2 (#38).

Two lower plugins exist: `agent-autonomous-developer` (code, test-first) and
`agent-autonomous-prompt-engineer` (prose a model executes, evidenced by blind
tests and step replays). A package runs in exactly one of them -- its *lane*.
The lane is decided here, from paths, not by a model's opinion: a package
whose change is a skill/agent/prompt file deadlocks the developer's critic
gates (`agent-autonomous-developer#122`, `#123`, this repo's `#35`), and a
model asked "is this prose?" answers differently on different nights.

The path -> lane table lives in this file and nowhere else (PROSE_RULES
below). Keep it small and review it as data.

Usage: no arguments. Reads one JSON list from stdin. Each entry is either a
repo-relative path string or an object with an optional role hint:

  ["scripts/check.py",
   {"path": "skills/run/SKILL.md"},
   {"path": "README.md", "role": "accompanying"}]

`role` is `deliverable` (default) or `accompanying`. An accompanying path is
one the ticket does not exist for -- the README line, the AGENTS.md note, the
docstring that follows a change. It is classified and listed like any other
path, but it does not vote: the verdict is taken over the deliverable paths
alone. Without that, every code ticket that also touches AGENTS.md would be
`mixed` and get split. When no path is a deliverable, every path votes and
`deliverables: none` is printed, so the caller can tell a split it can make
(`mixed` over deliverables) from one nobody can argue from the paths.

stdout:
  lane: code | prose | mixed
  deliverables: <n> | none
  path: <lane> <role> <path>      -- one line per input path, input order

exit code: 0 for a decided verdict (all three lanes are decided verdicts),
2 for unusable input (not JSON, not a list, an empty list, an entry without a
usable path, an unknown role) -- the reason is named on stdout.
"""

import fnmatch
import json
import sys

VALID_ROLES = {"deliverable", "accompanying"}

# Prose = a file a model executes. Everything else is code, including
# scripts, tests, workflow YAML, manifests, and docs that merely accompany
# code (README, docstrings). Patterns are matched against the normalised
# path AND against every suffix of it that starts at a directory boundary, so
# `plugins/x/skills/run/SKILL.md` matches `skills/*/SKILL.md` the same way a
# repo-root path does.
PROSE_RULES = (
    # skills: the entry file and the reference files it tells the model to read
    "skills/*.md",
    # subagent definitions and slash commands
    "agents/*.md",
    "commands/*.md",
    # project instructions every session loads
    "AGENTS.md",
    "CLAUDE.md",
    # prompt / system-prompt text files
    "prompts/*.md",
    "prompts/*.txt",
    "*.prompt",
    "*.prompt.md",
    "*.prompt.txt",
    "*system-prompt*.md",
    "*system-prompt*.txt",
    "*system_prompt*.md",
    "*system_prompt*.txt",
    # critic constraint prompts (agent-autonomous-developer's scripts/critic/)
    "*-constraints.md",
    "*-constraints.txt",
)


def normalise(path: str) -> str:
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def lane_of(path: str) -> str:
    segments = path.split("/")
    suffixes = ["/".join(segments[i:]) for i in range(len(segments))]
    for rule in PROSE_RULES:
        # fnmatch's `*` crosses `/`, which is what `skills/*.md` needs to
        # reach `skills/run/refs/x.md`.
        if any(fnmatch.fnmatchcase(s, rule) for s in suffixes):
            return "prose"
    return "code"


def unusable(reason: str) -> int:
    print(f"unusable input: {reason}")
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        return unusable(f"not JSON ({exc})")
    if not isinstance(payload, list):
        return unusable("expected a JSON list of paths")
    if not payload:
        return unusable("empty path list")

    entries = []
    for item in payload:
        if isinstance(item, str):
            path, role = item, "deliverable"
        elif isinstance(item, dict):
            path, role = item.get("path"), item.get("role") or "deliverable"
        else:
            return unusable(f"entry is neither a string nor an object: {item!r}")
        if not isinstance(path, str) or not normalise(path):
            return unusable(f"entry without a path: {item!r}")
        if role not in VALID_ROLES:
            return unusable(
                f"unknown role {role!r} (valid: {' '.join(sorted(VALID_ROLES))})"
            )
        path = normalise(path)
        entries.append((path, role, lane_of(path)))

    deliverables = [e for e in entries if e[1] == "deliverable"]
    voters = deliverables or entries
    lanes = {lane for _, _, lane in voters}
    verdict = lanes.pop() if len(lanes) == 1 else "mixed"

    print(f"lane: {verdict}")
    print(f"deliverables: {len(deliverables) or 'none'}")
    for path, role, lane in entries:
        print(f"path: {lane} {role} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
