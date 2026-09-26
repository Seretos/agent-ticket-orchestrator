#!/usr/bin/env python3
"""
Deterministic renderer for the gatekeeper's machine-readable comment blocks
(#77): a pure stdin-JSON -> stdout script that turns parsed `clarifier:frame`
fields into the `gatekeeper:frame v1` block (always) and the
`gatekeeper:chain v1` block (regression chain only). ecosystem-statistics#13
scrapes these counts, so they must come from a tested program, not a model's
arithmetic on every gatekeeper pass.

Usage: no arguments. Reads one JSON object from stdin:

  {
    "project": "owner/repo",
    "ac": "<raw>",
    "premise": ["<raw>", ...],
    "unprovable_here": ["<raw>", ...],
    "chain": "<raw>"
  }

`premise`/`unprovable_here` are lists (the clarifier's repeatable frame
keys); a missing key defaults to `[]`. `chain` is the frame value verbatim
(`none`, or `regression-chain:#90,#121`, per `agents/clarifier.md`
l.282-289); missing, `""` or `none` means no chain. A leading
`regression-chain:` prefix is stripped, members are split on `,`, each
stripped of surrounding whitespace.

Rules:
  - `ac_rewritten: no` iff `ac.strip() == "as-filed"`, else `yes`.
  - `premises` / `not_proven` = count of entries in `premise` /
    `unprovable_here` (respectively) whose stripped value is neither `""`
    nor `none`.
  - A chain member `#N` is qualified with `project` -> `<project>#N`; a
    member already shaped `owner/repo#N` is kept verbatim; order is
    preserved.

Output: exact text, LF only, fixed key order, blocks separated by one blank
line, one trailing `\n` after the last `-->`. The chain block is omitted
entirely when there is no chain.

Invalid input -> exit 1, `error: <what>` on stderr, empty stdout (the
`scripts/run/ato-event.py` convention). Rejected shapes:
  - stdin is not a JSON object.
  - `ac` is missing or not a string.
  - `premise` / `unprovable_here` is present but not a list of strings.
  - a chain member matches neither `^#\\d+$` nor `^[\\w.-]+/[\\w.-]+#\\d+$`.
  - a bare `#N` member with `project` missing or not `owner/repo`-shaped.
Exit 0 otherwise.
"""

import json
import re
import sys

BARE_MEMBER_RE = re.compile(r"^#\d+$")
QUALIFIED_MEMBER_RE = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")
PROJECT_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


class InvalidInput(Exception):
    pass


def _require_string_list(payload, key):
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidInput(f"{key} must be a list of strings")
    return value


def _count_non_empty(values):
    return sum(1 for v in values if v.strip() not in ("", "none"))


def _parse_chain_members(chain_raw, project):
    chain = chain_raw.strip()
    if chain.startswith("regression-chain:"):
        chain = chain[len("regression-chain:"):]
    raw_members = [m.strip() for m in chain.split(",")]

    members = []
    for member in raw_members:
        if QUALIFIED_MEMBER_RE.match(member):
            members.append(member)
        elif BARE_MEMBER_RE.match(member):
            if not project or not PROJECT_RE.match(project):
                raise InvalidInput(
                    f"chain member {member!r} needs project (owner/repo missing or malformed)"
                )
            members.append(f"{project}{member}")
        else:
            raise InvalidInput(f"chain member {member!r} is not a valid #N or owner/repo#N id")
    return members


def render(payload):
    if not isinstance(payload, dict):
        raise InvalidInput("stdin is not a JSON object")

    ac = payload.get("ac")
    if not isinstance(ac, str):
        raise InvalidInput("ac is missing or not a string")

    premise = _require_string_list(payload, "premise")
    unprovable_here = _require_string_list(payload, "unprovable_here")

    project = payload.get("project")
    chain_raw = payload.get("chain")

    ac_rewritten = "no" if ac.strip() == "as-filed" else "yes"
    premises = _count_non_empty(premise)
    not_proven = _count_non_empty(unprovable_here)

    frame_block = (
        "<!-- gatekeeper:frame v1\n"
        f"ac_rewritten: {ac_rewritten}\n"
        f"premises: {premises}\n"
        f"not_proven: {not_proven}\n"
        "-->\n"
    )

    has_chain = isinstance(chain_raw, str) and chain_raw.strip() not in ("", "none")
    if not has_chain:
        return frame_block

    members = _parse_chain_members(chain_raw, project)
    chain_block = (
        "<!-- gatekeeper:chain v1\n"
        f"members: {','.join(members)}\n"
        "-->\n"
    )
    return frame_block + "\n" + chain_block


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"error: stdin is not valid JSON ({exc})", file=sys.stderr)
        return 1

    try:
        output = render(payload)
    except InvalidInput as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
