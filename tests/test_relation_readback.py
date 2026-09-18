"""
Driving tests for `scripts/gatekeeper/relation-readback.py` (#25, R7): a pure
stdin-JSON -> stdout-verdict helper that the gatekeeper's Step 3.5 pipes
`{expected: [...], relations: [...], reasons: {...}}` into, so the read-back
diff and the three-reason vocabulary check (`not found` / `closed` /
`self-edge`) run as a deterministic process outside the same LLM whose
bookkeeping lost four of the five relations it reported (the incident this
package fixes), rather than as more prose telling that LLM to check its own
work.

Contract (settled here, since the script does not exist yet and this is
where its stdin/stdout shape is first pinned down for `implement` to build
against):

  stdin:  {"expected": ["#5", "#9", ...],
           "relations": [{"kind": "blocked_by"|"relates_to", "target": "#5"}, ...],
           "reasons": {"#9": "not found"|"closed"|"self-edge", ...}}
  stdout: "verdict: ok" or "verdict: gap", naming any gap target(s)
  exit:   0 on ok, 2 on gap, non-zero (and the offending value named in
          stdout/stderr) when `reasons` carries a value outside the three-
          word vocabulary.

Harness copied from tests/test_release_scripts.py's pattern (module-level
path constants + subprocess.run), but with an explicit interpreter
(`sys.executable`) rather than a bash path, since this script is Python, not
bash -- matching the plan's "python (`python3` if `python` is not on PATH)"
invocation.

The script does not exist yet at the time these tests are written
(phase=tests, RED only): every subprocess call below fails because Python
itself cannot open the missing file ("can't open file ... No such file or
directory", a non-zero return code that happens to collide with the `gap`
exit code in places) -- the RED reason actually observed is the assertion on
`result.stdout` finding nothing there (stdout is empty; the interpreter's
complaint lands on stderr), not a bare exit-code coincidence.
"""

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RELATION_READBACK = REPO_ROOT / "scripts" / "gatekeeper" / "relation-readback.py"


def run_readback(payload):
    return subprocess.run(
        [sys.executable, str(RELATION_READBACK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def test_relation_readback_all_expected_present_is_ok():
    result = run_readback({
        "expected": ["#5", "#9"],
        "relations": [
            {"kind": "blocked_by", "target": "#5"},
            {"kind": "blocked_by", "target": "#9"},
        ],
        "reasons": {},
    })
    assert "verdict: ok" in result.stdout, (
        f"expected 'verdict: ok' in stdout, got stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_relation_readback_missing_target_with_no_reason_is_a_gap():
    result = run_readback({
        "expected": ["#5", "#9"],
        "relations": [
            {"kind": "blocked_by", "target": "#5"},
        ],
        "reasons": {},
    })
    assert "verdict: gap" in result.stdout, (
        f"expected 'verdict: gap' in stdout, got stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    # F9 fix (test-critic round 4, minor): bare "is the missing id present
    # in stdout somewhere" is also satisfied by a script that unconditionally
    # echoes the whole `expected` list on a gap -- #9 would be named, but so
    # would the resolved #5, without the script ever distinguishing them.
    # Bind the check to a dedicated 'gap targets:' line and require the
    # resolved target to be absent from it.
    gap_line = next(
        (line for line in result.stdout.splitlines() if line.startswith("gap targets:")),
        None,
    )
    assert gap_line, (
        f"expected a 'gap targets:' line in stdout, got stdout={result.stdout!r}"
    )
    assert "#9" in gap_line, (
        f"expected the missing target id in the gap targets line: {gap_line!r}"
    )
    assert "#5" not in gap_line, (
        "the resolved target #5 must not appear in the gap targets line "
        f"(would mean the script echoes the whole expected set): {gap_line!r}"
    )
    assert result.returncode == 2, result.stderr


def test_relation_readback_missing_target_with_named_reason_is_ok():
    for reason in ("closed", "not found", "self-edge"):
        result = run_readback({
            "expected": ["#5", "#9"],
            "relations": [
                {"kind": "blocked_by", "target": "#5"},
            ],
            "reasons": {"#9": reason},
        })
        assert "verdict: ok" in result.stdout, (
            f"reason={reason!r}: expected 'verdict: ok' in stdout, got "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.returncode == 0, (reason, result.stderr)


def test_relation_readback_each_missing_target_needs_its_own_reason():
    """F8 fix (test-critic round 4, minor): every case above keys a single
    `reasons` entry to the one target that's actually missing, so a script
    that only checks "is `reasons` non-empty and are its values
    vocabulary-valid" -- ignoring which key they are attached to -- would
    pass every one of them. Two missing targets here (#9 and #12): first,
    each carries its OWN, DIFFERENT valid reason -- both resolve (ok).
    Second, the same reasons dict is narrowed so only #9 keeps its own
    entry; #12 must still report as a gap even though `reasons` stays
    non-empty and vocabulary-valid, because that entry is not #12's own."""
    # both missing targets carry their own, distinct reason -> resolved.
    result_ok = run_readback({
        "expected": ["#5", "#9", "#12"],
        "relations": [
            {"kind": "blocked_by", "target": "#5"},
        ],
        "reasons": {"#9": "not found", "#12": "closed"},
    })
    assert "verdict: ok" in result_ok.stdout, (
        f"expected 'verdict: ok' when each missing target (#9, #12) "
        f"carries its own reason: stdout={result_ok.stdout!r} "
        f"stderr={result_ok.stderr!r}"
    )
    assert result_ok.returncode == 0, result_ok.stderr

    # same two missing targets, but only #9 carries a reason of its own --
    # #12 must still gap, even though `reasons` stays non-empty and its
    # one value ('not found') is vocabulary-valid.
    result_gap = run_readback({
        "expected": ["#5", "#9", "#12"],
        "relations": [
            {"kind": "blocked_by", "target": "#5"},
        ],
        "reasons": {"#9": "not found"},
    })
    assert "verdict: gap" in result_gap.stdout, (
        "expected 'verdict: gap' -- #12 is missing and carries no reason "
        "of its own, even though the reasons dict is non-empty and "
        f"vocabulary-valid: stdout={result_gap.stdout!r} "
        f"stderr={result_gap.stderr!r}"
    )
    assert "#12" in result_gap.stdout, (
        "expected the unreasoned missing target (#12) named in stdout as "
        f"the gap: stdout={result_gap.stdout!r}"
    )
    assert result_gap.returncode == 2, result_gap.stderr


def test_relation_readback_rejects_a_fourth_reason_value():
    result = run_readback({
        "expected": ["#5", "#9"],
        "relations": [
            {"kind": "blocked_by", "target": "#5"},
        ],
        "reasons": {"#9": "absorbed"},
    })
    assert result.returncode != 0, (
        "a reason value outside the three-word vocabulary must not be "
        f"accepted silently, got returncode=0 stdout={result.stdout!r}"
    )
    assert "absorbed" in (result.stdout + result.stderr), (
        "expected the rejected reason value named in the output, got "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_relation_readback_accepts_gitlab_relates_to():
    result = run_readback({
        "expected": ["#5"],
        "relations": [
            {"kind": "relates_to", "target": "#5"},
        ],
        "reasons": {},
    })
    assert "verdict: ok" in result.stdout, (
        "a relates_to relation (GitLab's blocked_by/blocks-less fallback) "
        f"must satisfy the same expected target: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr


def test_relation_readback_target_mismatch_with_equal_counts_is_a_gap():
    """F12 fix (test-critic round 1): every prior case here only ever
    differs in list LENGTH between expected and returned relations, never in
    actual membership/identity -- a script comparing only counts (e.g.
    len(relations) + len(reasons) >= len(expected)) passes all of them
    without ever diffing which targets were actually written. Here the
    counts agree (2 expected, 2 relations) but the target set does not:
    #9 was never written, and #3 is a spurious relation with no bearing on
    the expected set. Only a real membership diff reports this as a gap."""
    result = run_readback({
        "expected": ["#5", "#9"],
        "relations": [
            {"kind": "blocked_by", "target": "#5"},
            {"kind": "blocked_by", "target": "#3"},
        ],
        "reasons": {},
    })
    assert "verdict: gap" in result.stdout, (
        f"expected 'verdict: gap' in stdout (counts match, 2 vs 2, but #9 "
        f"is missing while #3 is an unrelated extra), got "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    gap_line = next(
        (line for line in result.stdout.splitlines() if line.startswith("gap targets:")),
        None,
    )
    assert gap_line, f"expected a 'gap targets:' line in stdout, got stdout={result.stdout!r}"
    assert "#9" in gap_line, (
        "expected the missing target id (#9) named in the gap targets line "
        f"even though relation counts matched the expected count: {gap_line!r}"
    )
    assert "#5" not in gap_line and "#3" not in gap_line, (
        "neither the resolved target #5 nor the unrelated extra relation #3 "
        f"belongs in the gap targets line: {gap_line!r}"
    )
    assert result.returncode == 2, result.stderr


def test_relation_readback_duplicate_relation_is_not_a_gap():
    result = run_readback({
        "expected": ["#5"],
        "relations": [
            {"kind": "blocked_by", "target": "#5"},
            {"kind": "blocked_by", "target": "#5"},
        ],
        "reasons": {},
    })
    assert "verdict: ok" in result.stdout, (
        f"a duplicate relation must not produce a gap: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr
