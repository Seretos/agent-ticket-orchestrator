"""
Behaviour tests for `scripts/run/ato-event.py` (#63): a stdlib-only
render/parse CLI for the `<!-- ato:event v1 -->` machine-readable comment
block, the `ato:event` counterpart to `adev:event v1` (see AGENTS.md).

`render --event <e> --package <id> [--reason <r>] [--pr <n>]
[--merge-sha <s>]` prints the block to stdout (exit 0) or rejects invalid
input (exit 1, `error: ...` on stderr, empty stdout). `parse` reads stdin,
finds the first such block, applies the same validation, and prints JSON
`{"event","package","reason","pr","merge_sha"}` (exit 0), or rejects the
same way -- no block at all is also exit 1.

The symptom this closes (verbatim from the ticket): a tool reading this
plugin's ticket comments can only detect an escalation or a merge by
matching free-text lines and column moves, so its counts break when the
skill's wording changes. R2 below is the direct test of that: the same
rendered block, read back identically, regardless of the prose wrapped
around it.

`read_block` is a test-local dumb `key: value` reader -- deliberately not
imported from the script under test, so these tests do not depend on it
existing yet (RED) or share a bug with its implementation (GREEN).

Same harness as tests/test_package_branch.py: `subprocess.run` with
`sys.executable`, a module-level SCRIPT path constant, no mocking.
"""

import json
import pathlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run" / "ato-event.py"
SKILL_MD = REPO_ROOT / "skills" / "run" / "SKILL.md"

EVENTS = ["escalated", "triage-answered", "merged"]
REASONS = [
    "failed",
    "blocked",
    "triage-reblocked",
    "split-failed",
    "merge-failed",
    "merge-conflict",
    "rebase-decision",
]
KNOWN_KEYS = {"event", "package", "reason", "pr", "merge_sha"}


def read_block(text):
    """Test-local reader for the first `<!-- ato:event v1 -->` block: dumb
    `key: value` lines, unknown keys ignored, first block wins. Returns a
    dict of the known keys found, or None if no complete block is present."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "<!-- ato:event v1":
            body = []
            for later in lines[i + 1:]:
                if later.strip() == "-->":
                    result = {}
                    for entry in body:
                        if ":" not in entry:
                            continue
                        key, _, value = entry.partition(":")
                        key = key.strip()
                        if key in KNOWN_KEYS:
                            result[key] = value.strip()
                    return result
                body.append(later)
            return None  # opened but never closed
    return None


def run_render(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "render", *args],
        capture_output=True, text=True,
    )


def run_parse(text):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "parse"],
        input=text, capture_output=True, text=True,
    )


def render_block(event, package, reason=None, pr=None, merge_sha=None):
    """Render a block through the real script and return its raw stdout."""
    args = ["--event", event, "--package", package]
    if reason is not None:
        args += ["--reason", reason]
    if pr is not None:
        args += ["--pr", pr]
    if merge_sha is not None:
        args += ["--merge-sha", merge_sha]
    result = run_render(*args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def make_block(event="merged", package="63", reason="", pr="", merge_sha=""):
    """Hand-built block text, for parse-side tests that need an invalid
    combination `render` would itself refuse to produce."""
    return (
        "<!-- ato:event v1\n"
        f"event: {event}\n"
        f"package: {package}\n"
        f"reason: {reason}\n"
        f"pr: {pr}\n"
        f"merge_sha: {merge_sha}\n"
        "-->\n"
    )


# --- R1: render each event -------------------------------------------------

@pytest.mark.parametrize("event", EVENTS)
def test_render_event(event):
    """Requirement R1: `render` prints a block with all five keys for each
    event, exit 0. `escalated` requires a reason; the others carry one too
    here so every key is exercised."""
    reason = "failed" if event == "escalated" else "merge-conflict"
    result = run_render("--event", event, "--package", "63",
                         "--reason", reason, "--pr", "12",
                         "--merge-sha", "deadbeef")

    assert result.returncode == 0, result.stdout + result.stderr
    block = read_block(result.stdout)
    assert block is not None, f"no block found in: {result.stdout!r}"
    assert block["event"] == event
    assert block["package"] == "63"
    assert block["reason"] == reason
    assert block["pr"] == "12"
    assert block["merge_sha"] == "deadbeef"


@pytest.mark.parametrize("reason", REASONS)
def test_render_every_reason(reason):
    """Additional coverage: every one of the 7 reasons renders."""
    result = run_render("--event", "escalated", "--package", "63",
                         "--reason", reason)
    assert result.returncode == 0, result.stdout + result.stderr
    block = read_block(result.stdout)
    assert block is not None
    assert block["reason"] == reason


def test_render_omitted_pr_and_merge_sha_are_empty():
    """Additional coverage: omitted --pr/--merge-sha render as empty, not
    absent -- all five keys are always emitted."""
    result = run_render("--event", "merged", "--package", "63",
                         "--reason", "merge-conflict")
    assert result.returncode == 0, result.stdout + result.stderr
    block = read_block(result.stdout)
    assert block is not None
    assert block.get("pr", "") == ""
    assert block.get("merge_sha", "") == ""


def test_render_triage_answered_with_reason():
    """Additional coverage: `triage-answered` (reason optional) also accepts
    one, in-vocabulary."""
    result = run_render("--event", "triage-answered", "--package", "63",
                         "--reason", "blocked")
    assert result.returncode == 0, result.stdout + result.stderr
    block = read_block(result.stdout)
    assert block is not None
    assert block["event"] == "triage-answered"
    assert block["reason"] == "blocked"


def test_render_triage_answered_without_reason_is_empty():
    """Additional coverage: reason is optional for `triage-answered` /
    `merged` -- omitting it renders an empty `reason:`, not a rejection."""
    result = run_render("--event", "triage-answered", "--package", "63")
    assert result.returncode == 0, result.stdout + result.stderr
    block = read_block(result.stdout)
    assert block is not None
    assert block.get("reason", "") == ""


# --- R2: block reads back regardless of wording (the ticket's symptom) ----

PROSE_WRAPS = {
    "prefixed-en": lambda block: f"Escalated: something went wrong\n\n{block}\nMore text.\n",
    "prefixed-de": lambda block: f"Eskaliert: etwas ist schiefgelaufen\n\n{block}\n",
    "bare": lambda block: block,
    "fenced": lambda block: f"before\n```\n{block}```\nafter\n",
    "crlf": lambda block: block.replace("\n", "\r\n"),
}


@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("wrap_id", list(PROSE_WRAPS))
def test_block_independent_of_wording(event, wrap_id):
    """Requirement R2 (the symptom): the same rendered block, read back
    identically via `parse`, whether it sits under an `Escalated:` line, a
    reworded German equivalent, no prose at all, inside a code fence, or
    with CRLF line endings."""
    reason = "failed" if event == "escalated" else "merge-conflict"
    block = render_block(event, "63", reason=reason, pr="12",
                          merge_sha="deadbeef")
    expected = read_block(block)
    assert expected is not None  # sanity: the un-wrapped block parses

    wrapped = PROSE_WRAPS[wrap_id](block)

    result = run_parse(wrapped)
    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed == {
        "event": expected["event"],
        "package": expected["package"],
        "reason": expected.get("reason", ""),
        "pr": expected.get("pr", ""),
        "merge_sha": expected.get("merge_sha", ""),
    }


def test_parse_ignores_unknown_key():
    """Additional coverage: an unknown key inside the block is ignored, not
    fatal."""
    block = render_block("merged", "63", reason="merge-conflict")
    lines = block.splitlines()
    close_index = next(i for i, line in enumerate(lines) if line.strip() == "-->")
    lines.insert(close_index, "future_key: whatever")
    text = "\n".join(lines) + "\n"

    result = run_parse(text)
    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert "future_key" not in parsed
    assert parsed["event"] == "merged"


def test_parse_empty_value_is_empty_string():
    """Additional coverage: a present-but-empty value reads back as ""."""
    text = make_block(event="merged", package="63", reason="", pr="", merge_sha="")

    result = run_parse(text)
    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed == {"event": "merged", "package": "63", "reason": "",
                       "pr": "", "merge_sha": ""}


def test_parse_first_of_two_blocks_wins():
    """Additional coverage: with two blocks in the text, `parse` reads only
    the first."""
    first = render_block("escalated", "1", reason="failed")
    second = render_block("merged", "2", reason="merge-conflict")
    text = f"{first}\nsome text in between\n\n{second}\n"

    result = run_parse(text)
    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["event"] == "escalated"
    assert parsed["package"] == "1"


def test_parse_no_block_is_exit_1():
    """Requirement (line 11): no block at all -> exit 1, empty stdout."""
    result = run_parse("just an ordinary ticket comment, no marker here.\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == ""


# --- R3: reject out-of-vocabulary input -------------------------------------

RENDER_REJECTIONS = [
    (["--event", "bogus", "--package", "63"], "unknown event"),
    (["--event", "merged", "--package", "63", "--reason", "bogus"], "unknown reason"),
    (["--event", "escalated", "--package", "63"], "escalated without reason"),
    (["--event", "merged", "--package", ""], "empty package"),
    (["--event", "merged", "--package", "63\nx"], "package with embedded newline"),
    (["--event", "merged", "--package", "63-->x"], "package with close-comment marker"),
    (["--event", "merged", "--package", "63", "--reason", "merge-conflict",
      "--pr", "12\n34"], "pr with embedded newline"),
]


@pytest.mark.parametrize("args,desc", RENDER_REJECTIONS,
                         ids=[desc for _, desc in RENDER_REJECTIONS])
def test_render_rejects(args, desc):
    """Requirement R3: every listed rejection -> exit 1, empty stdout,
    `error:` on stderr."""
    result = run_render(*args)
    assert result.returncode == 1, f"{desc}: stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.stdout == "", f"{desc}: expected empty stdout, got {result.stdout!r}"
    assert "error:" in result.stderr.lower(), f"{desc}: missing 'error:' on stderr; stderr={result.stderr!r}"


PARSE_REJECTIONS = [
    (make_block(event="bogus"), "unknown event"),
    (make_block(reason="bogus"), "unknown reason"),
    (make_block(event="escalated", reason=""), "escalated without reason"),
    (make_block(package=""), "empty package"),
]


@pytest.mark.parametrize("text,desc", PARSE_REJECTIONS,
                         ids=[desc for _, desc in PARSE_REJECTIONS])
def test_parse_rejects(text, desc):
    """Requirement R3: the same validation applies on the `parse` side, once
    a block is actually found."""
    result = run_parse(text)
    assert result.returncode == 1, f"{desc}: stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.stdout == "", f"{desc}: expected empty stdout, got {result.stdout!r}"
    assert "error:" in result.stderr.lower(), f"{desc}: missing 'error:' on stderr; stderr={result.stderr!r}"


# --- R4: forward guard (vacuous today; see plan) ----------------------------

def test_skill_reasons_in_vocabulary():
    """R4's forward guard, declared evidence kind `none` in the plan -- not
    claimed as R1-R3 driving-test evidence. Extracts every reason following
    an `ato-event.py render ... --reason <value>` call in SKILL.md and
    confirms it renders (exit 0). SKILL.md carries no such calls yet (#67
    wires them in), so this loop is empty and the assertion is vacuously
    true -- it becomes a real regression guard only once #67 lands."""
    text = SKILL_MD.read_text(encoding="utf-8")
    reasons = re.findall(
        r"ato-event\.py\s+render\b[^\n]*?--reason[= ]([A-Za-z0-9-]+)", text)

    for reason in reasons:
        result = run_render("--event", "escalated", "--package", "0",
                             "--reason", reason)
        assert result.returncode == 0, f"reason {reason!r} from SKILL.md rejected: {result.stderr}"
