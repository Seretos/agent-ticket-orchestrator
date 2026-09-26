"""
Driving tests for `scripts/gatekeeper/state-repair.py` (#56 R9): a pure
stdin-JSON -> stdout-verdict helper, same convention as
`scripts/gatekeeper/relation-readback.py`, that resolves two independent
sources of board/ticket drift deterministically instead of trusting an LLM's
own bookkeeping to notice them:

1. **Duplicate `status:*` labels** on one ticket (a label-mode board write
   that adds the new status label without removing the old one) -- the
   label whose suffix matches the *furthest-along* entry in `columns` wins,
   the rest are named for removal.
2. **A reopened ticket that has since actually finished** -- `state: open`
   with `reopened_at` set, where the latest recorded event is `ci-green` for
   the same PR that later merged at/after the reopen -- should close again,
   as opposed to a stale/unrelated merge that must NOT close it.

Contract (settled here, since the script does not exist yet and this is
where its stdin/stdout shape is first pinned down for `implement` to build
against):

  stdin: {"columns": [str], "labels": [str], "state": "open"|"closed",
          "reopened_at": ISO|null,
          "latest_event": {"event": str, "at": ISO, "pr": int|null}|null,
          "pr": {"number": int, "merged": bool, "merged_at": ISO|null}|null}

  Label-duplicate rule: a label counts as a status label when its prefix
  (before the first `:`) matches `status` case-insensitively; its suffix is
  matched case-insensitively against `columns`. An unmatched suffix is
  invalid input (exit 1, `error: unknown status label <label>` -- ranking an
  unranked label would be a guess). With two or more status labels present,
  the one whose suffix matches the column with the greatest index in
  `columns` is kept (verbatim, original casing); every other status label is
  named for removal, one `remove: <label>` line per label, in the order the
  labels appeared in the input.

  Reopen-close rule: `close: yes` fires iff ALL of: `state == "open"`;
  `reopened_at` is set; `latest_event.event == "ci-green"`;
  `latest_event.at` is strictly after `reopened_at`; `latest_event.pr` is
  non-null and equals `pr.number` (the event's own `pr:` field is what ties
  it to the merge -- an unrelated PR's later `ci-green` must not close a
  different reopened ticket); `pr.merged` is true; and `pr.merged_at` is at
  or after `reopened_at`. A trailing `Z` is normalised to `+00:00` before
  `datetime.fromisoformat`; comparisons are real timezone-aware instant
  comparisons, not string comparisons (two ISO strings with different UTC
  offsets can order backwards as strings while ordering correctly as
  instants). An unparseable timestamp is invalid input (exit 1,
  `error: unparseable timestamp <value>`).

  stdout: `verdict: ok` or `verdict: repair` on the first line; then, only
  when its rule actually fired, `keep: <label>`, one `remove: <label>` line
  per removed label (input order), and `close: yes` -- in that order.
  exit: 0 on ok, 2 on repair, 1 on invalid input (unknown status suffix or
  unparseable timestamp).

Harness copied from tests/test_relation_readback.py's pattern (module-level
path constant, subprocess.run with sys.executable, real JSON in, real stdout
out).

The script does not exist yet at the time these tests are written
(phase=tests, RED only): every subprocess call below fails because Python
itself cannot open the missing file ("can't open file ... No such file or
directory") -- stdout is empty and the interpreter's complaint lands on
stderr, so every assertion on `result.stdout`/`result.stdout.splitlines()`
below is what actually fails, for the missing-behaviour reason, not a bare
exit-code coincidence (the same RED shape test_relation_readback.py's own
module docstring documents for this repo's other from-scratch script).
"""

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_REPAIR = REPO_ROOT / "scripts" / "gatekeeper" / "state-repair.py"

BOARD_COLUMNS = ["Backlog", "Planned", "Todo", "Doing", "Done", "Question"]


def run_repair(payload):
    return subprocess.run(
        [sys.executable, str(STATE_REPAIR)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def _reopen_payload(**overrides):
    """Base payload for the reopen-close rule: exactly the shape that
    closes -- state open, reopened, ci-green for PR 42 after the reopen,
    and PR 42 itself merged at/after the reopen. No status-label duplicates
    (labels: []), so the label rule stays silent and each edge-case test
    below isolates exactly one broken condition."""
    payload = {
        "columns": BOARD_COLUMNS,
        "labels": [],
        "state": "open",
        "reopened_at": "2026-09-01T00:00:00Z",
        "latest_event": {"event": "ci-green", "at": "2026-09-02T00:00:00Z", "pr": 42},
        "pr": {"number": 42, "merged": True, "merged_at": "2026-09-02T00:00:00Z"},
    }
    payload.update(overrides)
    return payload


# --- R9a: duplicate status:* labels -----------------------------------------

def test_duplicate_status_labels_keep_furthest_column():
    result = run_repair({
        "columns": BOARD_COLUMNS,
        "labels": ["status:todo", "status:doing", "bug"],
        "state": "open",
        "reopened_at": None,
        "latest_event": None,
        "pr": None,
    })
    assert result.stdout.splitlines() == [
        "verdict: repair",
        "keep: status:doing",
        "remove: status:todo",
    ], f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.returncode == 2, result.stderr


def test_duplicate_status_labels_three_way_keeps_the_furthest_removes_the_rest():
    """Input order (doing, todo, question, bug) deliberately differs from the
    labels' column-rank order (todo=2, doing=3, question=5 in BOARD_COLUMNS),
    so the `remove:` lines below can only be explained by preserving *input*
    order -- an implementation that instead sorted removals by ascending
    column rank would emit `status:todo` before `status:doing`."""
    result = run_repair({
        "columns": BOARD_COLUMNS,
        "labels": ["status:doing", "status:todo", "status:question", "bug"],
        "state": "open",
        "reopened_at": None,
        "latest_event": None,
        "pr": None,
    })
    assert result.stdout.splitlines() == [
        "verdict: repair",
        "keep: status:question",
        "remove: status:doing",
        "remove: status:todo",
    ], f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.returncode == 2, result.stderr


def test_duplicate_status_labels_reversed_columns_flips_the_winner():
    """Same two labels as the base case, but `columns` reversed: the ranking
    comes from `columns`' order, not from the labels' own order, so
    reversing it must flip which label is kept."""
    result = run_repair({
        "columns": list(reversed(BOARD_COLUMNS)),
        "labels": ["status:todo", "status:doing", "bug"],
        "state": "open",
        "reopened_at": None,
        "latest_event": None,
        "pr": None,
    })
    assert result.stdout.splitlines() == [
        "verdict: repair",
        "keep: status:todo",
        "remove: status:doing",
    ], f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.returncode == 2, result.stderr


def test_single_status_label_is_ok_not_a_repair():
    result = run_repair({
        "columns": BOARD_COLUMNS,
        "labels": ["status:todo", "bug"],
        "state": "open",
        "reopened_at": None,
        "latest_event": None,
        "pr": None,
    })
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_mixed_case_status_prefix_still_counts_as_a_status_label():
    result = run_repair({
        "columns": ["Todo", "Doing"],
        "labels": ["status:todo", "Status:Doing"],
        "state": "open",
        "reopened_at": None,
        "latest_event": None,
        "pr": None,
    })
    assert result.stdout.splitlines() == [
        "verdict: repair",
        "keep: Status:Doing",
        "remove: status:todo",
    ], f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.returncode == 2, result.stderr


def test_unknown_status_suffix_is_rejected():
    result = run_repair({
        "columns": ["Todo", "Doing"],
        "labels": ["status:archived", "bug"],
        "state": "open",
        "reopened_at": None,
        "latest_event": None,
        "pr": None,
    })
    assert result.returncode == 1, (
        f"an unmatched status suffix must be rejected as invalid input, got "
        f"returncode={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "unknown status label" in combined and "status:archived" in combined, combined


# --- R9b: reopened-then-finished ---------------------------------------------

def test_reopened_then_ci_green_and_merged_closes():
    result = run_repair(_reopen_payload())
    assert result.stdout.splitlines() == ["verdict: repair", "close: yes"], (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 2, result.stderr


def test_stale_merge_with_unrelated_later_ci_green_does_not_close():
    """PR 10 merged before the reopen; a LATER ci-green names an unrelated
    PR 12. Neither the PR-identity tie nor the merge-after-reopen condition
    holds, so this must not close."""
    result = run_repair(_reopen_payload(
        pr={"number": 10, "merged": True, "merged_at": "2026-08-01T00:00:00Z"},
        latest_event={"event": "ci-green", "at": "2026-09-03T00:00:00Z", "pr": 12},
    ))
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_latest_event_pr_mismatched_from_merged_pr_does_not_close():
    """Isolates the PR-identity tie: ci-green fires strictly after the
    reopen, and *some* PR merges at/after the reopen too -- every other
    close condition holds -- but that merged PR's number (99) does not match
    the ci-green event's own `pr` field (42, from the base payload). Only a
    check that latest_event.pr == pr.number can be what keeps this at `ok`;
    an implementation that merely checks latest_event.pr is non-null would
    wrongly close."""
    result = run_repair(_reopen_payload(
        pr={"number": 99, "merged": True, "merged_at": "2026-09-02T00:00:00Z"},
    ))
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"latest_event.pr=42 does not match the merged pr.number=99, so this "
        f"must not close even though the merge itself lands after the "
        f"reopen: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_same_pr_merged_before_reopen_does_not_close():
    result = run_repair(_reopen_payload(
        pr={"number": 42, "merged": True, "merged_at": "2026-08-01T00:00:00Z"},
    ))
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_latest_event_without_a_pr_does_not_close():
    result = run_repair(_reopen_payload(
        latest_event={"event": "ci-green", "at": "2026-09-02T00:00:00Z", "pr": None},
    ))
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_ci_green_exactly_at_reopen_does_not_close():
    result = run_repair(_reopen_payload(
        latest_event={"event": "ci-green", "at": "2026-09-01T00:00:00Z", "pr": 42},
    ))
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_ci_green_strictly_before_reopen_does_not_close():
    result = run_repair(_reopen_payload(
        latest_event={"event": "ci-green", "at": "2026-08-31T00:00:00Z", "pr": 42},
    ))
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_unmerged_pr_does_not_close():
    result = run_repair(_reopen_payload(
        pr={"number": 42, "merged": False, "merged_at": None},
    ))
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_latest_event_not_ci_green_does_not_close():
    result = run_repair(_reopen_payload(
        latest_event={"event": "blocked", "at": "2026-09-02T00:00:00Z", "pr": 42},
    ))
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_already_closed_ticket_is_never_reopen_closed_again():
    result = run_repair(_reopen_payload(state="closed"))
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_reopen_close_uses_real_instant_comparison_not_string_order():
    """A naive string compare between reopened_at and latest_event.at would
    wrongly call '2026-09-02T00:00:00+01:00' > '2026-09-01T23:30:00Z' (later
    calendar date, so lexically greater) even though, resolved to UTC, it is
    23:00 on the 1st -- thirty minutes BEFORE the 23:30 reopen. Only a real
    timezone-aware datetime comparison gets this right, and gets it to `ok`,
    not `repair`."""
    result = run_repair(_reopen_payload(
        reopened_at="2026-09-01T23:30:00Z",
        latest_event={"event": "ci-green", "at": "2026-09-02T00:00:00+01:00", "pr": 42},
        pr={"number": 42, "merged": True, "merged_at": "2026-09-02T00:00:00Z"},
    ))
    assert result.stdout.splitlines() == ["verdict: ok"], (
        f"latest_event.at is UTC 23:00 on the 1st, before the 23:30 reopen, "
        f"once the +01:00 offset is resolved: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_malformed_reopened_at_is_rejected():
    result = run_repair(_reopen_payload(reopened_at="not-a-date"))
    assert result.returncode == 1, (
        f"an unparseable timestamp must be rejected as invalid input, got "
        f"returncode={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "unparseable" in combined.lower() and "not-a-date" in combined, combined


def test_duplicate_labels_and_reopen_close_both_repair_in_one_call():
    result = run_repair(_reopen_payload(labels=["status:todo", "status:doing", "bug"]))
    assert result.stdout.splitlines() == [
        "verdict: repair",
        "keep: status:doing",
        "remove: status:todo",
        "close: yes",
    ], f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.returncode == 2, result.stderr
