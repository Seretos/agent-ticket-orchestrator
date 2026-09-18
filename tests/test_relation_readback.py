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
    assert "#9" in result.stdout, (
        "expected the missing target id named in stdout, "
        f"got stdout={result.stdout!r}"
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
