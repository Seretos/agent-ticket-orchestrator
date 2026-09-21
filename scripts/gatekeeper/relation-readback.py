#!/usr/bin/env python3
"""
Deterministic read-back verdict for the gatekeeper's Step 3.5 (#25).

Step 3.5's write loop is LLM bookkeeping, and an incident on this project's
own tracker showed that bookkeeping losing four of the five relations it
meant to write, with nothing noticing. Prose telling the same LLM to "check
its own work" is the same mechanism again. This script is the alternative: a
pure stdin-JSON -> stdout-verdict process, outside the LLM, that diffs what
was actually written against what was expected and enforces the reason
vocabulary mechanically.

Usage: no arguments. Reads one JSON object from stdin:

  {
    "expected": ["#5", "#9", ...],
    "relations": [{"kind": "blocked_by"|"relates_to", "target": "#5"}, ...],
    "reasons": {"#9": "not found"|"closed"|"self-edge", ...}
  }

`relations` is the package's relations from a fresh
`get_ticket(..., include_relations=True)` (Step 3.5). Only a relation whose
`kind` is `blocked_by` or `relates_to` counts toward satisfying an expected
target -- any other kind, even pointed at the same target, is not the
dependency edge Step 3.5 wrote and must not silently close the gap (a plan
review flagged this: GitLab's `relates_to` is this project's real, portable
stand-in for `blocked_by`, not a licence to accept an arbitrary relation
kind).

`reasons` maps a target id to exactly one of the three words Step 3.5
already records when it chose not to write a relation: `not found`,
`closed`, `self-edge`. A target that is both missing from `relations` and
absent from `reasons` (or present under a fourth, unrecognised word) is what
this script calls a gap.

stdout:
  verdict: ok            -- every expected target is either a written
                             relation (blocked_by/relates_to) or covered by
                             a valid reason.
  verdict: gap
  gap targets: <ids>      -- space-separated ids that are neither written
                             nor covered by a valid reason. Only the actual
                             gaps are listed here -- never the whole
                             `expected` set -- so a caller (or a test) can
                             tell "everything is a gap" apart from "nothing
                             is".

exit code: 0 on ok, 2 on gap, 1 when `reasons` carries a value outside the
three-word vocabulary (the rejected value is named in stdout).
"""

import json
import sys

VALID_REASONS = {"not found", "closed", "self-edge"}
SATISFYING_KINDS = {"blocked_by", "relates_to"}


def main() -> int:
    payload = json.load(sys.stdin)
    expected = payload.get("expected", [])
    relations = payload.get("relations", [])
    reasons = payload.get("reasons", {})

    bad_reasons = sorted(set(reasons.values()) - VALID_REASONS)
    if bad_reasons:
        print(f"invalid reason value(s): {' '.join(bad_reasons)}")
        print(f"valid reasons are: {' '.join(sorted(VALID_REASONS))}")
        return 1

    written_targets = {
        r.get("target") for r in relations if r.get("kind") in SATISFYING_KINDS
    }

    gaps = [
        target for target in expected
        if target not in written_targets and target not in reasons
    ]

    if gaps:
        print("verdict: gap")
        print(f"gap targets: {' '.join(gaps)}")
        return 2

    print("verdict: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
