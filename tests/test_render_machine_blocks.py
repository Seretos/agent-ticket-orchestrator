"""
Driving tests for `scripts/gatekeeper/render-machine-blocks.py` (#77): a pure
stdin-JSON -> stdout script that renders the `gatekeeper:frame v1` block
(always) and the `gatekeeper:chain v1` block (regression chain only) from
parsed `clarifier:frame` fields, so the values ecosystem-statistics#13
scrapes are computed by a tested program, not a model.

Input contract (from the plan, `.adev/77-1/plan.md`):

  stdin:  {"project": "owner/repo", "ac": "<raw>", "premise": ["<raw>", ...],
           "unprovable_here": ["<raw>", ...], "chain": "<raw>"}
  - `premise`/`unprovable_here` are lists (repeatable frame keys); missing
    defaults to `[]`.
  - `chain` is the frame value verbatim (`none` or
    `regression-chain:#90,#121`); missing/`""`/`none` means no chain. A
    leading `regression-chain:` is stripped, members split on `,`, each
    stripped.

Rules:
  - `ac_rewritten: no` iff `ac.strip() == "as-filed"`, else `yes`.
  - `premises`/`not_proven` = count of entries whose stripped value is
    neither `""` nor `none`.
  - Member `#N` is qualified with `project` -> `<project>#N`; a member
    already `owner/repo#N` is kept verbatim; order is preserved.

Output: exact text, LF, fixed key order, blocks separated by one blank
line, one trailing `\\n` after the last `-->`. The chain block is omitted
entirely when there is no chain.

Invalid input (stdin not a JSON object; `ac` missing/not a string;
`premise`/`unprovable_here` not a list of strings; a chain member matching
neither `^#\\d+$` nor `^[\\w.-]+/[\\w.-]+#\\d+$`; a bare `#N` with `project`
missing or not `owner/repo`-shaped) -> exit 1, `error: <what>` on stderr,
empty stdout (the `scripts/run/ato-event.py` convention). Exit 0 otherwise.

Harness copied from `tests/test_relation_readback.py`'s pattern (module-level
path constants + `subprocess.run([sys.executable, script], ...)`).

The script does not exist yet at the time these tests are written
(phase=tests, RED only): every subprocess call below fails because Python
itself cannot open the missing file ("can't open file ... No such file or
directory", exit code 2) -- the RED reason actually observed is the
assertion on `result.stdout`/`result.returncode` finding nothing there
(stdout is empty; the interpreter's complaint lands on stderr), not a bare
exit-code coincidence.
"""

import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RENDER_MACHINE_BLOCKS = REPO_ROOT / "scripts" / "gatekeeper" / "render-machine-blocks.py"


def run_script(payload):
    return subprocess.run(
        [sys.executable, str(RENDER_MACHINE_BLOCKS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def base_payload(**overrides):
    """A minimal valid payload: no chain, no premises, an ac that is not
    'as-filed'. Individual tests override only the fields they exercise."""
    payload = {
        "project": "o/r",
        "ac": "some rewritten acceptance text",
        "premise": [],
        "unprovable_here": [],
        "chain": "none",
    }
    payload.update(overrides)
    return payload


def frame_block(ac_rewritten, premises, not_proven):
    return (
        "<!-- gatekeeper:frame v1\n"
        f"ac_rewritten: {ac_rewritten}\n"
        f"premises: {premises}\n"
        f"not_proven: {not_proven}\n"
        "-->\n"
    )


def chain_block(members):
    return (
        "<!-- gatekeeper:chain v1\n"
        f"members: {members}\n"
        "-->\n"
    )


# ---------------------------------------------------------------------------
# R1 -- ac_rewritten
# ---------------------------------------------------------------------------

def test_ac_as_filed_renders_no():
    result = run_script(base_payload(ac="as-filed"))
    assert result.returncode == 0, result.stderr
    assert result.stdout == frame_block("no", 0, 0), (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_ac_written_renders_yes():
    result = run_script(base_payload(ac="a specific rewritten AC"))
    assert result.returncode == 0, result.stderr
    assert result.stdout == frame_block("yes", 0, 0), (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("ac_value", [" as-filed ", "\tas-filed\n"])
def test_ac_as_filed_with_surrounding_whitespace_renders_no(ac_value):
    """Additional edge-case coverage (R1)."""
    result = run_script(base_payload(ac=ac_value))
    assert result.returncode == 0, result.stderr
    assert result.stdout == frame_block("no", 0, 0), (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# R2 -- premises / not_proven counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,other_key", [
    ("premise", "unprovable_here"),
    ("unprovable_here", "premise"),
])
@pytest.mark.parametrize("values,expected_count", [
    ([], 0),
    (["none"], 0),
    (["x"], 1),
    (["a", "none", "b", "c"], 3),
])
def test_counts(key, other_key, values, expected_count):
    payload = base_payload()
    payload[key] = values
    payload[other_key] = []
    result = run_script(payload)
    assert result.returncode == 0, result.stderr
    counts = {"premise": 0, "unprovable_here": 0}
    counts[key] = expected_count
    assert result.stdout == frame_block("yes", counts["premise"], counts["unprovable_here"]), (
        f"key={key} values={values}: stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_counts_missing_keys_default_to_zero():
    """Additional edge-case coverage (R2): key missing -> 0."""
    payload = base_payload()
    del payload["premise"]
    del payload["unprovable_here"]
    result = run_script(payload)
    assert result.returncode == 0, result.stderr
    assert result.stdout == frame_block("yes", 0, 0), (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# R3 -- members formatting
# ---------------------------------------------------------------------------

def test_chain_same_repo_member_qualified():
    payload = base_payload(project="o/r", chain="regression-chain:#121")
    result = run_script(payload)
    assert result.returncode == 0, result.stderr
    assert result.stdout == frame_block("yes", 0, 0) + "\n" + chain_block("o/r#121"), (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_chain_cross_repo_member_kept():
    payload = base_payload(project="o/r", chain="regression-chain:other/lib#7")
    result = run_script(payload)
    assert result.returncode == 0, result.stderr
    assert result.stdout == frame_block("yes", 0, 0) + "\n" + chain_block("other/lib#7"), (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_chain_several_members_in_order():
    payload = base_payload(project="o/r", chain="regression-chain:#121,other/lib#7,#90")
    result = run_script(payload)
    assert result.returncode == 0, result.stderr
    expected = frame_block("yes", 0, 0) + "\n" + chain_block("o/r#121,other/lib#7,o/r#90")
    assert result.stdout == expected, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_chain_members_with_surrounding_whitespace():
    """Additional edge-case coverage (R3): whitespace around members."""
    payload = base_payload(project="o/r", chain="regression-chain:#90, #121")
    result = run_script(payload)
    assert result.returncode == 0, result.stderr
    expected = frame_block("yes", 0, 0) + "\n" + chain_block("o/r#90,o/r#121")
    assert result.stdout == expected, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# R4 -- chain block omitted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chain_value", [None, "", "none"])
def test_chain_block_omitted(chain_value):
    payload = base_payload(project="o/r")
    if chain_value is None:
        payload.pop("chain", None)
    else:
        payload["chain"] = chain_value
    result = run_script(payload)
    assert result.returncode == 0, result.stderr
    assert result.stdout == frame_block("yes", 0, 0), (
        f"chain_value={chain_value!r}: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "gatekeeper:chain" not in result.stdout


# ---------------------------------------------------------------------------
# R5 -- invalid input rejected
# ---------------------------------------------------------------------------

def _member_missing_hash(payload):
    payload["chain"] = "regression-chain:90"


def _bare_member_without_project(payload):
    payload["chain"] = "regression-chain:#90"
    payload.pop("project", None)


def _ac_missing(payload):
    payload.pop("ac", None)


def _premise_not_a_list(payload):
    payload["premise"] = "not-a-list"


@pytest.mark.parametrize("mutate,description", [
    (_member_missing_hash, "chain member missing leading #"),
    (_bare_member_without_project, "bare #N with project missing"),
    (_ac_missing, "ac missing"),
    (_premise_not_a_list, "premise not a list"),
])
def test_invalid_input_rejected(mutate, description):
    payload = base_payload(project="o/r")
    mutate(payload)
    result = run_script(payload)
    assert result.returncode == 1, (
        f"{description}: expected exit 1, got returncode={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.stdout == "", (
        f"{description}: expected empty stdout, got {result.stdout!r}"
    )
    assert result.stderr.startswith("error:"), (
        f"{description}: expected stderr to start with 'error:', got {result.stderr!r}"
    )


def test_chain_cross_repo_member_without_project_is_valid():
    """Additional edge-case coverage (R5): a cross-repo member does not need
    `project` at all -> exit 0, not rejected."""
    payload = base_payload(chain="regression-chain:other/lib#7")
    payload.pop("project", None)
    result = run_script(payload)
    assert result.returncode == 0, result.stderr
    expected = frame_block("yes", 0, 0) + "\n" + chain_block("other/lib#7")
    assert result.stdout == expected, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
