#!/usr/bin/env python3
"""
Deterministic repair verdict for two independent sources of board/ticket
drift (#56 R9), same stdin-JSON -> stdout-verdict convention as
`scripts/gatekeeper/relation-readback.py`.

1. **Duplicate `status:*` labels** on one ticket -- a label-mode board write
   that adds the new status label without removing the old one. The label
   whose suffix matches the *furthest-along* entry in `columns` wins; the
   rest are named for removal.
2. **A reopened ticket that has since actually finished** -- `state: open`
   with `reopened_at` set, where the latest recorded event is `ci-green` for
   the same PR that later merged at/after the reopen -- should close again,
   as opposed to a stale/unrelated merge that must NOT close it.

Both rules exist for the same reason `relation-readback.py` does: this is
LLM bookkeeping (a board write, an event read) that a plain prose "check
your own work" instruction fails to catch reliably, so it is pulled out into
a pure, testable process instead.

Usage: no arguments. Reads one JSON object from stdin:

  {
    "columns": [str], "labels": [str], "state": "open"|"closed",
    "reopened_at": ISO|null,
    "latest_event": {"event": str, "at": ISO, "pr": int|null}|null,
    "pr": {"number": int, "merged": bool, "merged_at": ISO|null}|null
  }

Label-duplicate rule: a label counts as a status label when its prefix
(before the first `:`) matches `status` case-insensitively; its suffix is
matched case-insensitively against `columns`. An unmatched suffix is invalid
input (exit 1, `error: unknown status label <label>` -- ranking an unranked
label would be a guess). With two or more status labels present, the one
whose suffix matches the column with the greatest index in `columns` is kept
(verbatim, original casing); every other status label is named for removal,
one `remove: <label>` line per label, in the order the labels appeared in
the input.

Reopen-close rule: `close: yes` fires iff ALL of: `state == "open"`;
`reopened_at` is set; `latest_event.event == "ci-green"`; `latest_event.at`
is strictly after `reopened_at`; `latest_event.pr` is non-null and equals
`pr.number` (the event's own `pr:` field is what ties it to the merge -- an
unrelated PR's later `ci-green` must not close a different reopened
ticket); `pr.merged` is true; and `pr.merged_at` is at or after
`reopened_at`. A trailing `Z` is normalised to `+00:00` before
`datetime.fromisoformat`; comparisons are real timezone-aware instant
comparisons, not string comparisons. An unparseable timestamp is invalid
input (exit 1, `error: unparseable timestamp <value>`).

stdout: `verdict: ok` or `verdict: repair` on the first line; then, only
when its rule actually fired, `keep: <label>`, one `remove: <label>` line
per removed label (input order), and `close: yes` -- in that order.
exit: 0 on ok, 2 on repair, 1 on invalid input (unknown status suffix or
unparseable timestamp).
"""

import json
import sys
from datetime import datetime


class UnknownStatusLabel(Exception):
    def __init__(self, label):
        super().__init__(label)
        self.label = label


class UnparseableTimestamp(Exception):
    def __init__(self, value):
        super().__init__(str(value))
        self.value = value


def parse_dt(value):
    if isinstance(value, str) and value.endswith("Z"):
        value_for_parse = value[:-1] + "+00:00"
    else:
        value_for_parse = value
    try:
        return datetime.fromisoformat(value_for_parse)
    except (ValueError, TypeError):
        raise UnparseableTimestamp(value)


def evaluate_labels(columns, labels):
    """Returns (keep_label_or_None, [remove_labels_in_input_order])."""
    status_labels = []  # (label, column_index), input order
    for label in labels:
        if ":" not in label:
            continue
        prefix, suffix = label.split(":", 1)
        if prefix.lower() != "status":
            continue
        index = None
        for i, column in enumerate(columns):
            if column.lower() == suffix.lower():
                index = i
                break
        if index is None:
            raise UnknownStatusLabel(label)
        status_labels.append((label, index))

    if len(status_labels) < 2:
        return None, []

    max_index = max(index for _, index in status_labels)
    keep_label = None
    for label, index in status_labels:
        if index == max_index and keep_label is None:
            keep_label = label
    remove_labels = [label for label, index in status_labels if label != keep_label]
    return keep_label, remove_labels


def evaluate_reopen(payload):
    """Returns True iff the reopen-close rule fires."""
    if payload.get("state") != "open":
        return False

    reopened_at_raw = payload.get("reopened_at")
    if not reopened_at_raw:
        return False
    reopened_at = parse_dt(reopened_at_raw)

    latest_event = payload.get("latest_event")
    if not latest_event or latest_event.get("event") != "ci-green":
        return False

    at_raw = latest_event.get("at")
    if not at_raw:
        return False
    at = parse_dt(at_raw)
    if not at > reopened_at:
        return False

    event_pr = latest_event.get("pr")
    if event_pr is None:
        return False

    pr = payload.get("pr")
    if not pr or pr.get("number") != event_pr:
        return False
    if not pr.get("merged"):
        return False

    merged_at_raw = pr.get("merged_at")
    if not merged_at_raw:
        return False
    merged_at = parse_dt(merged_at_raw)
    if not merged_at >= reopened_at:
        return False

    return True


def main() -> int:
    payload = json.load(sys.stdin)
    columns = payload.get("columns", [])
    labels = payload.get("labels", [])

    try:
        keep_label, remove_labels = evaluate_labels(columns, labels)
        should_close = evaluate_reopen(payload)
    except UnknownStatusLabel as e:
        print(f"error: unknown status label {e.label}")
        return 1
    except UnparseableTimestamp as e:
        print(f"error: unparseable timestamp {e.value}")
        return 1

    if not remove_labels and not should_close:
        print("verdict: ok")
        return 0

    print("verdict: repair")
    if keep_label:
        print(f"keep: {keep_label}")
    for label in remove_labels:
        print(f"remove: {label}")
    if should_close:
        print("close: yes")
    return 2


if __name__ == "__main__":
    sys.exit(main())
