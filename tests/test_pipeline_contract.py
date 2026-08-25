"""
Structural invariants of the differentiated-escalation rebuild (2026-08-25).

This plugin had no tests at all before this file (agent-plugin-dev#27). These
pin the specific invariants this change introduced — a package that only died
mid-CI-wait is checked before it is retried, a `blocked` event is triaged
before it costs a retry, and neither `gatekeeper` step blocks on a live chat
answer — without asserting on exact prose wording. Full contract coverage
(mirroring agent-autonomous-developer's test_pipeline_contract.py) remains
agent-plugin-dev#27's scope, not this file's.
"""

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = REPO_ROOT / "skills" / "run" / "SKILL.md"
GATEKEEPER = REPO_ROOT / "skills" / "gatekeeper" / "SKILL.md"
AGENTS_DIR = REPO_ROOT / "agents"
TRIAGE = AGENTS_DIR / "triage.md"


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "missing YAML front-matter"
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


# --- A1: the pre-retry CI check (agent-ticket-orchestrator#8) --------------

def test_run_checks_ci_before_treating_failed_as_retry_worthy():
    text = _read(RUN)
    assert "pre-retry ci check" in text.lower()
    assert "list_pipeline_runs" in text
    assert "get_pr" in text


def test_run_references_the_two_ci_wait_incidents():
    text = _read(RUN)
    assert "#165" in text
    assert "#268" in text


# --- A2: blocked-event triage (agent-ticket-orchestrator#7) ----------------

def test_run_dispatches_triage_on_blocked_before_retrying():
    text = _read(RUN)
    assert "triage" in text.lower()
    assert "STATUS: ANSWERED" in text or "ANSWERED" in text
    assert "ESCALATE" in text


def test_blocked_list_no_longer_used_as_a_mechanism():
    """`blocked_list` may still be named in passing (e.g. "does not exist"),
    but must never again appear as something the skill appends to or
    re-dispatches from — that would mean the old two-stage design survived
    alongside the new triage mechanism instead of being replaced by it."""
    text = _read(RUN)
    assert "append to `blocked_list`" not in text
    assert "entry of `blocked_list`" not in text
    assert "remaining entry of `blocked_list`" not in text


def test_second_pass_section_removed():
    text = _read(RUN)
    assert "Second pass for set-aside packages" not in text


def test_triage_agent_exists_and_is_read_only():
    fm = _frontmatter(_read(TRIAGE))
    assert fm.get("name") == "triage"
    assert fm.get("model") == "opus"
    tools = fm.get("tools", "")
    for forbidden in ("Edit", "Write", "Bash"):
        assert forbidden not in [t.strip() for t in tools.split(",")], (
            f"triage must stay read-only; found {forbidden!r} in its tools"
        )
    write_mcp_markers = ("add_comment", "update_ticket", "create_ticket", "merge_pr")
    for marker in write_mcp_markers:
        assert marker not in tools, f"triage must not carry the write tool {marker!r}"


def test_triage_never_instructed_to_write():
    text = _read(TRIAGE)
    assert "Never post" in text or "never post a comment" in text.lower()


# --- A3: bundler autopilot (no confirmation round) --------------------------

def test_gatekeeper_bundler_step_has_no_confirmation_question():
    text = _read(GATEKEEPER)
    # Split at the clarify step so a mention of AskUserQuestion's absence in
    # the intro/hard-rules doesn't accidentally satisfy this check.
    bundle_section = text.split("## Step 3", 1)[0]
    assert "AskUserQuestion" not in bundle_section or "no confirmation" in bundle_section.lower()


# --- A4: gatekeeper questions go to the ticket, not the chat ---------------

def test_gatekeeper_never_calls_askuserquestion():
    text = _read(GATEKEEPER)
    for m in re.finditer(r"AskUserQuestion", text):
        ctx = text[max(0, m.start() - 160): m.end() + 40].lower()
        assert any(
            w in ctx
            for w in ("not used", "no ", "never", "not part of this flow", "not granted")
        ), ctx


def test_gatekeeper_posts_questions_as_ticket_comment():
    text = _read(GATEKEEPER)
    assert "Clarification needed (gatekeeper)" in text
    assert "add_comment" in text


def test_gatekeeper_does_not_block_on_one_package():
    text = _read(GATEKEEPER)
    assert "next package" in text.lower() or "move on" in text.lower()


def test_clarifier_no_longer_receives_inlined_answers():
    clarifier = AGENTS_DIR / "clarifier.md"
    text = _read(clarifier)
    assert "chosen option` pairs, verbatim" not in text
