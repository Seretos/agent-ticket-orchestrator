"""
Structural invariants of the differentiated-escalation rebuild (2026-08-25),
and of the dependency-ordering + problem-frame change that followed it
(agent-ticket-orchestrator#10, #11).

This plugin had no tests at all before this file (agent-plugin-dev#27). These
pin the specific invariants this change introduced — a package that only died
mid-CI-wait is checked before it is retried, a `blocked` event is triaged
before it costs a retry, and neither `gatekeeper` step blocks on a live chat
answer — without asserting on exact prose wording. Full contract coverage
(mirroring agent-autonomous-developer's test_pipeline_contract.py) remains
agent-plugin-dev#27's scope, not this file's.

#10/#11 add: `run` orders Todo by `blocked_by` relations and skips a package
whose blocker has not landed rather than escalating or reordering past it;
the `clarifier` interrogates a ticket's problem frame (symptom, measurement,
prior attempts) before any detail question and detects regression chains.
Both issues are prose/behaviour changes to an LLM-judged pipeline — the
groups below assert structure (headings, load-bearing substrings, ordering of
sections), never simulate the clarifier's or run's actual judgement. Ticket
#11's acceptance criteria 3 and 4 ask for fixture-ticket behaviour that only a
live `clarifier` dispatch could produce; this repo has no harness for that
(no live `claude -p`, no API key, no tracker in CI), so the only executable
form is the "Worked frames" section in `agents/clarifier.md`, and
`test_clarifier_ships_the_two_worked_frames` below is the one test that
checks it — it is not a substitute for actually running the clarifier against
a real ticket.
"""

import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = REPO_ROOT / "skills" / "run" / "SKILL.md"
GATEKEEPER = REPO_ROOT / "skills" / "gatekeeper" / "SKILL.md"
TICKET_SKILL = REPO_ROOT / "skills" / "ticket" / "SKILL.md"
AGENTS_DIR = REPO_ROOT / "agents"
TRIAGE = AGENTS_DIR / "triage.md"
BUNDLER = AGENTS_DIR / "bundler.md"
CLARIFIER = AGENTS_DIR / "clarifier.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"
README = REPO_ROOT / "README.md"
DESCRIPTION = REPO_ROOT / "description.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
LINT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "lint.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
MARKETPLACE_PAYLOAD_SCRIPT = REPO_ROOT / ".github" / "scripts" / "marketplace-payload.sh"
TEMPLATES = REPO_ROOT / "templates" / "ISSUE_TEMPLATE"


def _slice(text: str, start: str, end: str) -> str:
    """The text between the first occurrence of `start` and the first
    occurrence of `end` after it — used to scope an assertion to one
    section instead of the whole file."""
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def _positions(text: str, pat) -> list:
    """Start offsets of every match of `pat` in `text`. `pat` may be a plain
    string (matched case-insensitively, literally) or a compiled regex."""
    if isinstance(pat, str):
        return [m.start() for m in re.finditer(re.escape(pat), text, re.IGNORECASE)]
    return [m.start() for m in pat.finditer(text)]


def _assert_near(text: str, a, b, window: int = 200, msg: str = "") -> None:
    """Assert `a` and `b` each occur in `text`, with at least one occurrence
    of each within `window` characters of the other. Used to bind a trigger
    phrase to its outcome (or an exclusion to the rule it excludes from)
    instead of letting two unrelated substrings anywhere in a large section
    satisfy the same assertion."""
    pa, pb = _positions(text, a), _positions(text, b)
    assert pa, f"{a!r} not found in text"
    assert pb, f"{b!r} not found in text"
    assert any(abs(x - y) <= window for x in pa for y in pb), (
        msg or f"{a!r} and {b!r} never occur within {window} chars of each other"
    )


def _frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "missing YAML front-matter"
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def _clarifier_heading_labels() -> set:
    """The canonical heading vocabulary from clarifier.md's own "The heading
    vocabulary" subsection (package #19) — the set of ticket-body heading
    labels every filed-ticket surface (the `ticket` skill, the GitHub issue
    forms, the gatekeeper's epic body) must draw from. Each accepted heading
    is documented as a backticked `## <Label>` (optionally with a
    parenthetical alias or a separate backticked alias entry); this pulls out
    just the `<Label>` text of every such backtick span in that subsection."""
    text = _read(CLARIFIER)
    section = _slice(text, "### The heading vocabulary", "## Inputs you receive")
    return {m.strip() for m in re.findall(r"`##\s+([^`(]+?)`", section)}


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


# --- CI trigger (agent-ticket-orchestrator#4) and release changelog (#5) ---

def test_lint_workflow_is_pull_request_only():
    # No YAML dependency here on purpose -- lint.yml's own CI step only
    # installs pytest, and this repo has no other Python dependency yet.
    text = _read(LINT_WORKFLOW)
    m = re.search(r"^on:\s*\n((?:^[ \t]+\S.*\n?)*)", text, re.MULTILINE)
    assert m, "could not find the on: trigger block"
    on_block = m.group(1)
    assert "pull_request" in on_block
    assert "push" not in on_block, (
        "lint.yml must trigger on pull_request only -- a push trigger "
        "duplicates every PR run for the same commit (#4)"
    )


def test_release_workflow_untouched_by_the_lint_trigger_change():
    text = _read(RELEASE_WORKFLOW)
    assert "workflow_dispatch" in text


def test_release_workflow_sends_a_changelog_field():
    text = _read(RELEASE_WORKFLOW)
    assert "changelog" in text
    assert "gh release view" in text  # reads back the notes already generated, never a second computation


def test_release_workflow_builds_the_dispatch_payload_with_jq_not_a_bare_heredoc():
    # The payload builder moved out of release.yml's inline YAML into its
    # own script (agent-ticket-orchestrator#15) -- follow it there instead
    # of asserting on inline `jq -n`, which the dispatch step no longer
    # contains.
    workflow_text = _read(RELEASE_WORKFLOW)
    dispatch_step = workflow_text.split("Dispatch to agent-marketplace", 1)[1]
    assert "marketplace-payload.sh" in dispatch_step
    # the old unquoted `-d @- <<EOF ... ${VAR} ...` pattern must be gone for
    # the dispatch step specifically -- a multi-line changelog would break it.
    # A real heredoc use is an unindented `<<EOF` starting a shell line, not
    # this pattern mentioned in an explanatory comment (`# ... <<EOF ...`).
    assert not re.search(r"^\s*[^#\n]*<<EOF", dispatch_step, re.MULTILINE)
    assert '-d "$PAYLOAD"' in dispatch_step

    script_text = _read(MARKETPLACE_PAYLOAD_SCRIPT)
    assert "jq -n" in script_text
    assert not re.search(r"^\s*[^#\n]*<<EOF", script_text, re.MULTILINE)


# --- B1: inter-ticket dependencies (agent-ticket-orchestrator#10) ----------

def test_bundler_schema_carries_depends_on():
    text = _read(BUNDLER)
    assert '"depends_on"' in text
    assert '"evidence"' in text


def test_bundler_distinguishes_collision_from_dependency():
    text = _read(BUNDLER)
    assert "collision" in text.lower()
    assert "dependency" in text.lower()
    assert "disjoint" in text.lower()


def test_bundler_never_emits_a_dependency_without_evidence():
    text = _read(BUNDLER)
    assert "without `evidence`" in text or "without evidence" in text.lower()


def test_clarifier_frame_block_carries_depends_on():
    text = _read(CLARIFIER)
    assert "clarifier:frame" in text
    assert "depends_on:" in text


def test_gatekeeper_writes_blocked_by_from_the_dependent_side():
    text = _read(GATEKEEPER)
    assert "add_relation" in text
    assert "blocked_by" in text
    assert "list_relation_kinds" in text


def test_gatekeeper_documents_the_gitlab_fallback():
    text = _read(GATEKEEPER)
    assert "relates_to" in text
    assert "gitlab" in text.lower()
    assert "gatekeeper:deps" in text


def test_gatekeeper_lifts_dependencies_to_the_package_ticket():
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 3.5", "## Step 3.6")
    assert "list_hierarchy" in section
    assert "package map" in section


def test_blocked_package_still_reaches_planned():
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 3.5", "## Step 3.6")
    assert "withholds a package from Planned" in section
    assert "Planned" in section


def test_gatekeeper_never_writes_a_relation_from_the_child_side():
    text = _read(GATEKEEPER)
    assert "child side" in text.lower()


def test_run_reads_relations_before_dispatch():
    text = _read(RUN)
    section = _slice(text, "### 1a. Order Todo by dependency", "### 2. Per package, sequentially")
    assert "include_relations=True" in section
    assert "blocked_by" in section


# --- #56 R7: forward-compatible with #45's future Done-column removal ------
# #56 merges before #45 rewrites skills/run/SKILL.md to drop Done as a board
# column; the suite must stay green on today's text (Done still present) and
# on #45's eventual text (Done gone).
#
# R7b (this helper) has no fixture-based "survives #45" test: a synthetic
# fixture written by this ticket can only prove that the fixture's own tokens
# satisfy the check, never that the check verifies actual meaning in prose
# #45 (a different, not-yet-run package) has not written yet -- test-critic
# flagged that self-referential tautology three rounds running
# (.adev/56-1/test-critic-3/critique-merged.json). R7's own acceptance
# criterion only asks that this test expect `closed` instead of a `Done`
# column, which `test_run_defines_resolved_as_closed` below already does
# against the real file; the negative-case tests below it exercise the
# helper's ability to actually reject, which is what stands in for RED/GREEN
# evidence here since #56 does not change SKILL.md itself.

def _assert_blocker_resolved_by_closed(text: str) -> None:
    """(#56 R7b round 5, test-critic round 4) A bare `\\bclosed\\b` +
    substring check cannot tell the required rule from its negation -- a
    section reading "resolved once Done; need not be closed" contains both
    tokens and would pass. Mirrors the structural-extraction technique
    `_assert_run_required_columns` and the `result (...)` capture in
    `test_run_report_vocabulary_drops_review_and_not_permitted` already use
    elsewhere in this file: pull the specific enumerated closed-condition
    (list item 3 of the "resolved when any of" list) out with a targeted
    regex and require the declarative `#b is status: closed` form inside it,
    not just the word anywhere in the section. `Closes #<n>` stays a plain
    substring check -- it is the secondary PR-linking mention, not the
    condition under test, and has no plausible negated phrasing to guard
    against."""
    assert "### When is a blocker resolved" in text
    section = _slice(text, "### When is a blocker resolved", "### 2. Per package, sequentially")
    m = re.search(r"\n\s*3\.\s+(.*?)\n\s*\n", section, re.S)
    assert m, "the enumerated closed-condition (list item 3) must be present"
    item3 = " ".join(m.group(1).split())
    assert re.search(r"`#b`\s+is\s+`status:\s*closed`", item3), item3
    assert "Closes #<n>" in section


def test_run_defines_resolved_as_closed():
    _assert_blocker_resolved_by_closed(_read(RUN))


def test_assert_blocker_resolved_by_closed_rejects_text_missing_closed():
    """(#56 R7b, test-critic F1; tightened round 5, test-critic round 4) The
    helper must actually be able to reject, not just accept -- a section
    with the right heading, a proper enumerated list and a `Closes #<n>`
    reference, but whose item 3 never states the declarative `#b is
    status: closed` form, states a different resolution rule and must fail,
    proving the helper is not `pass`-shaped."""
    bad_text = (
        "### When is a blocker resolved\n\n"
        "A blocker `#b` counts as resolved when any of:\n\n"
        "1. Something.\n\n"
        "2. Something else.\n\n"
        "3. `#b` counts as resolved once `run` itself decides it no longer\n"
        "   matters, regardless of its board state. See Closes #<n> for the\n"
        "   unrelated PR-linking convention.\n\n"
        "### 2. Per package, sequentially\n"
    )
    with pytest.raises(AssertionError):
        _assert_blocker_resolved_by_closed(bad_text)


def test_assert_blocker_resolved_by_closed_rejects_missing_heading():
    """(#56 R7b, test-critic F1) Text that never states the required heading
    at all -- even though 'closed' and 'Closes #<n>' both appear somewhere --
    must be rejected, since the heading is what scopes the rule to the right
    section."""
    bad_text = (
        "### Some unrelated heading\n\n"
        "This ticket is closed. Closes #<n>.\n\n"
        "### 2. Per package, sequentially\n"
    )
    with pytest.raises(AssertionError):
        _assert_blocker_resolved_by_closed(bad_text)


def test_assert_blocker_resolved_by_closed_rejects_missing_closes_reference():
    """(#56 R7b, test-critic round 2 F1; tightened round 5, test-critic round
    4) Text with the right heading and a proper item 3 stating the
    declarative `#b is status: closed` form, but no `Closes #<n>` reference
    anywhere in the section, states a resolution rule that never requires
    the PR-linking convention -- a helper that omits the `Closes #<n>`
    assertion would pass this fixture silently, so this case must fail on
    its own, and specifically not for the item-3 structural reason the two
    tests above already cover."""
    bad_text = (
        "### When is a blocker resolved\n\n"
        "A blocker `#b` counts as resolved when any of:\n\n"
        "1. Something.\n\n"
        "2. Something else.\n\n"
        "3. `#b` is `status: closed`, regardless of how it came to be\n"
        "   closed.\n\n"
        "### 2. Per package, sequentially\n"
    )
    with pytest.raises(AssertionError):
        _assert_blocker_resolved_by_closed(bad_text)


def test_assert_blocker_resolved_by_closed_rejects_negated_closed_condition():
    """(#56 R7b round 5, test-critic round 4) A section that states the
    *negation* of the closed condition ("need not be closed") still contains
    the bare word `closed` and a `Closes #<n>` reference, so it would pass
    the old `\\bclosed\\b` + substring check silently -- exactly the gap the
    test-critic flagged. The tightened helper must require the specific
    declarative `#b` is `status: closed` structural form, not just the bare
    word floating anywhere in the section."""
    bad_text = (
        "### When is a blocker resolved\n\n"
        "A blocker `#b` counts as resolved when any of:\n\n"
        "1. Something.\n\n"
        "2. Something else.\n\n"
        "3. `#b` need not be `status: closed` to count as resolved -- being\n"
        "   in the Done column already covers it. See Closes #<n> for the\n"
        "   unrelated PR-linking convention.\n\n"
        "### 2. Per package, sequentially\n"
    )
    with pytest.raises(AssertionError):
        _assert_blocker_resolved_by_closed(bad_text)


def test_run_orders_topologically_with_board_order_tiebreak():
    text = _read(RUN)
    section = _slice(text, "### 1a. Order Todo by dependency", "### When is a blocker resolved")
    assert "topolog" in section.lower()
    assert "board order" in section.lower()


def test_run_never_aborts_on_a_dependency_cycle():
    text = _read(RUN)
    section = _slice(text, "### 1a. Order Todo by dependency", "### When is a blocker resolved")
    assert "cycle" in section.lower()
    assert "abort" in section.lower()
    assert "STOP" not in section


def test_run_skips_rather_than_escalating_a_blocked_package():
    text = _read(RUN)
    section = _slice(text, "### 1a. Order Todo by dependency", "### When is a blocker resolved")
    assert "skipped:" in section
    assert "leave its card in **Todo**" in section or "do not move its card" in section


def test_run_rechecks_blockers_at_dispatch_time():
    text = _read(RUN)
    section = _slice(text, "**Re-check blockers at dispatch time.**", "**Gate on the previous package")
    assert "blocker" in section.lower()
    assert "ended in" in section


def test_run_reports_a_skipped_package_as_benign_partial():
    text = _read(RUN)
    section = _slice(text, "### 3. Final report", "## Waiting rule")
    assert "Skipped" in section
    assert "benign" in section.lower()


# --- B2: the problem frame (agent-ticket-orchestrator#11) ------------------

def test_clarifier_frame_section_precedes_everything_else():
    text = _read(CLARIFIER)
    assert text.index("### Frame") < text.index("### Resolved by reading") < text.index("### Open Questions")


def test_clarifier_frame_names_all_three_questions():
    text = _read(CLARIFIER)
    lowered = text.lower()
    assert "symptom" in lowered
    assert "measurement" in lowered
    assert "prior attempt" in lowered


def test_clarifier_status_line_contract_is_intact():
    text = _read(CLARIFIER)
    assert "STATUS: CLEAR" in text
    assert "STATUS: NEEDS_INPUT" in text
    assert "last line" in text.lower()
    assert "prefix" in text.lower()


def test_clarifier_writes_the_ac_instead_of_asking_for_it():
    """2026-08-29: an internal-only AC is repaired by the clarifier, not
    turned into a question — the frame block carries the written AC."""
    text = _read(CLARIFIER)
    assert "internal:" in text
    assert "ac: as-filed" in text
    section = _slice(text, "## When STATUS: CLEAR is not available", "## Worked frames")
    assert "you write the AC" in section
    assert "you reframe" in section
    assert "not CLEAR" in section  # the one remaining case: symptom cannot be named


def test_clarifier_has_the_five_question_filters():
    text = _read(CLARIFIER)
    section = _slice(text, "**3a. The question filter.**", "4. **Check readiness facts**")
    assert "literal reading" in section
    assert "scope" in section
    assert "reframe" in section.lower()
    assert "cost test" in section.lower()
    assert "without opening the code" in section


def test_clarifier_questions_carry_an_about_line():
    text = _read(CLARIFIER)
    assert "**About:**" in text
    assert "does not have the code open" in text


def test_clarifier_escape_hatch_exists_and_is_named():
    text = _read(CLARIFIER)
    section = _slice(text, "## When STATUS: CLEAR is not available", "## Worked frames")
    for category in ("refactor", "docs", "ci"):
        assert category in section.lower()


def test_clarifier_escape_hatch_is_closed_for_bug_tickets():
    text = _read(CLARIFIER)
    section = _slice(text, "## When STATUS: CLEAR is not available", "## Worked frames")
    assert "bug" in section.lower()
    assert "closed" in section.lower()


def test_clarifier_chain_detection_is_capped():
    text = _read(CLARIFIER)
    assert "updated_after" in text
    assert 'status="closed"' in text
    assert "at most two" in text.lower() or "at most TWO" in text


def test_clarifier_chain_rule_requires_two_of_three_signals():
    text = _read(CLARIFIER)
    assert "two of these three" in text.lower() or "two of" in text.lower()
    assert "same file" in text.lower()


def test_clarifier_keeps_list_tickets_in_its_tools():
    fm = _frontmatter(_read(CLARIFIER))
    tools = [t.strip() for t in fm.get("tools", "").split(",")]
    assert any("list_tickets" in t for t in tools)


def test_clarifier_ships_the_two_worked_frames():
    """The only executable form of ticket #11's acceptance criteria 3 and 4
    in this repo: there is no harness to drive a live clarifier against a
    fixture ticket in CI, so the worked examples live here as prompt content
    instead of under tests/, and this test only checks that they exist and
    state the outcome they claim to."""
    text = _read(CLARIFIER)
    section = _slice(text, "## Worked frames", "## Hard rules")
    assert "#148" in section
    assert "#90" in section
    assert "#156" in section
    assert "the reframe is applied and" in section
    assert "Zero questions" in section
    assert "STATUS: CLEAR" in section


def test_clarifier_never_rewrites_the_ticket():
    text = _read(CLARIFIER)
    assert "Read-only" in text


def test_gatekeeper_applies_the_regression_chain_label_and_comment():
    text = _read(GATEKEEPER)
    assert "regression-chain" in text
    assert "create_label" in text
    assert "Regression chain (gatekeeper)" in text


def test_gatekeeper_moves_an_asked_package_to_question_not_backlog():
    """2026-08-29: a package waiting on a human answer sits in Question, the
    one column that means "needs me" across projects, instead of vanishing
    into a Backlog of a hundred cards."""
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 3 — clarify each package", "## Step 3.5")
    assert "<native of Question>" in section
    assert "Leave the package in **Backlog**" not in section


def test_gatekeeper_reclaims_only_its_own_answered_question_cards():
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 1", "## Step 2")
    assert 'column="Question"' in section
    assert "adev:event" in section       # ownership test: never dispatched
    assert "newer" in section            # somebody answered
    hard = text[text.index("## Hard rules"):]
    assert "Never touch a Question card you did not put there" in hard


def test_gatekeeper_posts_the_frame_comment_when_the_ac_was_rewritten():
    text = _read(GATEKEEPER)
    assert "## Frame (gatekeeper)" in text
    assert "as-filed" in text
    assert "context-extractor" in text


RELEASED_HEADING = "## Released (gatekeeper)"


def _release_comment_call(step4: str):
    """The add_comment( call in Step 4 that carries RELEASED_HEADING: the
    heading sits inside the call's own parentheses, or in the fenced body
    block directly after it (the same call-then-body shape the frame comment
    uses; only whitespace/backticks may sit between). Returns (start,
    call_text, body) or None; a free-standing note elsewhere never matches."""
    heading = step4.find(RELEASED_HEADING)
    if heading < 0:
        return None
    for start, call in _call_spans(step4, "add_comment"):
        end = start + len(call)
        if start <= heading < end:
            body = step4[heading:end]
        elif end <= heading and re.fullmatch(r"[\s`]*", step4[end:heading]):
            close = step4.find("\n```", heading)
            body = step4[heading:close if close >= 0 else heading + 900]
        else:
            continue
        return start, call, body
    return None


def test_gatekeeper_confirms_a_cleared_package_on_the_ticket():
    """#28: on CLEAR the gatekeeper leaves a ticket-level trace of the
    clearance, not only a column move. The heading must belong to a Step 4
    add_comment( call, and its body names what was checked, that no open
    questions were found and the target column."""
    step4 = _slice(_read(GATEKEEPER), "## Step 4", "## Step 5")
    found = _release_comment_call(step4)
    assert found, (
        f"no add_comment( call in Step 4 carries {RELEASED_HEADING!r} "
        "(inside the call or in the body block directly after it)"
    )
    _, _, body = found
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    assert re.search(r"no open questions", body, re.IGNORECASE), (
        "comment body must state that no open questions were found"
    )

    def first(pattern):
        return next((i for i, l in enumerate(lines)
                     if re.search(pattern, l)), None)

    pkg = first(r"^\W*(Package|Ticket|Epic)\b")
    chk = first(r"^\W*Checked\b\W*\s*\S{3,}")
    mov = first(r"^\W*Moved:\s*\W*(Backlog|Question)\W*\s*(→|->)\s*\W*Planned")
    assert pkg is not None, "body lacks a Package/Ticket/Epic line"
    assert chk is not None, "body lacks a 'Checked <what>' line naming something"
    checked = lines[chk]
    for term, why in ((r"bundl", "bundling"), (r"Backlog", "the open Backlog"),
                      (r"clarif", "clarification"), (r"ticket", "the ticket"),
                      (r"comments", "comments"), (r"code", "code")):
        assert re.search(term, checked, re.IGNORECASE), (
            f"the Checked line must name {why} as something checked: {checked!r}"
        )
    assert mov is not None, "body lacks a 'Moved: <Backlog|Question> -> Planned' line"
    assert pkg < chk < mov, (
        "body lines must come in order: Package/..., Checked ..., Moved: ..."
    )

    # F4: the comment belongs to the CLEAR path -- Step 4 is entered from
    # STATUS: CLEAR, and the release call sits in an unconditional lead-in,
    # not under the (deliberately conditional) frame-comment trigger.
    text = _read(GATEKEEPER)
    step3 = _slice(text, "## Step 3 — clarify each package", "## Step 3.5")
    assert re.search(r"STATUS: CLEAR`?\s*→\s*go to Step 4", step3), (
        "Step 3 must route STATUS: CLEAR into Step 4"
    )
    assert re.match(r"## Step 4[^\n]*\n+\s*On CLEAR", step4), (
        "Step 4 must be described as entered on CLEAR"
    )
    start = found[0]
    lead = re.sub(r"[\s`]+$", "", step4[:start])
    lead = lead.rsplit("\n\n", 1)[-1]
    assert not re.search(r"\b(if|only if|unless|when|whenever|epic)\b", lead,
                         re.IGNORECASE), (
        "the release comment's lead-in must not gate it behind a condition: "
        f"{lead!r}"
    )
    assert "`ac:`" not in lead and "premise" not in lead.lower(), (
        "the release comment must not hang off the frame-comment trigger"
    )
    assert not re.search(r"\b(if|only if|unless|whenever)\b", body,
                         re.IGNORECASE), (
        "the release comment body must not be conditional"
    )


def test_gatekeeper_release_comment_follows_the_planned_move():
    """The comment asserts the move happened, so the add_comment( call that
    carries the release heading comes after the Planned update_ticket; and
    Step 1's ownership test gains no release-comment clause at all."""
    text = _read(GATEKEEPER)
    step4 = _slice(text, "## Step 4", "## Step 5")
    found = _release_comment_call(step4)
    assert found, f"no add_comment( call in Step 4 carries {RELEASED_HEADING!r}"
    call_pos = found[0]
    move = [p for p, c in _call_spans(step4, "update_ticket")
            if "native of Planned" in c]
    assert move, "Planned update_ticket not found in Step 4"
    assert move[0] < call_pos, (
        "the release-heading add_comment( call must come after the Planned "
        "update_ticket( call"
    )
    step1 = _slice(text, "## Step 1", "## Step 2")
    assert "adev:event" in step1 and "newer" in step1
    signal3 = ("at least one comment is **newer** than your latest "
               "clarification comment — somebody answered.")
    assert signal3 in re.sub(r"\s+", " ", step1), (
        "Step 1 ownership signal 3 wording must stay byte-identical"
    )
    assert "Released" not in step1 and not re.search(
        r"releas\w*[\s-]+(confirmation|comment)", step1, re.IGNORECASE), (
        "Step 1's ownership test must need no exclusion clause for the "
        "release comment: no mention of Released / release confirmation"
    )


def test_gatekeeper_release_comment_is_named_in_the_write_list():
    hard = _read(GATEKEEPER)
    hard = hard[hard.index("## Hard rules"):]
    rule = _slice(hard, "- **Never edit code", "- **Never close")
    writes = _slice(rule, "Your writes are", "moves")
    assert re.search(r"release[- ]confirmation", writes, re.IGNORECASE), (
        "Hard-rules write enumeration does not name release-confirmation comments"
    )
    assert "frame comments" in writes
    agents = _read(AGENTS_MD)
    row = next(l for l in agents.splitlines() if l.startswith("| `gatekeeper` |"))
    writes_cell = row.rstrip().rstrip("|").split("|")[-1]
    assert "frame" in writes_cell
    assert re.search(r"release[- ]confirmation", writes_cell, re.IGNORECASE), (
        "AGENTS.md gatekeeper row's writes cell does not name release-confirmation comments"
    )


def test_gatekeeper_chain_comment_states_the_reframe():
    text = _read(GATEKEEPER)
    assert "Implemented as:" in text
    assert "reframe and stay CLEAR" in text


def test_gatekeeper_chain_comment_is_written_once():
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 3.6", "## Step 4")
    assert "already exists" in section


def test_gatekeeper_report_names_symptom_and_measurement_per_package():
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 5", "## Hard rules")
    assert "symptom" in section
    assert "measurement" in section


def test_gatekeeper_parses_the_frame_block_but_never_aborts_on_it():
    text = _read(GATEKEEPER)
    assert "clarifier:frame" in text
    assert "frame block missing" in text


def test_agents_md_documents_both_new_mechanisms():
    text = _read(AGENTS_MD)
    assert "### Dependencies are relations" in text
    assert "### The frame comes before the questions" in text
    assert "### Release notes are generated from `main` via `src/*` markers" in text


# --- C1: the written-AC evidence rule (agent-ticket-orchestrator#16) -------
#
# Package #19 closes the frame contract's remaining gap at three points on
# one ticket's life: the clarifier writes an AC when a ticket has *no*
# acceptance section at all (distinct from the 2026-08-29 case already
# covered above, where a section exists but measures an internal quantity)
# and records every unverifiable premise the plan rests on; the new `ticket`
# skill and the GitHub issue forms produce the same shape up front. None of
# the target files/sections these tests read exist yet -- RED here is
# FileNotFoundError or "substring not found", not an import error.

def test_clarifier_writes_the_ac_when_none_was_filed():
    text = _read(CLARIFIER)
    section = _slice(text, "## When STATUS: CLEAR is not available", "## Worked frames")

    # F5-followup: bind `acceptance_criteria` to the actual detection
    # condition (missing/empty/no acceptance section) via proximity -- a
    # bare presence check could be satisfied by an incidental mention of
    # the field elsewhere in this (large) section.
    _assert_near(
        section, "acceptance_criteria",
        re.compile(r"no acceptance section|missing|empty", re.IGNORECASE),
        window=200,
        msg="the no-AC-filed detection must check the structured "
            "`ticket.acceptance_criteria` field near the detection "
            "condition itself (missing/empty/no acceptance section), not "
            "merely mention the field somewhere in the section "
            "(plan-critic finding on ticket #16)",
    )
    lowered = section.lower()
    assert "no acceptance section" in lowered
    assert "explicit acceptance section" in lowered
    assert "as-filed" in section

    # F5: five unjoined token presences don't constrain the branch->outcome
    # mapping -- bind each trigger phrase to its actual outcome instead.
    _assert_near(
        section, "no acceptance section", "you write the ac",
        msg="the 'no acceptance section' branch must map to 'you write "
            "the AC' near it, not merely have both phrases appear "
            "somewhere in the section",
    )
    _assert_near(
        section, "explicit acceptance section", "as-filed",
        msg="the 'explicit acceptance section' branch must map to "
            "'as-filed' near it, not merely have both phrases appear "
            "somewhere in the section",
    )


def test_written_ac_requires_real_call_evidence():
    text = _read(CLARIFIER)
    section = _slice(text, "## When STATUS: CLEAR is not available", "## Worked frames")
    lowered = section.lower()
    assert "real call" in lowered
    assert "real component" in lowered
    assert "in the state" in lowered

    # F6: "prose" and "in the state" are ordinary words that can appear
    # independently; require the exclusion to sit next to the verdict it
    # excludes, and the verdict to sit next to the rule it is an exception
    # to.
    _assert_near(
        section, "prose", "does not satisfy",
        msg="the prose/documentation exclusion must sit next to the "
            "'does not satisfy' verdict, not just appear somewhere in the "
            "section",
    )
    _assert_near(
        section, "does not satisfy", "real call",
        window=300,
        msg="'does not satisfy' must be stated in the same breath as the "
            "real-call evidence rule it is an exception to",
    )


def test_frame_block_carries_repeatable_premise():
    text = _read(CLARIFIER)
    section = _slice(
        text,
        "## Output format (load-bearing — the gatekeeper parses the last line)",
        "**The human who answers does not have the code open.**",
    )
    assert "premise:" in section

    # F7: bind the repeatability claim to the `premise` key specifically --
    # "more than once"/"accumulat" floating anywhere in this (large) section
    # proves nothing about the premise key on its own. The generic
    # unknown-key-tolerance disjuncts from the old version are dropped: the
    # plan does not specify particular wording for that, so asserting on it
    # was asserting on nothing in particular.
    _assert_near(
        section, "premise",
        re.compile(r"more than once|\brepeats?\b|accumulat", re.IGNORECASE),
        msg="the frame block's `premise:` key must be documented as "
            "repeatable ('more than once'/'repeat'/'accumulat...') near "
            "the key itself, not have that language float free elsewhere "
            "in the section",
    )


def test_gatekeeper_renders_premises_in_both_comments():
    text = _read(GATEKEEPER)
    step3 = _slice(text, "## Step 3", "## Step 3.5")
    step36 = _slice(text, "## Step 3.6", "## Step 4")
    step4 = _slice(text, "## Step 4", "## Step 5")

    rendering_prefix = "Premises to verify before planning:"

    # F2: the exact rendering shape (with the trailing colon this test
    # previously ignored), AND Step 3 -- not 3.6, not 4 -- is where the rule
    # itself is defined once; 3.6/4 reuse it rather than re-defining it.
    assert rendering_prefix in step3, (
        "Step 3 must define the premise-rendering rule once, as the "
        "canonical point, rather than leaving 3.6 and 4 to each invent "
        "their own rendering"
    )
    assert rendering_prefix in step36
    assert rendering_prefix in step4

    # F2b: bare prefix presence doesn't constrain the multi-premise
    # separator shape -- require Step 3's template to actually show how
    # MULTIPLE premises are rendered (a separator, or an explicit
    # one-line-per-premise note), not just the bare prefix followed by
    # arbitrary prose.
    prefix_idx = step3.index(rendering_prefix)
    rendering_window = step3[prefix_idx: prefix_idx + 300]
    assert (
        "; " in rendering_window
        or "one line per premise" in rendering_window.lower()
        or re.search(
            r"<p1>.*<p2>|premise 1.*premise 2",
            rendering_window, re.IGNORECASE | re.DOTALL,
        )
    ), (
        "Step 3's premise-rendering template must show how multiple "
        "premises are joined/separated (e.g. '; ' or a 'one line per "
        "premise' note), not just the bare prefix -- otherwise a "
        "single-premise-only template would satisfy this just as well"
    )

    # F2c: Step 3.6 and Step 4 must REUSE Step 3's rule, not each redefine
    # it from scratch -- require a "Step 3" reference near their own
    # premises-rendering line.
    for name, section in (("Step 3.6", step36), ("Step 4", step4)):
        idx = section.index(rendering_prefix)
        window = section[max(0, idx - 200): idx + 200]
        assert "step 3" in window.lower(), (
            f"{name} must reference Step 3 near its premises-rendering "
            "line, reusing the rule defined there rather than "
            "re-defining it independently"
        )


def test_gatekeeper_posts_frame_comment_on_either_condition():
    text = _read(GATEKEEPER)
    step4 = _slice(text, "## Step 4", "## Step 5")
    ac_trigger = "`ac:` is anything other than `as-filed`"
    assert ac_trigger in step4

    # F1: "premise" must appear as an independent trigger condition -- a
    # negation of `none` -- not merely be mentioned in passing; "or" alone
    # matches any English prose and "premise" is already guaranteed present
    # by another test in this slice, so neither constrained anything.
    premise_trigger = re.search(
        r"premise[^.\n]{0,80}(?:!=|is not|other than)\s*`?none`?",
        step4, re.IGNORECASE,
    )
    assert premise_trigger, (
        "Step 4 must state a `premise != none` trigger as an independent "
        "condition for posting the frame comment, not merely mention "
        "'premise' incidentally"
    )

    # the two conditions must be joined as alternatives ("posted on
    # EITHER"), not just both happen to be true statements elsewhere in
    # Step 4.
    lo, hi = sorted([step4.index(ac_trigger), premise_trigger.start()])
    between = step4[lo:hi]

    # F1-followup: require the literal word "either" as a tight disjunction
    # anchor -- an incidental "or" elsewhere between the two triggers is not
    # enough, since ordinary prose between two mentioned conditions almost
    # always contains an "or" somewhere without actually joining them as
    # alternatives. Also refuse a gating/AND word sitting between them,
    # which would mean the two conditions are joined as a conjunction (or a
    # conditional), not alternatives.
    assert re.search(r"\beither\b", between, re.IGNORECASE), (
        "expected the ac-trigger and premise-trigger to be joined by the "
        "literal word 'either', not merely an incidental 'or' somewhere "
        "in the prose between them"
    )
    for negator in ("unless", "only if", " and "):
        assert negator not in between.lower(), (
            f"found {negator!r} between the ac-trigger and premise-trigger "
            "-- expected a clean 'either ... or' disjunction, not a "
            "gated/AND-joined condition"
        )


def test_clarifier_ships_the_missing_ac_worked_frame():
    text = _read(CLARIFIER)
    section = _slice(text, "## Worked frames", "## Hard rules")

    # F3/F4: scope tightly to the #20 example's own text block, bounded by
    # its own start marker and the next worked-example bullet (or the
    # section end) -- otherwise a pre-existing worked frame (e.g. the #148
    # shape, which already contains "STATUS: CLEAR" and "#1"-shaped tokens)
    # could satisfy these assertions on its own.
    start_marker = "agent-web-tester#20"
    assert start_marker in section, "no agent-web-tester#20 worked example found"
    start = section.index(start_marker)
    rest = section[start + len(start_marker):]
    next_bullet = re.search(r"\n- \*\*", rest)
    end = start + len(start_marker) + next_bullet.start() if next_bullet else len(section)
    example = section[start:end]

    assert "browser_install" in example
    assert "premise:" in example, (
        "the #20 worked example must actually show a `premise:` line in "
        "its own text, not merely exist somewhere in the file"
    )
    assert "STATUS: CLEAR" in example
    assert "Clarification needed (gatekeeper)" in example
    # MAJOR-followup: a bare "#1" substring check matches #16/#18/#19 (and
    # any other #1x reference) -- require a delimited "#1" reference (not
    # immediately followed by another digit).
    assert re.search(r"#1(?!\d)", example), (
        "the example must show the premise inherited from #1's "
        "clarification as a delimited '#1' reference, scoped to the #20 "
        "example itself -- not merely contain '#1' as a substring of "
        "#16/#18/#19"
    )


# --- C2: the `ticket` skill (agent-ticket-orchestrator#17) -----------------

def test_ticket_skill_exists_and_is_user_invocable():
    fm = _frontmatter(_read(TICKET_SKILL))
    assert fm.get("name") == "ticket"
    assert fm.get("disable-model-invocation") == "true"


def test_ticket_skill_body_headings_are_exactly_the_five():
    text = _read(TICKET_SKILL)
    fenced_blocks = re.findall(r"```(?:[a-zA-Z]*)\n(.*?)```", text, re.DOTALL)
    template_headings: set = set()
    for block in fenced_blocks:
        template_headings.update(re.findall(r"^## (.+)$", block, re.MULTILINE))
    assert template_headings == {
        "Problem",
        "Acceptance",
        "Prior attempts",
        "Suggested fix",
        "Non-goals",
    }


def test_ticket_skill_files_one_ticket_with_label_discipline():
    text = _read(TICKET_SKILL)
    # F17: "one create_ticket" was checked as mere presence, not
    # cardinality -- count call-shaped occurrences and require exactly one.
    call_count = len(re.findall(r"create_ticket\(", text))
    assert call_count == 1, (
        f"expected exactly one create_ticket( call in the skill, found "
        f"{call_count}"
    )
    assert "list_labels" in text
    assert "create_label" not in text
    assert "template=" not in text
    assert "list_ticket_templates" not in text
    # MINOR-followup: two more prohibitions the skill must respect (files
    # exactly one ticket, no relation, no custom board-column write) were
    # never actually asserted absent.
    assert "add_relation" not in text
    assert "custom_fields" not in text
    lowered = text.lower()
    for symptom in ("hang", "crash", "wrong result", "slow", "leak"):
        assert symptom in lowered
    assert "never edits code" in lowered
    assert "never creates an epic" in lowered
    assert "never moves a card" in lowered


def test_ticket_skill_resolves_project_id_like_its_siblings():
    text = _read(TICKET_SKILL)
    assert "git remote get-url origin" in text
    assert "project_id=" in text
    # F18: the mechanism must be described in one place, not as two
    # independently-appearing tokens.
    _assert_near(
        text, "git remote get-url origin", "project_id=",
        window=250,
        msg="the project-id resolution mechanism must be described in one "
            "place -- 'git remote get-url origin' and 'project_id=' must "
            "appear near each other, not scattered independently",
    )


def test_ticket_skill_asks_the_three_frame_questions_with_content():
    text = _read(TICKET_SKILL)
    lowered = text.lower()

    # F15: no assertion previously mentioned AskUserQuestion, a question,
    # or the symptom/measurement/prior-attempts triple structurally.
    assert "AskUserQuestion" in text
    for topic in ("symptom", "measurement", "prior attempt"):
        assert topic in lowered

    # F16: the none:<category> escape hatch must be the literal delimited
    # pattern naming all seven categories together -- a SKILL.md that
    # merely uses the English words "ci"/"test"/"docs"/"prose" incidentally
    # elsewhere must not satisfy this.
    hatch_pattern = re.compile(
        r"none:<(?:refactor|docs|ci|infra|test|chore|prose)"
        r"(?:\|(?:refactor|docs|ci|infra|test|chore|prose)){6}>"
    )
    assert hatch_pattern.search(text), (
        "expected the literal `none:<cat1|cat2|...>` escape-hatch pattern "
        "naming all seven categories together, matching the clarifier's "
        "own vocabulary -- not incidental word matches"
    )

    assert "real call" in lowered
    assert "prose" in lowered
    assert 'status="closed"' in text
    assert "search=" in text
    assert "list_tickets" in text
    assert "why the symptom survived" in lowered

    # CRITICAL-followup: a bare \burl\b / "clear" / "open question" search
    # over the WHOLE file can't fail -- \burl\b is already satisfied
    # elsewhere by "git remote get-url origin" (which contains the word
    # "url"), and "clear"/"open question" match unrelated prose ("state the
    # symptom clearly", "leave no open question"). Scope this to a real
    # "Output"/"When you're done" section, then check for a specific
    # "ticket's URL" phrase (not the bare word "url", which the git-remote
    # line already satisfies) and a CLEAR-or-open-question line within that
    # section specifically.
    output_match = re.search(r"##\s*(output|when you'?re done)", text, re.IGNORECASE)
    assert output_match, (
        "expected an 'Output' or \"When you're done\" section describing "
        "what the skill's final message contains"
    )
    output_section = text[output_match.start():]
    next_heading = re.search(r"\n#{1,3} ", output_section[1:])
    if next_heading:
        output_section = output_section[:next_heading.start() + 1]
    output_lower = output_section.lower()

    assert re.search(r"ticket'?s? url|url of the (?:filed |new )?ticket", output_lower), (
        "expected the Output section to name the ticket's URL specifically "
        "-- not the bare word 'url', which is already satisfied elsewhere "
        "in the file by the unrelated 'git remote get-url origin' line"
    )
    assert "clear" in output_lower and re.search(r"open (?:frame )?question", output_lower), (
        "expected the Output section to predict either CLEAR or name a "
        "specific open frame question"
    )


# --- C3: AGENTS.md / README record the new mechanisms ----------------------

def test_agents_md_scopes_askuserquestion_to_the_unattended_skills():
    text = _read(AGENTS_MD)
    section = _slice(text, "### Two skills, neither blocks on `AskUserQuestion`", "### Board model")

    # F12: word-boundary-safe -- "run" must not be satisfied by "running",
    # "ticket" must not be satisfied by "tickets" -- and each name must
    # appear near an actual discussion of AskUserQuestion, not just
    # anywhere in the section.
    for name in (r"\bgatekeeper\b", r"\brun\b", r"\bticket\b"):
        matches = list(re.finditer(name, section, re.IGNORECASE))
        assert matches, f"{name!r} never appears in the AskUserQuestion-scoping section"
        assert any(
            "askuserquestion" in section[max(0, m.start() - 200): m.end() + 200].lower()
            for m in matches
        ), f"{name!r} appears but never near a discussion of AskUserQuestion"

    # the old, now-false, unscoped claim must be genuinely gone -- check
    # common paraphrases too, not only the exact original sentence.
    old_claim_paraphrases = (
        "not part of this plugin at all",
        "askuserquestion is not part of this plugin",
        "not used anywhere in this plugin",
        "no skill here uses askuserquestion",
    )
    lowered = section.lower()
    for phrase in old_claim_paraphrases:
        assert phrase not in lowered, f"old unscoped claim survives as: {phrase!r}"

    # a new, scoped statement must exist: gatekeeper/run specifically are
    # named as where AskUserQuestion is forbidden.
    # MINOR-followup: (gatekeeper|run) here lacked word boundaries too,
    # matching "running"/"rerun" -- add \b so only the actual skill names
    # count.
    assert re.search(
        r"(forbidden|never granted|not (?:used|available|granted))"
        r".{0,120}\b(gatekeeper|run)\b"
        r"|\b(gatekeeper|run)\b.{0,120}"
        r"(forbidden|never granted|not (?:used|available|granted))",
        section, re.IGNORECASE | re.DOTALL,
    ), (
        "expected a new statement scoping the AskUserQuestion ban to "
        "gatekeeper/run specifically, not the removed blanket claim"
    )


def test_agents_md_records_the_written_ac_and_the_forms():
    text = _read(AGENTS_MD)

    # F13: scope the written-AC rationale to its actual home section --
    # a whole-file substring search could be satisfied by an unrelated
    # mention elsewhere.
    frame_section = _slice(
        text,
        "### The frame comes before the questions",
        "### Why state lives in the ticket, not in the return value",
    )
    assert "no acceptance section" in frame_section.lower(), (
        "the written-AC-when-none-was-filed rationale belongs in "
        "'The frame comes before the questions' section"
    )

    # F13: the forms-rationale check requires BOTH the clarifier's heading
    # vocabulary AND templates/ as a release artifact, not either alone --
    # an ISSUE_TEMPLATE mention with no vocabulary link proves nothing
    # about the actual contract between the forms and the clarifier. Four
    # independent whole-file token searches don't constrain that they are
    # discussed together -- bind them via proximity instead.
    assert "skills/ticket" in text or "`ticket`" in text
    assert "ISSUE_TEMPLATE" in text
    _assert_near(
        text, "heading vocabulary", "templates/",
        window=500,
        msg="the forms-rationale must state the heading-vocabulary link "
            "near the templates/-as-release-artifact fact, not as two "
            "independent whole-file mentions",
    )
    _assert_near(
        text, "templates/", "release artifact",
        window=200,
        msg="'templates/' and 'release artifact' must be discussed near "
            "each other",
    )


def test_readme_documents_the_ticket_skill():
    text = _read(README)
    assert "/agent-ticket-orchestrator:ticket" in text

    # MINOR-followup: a bare standalone-token check passes on the
    # invocation string appearing anywhere, e.g. in a terse command index
    # with no description. Bind it to descriptive words about what the
    # skill actually does -- excluding "ticket" itself, since that word is
    # already a substring of "agent-ticket-orchestrator" and would
    # trivially self-satisfy any proximity check.
    _assert_near(
        text, "/agent-ticket-orchestrator:ticket",
        re.compile(r"\bfile\b|\bsymptom\b|\bframe\b", re.IGNORECASE),
        window=400,
        msg="the invocation string must sit near descriptive words about "
            "the skill (file/symptom/frame), not stand alone as a bare "
            "token",
    )


def test_readme_form_adoption_covers_copy_config_and_both_audiences():
    text = _read(README)
    section = _slice(text, "## Adopting the forms in a project", "## Install")
    assert ".github/ISSUE_TEMPLATE" in section
    assert "tickets.templates: enforce" in section
    assert "create_ticket" in section

    refusal_verbs = ("refuse", "reject", "declin")
    assert any(v in section.lower() for v in refusal_verbs), (
        "expected the agent-audience half: create_ticket refuses a ticket "
        "that skips the required headings"
    )

    # F14: the old test only checked the agent-refusal half of "both
    # audiences" -- add the human/web-form half: what a person filing via
    # the GitHub UI sees. A bare "web"/"form" match is satisfied by almost
    # any prose about the templates themselves (e.g. "the form fields are
    # YAML"), so bind it to human-audience language specifically.
    _assert_near(
        section,
        re.compile(r"\bweb\b|\bform\b", re.IGNORECASE),
        re.compile(r"\bhuman\b|\bfiling\b|\bpresents?\b", re.IGNORECASE),
        window=200,
        msg="expected the human-audience half: 'web'/'form' co-occurring "
            "with 'human'/'filing'/'presents', describing what a person "
            "filing through the GitHub web UI sees -- not a generic "
            "'form' mention about the YAML templates themselves",
    )

    # F14: the "inert until agent-project-issues#307 ships" caveat must sit
    # near the tickets.templates: enforce line, not float free elsewhere.
    _assert_near(
        section, "tickets.templates: enforce", "#307",
        window=400,
        msg="the 'inert until agent-project-issues#307 ships' caveat must "
            "sit near the tickets.templates: enforce line",
    )


# --- C4: GitHub issue forms (agent-ticket-orchestrator#18) -----------------

def test_issue_forms_parse_and_declare_required_fields():
    vocab = _clarifier_heading_labels()
    # F8: the original nested this vocabulary check inside "if required",
    # so a form with nothing marked required passed vacuously. Assert the
    # expected fields are actually required FIRST, then check vocabulary
    # membership.
    expected_required = {
        "bug.yml": {"Problem", "Acceptance", "Prior attempts"},
        "feature.yml": {"Goal", "Acceptance"},
        "task.yml": {"Goal", "Acceptance"},
        "epic.yml": {"Children", "Rationale"},
    }
    for name, expected in expected_required.items():
        data = yaml.safe_load(_read(TEMPLATES / name))
        required_labels = set()
        for field in data.get("body", []):
            attrs = field.get("attributes", {})
            label = attrs.get("label")
            if label is None:
                continue
            if field.get("validations", {}).get("required"):
                required_labels.add(label)

        missing = expected - required_labels
        assert not missing, (
            f"{name}: expected {sorted(expected)} to be marked "
            f"`required: true`, but {sorted(missing)} were not -- a form "
            "with nothing required would vacuously pass a vocabulary-only "
            "check"
        )
        for label in required_labels:
            assert label in vocab, (
                f"{name}: required field {label!r} is not in the "
                "clarifier's heading vocabulary"
            )


def test_issue_form_labels_match_the_gatekeeper():
    bug = yaml.safe_load(_read(TEMPLATES / "bug.yml"))
    epic = yaml.safe_load(_read(TEMPLATES / "epic.yml"))
    task = yaml.safe_load(_read(TEMPLATES / "task.yml"))
    assert bug.get("labels") == ["bug"]
    assert epic.get("labels") == ["epic"]
    assert task.get("labels") == ["task"]


def test_epic_form_fields_match_the_gatekeeper_epic_body():
    epic = yaml.safe_load(_read(TEMPLATES / "epic.yml"))
    required_labels = {
        f["attributes"]["label"]
        for f in epic.get("body", [])
        if f.get("validations", {}).get("required")
    }
    assert required_labels == {"Children", "Rationale"}

    gk_text = _read(GATEKEEPER)
    section = _slice(
        gk_text,
        "### Materialise multi-ticket packages as epics",
        "## Step 3 — clarify each package",
    )
    assert "## Children" in section
    assert "## Rationale" in section


def test_issue_forms_carry_the_evidence_rule_in_field_descriptions():
    bug = yaml.safe_load(_read(TEMPLATES / "bug.yml"))
    task = yaml.safe_load(_read(TEMPLATES / "task.yml"))
    epic = yaml.safe_load(_read(TEMPLATES / "epic.yml"))  # F9: was never loaded

    def field_by_label(doc, label):
        for f in doc.get("body", []):
            if f.get("attributes", {}).get("label") == label:
                return f["attributes"]
        raise AssertionError(f"{doc}: no field labelled {label!r}")

    # F9: every required field across all three forms carries a non-empty
    # description -- a required field with no description leaves a
    # web-form filer with nothing to go on, and the old test never checked
    # for this at all.
    for name, doc in (("bug.yml", bug), ("task.yml", task), ("epic.yml", epic)):
        for f in doc.get("body", []):
            attrs = f.get("attributes", {})
            if f.get("validations", {}).get("required"):
                assert attrs.get("description", "").strip(), (
                    f"{name}: required field {attrs.get('label')!r} has no "
                    "description"
                )

    epic_children = field_by_label(epic, "Children")
    assert epic_children.get("description", "").strip()
    epic_rationale = field_by_label(epic, "Rationale")
    assert epic_rationale.get("description", "").strip()

    bug_acceptance = field_by_label(bug, "Acceptance")
    desc = bug_acceptance.get("description", "").lower()
    assert "does not satisfy" in desc or "does not count" in desc
    assert "prose" in desc or "documentation" in desc or "string literal" in desc
    assert "real call" in desc

    # F10: "observ" alone passes on "What did you observe?", which names
    # none of the three required things and never excludes an internal
    # quantity as the observation.
    bug_problem = field_by_label(bug, "Problem")
    problem_desc = bug_problem.get("description", "").lower()
    content_hits = sum(
        1 for token in ("call", "state", "outcome", "result")
        if token in problem_desc
    )
    assert content_hits >= 2, (
        "Problem description must name at least two of call/state/"
        f"outcome/result, got: {problem_desc!r}"
    )
    assert any(
        phrase in problem_desc
        for phrase in ("internal quantity", "counter", "thread count")
    ), (
        "Problem description must explicitly exclude an internal quantity "
        f"(counter/thread count) as the observation, got: {problem_desc!r}"
    )

    # F11: word-boundary-safe "none" (the old check matched "nonetheless"),
    # plus the specific "closed tickets" phrasing the plan requires.
    bug_prior = field_by_label(bug, "Prior attempts")
    prior_desc = bug_prior.get("description", "").lower()
    assert re.search(r"\bnone\b", prior_desc), (
        f"Prior attempts description must offer the literal word 'none', "
        f"got: {prior_desc!r}"
    )
    assert "closed ticket" in prior_desc

    # F11: task.yml's Acceptance must carry both the exclusion AND a
    # positive statement of what a reviewer can check for a non-runtime
    # task -- negation phrasing alone was the old, weaker check.
    task_acceptance = field_by_label(task, "Acceptance")
    task_desc = task_acceptance.get("description", "").lower()
    assert "does not satisfy" in task_desc or "does not count" in task_desc

    # MINOR-followup: a bare "check" match is satisfied by any incidental
    # use of the word (e.g. "check the box"). Require "check" to co-occur
    # with a concrete checkable noun -- diff/output/result/code -- so this
    # actually states what a reviewer can check, not just that checking
    # exists as a concept.
    assert re.search(r"\breviewer\b", task_desc), (
        "task.yml's Acceptance description must mention what a reviewer "
        f"does for a non-runtime task, got: {task_desc!r}"
    )
    assert re.search(
        r"check\w*\D{0,40}(diff|output|result|code)"
        r"|(diff|output|result|code)\D{0,40}check",
        task_desc,
    ), (
        "task.yml's Acceptance description must name a concrete checkable "
        f"thing (diff/output/result/code) near 'check', got: {task_desc!r}"
    )


# --- D1: #25 bundler ordering is not a collision; verified relation writes -
#
# A gatekeeper pass cut two large tickets -- one of which itself sequenced
# their overlap ("a second step after #9") -- into one collision epic, then
# wrote only one of the five dependency relations the bundler reported. This
# package (a) teaches the bundler that a ticket's own sequencing statement is
# `depends_on`, never `collision` (R1), ships the #9/#14 worked example (R2),
# caps a `collision` package at one `large` ticket (R3; the `recut` it once
# owed for an overlapping large pair the cap rejects became an `oversized`
# report and a Question in #41), (b) has the gatekeeper reject
# an oversized collision package into `single`s (R4), post the recut as a
# `## Frame (gatekeeper)` comment on both endpoints (R5), carry `previous_cut`
# across passes with a named-change gate (R6), and (c) makes Step 3.5's
# relation read-back mechanically verified via
# `scripts/gatekeeper/relation-readback.py` rather than prose bookkeeping
# (R7; the script's own driving tests live in tests/test_relation_readback.py).
# R8 (docs) and R9 (a live gatekeeper pass) are evidence kind `none` per the
# plan -- no driving test for either.
#
# Per this package's own test-design rule: a bare literal-presence assertion
# is permitted only for R2 (the "is the worked example shipped" check, this
# repo's accepted idiom) -- every other assertion here binds a trigger to its
# verdict via `_assert_near` plus an explicit negation or exact-set check
# that an inverted implementation would fail.

def test_bundler_sequencing_clause_is_depends_on_not_collision():
    text = _read(BUNDLER)
    section = _slice(text, "3. **Cut packages.**", "4. **Respect explicit structure.**")

    # F1 fix (test-critic round 1): the old checks proved "sequenc" is near
    # "depends_on" *somewhere* and "never"/"collision" co-occur *somewhere*
    # -- two independently-satisfiable facts that don't bind the sequencing
    # trigger to its own verdict. Anchor all three to the SAME sentence, so
    # a separate unrelated "never ... collision" sentence elsewhere in Step
    # 3 (e.g. "Never leave a collision unreported") can no longer stand in
    # for the actual sequencing-resolves-to-depends_on rule.
    sentences = re.split(r"\.\s+", section)
    sequencing_sentence = next(
        (s for s in sentences if re.search(r"sequenc", s, re.IGNORECASE)), None,
    )
    assert sequencing_sentence, (
        "expected a sentence introducing the sequencing clause (containing "
        "'sequenc...')"
    )
    assert "depends_on" in sequencing_sentence, (
        "expected the sequencing sentence itself to resolve to depends_on, "
        f"not just something nearby: {sequencing_sentence!r}"
    )
    assert re.search(
        r"never[^.]{0,150}\bcollision\b|\bcollision\b[^.]{0,150}never",
        sequencing_sentence, re.IGNORECASE,
    ), (
        "expected 'never' and 'collision' bound together within the "
        f"sequencing sentence itself: {sequencing_sentence!r}"
    )

    assert "is the other's precondition" not in section, (
        "the removed clause ('or one ticket's change is the other's "
        "precondition') must actually be gone, not just supplemented"
    )

    # F2 fix (test-critic round 2, major): the two checks above only prove
    # 'never' and 'collision' co-occur somewhere in the sentence -- for an
    # INVERSE rule ("never treat that as depends_on; record it as a
    # collision") 'never' negates depends_on, not collision, yet both checks
    # above still pass. And the old negative guard only matched the one
    # literal shape "sequenc... is (a) collision", missing any other
    # phrasing of the same inverse ("record it as a collision", "treat it as
    # a collision", "that counts as a collision"). Broadened: split the
    # sequencing sentence into clauses (on , / ;) and require every clause
    # that mentions 'collision' to carry its own negation word -- this binds
    # the negation specifically to collision, regardless of phrasing, rather
    # than matching one fixed regex shape.
    clauses = re.split(r"[,;]\s*", sequencing_sentence)
    for clause in clauses:
        if re.search(r"\bcollision\b", clause, re.IGNORECASE):
            assert re.search(
                r"\b(never|not|n't|isn't|doesn't|cannot|can't|won't|no longer)\b",
                clause, re.IGNORECASE,
            ), (
                "expected every clause mentioning 'collision' in the "
                f"sequencing sentence to carry its own negation, not just "
                f"co-occur with 'never' elsewhere in the sentence: {clause!r}"
            )

    # edge case: the pre-existing collision/dependency distinction paragraph
    # must survive this edit.
    assert "Collision and dependency are different findings" in text


def test_bundler_ships_the_worked_cut():
    """F2 fix (test-critic round 1, critical): the plan's own R2 test-scope
    description declares this requirement's evidence as presence-based --
    "the section must say `depends_on` and contain a line rejecting the
    `collision`/epic reading" -- not a polarity-bound behavioural check. The
    round-1 "not/never near collision/epic" regex reached past that scope
    and was satisfiable by a section that states the OPPOSITE verdict (e.g.
    "not the only case where a collision epic is right"). Narrowed here to
    exactly what the plan specifies: presence of the quotes, and presence of
    which verdict the section names -- nothing more. This is this package's
    one deliberately bare-presence test (the accepted
    test_clarifier_ships_the_two_worked_frames idiom), same as R2's own
    "bare presence is deliberate here and only here" note.

    F1 fix (test-critic round 2, critical): "presence of which verdict the
    section names" was stripped down too far in round 2 -- five bare
    presence checks, none of which requires the section actually reach the
    stated (`depends_on`) verdict rather than the opposite one (`collision` /
    one epic). A section reproducing all three quotes and then concluding
    "this IS a collision, bundle them as one epic" passed every assertion.
    Restored: a sentence that names BOTH `depends_on` and the collision/epic
    reading, with the collision/epic mention itself explicitly negated --
    this stays within R2's stated scope (presence + which verdict is named),
    it says nothing about HOW the bundler reaches that verdict (R1's job)."""
    text = _read(BUNDLER)
    section = _slice(text, "## Worked cuts", "## Hard rules")

    # F1 fix (test-critic round 4, critical): bare presence of '#9', '#14'
    # and the quote fragment ANYWHERE in the section (plus a separate bare
    # 'depends_on' presence check) lets those tokens satisfy the assertion
    # even when scattered across unrelated sentences, rather than forming
    # the described worked example. This stays within R2's accepted
    # "presence, not polarity" scope by tightening what "presence" means:
    # require the three quote tokens to appear bound together, in a single
    # paragraph (blank-line-delimited), as a coherent unit -- not merely
    # present somewhere in the whole section. The standalone bare
    # 'depends_on' check is dropped as redundant/weaker than the
    # verdict_sentence binding below, which already requires 'depends_on'
    # in a specific, polarity-checked sentence.
    paragraphs = section.split("\n\n")
    worked_para = next(
        (
            p for p in paragraphs
            if "#9" in p and "#14" in p and "a second step after #9" in p
        ),
        None,
    )
    assert worked_para, (
        "expected '#9', '#14' and the verbatim fragment 'a second step "
        "after #9' bound together in a single paragraph/quote-block, not "
        f"scattered across the section: {section[:400]!r}"
    )

    # which verdict it names -- bound with proximity + negation so a section
    # reaching the OPPOSITE verdict (bundle as one epic / collision) does not
    # pass by mere co-presence of the same words.
    sentences = re.split(r"\.\s+", section)
    verdict_sentence = next(
        (
            s for s in sentences
            if "depends_on" in s and re.search(r"\b(collision|epic)\b", s, re.IGNORECASE)
        ),
        None,
    )
    assert verdict_sentence, (
        "expected a sentence naming both the depends_on verdict and the "
        "collision/epic reading it names -- section: " + section[:400]
    )

    # F1 fix (test-critic round 4, major): a negation anywhere within 80
    # chars of collision/epic is also satisfied by a sentence stating the
    # OPPOSITE verdict, e.g. "The pair is not two singles with
    # `depends_on`; it is a `collision`, bundled as one epic." -- there the
    # negation attaches to depends_on, not to collision/epic, yet the old
    # regex still matched. Anchor per-clause instead: split the verdict
    # sentence on its own punctuation and require the depends_on clause to
    # carry NO negation of its own, while the collision/epic clause DOES.
    clauses = re.split(r"[,;:]\s*", verdict_sentence)
    depends_clause = next((c for c in clauses if "depends_on" in c), None)
    collision_clause = next(
        (c for c in clauses if re.search(r"\b(collision|epic)\b", c, re.IGNORECASE)), None,
    )
    assert depends_clause and collision_clause, (
        "expected depends_on and collision/epic to sit in distinguishable "
        f"clauses of the verdict sentence: {verdict_sentence!r}"
    )
    negation_re = re.compile(r"\b(not|never|instead of|rather than|no|n't)\b", re.IGNORECASE)
    assert not negation_re.search(depends_clause), (
        f"the depends_on clause must not itself be negated: {depends_clause!r}"
    )
    assert negation_re.search(collision_clause), (
        f"expected the collision/epic clause to carry its own negation: {collision_clause!r}"
    )


def test_bundler_schema_requires_size_and_caps_collision():
    text = _read(BUNDLER)
    # F8 fix (test-critic round 2, minor): a bare "does a ```json fence
    # exist" check is guaranteed true independent of this package --
    # agents/bundler.md already carries an output-format fence today. Bind
    # the extraction to the SPECIFIC schema block that carries the
    # "tickets" key, not just any fenced json block in the file.
    fenced_blocks = re.findall(r"```json\n(.*?)```", text, re.DOTALL)
    assert fenced_blocks, "no fenced JSON block found in agents/bundler.md"
    json_block = next((b for b in fenced_blocks if '"tickets"' in b), None)
    assert json_block, (
        "expected a fenced JSON block containing the 'tickets' schema key"
    )

    assert re.search(r'"tickets":\s*\[\s*\{[^}]*"size"', json_block), (
        "'tickets' must be an array of objects carrying 'size', not a bare "
        "array of ids"
    )
    # the `oversized` array (#41) names a pair by bare id, by design; the
    # rule guards the `packages` shape.
    packages_text = re.sub(r'"oversized":\s*\[.*?\n  \]', "", text, flags=re.DOTALL)
    assert not re.search(r'"tickets":\s*\[\s*<id>', packages_text), (
        "the old bare-id 'tickets': [<id>, ...] form must be gone -- exactly "
        "one 'tickets' shape after this change"
    )
    for size in ("small", "medium", "large"):
        assert f'"{size}"' in json_block, f"expected size enum value {size!r} in the schema"

    cap_sentence = (
        re.search(r"[^.\n]*\bcollision\b[^.\n]*\bat most one\b[^.\n]*\blarge\b[^.\n]*\.", text, re.IGNORECASE)
        or re.search(r"[^.\n]*\bat most one\b[^.\n]*\blarge\b[^.\n]*\bcollision\b[^.\n]*\.", text, re.IGNORECASE)
    )
    assert cap_sentence, (
        "expected a sentence naming 'collision', 'at most one', and 'large' "
        "together -- the cap rule"
    )

    # F2 fix (test-critic round 4, major): token co-occurrence alone is also
    # satisfied by a sentence that cancels its own cap in the same breath,
    # e.g. "at most one large ticket unless the overlap is worth the cost".
    # Guard against a conditional/exception word inside the cap sentence
    # itself.
    assert not re.search(r"\bunless\b|\bexcept\b", cap_sentence.group(0), re.IGNORECASE), (
        f"the cap sentence must not be conditioned away: {cap_sentence.group(0)!r}"
    )

    # polarity guard: the cap is on `collision` only -- a sentence stating
    # 'at most one ... large' must never also rope in `effort`, which keeps
    # its own independent ~5-ticket cap.
    for sentence in re.split(r"\.\s+", text):
        if "at most one" in sentence.lower():
            assert "effort" not in sentence.lower(), (
                f"the collision-only cap sentence must not mention 'effort': {sentence!r}"
            )

    # edge case: effort's own cap sentence must still be present, untouched.
    # F6 fix (test-critic round 4, minor): a bare 2-character substring
    # search for '~5' anywhere in the file is satisfied by any unrelated
    # '~5' (e.g. '~5 min' elsewhere) even if the actual effort cap sentence
    # were deleted. Bind '~5' to its own cap-sentence context -- near both
    # 'effort' and 'ticket' within a bounded window -- rather than a bare
    # substring search.
    _assert_near(
        text, "~5", "effort", window=100,
        msg="expected the effort ~5-ticket cap sentence's own context "
            "(near 'effort') to still be present, not just any '~5' "
            "elsewhere in the file",
    )
    _assert_near(
        text, "~5", "ticket", window=100,
        msg="expected the effort ~5-ticket cap sentence's own context "
            "(near 'ticket') to still be present, not just any '~5' "
            "elsewhere in the file",
    )


def test_bundler_reports_an_oversized_pair_instead_of_cutting_it():
    # #41: the declined overlapping two-`large` pair used to OWE a `recut` the
    # gatekeeper applied unconfirmed. It is now reported as `oversized`, with
    # a proposed vertical split, and nothing is cut.
    text = _read(BUNDLER)
    assert "recut" not in text.lower(), "the bundler no longer knows a recut at all"

    overlap = [
        s for s in re.split(r"\.\s+", text)
        if re.search(r"\btwo\b|\bboth\b", s, re.IGNORECASE)
        and "large" in s.lower() and "overlap" in s.lower() and "oversized" in s
    ]
    assert overlap, "expected a sentence binding two + large + overlap to `oversized`"
    assert re.search(r"\bmust\b", overlap[0]), overlap[0]
    assert not re.search(r"\bmust\s+not\b|\bnever\b|\boptional\b|\bmay\b", overlap[0], re.IGNORECASE), (
        f"the duty to report must not be softened or inverted: {overlap[0]!r}"
    )
    assert any(
        re.search(r"does not overlap|no overlap|non-overlap", s, re.IGNORECASE)
        and re.search(r"\bnothing\b|\bno\b", s.split("overlap", 1)[1], re.IGNORECASE)
        for s in re.split(r"\.\s+", text)
    ), "a non-overlapping declined pair owes nothing"

    fenced = re.findall(r"```json\n(.*?)```", text, re.DOTALL)
    block = next(b for b in fenced if '"packages"' in b)
    over = block.split('"oversized"', 1)
    assert len(over) == 2, "the output format must carry an `oversized` array"
    assert block.index('"packages"') < block.index('"oversized"')
    for key in ('"tickets"', '"why"', '"slices"', '"slice"', '"observable"', '"covers"'):
        assert key in over[1], f"{key} missing from the oversized entry"
    doc = next(p for p in text.split("\n\n") if p.startswith("`oversized` is"))
    assert re.search(r"\*\*optional\*\*", doc), doc


def test_bundler_slices_pass_the_observable_test_or_are_not_emitted():
    text = _read(BUNDLER)
    para = next(
        (p for p in text.split("\n\n") if "proposed vertical split" in p and "horizontal" in p), None
    )
    assert para, "the observable test must be stated in the bundler"
    flat = " ".join(para.split())
    assert re.search(r"user of the software", flat)
    horizontal = next(s for s in re.split(r"(?<=\.)\s+", flat) if "horizontal" in s)
    for shape in ("shared class", "extracted core", "refactor", "test harness", "CI step",
                  "next slice can now be built"):
        assert shape in horizontal, f"horizontal shape {shape!r} missing: {horizontal!r}"
    assert re.search(r"must never be emitted|must not be emitted", horizontal), horizontal
    assert re.search(r"stands alone", flat)

    fab = next((p for p in text.split("\n\n") if "Never fabricate a split" in p), None)
    assert fab, "the no-fabrication rule must be stated"
    fab = " ".join(fab.split())
    assert '"slices": []' in fab and re.search(r"at least two", fab), fab

    worked = _slice(text, "## Worked cuts", "## Hard rules")
    assert "RigMotionCore" in worked and "ci-green" in worked
    assert "unfinished" not in worked and "$70" not in worked, (
        "#16 finished ci-green on attempt 1; the old cost claim was wrong"
    )


def test_gatekeeper_turns_an_oversized_pair_into_one_question():
    text = _read(GATEKEEPER)
    sec = _slice(
        text, "### An oversized pair is a Question, not a cut",
        "From here on, *package ticket* means",
    )
    posts = _call_spans(sec, "add_comment")
    assert len(posts) == 1, "exactly one proposal comment is defined, on the lower id"
    body = posts[0][1]
    for tok in ("## Clarification needed (gatekeeper)", "**About:**", "**Decision:**",
                "(a)", "(b)", "(c)", "*(recommended)*",
                "<!-- gatekeeper:oversized v1", "pair:", "proposal_on:"):
        assert tok in body, f"the proposal comment must carry {tok!r}"
    assert re.search(r"lower ticket id", sec)
    flat = " ".join(sec.split())
    assert re.search(r'"slices": \[\], option \(a\) is absent', flat.replace("`", ""))

    reads = _call_spans(sec, "list_comments")
    assert reads and sec.index("list_comments(") < sec.index("add_comment("), (
        "the idempotency read precedes the post"
    )
    assert re.search(r"body_max_chars\s*=\s*\d+", reads[0][1])
    assert re.search(r"no comment newer[^.]*: post nothing and move nothing", flat), (
        "an unanswered question is neither re-posted nor re-moved"
    )

    moves = _call_spans(sec, "update_ticket")
    assert moves and all("Question" in m[1] and "Planned" not in m[1] for m in moves)
    assert re.search(r"\*\*Both cards go to Question\*\*", sec)
    assert re.search(r"[Nn]either is clarified and neither is released", flat)
    assert re.search(r"pointer|Point the other card", sec)
    assert "create_ticket" not in sec and "add_relation" not in sec

    step1 = _slice(text, "## Step 1", "## Step 2")
    assert "proposal_on:" in step1, "Step 1 must bring the pointer card back with its pair"

    json_block = _slice(_slice(text, "## Step 2", "## Step 3 — clarify"), "```json\n", "\n```")
    assert '"oversized"' in json_block and '"recut"' not in json_block


def test_gatekeeper_step_3_7_has_the_lane_split_as_its_only_source():
    text = _read(GATEKEEPER)
    first_para = _slice(text, "## Step 3.7", "## Step 4").split("\n\n", 1)[0]
    assert re.search(r"lane split is the only source", first_para), first_para
    assert re.search(r"bundler emits none", first_para), first_para
    assert "Step 3.4" not in first_para
    rules = text.split("## Hard rules", 1)[1]
    assert any(
        l.startswith("- **") and "size-driven cut" in l and "lane split" in l
        for l in rules.splitlines()
    ), "a Hard rule must forbid a size-driven cut and name the lane split's recut"


def test_agents_md_records_the_oversized_question_rule():
    text = _read(AGENTS_MD)
    assert "with a `recut` escape hatch" not in text
    assert "6+ hours" not in text, "the mis-recorded #16 cost sentence must be corrected"
    para = next(p for p in text.split("\n\n") if p.startswith("**A `collision` package is capped by size"))
    for tok in ("oversized", "observable", "Question", "gatekeeper:oversized"):
        assert tok in para, tok


def test_gatekeeper_rejects_oversized_collision_package():
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 2", "## Step 3 — clarify each package")

    reject_sentence = None
    for s in re.split(r"\.\s+", section):
        low = s.lower()
        if ("two" in low or "more than one" in low) and "large" in low and "reject" in low and "single" in low:
            reject_sentence = s
            break
    assert reject_sentence, (
        "expected a Step 2 sentence binding two/more-than-one + large -> "
        "reject -> single"
    )

    low = reject_sentence.lower()
    idx_large = low.index("large")
    idx_reject = low.index("reject")
    idx_single = low.index("single")
    assert idx_large < idx_reject < idx_single, (
        "expected the elements bound in order: large -> reject -> single, "
        f"got: {reject_sentence!r}"
    )

    # F4 fix (test-critic round 1): token order alone is also satisfied by a
    # sentence stating the rule is negated (e.g. "is never rejected into
    # single packages"), which preserves the same large -> reject -> single
    # order. Guard against a negation word directly modifying "reject".
    pre_reject_ctx = low[max(0, idx_reject - 20): idx_reject]
    assert not re.search(r"\b(never|not|n't|cannot|doesn't|won't)\b", pre_reject_ctx), (
        "the word 'reject' must not be directly negated (the rule must "
        f"state the rejection actually happens): {reject_sentence!r}"
    )

    start = section.index(reject_sentence)
    window = section[max(0, start - 250): start + len(reject_sentence) + 250]
    assert "depends_on" in window, (
        "expected 'depends_on' (still written per member) within 250 chars "
        "of the rejection sentence"
    )

    # F3 fix (test-critic round 2, major): bare presence of the string
    # "depends_on" in the window is also satisfied by prose stating the
    # entries are DROPPED (the pre-existing intra-package drop-rule makes
    # that the likely incumbent). Bind it to an affirmative write/keep/
    # retain statement, with a negation guard against the opposite.
    dep_sentence = None
    for s in re.split(r"\.\s+", window):
        if "depends_on" in s:
            dep_sentence = s
            break
    assert dep_sentence, "expected a sentence naming depends_on near the rejection"
    low_dep = dep_sentence.lower()
    assert re.search(r"\b(written|keep|kept|retain\w*|still)\b", low_dep), (
        "expected the depends_on sentence to affirmatively state the "
        f"entries are written/kept/retained: {dep_sentence!r}"
    )
    assert not re.search(r"\b(dropped|not written|discarded|removed)\b", low_dep), (
        f"the depends_on sentence must not state the entries are dropped: {dep_sentence!r}"
    )

    assert "epic" not in low, (
        "the rejection sentence must not mention creating an epic -- "
        f"rejected members become single packages: {reject_sentence!r}"
    )

    # edge case: a one-large collision package is still materialised as an
    # epic. F6 fix (test-critic round 2, minor): the heading's bare text is
    # satisfied regardless of what's under it, including contradicting body
    # text. Bind the check to the heading's own body still describing the
    # epic-materialisation behaviour, not just the heading string.
    assert "### Materialise multi-ticket packages as epics" in text
    materialise_section = _slice(
        text, "### Materialise multi-ticket packages as epics",
        "## Step 3 — clarify each package",
    )
    assert re.search(r"\bcreate_ticket\b", materialise_section), (
        "expected the body under 'Materialise multi-ticket packages as "
        "epics' to still describe creating the epic ticket, not just carry "
        "the heading"
    )
    # F7 fix (test-critic round 4, minor): the `|\bepic\b` alternative
    # reduces this check to "the word epic appears anywhere in the
    # section" -- satisfied even by body text saying a collision package
    # is explicitly NOT materialised as an epic. Drop the bare `\bepic\b`
    # alternative; require the body to affirmatively describe materialising
    # as an epic (the `labels=["epic"]` call, or a "materialised as an
    # epic" phrase), with a negation guard against the opposite statement.
    assert re.search(
        r'labels=\["?epic"?\]|materiali[sz]ed?\s+as\s+an?\s+epic',
        materialise_section, re.IGNORECASE,
    ), (
        "expected the epic-materialisation body to affirmatively describe "
        "materialising as an epic, not just name the word 'epic'"
    )
    assert not re.search(
        r"\b(not|never|no longer|isn't|is not)\b[^.\n]{0,60}"
        r"materiali[sz]ed?\s+as\s+an?\s+epic"
        r"|materiali[sz]ed?\s+as\s+an?\s+epic[^.\n]{0,60}"
        r"\b(not|never|no longer)\b",
        materialise_section, re.IGNORECASE,
    ), (
        "the epic-materialisation body must not state a collision package "
        "is NOT materialised as an epic"
    )


def test_gatekeeper_applies_recut_to_both_endpoints():
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 3.7", "## Step 4")

    frame_idx = section.index("## Frame (gatekeeper)")
    # F7 fix (test-critic round 1): the old bound was `match.start() >
    # frame_idx` -- satisfied by ANY text after the first heading occurrence,
    # including the separate '## Re-cut (gatekeeper)' comment the plan
    # defines. Bind the labelled lines to sitting INSIDE the frame-comment
    # content specifically, by scoping the search to the slice between the
    # two headings.
    recut_heading_idx = section.index("## Re-cut (gatekeeper)")
    assert recut_heading_idx > frame_idx, (
        "expected '## Frame (gatekeeper)' to be introduced before "
        "'## Re-cut (gatekeeper)' in Step 3.7"
    )
    frame_block = section[frame_idx:recut_heading_idx]

    add_req_match = re.search(r"^.*Additional requirement \(re-cut from #.*$", frame_block, re.MULTILINE)
    assert add_req_match, (
        "expected a line with 'Additional requirement (re-cut from #' "
        "inside the '## Frame (gatekeeper)' block (before "
        "'## Re-cut (gatekeeper)')"
    )
    add_ctx = frame_block[max(0, add_req_match.start() - 150): add_req_match.end()]
    # F6 fix (test-critic round 1): checking for the bare nouns
    # "target"/"source" is satisfied even when the endpoint variables
    # themselves are swapped (e.g. "on the target (`from`)"). Bind "target"
    # specifically to the `to` variable, per the plan's own literal
    # phrasing ("on the target (`to`) ...").
    assert (
        re.search(r"target[^\n]{0,30}\(`to`\)", add_ctx, re.IGNORECASE)
        or re.search(r"\(`to`\)[^\n]{0,30}target", add_ctx, re.IGNORECASE)
    ), (
        f"expected 'target' bound specifically to the `to` variable near "
        f"the Additional requirement line: {add_ctx!r}"
    )
    assert "(`from`)" not in add_ctx, (
        f"the Additional requirement line's context must not bind the "
        f"target label to the `from` variable (direction-inversion guard): {add_ctx!r}"
    )

    non_goal_match = re.search(r"^.*Non-goal \(re-cut to #.*$", frame_block, re.MULTILINE)
    assert non_goal_match, (
        "expected a line with 'Non-goal (re-cut to #' inside the "
        "'## Frame (gatekeeper)' block (before '## Re-cut (gatekeeper)')"
    )
    non_goal_ctx = frame_block[max(0, non_goal_match.start() - 150): non_goal_match.end()]
    assert (
        re.search(r"source[^\n]{0,30}\(`from`\)", non_goal_ctx, re.IGNORECASE)
        or re.search(r"\(`from`\)[^\n]{0,30}source", non_goal_ctx, re.IGNORECASE)
    ), (
        f"expected 'source' bound specifically to the `from` variable near "
        f"the Non-goal line: {non_goal_ctx!r}"
    )
    assert "(`to`)" not in non_goal_ctx, (
        f"the Non-goal line's context must not bind the source label to "
        f"the `to` variable (direction-inversion guard): {non_goal_ctx!r}"
    )

    # F7 fix (test-critic round 2, minor): "no epic" and "object by
    # replying" were checked as bare substrings anywhere in the whole Step
    # 3.7 slice, not bound to their specific role. Bind "no epic" to an
    # UNCONDITIONAL statement (no conditional word governing it, so
    # "no epic is created unless both endpoints are large" -- which
    # reintroduces the epic -- fails), and bind "object by replying" to
    # appearing as its own closing sentence inside EACH of the two comment
    # blocks the step posts (frame_block and the re-cut block), consistent
    # with how the F6/F7/F8 (round-1) fixes above already bind the labelled
    # lines to their specific block rather than the whole section.
    no_epic_sentence = None
    for s in re.split(r"\.\s+", section):
        if "no epic" in s.lower():
            no_epic_sentence = s
            break
    assert no_epic_sentence, "expected a 'no epic' statement in Step 3.7"
    assert not re.search(
        r"\b(unless|if|except|when)\b", no_epic_sentence, re.IGNORECASE,
    ), (
        "'no epic' must be stated unconditionally, not gated behind a "
        f"conditional that could re-permit an epic: {no_epic_sentence!r}"
    )

    closing_line_re = re.compile(
        r"(^|\n|\.\s)Object by replying on this ticket\.", re.IGNORECASE,
    )
    recut_block = section[recut_heading_idx:]
    assert closing_line_re.search(frame_block), (
        "expected 'Object by replying on this ticket.' as its own closing "
        f"sentence inside the frame-comment block: {frame_block!r}"
    )
    assert closing_line_re.search(recut_block), (
        "expected 'Object by replying on this ticket.' as its own closing "
        f"sentence inside the re-cut comment block: {recut_block!r}"
    )

    # edge case: the third closing-sentence variant (recut-only trigger) is
    # present -- a fourth copy of one of Step 4's two (false, for this case)
    # variants must fail this.
    third_variant = (
        "The ticket's own acceptance criterion is unchanged; the re-cut "
        "line above is part of this package's frame."
    )
    assert third_variant in section, (
        "expected the third closing-sentence variant, verbatim, for the "
        "recut-only trigger"
    )

    # F8 fix (test-critic round 1): bare presence of the third variant does
    # not bind it to its trigger -- an implementation emitting it
    # unconditionally (including on endpoints where it is FALSE) would also
    # pass. Require its immediately preceding context to state the "neither
    # ac: nor premise: fired" trigger.
    third_variant_idx = section.index(third_variant)
    trigger_ctx = section[max(0, third_variant_idx - 300): third_variant_idx]
    assert (
        re.search(r"neither\b[^.\n]{0,80}\bac\b[^.\n]{0,80}\bpremise\b", trigger_ctx, re.IGNORECASE)
        or re.search(r"neither\b[^.\n]{0,80}\bpremise\b[^.\n]{0,80}\bac\b", trigger_ctx, re.IGNORECASE)
    ), (
        "expected the third variant to be introduced by its 'neither ac: "
        f"nor premise:' trigger, not stated unconditionally: {trigger_ctx!r}"
    )


def test_gatekeeper_recut_frame_comment_is_not_clear_only():
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 3.7", "## Step 4")

    first_para = section.split("\n\n", 1)[0]
    assert re.search(r"\bboth\b", first_para, re.IGNORECASE), (
        f"expected Step 3.7's opening paragraph to say 'both': {first_para!r}"
    )
    assert "CLEAR" in first_para and "NEEDS_INPUT" in first_para, (
        f"expected both statuses named in Step 3.7's opening paragraph: {first_para!r}"
    )
    assert not first_para.strip().lower().startswith("on clear"), (
        "Step 3.7 must not open with 'On CLEAR' -- that is Step 4's "
        "CLEAR-only trigger, and Step 3.7 runs on both statuses"
    )
    # F5 fix (test-critic round 1): "both"/"CLEAR"/"NEEDS_INPUT" co-presence
    # is also satisfied by a paragraph that explicitly SCOPES the step to
    # CLEAR only while still mentioning all three words (e.g. "this step
    # does not run on both statuses -- run it only when CLEAR"). Reject a
    # negated "both" and a "only ... CLEAR" restriction directly.
    assert not re.search(
        r"\bnot\b[^.\n]{0,60}\bboth\b|\bboth\b[^.\n]{0,60}\bnot\b",
        first_para, re.IGNORECASE,
    ), (
        "Step 3.7's opening paragraph must not negate 'both' (e.g. 'does "
        f"not run on both statuses'): {first_para!r}"
    )
    assert not re.search(r"\bonly\b[^.\n]{0,40}\bCLEAR\b", first_para, re.IGNORECASE), (
        f"Step 3.7's opening paragraph must not restrict itself to CLEAR only: {first_para!r}"
    )

    # F2 fix (test-critic round 4, major): 'both' + CLEAR + NEEDS_INPUT
    # co-occurring anywhere in the paragraph is also satisfied when 'both'
    # refers to the two ENDPOINTS (target/source) in one sentence while
    # CLEAR/NEEDS_INPUT sit in a separate sentence that scopes the step to
    # CLEAR only. Bind 'both' to a single sentence that also names CLEAR
    # and NEEDS_INPUT together, so 'both' cannot be about something else.
    both_sentences = re.split(r"\.\s+", first_para)
    both_sentence = next(
        (s for s in both_sentences if re.search(r"\bboth\b", s, re.IGNORECASE)), None,
    )
    assert both_sentence, (
        f"expected a sentence containing 'both' in Step 3.7's opening "
        f"paragraph: {first_para!r}"
    )
    assert "CLEAR" in both_sentence and "NEEDS_INPUT" in both_sentence, (
        "expected 'both' to be bound, within its own sentence, to the two "
        f"clarifier statuses CLEAR and NEEDS_INPUT: {both_sentence!r}"
    )

    # F10 fix (test-critic round 4, minor): 'both'/CLEAR/NEEDS_INPUT
    # co-occurring in one sentence is also satisfied by a COMPARATIVE
    # sentence describing another step's scope, e.g. "Unlike Step 3.6,
    # which runs on both CLEAR and NEEDS_INPUT, this step runs after a
    # CLEAR verdict." -- 'both' there describes Step 3.6, not Step 3.7
    # itself, while still scoping Step 3.7 to CLEAR-only elsewhere in the
    # same paragraph. Reject a comparative opening and a same-paragraph
    # restriction of THIS step to running after/only-on CLEAR.
    assert not re.search(r"\bunlike\b|\bin contrast\b", both_sentence, re.IGNORECASE), (
        "the both-statuses sentence must describe Step 3.7's own scope "
        f"directly, not compare against another step's scope: {both_sentence!r}"
    )
    assert not re.search(
        r"\bafter a CLEAR\b|\bonly after CLEAR\b|\brequires? a CLEAR\b",
        first_para, re.IGNORECASE,
    ), (
        "Step 3.7's opening paragraph must not restrict this step itself "
        f"to running after/only-on a CLEAR verdict: {first_para!r}"
    )

    step4 = _slice(text, "## Step 4", "## Step 5")
    deferral_sentence = None
    for s in re.split(r"\.\s+", step4):
        if "Step 3.6" in s and "already posted" in s.lower():
            deferral_sentence = s
            break
    assert deferral_sentence, "expected Step 4's deferral clause naming Step 3.6"
    assert "Step 3.7" in deferral_sentence, (
        "expected Step 4's deferral clause widened to name Step 3.7 "
        f"alongside Step 3.6: {deferral_sentence!r}"
    )


def test_gatekeeper_passes_previous_cut_and_requires_named_change():
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 2", "## Step 3 — clarify each package")

    assert "previous_cut" in section
    _assert_near(section, "previous_cut", "Question", window=300)
    _assert_near(section, "previous_cut", "include_relations=True", window=300)
    _assert_near(section, "previous_cut", "## Frame (gatekeeper)", window=400)

    assert "prior_rationale" in section
    # F9 fix (test-critic round 1): the old "reason...kind" proximity check
    # is satisfied by exactly the collapse it says must fail -- a definition
    # like "prior_rationale: the reason kind recorded for the previous
    # package (collision|effort|single|unknown)" contains that same phrase.
    # Instead: (a) forbid the reason-kind enum listing from appearing right
    # in prior_rationale's own definition, and (b) require the definition to
    # say it carries reasoning distinct from the kind enum.
    prior_rationale_idx = section.index("prior_rationale")
    definition_window = section[prior_rationale_idx: prior_rationale_idx + 300]
    assert not re.search(
        r"collision\s*\|\s*effort\s*\|\s*single\s*\|\s*unknown", definition_window,
    ), (
        "prior_rationale's own definition must not collapse into the "
        f"reason-kind enum listing: {definition_window!r}"
    )
    assert re.search(
        r"actual reasoning"
        r"|distinct from[^.\n]{0,40}(reason|kind)"
        r"|not (just |merely )?the (reason|kind)",
        definition_window, re.IGNORECASE,
    ), (
        "expected prior_rationale's definition to state it carries "
        f"reasoning distinct from the kind enum: {definition_window!r}"
    )

    # polarity: absent/empty changed_by -> previous cut stands;
    # non-empty changed_by -> the new cut is accepted.
    #
    # F6 fix (test-critic round 4, major): the old regexes only checked
    # token order, which a DOUBLE negation also satisfies -- "the previous
    # cut no longer stands" still contains "...previous cut..." followed
    # eventually by "stand", and "does not by itself accept" still contains
    # "...accept" downstream of "changed_by"/"non-empty". Anchor each
    # branch to its own sentence, then forbid a negation word from sitting
    # directly in front of the outcome phrase (stand/accept) within that
    # sentence -- that is where a double negation would have to attach.
    sentences = re.split(r"\.\s+", section)
    absent_sentence = next(
        (
            s for s in sentences
            if re.search(r"\b(absent|empty)\b", s, re.IGNORECASE) and "changed_by" in s
        ),
        None,
    )
    assert absent_sentence, "expected a sentence describing the absent/empty changed_by branch"
    assert re.search(r"previous cut stand", absent_sentence, re.IGNORECASE), (
        "expected the absent/empty changed_by branch to resolve to 'the "
        f"previous cut stands': {absent_sentence!r}"
    )
    # Scope the negation check to the CLAUSE that actually carries "previous
    # cut stand" (split on ,;:), not a fixed character window -- a fixed
    # window is either too narrow to catch "no longer stands" or, as here,
    # wide enough to snag an unrelated negation word ("never arrived") from
    # the PRECEDING clause that has nothing to do with whether the cut
    # stands.
    stand_match = re.search(r"previous cut stand", absent_sentence, re.IGNORECASE)
    clause_start = max(
        (absent_sentence.rfind(c, 0, stand_match.start()) + 1 for c in ",;:"),
        default=0,
    )
    pre_stand_ctx = absent_sentence[clause_start: stand_match.start()]
    assert not re.search(
        r"\b(no longer|not|never|doesn't|isn't|won't)\b", pre_stand_ctx, re.IGNORECASE,
    ), (
        "the absent/empty changed_by branch's 'previous cut stands' must "
        f"not itself be negated (e.g. 'no longer stands'): {absent_sentence!r}"
    )

    nonempty_sentence = next(
        (s for s in sentences if "non-empty" in s.lower() and "changed_by" in s), None,
    )
    assert nonempty_sentence, "expected a sentence describing the non-empty changed_by branch"
    accept_match = re.search(r"\baccept", nonempty_sentence, re.IGNORECASE)
    assert accept_match, (
        "expected the non-empty changed_by branch to resolve to accepting "
        f"the new cut: {nonempty_sentence!r}"
    )
    pre_accept_ctx = nonempty_sentence[max(0, accept_match.start() - 30): accept_match.start()]
    assert not re.search(
        r"\b(not|never|doesn't|does not|n't)\b", pre_accept_ctx, re.IGNORECASE,
    ), (
        "the non-empty changed_by branch's acceptance must not itself be "
        f"negated (e.g. 'does not ... accept'): {nonempty_sentence!r}"
    )

    # negation guard: locatability never rejects a cut -- 'not found' /
    # 'unverified' must only ever co-occur with 'verified'/'report' (a
    # confidence signal), never with 'previous cut stands' (a rejection).
    # F10 fix (test-critic round 1): "verified" is a substring of
    # "unverified", so a plain `"verified" in low` check auto-passes for any
    # sentence whose only trigger word is "unverified" -- it can never come
    # out false. Use word-boundary-aware matching so the two are told apart.
    for s in re.split(r"\.\s+", section):
        low = s.lower()
        if "not found" in low or re.search(r"\bunverified\b", low):
            assert re.search(r"\bverified\b", low) or "report" in low, (
                f"a locatability sentence must co-occur with verified/report: {s!r}"
            )
            assert "previous cut stand" not in low, (
                f"locatability must never be a rejection condition: {s!r}"
            )

    step5 = _slice(text, "## Step 5", "## Hard rules")
    assert re.search(r"\bverified\b", step5) and re.search(r"\bunverified\b", step5), (
        "expected both 'verified' and 'unverified' as Step 5 report values "
        "(word-boundary-checked, since 'unverified' contains 'verified' as "
        "a substring)"
    )
    # F5 fix (test-critic round 2, minor): presence anywhere in Step 5 is
    # also satisfied by the two words belonging to an unrelated report line
    # (e.g. the relation read-back report). Bind both to the specific
    # reporting context the plan describes -- the changed_by confidence
    # signal -- rather than anywhere in Step 5.
    verified_re = re.compile(r"\bverified\b")
    unverified_re = re.compile(r"\bunverified\b")
    _assert_near(
        step5, verified_re, "changed_by", window=300,
        msg="expected 'verified' bound to the changed_by confidence-signal "
            "context in Step 5, not just present anywhere in the section",
    )
    _assert_near(
        step5, unverified_re, "changed_by", window=300,
        msg="expected 'unverified' bound to the changed_by confidence-signal "
            "context in Step 5, not just present anywhere in the section",
    )

    # edge case: agents/bundler.md documents `changed_from_previous` and
    # ties the naming obligation to `rationale` in the same sentence that
    # introduces it, so the field doesn't relocate the obligation, just
    # restate it.
    bundler_text = _read(BUNDLER)
    divergent_sentence = None
    for s in re.split(r"\.\s+", bundler_text):
        if "changed_from_previous" in s:
            divergent_sentence = s
            break
    assert divergent_sentence, (
        "expected agents/bundler.md to document `changed_from_previous`"
    )
    assert "rationale" in divergent_sentence.lower(), (
        f"expected 'rationale' named in the sentence introducing "
        f"changed_from_previous: {divergent_sentence!r}"
    )
    assert re.search(r"\bname\b", divergent_sentence, re.IGNORECASE), (
        f"expected the obligation to 'name' the change stated in the same "
        f"sentence: {divergent_sentence!r}"
    )
    # F4 fix (test-critic round 4, major): 'rationale' + 'name' co-presence
    # is also satisfied by a sentence that describes the rationale
    # obligation as RELOCATED/REPLACED/SUPERSEDED by the new field (e.g.
    # "the `changed_by` name carries what the `rationale` used to state"),
    # which still contains both tokens while dropping the obligation.
    # Guard against relocation phrasing.
    assert not re.search(
        r"\b(replaces?|replaced|relocat\w*|supersed\w*|"
        r"used to (state|carry|name)|no longer (need|needs|must|has to))\b",
        divergent_sentence, re.IGNORECASE,
    ), (
        "the divergent-verdict sentence must not state the rationale "
        f"obligation is relocated/replaced/superseded by the new field: "
        f"{divergent_sentence!r}"
    )


def test_gatekeeper_step_3_5_invokes_relation_readback_script():
    text = _read(GATEKEEPER)
    section = _slice(text, "## Step 3.5", "## Step 3.6")

    assert "relation-readback.py" in section, (
        "expected Step 3.5 to name scripts/gatekeeper/relation-readback.py"
    )
    # F4 fix (test-critic round 2, critical): a bare literal-presence check
    # on the path string is satisfied by a passing mention with no actual
    # invocation -- e.g. "(a helper, scripts/gatekeeper/relation-readback.py,
    # exists for this diff)" while the read-back stays LLM prose bookkeeping.
    # Require the section to actually show the invocation contract: piped
    # stdin, the interpreter, and the verdict/exit-code vocabulary the plan
    # specifies (`{expected, relations, reasons}` piped in; `verdict: ok|gap`,
    # exit 0/2) -- not just the filename mentioned.
    _assert_near(
        section, "relation-readback.py", "stdin", window=300,
        msg="expected the script invocation to be described with a piped "
            "stdin input near the script name, not just the name mentioned",
    )
    assert re.search(r"\bpython3?\b", section), (
        "expected Step 3.5 to name the interpreter (python/python3) "
        "invoking the script"
    )
    assert re.search(r"verdict:\s*ok\s*\|\s*gap", section, re.IGNORECASE), (
        "expected Step 3.5 to state the script's 'verdict: ok|gap' contract"
    )
    assert re.search(r"exit\s*0", section, re.IGNORECASE) and re.search(
        r"exit\s*2", section, re.IGNORECASE,
    ), (
        "expected Step 3.5 to name the script's exit codes (0 / 2)"
    )
    # F11 fix (test-critic round 1): "gap" + a negation + "Planned" anywhere
    # in one sentence matches both the gate ("does not move to Planned")
    # AND its exact inverse ("does not withhold Planned" -- a double
    # negative meaning the gap does NOT block Planned). Bind the negation
    # specifically to "move ... Planned", per the plan's own wording ("does
    # not move to Planned"), and reject the inverted "withhold" phrasing
    # outright.
    # F7 fix (test-critic round 4, major): the old check accepted a negation
    # ANYWHERE within 60 chars before "move ... Planned", including a
    # DOUBLE negation like "does not prevent the package from moving to
    # Planned" -- which states the opposite gate (the gap does NOT block
    # Planned) while still matching. The companion guard also fired as a
    # false positive on this file's own pre-existing, unrelated "Being
    # blocked never withholds a package from Planned" sentence (about
    # `blocked_by`, not about a relation-write `gap`), since it scanned the
    # whole section rather than the gap sentence specifically. Fixed by
    # anchoring everything to the ONE sentence that actually mentions
    # 'gap', 'move' and 'Planned' together, then forbidding a negation word
    # from sitting directly in front of prevent/stop/block/withhold within
    # that sentence (where a double negation would have to attach).
    gate_sentence = next(
        (
            s for s in re.split(r"\.\s+", section)
            if "gap" in s.lower() and re.search(r"\bmove\w*\b", s, re.IGNORECASE)
            and "planned" in s.lower()
        ),
        None,
    )
    assert gate_sentence, (
        "expected a sentence binding the 'gap' verdict, 'move', and "
        "'Planned' together"
    )
    assert re.search(r"\b(not|never|does not|no longer)\b", gate_sentence, re.IGNORECASE), (
        f"expected the gap/move/Planned sentence to carry a negation: {gate_sentence!r}"
    )
    assert not re.search(
        r"\b(not|never|does not|no longer)\b[^.\n]{0,30}\b(prevent\w*|stop\w*|block\w*|withhold\w*)\b",
        gate_sentence, re.IGNORECASE,
    ), (
        "found a double-negation phrasing (negation modifying prevent/stop/"
        f"block/withhold rather than move/Planned directly): {gate_sentence!r}"
    )

    # edge case: the new Hard rule's coexistence with the pre-existing
    # "Blocked is not unplanned" rule, asserted once.
    hard = text[text.index("## Hard rules"):]
    assert "Blocked is not unplanned" in hard
    _assert_near(
        hard, "Blocked is not unplanned", "unexplained", window=500,
        msg="expected the new 'unexplained relation gap withholds Planned' "
            "Hard rule to sit near the pre-existing 'Blocked is not "
            "unplanned' rule, naming the coexistence explicitly",
    )

    # F5 fix (test-critic round 4, minor): proximity to the old rule alone
    # never checks the new rule's own polarity -- a Hard rule stating the
    # OPPOSITE direction ("an unexplained gap does not withhold Planned")
    # still sits within 500 chars of 'Blocked is not unplanned' and passes.
    # Bind the sentence naming 'unexplained' to state the correct
    # direction: a failed write withholds/blocks Planned, not the inverse.
    unexplained_sentence = None
    for s in re.split(r"\.\s+", hard):
        if "unexplained" in s.lower():
            unexplained_sentence = s
            break
    assert unexplained_sentence, (
        "expected a Hard rule sentence naming 'unexplained'"
    )
    low_u = unexplained_sentence.lower()
    assert "planned" in low_u, (
        f"expected the 'unexplained' Hard rule sentence to name Planned: "
        f"{unexplained_sentence!r}"
    )
    assert not re.search(
        r"\b(does not|doesn't|never|no longer)\b[^.\n]{0,80}"
        r"\b(withhold\w*|block\w*|hold\w*|keep\w*)\b",
        low_u,
    ), (
        "the new Hard rule must not negate withholding/blocking Planned "
        f"(inverted polarity): {unexplained_sentence!r}"
    )
    assert re.search(
        r"\bwithhold\w*\b|\bblock\w*\b|\bholds?\b[^.\n]{0,30}\bout\b"
        r"|\bkeeps?\b[^.\n]{0,30}\bout\b",
        low_u,
    ), (
        "expected the new Hard rule to affirmatively state a failed write "
        f"withholds/blocks Planned: {unexplained_sentence!r}"
    )


# --- D1: lean MCP requests (#26) -------------------------------------------
# Both skills request only what they read: bounded comment reads, body-less
# enumerations, light write echoes, resolution without a full project dump, and
# a one-hour fallback heartbeat. Prose executed by an LLM -- these assertions
# bind each knob to its own call site (proximity / per-call parentheses), so a
# knob mentioned somewhere else in the file cannot satisfy them.

PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"


def _call_spans(text: str, name: str) -> list:
    """Every `name(` call in `text` as (start, full_call_text), the call text
    running to the matching closing parenthesis (balanced), so a multi-line
    call is checked as one unit."""
    spans = []
    for m in re.finditer(re.escape(name) + r"\(", text):
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        assert i < len(text), (
            f"unbalanced parenthesis in {name}( call at offset {m.start()}: "
            f"{text[m.start():m.start() + 80]!r}"
        )
        spans.append((m.start(), text[m.start():i + 1]))
    return spans


def _run_step0() -> str:
    return _slice(_read(RUN), "### 0. Pre-flight", "### 1. Enumerate")


def _run_step3c() -> str:
    return _slice(_read(RUN), "**c. Read the ticket, react.**", "**d. Worktree removal.**")


def _lean_event_read(slice_: str, where: str) -> None:
    calls = _call_spans(slice_, "list_comments")
    assert calls, f"{where}: no list_comments( call"
    assert any(
        'order="desc"' in c and "limit=3" in c and "body_max_chars=600" in c
        for _, c in calls
    ), f'{where}: the event read must be list_comments(order="desc", limit=3, body_max_chars=600)'
    assert not any("limit=20" in c and "body_max_chars" not in c for _, c in calls), (
        f"{where}: unbounded limit=20 full-body event read still present"
    )


def test_run_reads_the_event_block_leanly():
    step0, step3c = _run_step0(), _run_step3c()
    _lean_event_read(step0, "Step 0")
    _lean_event_read(step3c, "Step 3c")
    for where, sl in (("Step 0", step0), ("Step 3c", step3c)):
        # widen once to limit=10 when none of the three carries the event block:
        # trigger (none carries it) + single retry + consequence (no terminal event)
        assert re.search(r"limit=10", sl), f"{where}: no widen-once retry at limit=10"
        _assert_near(sl, "limit=10", "<!-- adev:event", window=600,
                     msg=f"{where}: widen-once sentence not bound to the adev:event block")
        _assert_near(sl, "limit=10", re.compile(r"\bnone\b|\bneither\b|\bno\b[^.]{0,40}carries", re.I),
                     window=250, msg=f"{where}: widen-once has no trigger condition")
        _assert_near(sl, "limit=10", re.compile(r"\bonce\b|\bone retry\b|\bsingle retry\b", re.I),
                     window=250, msg=f"{where}: widen-once is not stated as a single retry")
        _assert_near(sl, "limit=10", "no terminal event", window=400,
                     msg=f"{where}: widen-once does not precede the 'no terminal event' conclusion")
        assert not re.search(
            r"keep widening|widen(?:ing)? (?:again|repeatedly)|until (?:you|an|the)[^.]{0,30}(?:found|event)",
            sl, re.I), f"{where}: widening must not be unbounded"
        assert not any(re.search(r"limit=(?:2[1-9]|[3-9]\d|\d{3,})\b", c)
                       for _, c in _call_spans(sl, "list_comments")), (
            f"{where}: a list_comments( call reads a wide page")
        assert not re.search(
            r"(?:never|do not|don't|must not|not)\s+widen|no\s+widen", sl, re.I
        ), f"{where}: the widen-once retry must be stated positively, not forbidden"
    # full text of a blocked/failed event: exactly that one comment via get_comment
    _assert_near(step3c, "get_comment(", "blocked", window=400,
                 msg="Step 3c: get_comment not bound to the blocked/failed full-text need")
    _assert_near(step3c, "get_comment(", "failed", window=400,
                 msg="Step 3c: get_comment not bound to the blocked/failed full-text need")
    gc = _call_spans(step3c, "get_comment")
    assert any("comment_id=" in c and "ticket_id=" in c for _, c in gc), (
        "Step 3c: get_comment( must carry comment_id= and ticket_id= (one identified comment)"
    )
    flat3c = re.sub(r"\s+", " ", step3c)
    intro = [x for x in re.split(r"(?<=[.?!;:])\s+", flat3c) if "get_comment(" in x]
    assert intro and any(re.search(
        r"exactly (?:that )?one comment|that (?:single|one) comment|the single (?:event )?comment|a single comment|one comment",
        x, re.I) for x in intro), (
        "Step 3c: the get_comment( sentence must say it fetches exactly one (the single event) comment"
    )


def test_every_list_comments_call_is_body_bounded():
    """File-wide invariant: no list_comments( call is unbounded. gatekeeper
    Step 2's previous_cut / changed_by lookups read comment *content* and are
    the declared full-body exception, so that section is excluded."""
    run_text = _read(RUN)
    gk_text = _read(GATEKEEPER)
    gk_step2 = _slice(gk_text, "## Step 2 — bundle", "## Step 3 — clarify")
    gk_text = gk_text.replace(gk_step2, "")
    for name, text in (("run", run_text), ("gatekeeper", gk_text)):
        calls = _call_spans(text, "list_comments")
        assert calls, f"{name}: expected list_comments( calls"
        unbounded = [c for _, c in calls if "body_max_chars=" not in c]
        assert unbounded == [], f"{name}: list_comments without body_max_chars: {unbounded}"
        too_wide = [c for _, c in calls
                    if any(int(n) > 600 for n in re.findall(r"body_max_chars=(\d+)", c))]
        assert too_wide == [], f"{name}: body_max_chars above 600 is not a lean bound: {too_wide}"
    for label, sl in (
        ("gatekeeper Step 1 ownership test",
         _slice(_read(GATEKEEPER), "## Step 1 — enumerate", "## Step 2 — bundle")),
        ("gatekeeper Step 3.6 idempotency",
         _slice(_read(GATEKEEPER), "## Step 3.6 — regression chains", "## Step 3.7")),
    ):
        cs = _call_spans(sl, "list_comments")
        assert any(re.search(r"\blimit=20\b", c) and "body_max_chars=200" in c for _, c in cs), (
            f"{label}: heading-only scan must be list_comments(..., limit=20, body_max_chars=200)"
        )


def test_run_triage_idempotency_scan_stays_wide():
    text = _read(RUN)
    scan = _slice(text, "Triage once per package per run", "This replaces the old two-stage design")
    calls = _call_spans(scan, "list_comments")
    assert calls, "triage idempotency scan must spell out its list_comments( call"
    assert any(re.search(r"\blimit=20\b", c) and "body_max_chars=200" in c for _, c in calls), (
        "the heading-only scan must keep limit=20 and add body_max_chars=200"
    )
    wide = [c for _, c in _call_spans(text, "list_comments") if re.search(r"\blimit=20\b", c)]
    assert wide and all("body_max_chars=200" in c for c in wide), (
        "every limit=20 list_comments( call in run is a heading scan and must carry body_max_chars=200"
    )


def test_enumerations_omit_bodies():
    run_step1 = _slice(_read(RUN), "### 1. Enumerate", "### 1a.")
    gk_step1 = _slice(_read(GATEKEEPER), "## Step 1 — enumerate", "## Step 2 — bundle")
    sites = [
        ("run Todo", run_step1, "list_tickets", 'column="Todo"'),
        ("gatekeeper Backlog", gk_step1, "list_tickets", 'column="Backlog"'),
        ("gatekeeper Question", gk_step1, "list_tickets", 'column="Question"'),
    ]
    for label, sl, fn, col in sites:
        calls = [c for _, c in _call_spans(sl, fn) if col in c]
        assert calls, f"{label}: no {fn}( call with {col}"
        assert all("omit_body=True" in c for c in calls), f"{label}: {fn} must pass omit_body=True"
    # Step 1a keeps reading relations exactly as before
    step1a = _slice(_read(RUN), "### 1a.", "### When is a blocker resolved")
    gt = [c for _, c in _call_spans(step1a, "get_ticket")]
    assert any("include_relations=True" in c and "include_comments=False" in c
               for c in gt), (
        "Step 1a: one get_ticket( call must carry both include_relations=True "
        "and include_comments=False")
    assert not any("include_comments=True" in c for c in gt), (
        "Step 1a: a get_ticket( call re-enables include_comments")


def test_project_resolution_is_lean():
    for name, path in (("run", RUN), ("gatekeeper", GATEKEEPER)):
        text = _read(path)
        assert not re.search(r"list_projects\(\s*\)", text), (
            f"{name}: bare list_projects() full dump still present"
        )
        searches = _call_spans(text, "search_projects")
        assert searches and all("query=" in c and re.search(r"limit=5\b", c)
                                for _, c in searches), (
            f"{name}: every search_projects( call must carry query= and limit=5"
        )
        lists = [c for _, c in _call_spans(text, "list_projects")]
        assert all('fields="light"' in c for c in lists), (
            f"{name}: every list_projects( call must be the fields=\"light\" one: {lists}"
        )
        _assert_near(
            text, "search_projects(",
            re.compile(r"single[^.]*`path`[^.]*(?:equals|exactly)", re.I),
            window=400,
            msg=f"{name}: search_projects not bound to the single exact-`path`-match rule",
        )
        _assert_near(text, 'list_projects(fields="light")', "STOP", window=300,
                     msg=f"{name}: light list_projects not bound to the STOP diagnostic")
    # guard: the fields each skill reads from the resolved entry are still named
    # in that skill's own Inputs/Preconditions (not merely somewhere in the file)
    run_pre = _slice(_read(RUN), "## Inputs", "## Flow per project")
    gk_pre = _slice(_read(GATEKEEPER), "## Inputs", "## Step 1 — enumerate")
    for name, sl, fields in (
        ("run", run_pre, ("permissions", "local_path")),
        ("gatekeeper", gk_pre, ("permissions", "local_path", "provider")),
    ):
        sentences = re.split(r"(?<=[.?!:])\s+", re.sub(r"\s+", " ", sl))
        for field in fields:
            bound = [
                s for s in sentences
                if field in s
                and re.search(r"\b(?:read|reads|take|takes|taken)\b", s, re.I)
                and re.search(r"resolved|\bentry\b|\brecord\b|search_projects|list_projects", s, re.I)
                and not re.search(r"no longer|not available|not carried|not returned", s, re.I)
            ]
            assert bound, (
                f"{name}: no sentence in Inputs/Preconditions reads {field} "
                "from the resolved project entry"
            )


def test_write_calls_request_the_light_response():
    for name, path, fns in (
        ("run", RUN, ("update_ticket", "merge_pr")),
        ("gatekeeper", GATEKEEPER, ("update_ticket",)),
    ):
        text = _read(path)
        for fn in fns:
            calls = _call_spans(text, fn)
            assert calls, f"{name}: expected {fn}( call sites"
            missing = [c for _, c in calls if 'response="light"' not in c]
            assert missing == [], f'{name}: {fn} calls without response="light": {missing}'
        assert not any("response=" in c for _, c in _call_spans(text, "add_comment")), (
            f"{name}: add_comment must stay out of scope"
        )
    # the #314 reference must sit in the same paragraph as a response="light"
    # call site and explain the light form -- not float anywhere in the file
    run_text = _read(RUN)
    flat_run = re.sub(r"\s+", " ", run_text)
    sents = [x for x in re.split(r"(?<=[.?!;])\s+", flat_run)
             if "agent-project-issues#314" in x]
    assert sents, "run: no reference to agent-project-issues#314"
    assert any(
        'response="light"' in x
        and re.search(r"update_ticket|merge_pr|write", x)
        and re.search(r"\blight\b", x.replace('response="light"', ""), re.I)
        for x in sents
    ), 'run: the sentence naming agent-project-issues#314 must itself name response="light" and the write call(s)'
    # guards: the lean write form did not drop a field the skill reads
    ci_green = _slice(_read(RUN), "| `ci-green` |", "\n| ")
    mp = [c for _, c in _call_spans(ci_green, "merge_pr")]
    assert mp and all('response="light"' in c for c in mp), (
        "ci-green row: merge_pr call must request response=\"light\""
    )
    assert "pull_request.merged == true" in ci_green, (
        "ci-green row: must still require pull_request.merged == true"
    )
    assert ci_green.find("merge_pr(") < ci_green.find("pull_request.merged == true"), (
        "ci-green row: the merged check must follow (be bound to) the merge_pr call"
    )
    failure = _slice(_read(RUN), "**When the merge fails", "**The pre-retry CI check")
    _assert_near(failure, "mergeable_state", "get_pr", window=300)


def test_plugin_manifest_pins_the_light_write_build():
    import json
    deps = {d["name"]: d["version"] for d in json.loads(_read(PLUGIN_MANIFEST))["dependencies"]}
    floor = re.match(r">=\s*(\d+)\.(\d+)\.(\d+)", deps["agent-project-issues"])
    assert floor, deps["agent-project-issues"]
    assert tuple(int(x) for x in floor.groups()) >= (0, 3, 4), (
        f"agent-project-issues floor {deps['agent-project-issues']!r} predates the light write form"
    )


def test_run_fallback_heartbeat_is_one_hour():
    text = _read(RUN)
    waiting = _slice(text, "## Waiting rule", "## Why there is no dollar budget")
    assert re.search(r"\b3600\b", waiting), "Waiting rule names no 3600 s fallback interval"
    _assert_near(waiting, re.compile(r"\b3600\b"), "fallback", window=300)
    _assert_near(waiting, re.compile(r"\b3600\b"), "notification", window=300)
    para = next((p for p in re.split(r"\n\s*\n", waiting) if re.search(r"\b3600\b", p)), "")
    flat = re.sub(r"\s+", " ", para)
    n_pos, h_pos = flat.find("notification"), flat.find("3600")
    assert 0 <= n_pos < h_pos, (
        "Waiting rule: the completion notification must be stated (as the wake) before the 3600 s fallback"
    )
    hs = [s for s in re.split(r"(?<=[.?!;])\s+", flat) if "3600" in s]
    assert any(re.search(
        r"not a poll|no poll|never poll|does not poll|is not polling|without (?:reading|polling|checking)"
        r"|reads? nothing|no (?:ticket|CI)[^.]{0,20}(?:read|check)", s, re.I) for s in hs), (
        "Waiting rule: the 3600 s fallback wake must be stated as not a poll (reads nothing)"
    )
    assert not any(re.search(r"re-?check|re-?read|until", s, re.I) for s in hs), (
        "Waiting rule: the 3600 s sentence describes a poll (re-check/re-read/until)"
    )
    assert not re.search(r"\b1800\b", text), "the improvised 1800 s heartbeat must not appear"
    step2b = _slice(text, "**b. Start the package session", "**c. Read the ticket, react.**")
    flat2b = re.sub(r"\s+", " ", step2b)
    pointers = [
        x for x in re.split(r"(?<=[.?!;])\s+", flat2b)
        if re.search(r"waiting rule", x, re.I)
        and re.search(r"\b(?:see|per|as in|as described in|described in|governed by|follows?|defined in|under)\b", x, re.I)
        and not re.search(r"\b(?:no|not|never)\b|n't", x, re.I)
    ]
    assert pointers, "Step 2b must have a sentence that defers to the Waiting rule (see/per/as in ...)"
    assert not any(re.search(r"\d", x) for x in pointers), (
        "the Step 2b pointer sentence must not itself carry an interval literal"
    )
    assert not re.search(r"heartbeat|fallback|hourly|interval|wake-?\s?up|ScheduleWakeup|\bevery\b",
                         step2b, re.I), (
        "Step 2b must not describe wake scheduling of its own"
    )
    assert not re.search(
        r"\b\d{2,5}\s*(?:s|sec|secs|seconds?|min|minutes?|h|hours?)\b|\b(?:one|an|half an) hour\b",
        step2b, re.I,
    ), "Step 2b must point at the Waiting rule, not restate an interval"


# --- cross-cutting: LF only (Claude Code silently ignores CRLF) ------------

def test_every_parsed_markdown_file_is_lf_only():
    """lint.yml only checks skills/*/SKILL.md and agents/*.md for CRLF; this
    closes the gap for AGENTS.md, CLAUDE.md and README.md too, and matters
    concretely here because these edits were made on Windows. Extended for
    package #19 to also cover the new `ticket` skill and the issue-form YAML
    once they exist -- guarded with .exists() since, in the tests phase,
    they don't yet."""
    paths = [AGENTS_MD, CLAUDE_MD, README, RUN, GATEKEEPER, BUNDLER, CLARIFIER, TRIAGE, TICKET_SKILL]
    if TEMPLATES.exists():
        paths += sorted(TEMPLATES.glob("*.yml"))
    offenders = [str(p) for p in paths if p.exists() and b"\r\n" in p.read_bytes()]
    assert offenders == []


# --- #31: no Review column; run refuses a project without pulls.merge ------

def test_run_stops_when_merge_is_not_permitted():
    """Driving test (#31 R1): Precondition 3 is a STOP for `pulls.merge:
    false`, stated before Step 0, pointing at the single-ticket path and
    saying nothing was touched. The old 'still run' fallback is gone."""
    text = _read(RUN)
    pre = _slice(text, "## Preconditions (per project)", "## Flow per project")
    p3 = _slice(pre, "3. **Merge permission.**", "4. **Repo root.**")
    # semantic: the ONE sentence of Precondition 3 that contains STOP must
    # itself carry the condition (`pulls.merge`, `false`) and the redirect
    flat = " ".join(p3.split())
    stop_sents = [sn for sn in re.split(r"(?<=[.!?])\s+", flat) if "STOP" in sn]
    assert len(stop_sents) == 1, f"Precondition 3 needs exactly one sentence with STOP, got {stop_sents}"
    stop_sent = stop_sents[0]
    for needle in ("pulls.merge", "false", "/agent-autonomous-developer:process-developer"):
        assert needle in stop_sent, f"the STOP sentence must contain {needle!r}: {stop_sent!r}"
    assert not re.search(r"still run|still works|carry on|continue", p3, re.I), (
        "Precondition 3 must contain no continue-anyway wording"
    )
    assert re.search(r"no (?:column|card)[^.]*(?:worktree)[^.]*(?:session)|nothing (?:was|is|has been) touched",
                     p3, re.I), "Precondition 3 must state that nothing was touched"
    # the STOP sentence itself sits in Precondition 3 (slice membership) and
    # precedes the first board-write / worktree / session / enumeration call
    # anywhere in the file -- a real, falsifiable ordering, not a layout fact
    stop_off = text.index("3. **Merge permission.**") + p3.index("STOP")
    first_action = min(
        text.index(tok)
        for tok in ("worktree_create", "update_ticket", "start-package-session.sh", "list_tickets(")
    )
    assert stop_off < first_action, "the STOP must precede every board write, worktree and session call"
    assert not re.search(r"still run|merge not permitted for this project", text, re.I), (
        "the run-anyway-and-park fallback must be deleted"
    )


def test_run_precondition_3_still_reads_pulls_merge():
    """Preservation guard (green before and after #31, NOT driving evidence):
    keeps test_project_resolution_is_lean's slice of the resolved entry valid."""
    text = _read(RUN)
    p3 = _slice(text, "3. **Merge permission.**", "4. **Repo root.**")
    assert re.search(r"read\s+`permissions\.pulls\.merge`", p3)


def _assert_run_required_columns(text: str) -> None:
    """(#31 R2, forward-compatible per #56 R7) Precondition 2 lists
    Todo/Doing/Question, with Done optional (present today, gone once #45
    lands) and nothing else in its place; existing boards keep an extra
    column, which run neither reads nor requires. The clause is phrased
    without the capitalised column name on purpose, so the file-wide absence
    test below can hold at the same time."""
    pre = _slice(text, "## Preconditions (per project)", "3. **Merge permission.**")
    item2 = pre[pre.index("2. **Board columns.**"):]
    m = re.search(r"logical\s+columns\s+((?:`\w+`[,\s]*(?:and\s+)?)+)", item2)
    assert m, "Precondition 2 must list the required logical columns"
    cols = re.findall(r"`(\w+)`", m.group(1))
    assert [c for c in cols if c != "Done"] == ["Todo", "Doing", "Question"], cols
    assert not re.search(r"\bReview\b", pre)
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(item2.split()))
    assert any(
        re.search(r"existing boards?", sn, re.I)
        and re.search(r"neither reads nor requires|does not read|never reads", sn, re.I)
        for sn in sentences
    ), "one sentence of Precondition 2 must say existing boards with an extra column stay valid and unread"


_POST_45_COLUMNS_FIXTURE = (
    "## Preconditions (per project)\n\n"
    "1. **Project resolution.** Resolve the project entry.\n"
    "2. **Board columns.** `list_board_columns(project_id)` must contain the logical\n"
    "   columns `Todo`, `Doing`, `Question`. Keep the\n"
    "   `logical -> native` map; every board write uses the *native* value. Missing\n"
    "   column -> STOP for this project with the missing name. Existing boards\n"
    "   that still carry an extra column keep it: this skill neither reads nor\n"
    "   requires it.\n"
    "3. **Merge permission.** From the resolved project entry read `permissions.pulls.merge`.\n"
)


def test_run_required_columns_drop_review():
    _assert_run_required_columns(_read(RUN))


_BAD_COLUMNS_FIXTURE_MISSING_TODO = (
    "## Preconditions (per project)\n\n"
    "1. **Project resolution.** Resolve the project entry.\n"
    "2. **Board columns.** `list_board_columns(project_id)` must contain the logical\n"
    "   columns `Doing`, `Question`. Keep the\n"
    "   `logical -> native` map; every board write uses the *native* value. Missing\n"
    "   column -> STOP for this project with the missing name. Existing boards\n"
    "   that still carry an extra column keep it: this skill neither reads nor\n"
    "   requires it.\n"
    "3. **Merge permission.** From the resolved project entry read `permissions.pulls.merge`.\n"
)


_BAD_COLUMNS_FIXTURE_EXTRA_COLUMN = (
    "## Preconditions (per project)\n\n"
    "1. **Project resolution.** Resolve the project entry.\n"
    "2. **Board columns.** `list_board_columns(project_id)` must contain the logical\n"
    "   columns `Todo`, `Doing`, `Question`, `Blocked`. Keep the\n"
    "   `logical -> native` map; every board write uses the *native* value. Missing\n"
    "   column -> STOP for this project with the missing name. Existing boards\n"
    "   that still carry an extra column keep it: this skill neither reads nor\n"
    "   requires it.\n"
    "3. **Merge permission.** From the resolved project entry read `permissions.pulls.merge`.\n"
)


def test_assert_run_required_columns_rejects_missing_todo():
    """(#56 R7a, test-critic F2) The helper must actually be able to reject a
    columns list that dropped a required column, not merely re-confirm a
    fixture the test itself wrote to already satisfy the rule."""
    with pytest.raises(AssertionError):
        _assert_run_required_columns(_BAD_COLUMNS_FIXTURE_MISSING_TODO)


def test_assert_run_required_columns_rejects_unexpected_extra_column():
    """(#56 R7a, test-critic F2) 'rejects anything else' includes an
    unexpected extra column such as `Blocked` sitting alongside the required
    three -- a filter that merely keeps the known columns and drops the rest
    would wrongly accept this."""
    with pytest.raises(AssertionError):
        _assert_run_required_columns(_BAD_COLUMNS_FIXTURE_EXTRA_COLUMN)


@pytest.mark.parametrize("kind", ["columns"])
def test_run_contract_survives_done_as_closed(kind):
    """(#56 R7a) The columns rule must hold both on today's SKILL.md (Done
    still a board column) and on a fixture shaped like #45's future rewrite
    (Done gone entirely) -- #56 merges first and cannot predict #45's exact
    prose, only that Done becomes optional/absent. There is no "blocker" case
    here any more: a synthetic fixture for the blocker-resolution prose can
    only prove itself, not that the check verifies #45's actual (not yet
    written) wording -- see the comment above `_assert_blocker_resolved_by_closed`."""
    _assert_run_required_columns(_read(RUN))
    _assert_run_required_columns(_POST_45_COLUMNS_FIXTURE)


def test_review_column_is_gone_from_the_contract():
    """(#31 R2) The column name is absent (case-sensitive, word-bounded) from
    all four documents. Lower-case 'review' (rounds, review-verdict) is fine."""
    for path in (RUN, AGENTS_MD, README, DESCRIPTION):
        m = re.search(r"\bReview\b", _read(path))
        assert m is None, f"{path.name}: 'Review' survives at offset {m.start()}"
    # the lower-case review vocabulary survives where the comment-event
    # contract describes the lower plugin's events (bound to that paragraph)
    paras = re.split(r"\n\s*\n", _read(AGENTS_MD))
    assert any(
        "`review-verdict`" in para and "Events, exhaustive" in para and "`rounds`" in para
        for para in paras
    ), "sweep over-reached: review-verdict gone from the event-vocabulary paragraph"


def test_agents_md_board_table_and_permissions():
    text = _read(AGENTS_MD)
    assert not re.search(r"^\|\s*Review\s*\|", text, re.M)
    bullet = next(l for l in text.splitlines() if "`pulls.merge`" in l and l.startswith("- "))
    assert not re.search(r"still works|optional|not required|not by `run`", bullet, re.I)
    assert re.search(
        r"`run`[^.;]{0,60}(?:requires?|needs?|refus\w*|STOP\w*)|(?:required|needed)\s+by\s+`run`",
        bullet, re.I,
    ), "the pulls.merge bullet must make `run` the subject that requires it (or refuses without it)"


def test_non_conflict_merge_failures_end_in_question():
    """(#31 R3) Protection / permission / unknown-state rows -> add_comment +
    **Question**; conflict rows still route to the rebase retry."""
    text = _read(RUN)
    sect = _slice(text, "**When the merge fails", "**The pre-retry CI check")
    rows = [l for l in sect.splitlines() if l.startswith("|")][2:]
    non_conflict, conflict = [], []
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        kind = cells[1].lower()
        if any(k in kind for k in ("protection", "permission", "unknown")):
            non_conflict.append(row)
        elif re.search(r'"dirty"|"behind"|conflict', row) and "already merged" not in kind:
            conflict.append(row)
    assert len(non_conflict) == 3, non_conflict
    for row in non_conflict:
        assert "add_comment" in row and re.search(r"\*\*Question\*\*", row), row
        assert "worktree_remove" in row and not re.search(r"\bReview\b", row), row
    assert conflict and all(
        "rebase retry" in r and not re.search(r"\*\*Question\*\*", r) for r in conflict
    ), conflict


def test_run_report_vocabulary_drops_review_and_not_permitted():
    text = _read(RUN)
    rep = _slice(text, "### 3. Final report", "## Waiting rule")
    m = re.search(r"result \(([^)]*)\)", rep)
    assert m, "final report must enumerate the result set in `result (...)`"
    assert [x.strip() for x in m.group(1).split("/")] == ["Done", "Question", "Skipped"], m.group(1)
    assert "merge-not-permitted" not in rep


# --- G1: an unprovable criterion is struck and recorded (#40) ---------------
# Supersedes E1 (agent-ticket-orchestrator#33), which promoted such a clause
# to a `pipeline-capability` ticket in a gatekeeper Step 3.4 and blocked the
# package on its `auto:` flavour. That branch is deleted as one seam: the
# clarifier REPORTS the clause (`unprovable_here`) and writes the automatable
# residue into `ac:`, the gatekeeper RECORDS it in the frame comment, triage
# ANSWERS a `blocked` event about it, and nothing is ever created or made to
# wait. Prose executed by an LLM: these assertions pin structure and bind
# outcomes to their triggers by sentence scope; they do not simulate the
# clarifier's judgement.

def _clarifier_step_1d() -> str:
    return _slice(_read(CLARIFIER), "**1d.", "\n2. **Read the code")


def _clarifier_hatch_section() -> str:
    return _slice(
        _read(CLARIFIER), "## When STATUS: CLEAR is not available", "## Worked frames"
    )


def _sentences(text: str) -> list:
    return re.split(r"(?<=[.!?])\s+|\n\s*\n|\n\s*-\s", text)


def _blocks(text: str) -> list:
    """Paragraphs and list items, so an assertion can be scoped to the one
    bullet that documents a given key."""
    return re.split(r"\n\s*\n|\n\s*-\s", text)


_NEGATION = re.compile(r"\bnever\b|\bnot\b|\bnon-|\bexclud|\bexcept\b", re.IGNORECASE)

_REMOVED_VOCABULARY = (
    "needs_pipeline_support", "pipeline-capability", "Step 3.4",
    "Capability split", "gatekeeper:capability", "capability_ticket",
)


def test_capability_split_vocabulary_is_gone():
    for path in (CLARIFIER, GATEKEEPER, TRIAGE):
        text = _read(path)
        for token in _REMOVED_VOCABULARY:
            assert token not in text, f"{token!r} still in {path}"
    clar = _read(CLARIFIER)
    scopes = {
        "frame block": _slice(clar, "<!-- clarifier:frame v1", "-->"),
        "step 1d": _clarifier_step_1d(),
        "hatch section": _clarifier_hatch_section(),
        "worked frames": _slice(clar, "## Worked frames", "## Hard rules"),
    }
    for name, scope in scopes.items():
        for token in ("auto:", "manual:"):
            assert token not in scope, f"{token!r} still in the clarifier's {name}"
    row = next(l for l in _read(AGENTS_MD).splitlines() if l.startswith("| `gatekeeper` |"))
    assert "capability" not in row.lower(), row


def test_clarifier_frame_block_carries_unprovable_here():
    text = _read(CLARIFIER)
    block = _slice(text, "<!-- clarifier:frame v1", "-->")
    line = next((l for l in block.splitlines() if l.startswith("unprovable_here:")), None)
    assert line, "frame block must carry an `unprovable_here:` line"
    assert re.match(r"unprovable_here:\s*none\s*\|\s*<", line), line
    out = _slice(text, "## Output format", "## When STATUS: CLEAR is not available")
    docs = [
        b for b in _blocks(out)
        if "unprovable_here" in b and "clarifier:frame v1" not in b
    ]
    assert docs, "the key needs its own documenting paragraph"
    assert any(
        re.search(r"\brepeatable\b", b)
        and not re.search(r"\bnot repeatable\b|non-repeatable", b)
        and re.search(r"both\s+statuses|CLEAR\s+and\s+NEEDS_INPUT", b)
        for b in docs
    ), "one paragraph must state the key is repeatable AND emitted on both statuses"
    assert any(re.search(r"nothing is created|no ticket", b, re.IGNORECASE) for b in docs), (
        "the documenting paragraph must say the report creates nothing"
    )


def test_clarifier_detection_stays_cheap_and_searches_nothing():
    s = _clarifier_step_1d()
    assert ".github/workflows" in s
    assert "runs-on" in s and "matrix" in s, "step 1d must read runs-on and the matrix"
    for shape in ("another OS", "shell", "artifact", "external service", "person", "release-only"):
        assert shape.lower() in s.lower(), f"flaggable shape {shape!r} missing"
    assert "list_tickets" not in s, "the capability look-up is gone; step 1d searches nothing"
    assert not re.search(r"depends_on:\s*#", s), "step 1d no longer routes anything into depends_on"
    assert any(
        "unprovable_here" in x and "none" in x
        and re.search(r"no shape|names no|not name|does not|nothing", x, re.IGNORECASE)
        for x in _sentences(s)
    ), "the no-shape fallback to `unprovable_here: none` must be stated"
    rules = _slice(_read(CLARIFIER), "## Hard rules", "\n- **Never read outside")
    stay = next(b for b in _blocks(rules) if "Stay inside the package" in b)
    assert not re.search(r"\bmay emit\b|\bone exception\b", stay), (
        f"'Do not propose new tickets' has no exception any more: {stay!r}"
    )
    budget = next(b for b in _blocks(_read(CLARIFIER).split("## Hard rules", 1)[1])
                  if "list_tickets" in b)
    assert "1d" not in budget, f"no list_tickets budget is left for step 1d: {budget!r}"


def test_clarifier_writes_the_residue_and_stays_clear():
    s = _clarifier_step_1d()
    residue = [x for x in _sentences(s) if "residue" in x.lower()]
    assert residue, "step 1d must state the residue rule"
    assert any("`ac:`" in x for x in residue), "the residue goes into `ac:`"
    assert any(
        "as-filed" in x and re.search(r"\bnever\b|\bnot\b", x) for x in _sentences(s)
    ), "`ac:` is never `as-filed` when the residue is the only AC"
    assert any(
        re.search(r"never the performing|not the performing", x) for x in _sentences(s)
    ), "the residue is the artifact, never the performing of the check"
    for scope in (s, _clarifier_hatch_section()):
        assert any(
            "NEEDS_INPUT" in x and re.search(r"\bnever\b", x) for x in _sentences(scope)
        ), "a struck clause never produces NEEDS_INPUT"
        assert "CLEAR" in scope


def test_clarifier_ships_the_struck_clause_worked_frames():
    text = _slice(_read(CLARIFIER), "## Worked frames", "## Hard rules")
    bullets = text.split("\n- **")
    for tag in ("agent-project-issues#347", "lib-python-harness#24"):
        hit = [b for b in bullets if tag in b.split("**", 1)[0]]
        assert hit, f"a worked frame for {tag} is required"
        b = hit[0]
        k = b.find("`unprovable_here: ")
        assert k != -1 and "unprovable_here: none" not in b, b
        ac = re.search(r"`ac: ([^`]+)`", b)
        assert ac and ac.group(1).strip() != "as-filed", f"{tag} must carry a residue ac: {b!r}"
        assert b.rfind("STATUS: CLEAR") > k, f"{tag} must end STATUS: CLEAR after the struck clause"
        assert re.search(r"no\s+ticket\s+is\s+created", b, re.IGNORECASE), b
        assert "NEEDS_INPUT" not in b
    assert bullets and any(
        "`unprovable_here: none`" in b and "STATUS: CLEAR" in b for b in bullets
    ), "a separate negative frame must resolve to `unprovable_here: none`"


def test_gatekeeper_records_the_struck_clause_in_the_frame_comment():
    text = _read(GATEKEEPER)
    step3 = _slice(text, "## Step 3 — clarify each package", "## Step 3.5")
    assert "unprovable_here" in step3
    rendered = "Not proven by this package: <clause>"
    assert rendered in step3, "Step 3 defines the rendering once"
    step4 = _slice(text, "## Step 4", "## Step 5")
    trigger = next(x for x in _sentences(step4) if x.startswith("Post the frame comment"))
    assert "unprovable_here" in trigger, f"Step 4 must post the frame comment for it: {trigger!r}"
    body = _slice(step4, "\n## Frame (gatekeeper)\n", "```")
    assert rendered in body, "the struck-clause line sits inside the frame comment body"
    for sec in (_slice(text, "## Step 3.6", "## Step 3.7"), _slice(text, "## Step 3.7", "## Step 4")):
        assert "Not proven by this package:" in sec
    assert "struck (unprovable here)" in _slice(text, "## Step 5", "## Hard rules")


def test_gatekeeper_creates_nothing_for_an_unprovable_criterion():
    text = _read(GATEKEEPER)
    assert len(_call_spans(text, "create_ticket")) == 2, (
        "exactly two create_ticket call sites remain: the lane split and the epic"
    )
    step35 = _slice(text, "## Step 3.5", "## Step 3.6")
    deps = _slice(step35, "deps =", "for each raw target")
    assert "capab" not in deps.lower() and "unprovable" not in deps, deps
    rules = text.split("## Hard rules", 1)[1]
    rule = [l for l in rules.splitlines() if l.startswith("- **") and "unprovable" in l.lower()]
    assert rule, "a Hard rule must cover the struck criterion"
    for word in ("no ticket", "no relation", "no label"):
        assert word in rule[0], f"{word!r} missing from {rule[0]!r}"


def test_triage_answers_a_blocked_event_about_struck_evidence():
    text = _read(TRIAGE)
    step = _slice(text, "\n6. **", "## Output format")
    assert "Not proven by this package:" in step and "## Frame (gatekeeper)" in step
    answered = [x for x in _sentences(step) if "STATUS: ANSWERED" in x]
    assert answered, "step 6 must end STATUS: ANSWERED"
    for x in answered:
        assert not _NEGATION.search(x), f"the ANSWERED outcome must be affirmative: {x!r}"
    assert "STATUS: ESCALATE" not in step
    worked = _slice(text, "## Worked answers", "## Hard rules")
    b = next(b for b in _blocks(worked) if "Not proven by this package:" in b)
    assert "STATUS: ANSWERED" in b and "ESCALATE" not in b, b


def test_agents_md_records_the_struck_criterion_rule():
    text = _read(AGENTS_MD)
    assert "A capability the PR cannot execute is its own ticket" not in text
    heading = "### A criterion the PR run cannot prove is struck and recorded, never a ticket"
    assert heading in text
    sec = text.split(heading, 1)[1].split("\n### ", 1)[0]
    for token in ("unprovable_here", "residue", "Not proven by this package:", "lib-python-harness"):
        assert token in sec, token
    assert re.search(r"must not read[^.]*gate", sec), "the release layer must not read it as a gate"


# --- F1: triage's test-evidence rule (agent-ticket-orchestrator#35) ---------

def _triage_step_5() -> str:
    return _slice(_read(TRIAGE), "\n5. ", "## Output format")


def _triage_without_worked_answers() -> str:
    text = _read(TRIAGE)
    i = text.find("## Worked answers")
    j = text.index("## Hard rules")
    return text if i == -1 else text[:i] + text[j:]


# A negation bound to the kind itself: "never (a) driving-test", "not a
# driving-test". A negation elsewhere in the sentence does not count.
_NEG_DRIVING = re.compile(
    r"\b(?:never|not|no)\s+(?:\w+\s+){0,2}`?driving-test", re.IGNORECASE
)
# "never leave it at `none`" style: the allowed kinds must not be the negated ones
_NEG_ALLOWED = re.compile(
    r"\b(?:never|not)\s+(?:[\w-]+\s+){0,3}`?(?:none|ci-evidence)\b", re.IGNORECASE
)


# "extract/move/put ... into a script" as an imperative (not "did not extract")
_EXTRACT_IMPERATIVE = re.compile(
    r"\b(?:extract|move|put|split)\w*\b[^.]{0,160}?\b(?:into|to|out to)\b[^.]{0,40}?\bscripts?\b",
    re.IGNORECASE,
)
_REJECTED = re.compile(r"\breject|\binstead of\b|\brather than\b|n't\b|\bfailed\b", re.IGNORECASE)


def test_triage_states_the_test_evidence_rule_once():
    text = _read(TRIAGE)
    step = _triage_step_5()
    rest = _triage_without_worked_answers()
    # stated once: the rule's vocabulary lives in step 5 and nowhere else
    # outside the worked answer (an instance, not a second statement)
    for tok in ("agents/**", "skills/**", "AGENTS.md", "mechanically"):
        assert rest.count(tok) == 1 and tok in step, (
            f"{tok!r} must occur exactly once outside the worked answers, in step 5"
        )
    hard = text.split("## Hard rules", 1)[1]
    assert not re.search(r"\bpin\b|string-presence|test evidence", hard, re.IGNORECASE), (
        "Hard rules must not restate the test-evidence rule"
    )
    sents = _sentences(step)
    # 1: decidable part -> script -> real tests
    assert any(
        _EXTRACT_IMPERATIVE.search(s) and re.search(r"decid", s, re.IGNORECASE)
        and re.search(r"\btests?\b", s, re.IGNORECASE) and not _NEGATION.search(s)
        for s in sents
    ), "one non-negated sentence must say to extract the decidable part into a script, with a test"
    # 2: prose files carry no test; verified by real run or reviewer
    assert any(
        all(g in s for g in ("skills/**", "agents/**", "AGENTS.md"))
        and re.search(r"\bno test\b|carries? no test|without a test", s, re.IGNORECASE)
        and re.search(r"real run|reviewer", s, re.IGNORECASE)
        and not _NEGATION.search(s)
        for s in sents
    ), "one non-negated sentence must tie all three prose globs to: no test, verified by a real run or the reviewer"
    # 3: never recommend a string-presence test, exception names a reader
    # other than the proposed test itself (F1: must not re-license the pin)
    exc = [
        s for s in sents
        if re.search(r"\bnever\b", s, re.IGNORECASE)
        and re.search(r"string|pin", s, re.IGNORECASE)
        and re.search(r"\bunless\b", s, re.IGNORECASE) and "mechanically" in s
    ]
    assert exc, "one sentence must forbid the string-presence test with an `unless ... mechanically` exception"
    assert any(
        re.search(r"besides|other than|apart from|independent", s, re.IGNORECASE)
        for s in exc
    ), "the exception must name a mechanical reader other than a human/model (besides/other than/...)"


def test_triage_never_invents_a_test_kind():
    step = _triage_step_5()
    def has(k, s):
        return re.search(r"(?<![\w-])" + re.escape(k) + r"(?![\w-])", s)
    kinds = ("driving-test", "existing-suite", "ci-evidence", "none")
    for kind in kinds:
        assert has(kind, step), f"step 5 must name the lower plugin's kind {kind!r} as a whole word"
    sents = _sentences(step)
    assert any(
        all(has(k, s) for k in kinds)
        and re.search(r"declared|lower plugin", s, re.IGNORECASE)
        and re.search(r"never invent|only|no other", s, re.IGNORECASE)
        for s in sents
    ), "one sentence must name the four declared kinds as the only ones"
    assert any(
        re.search(r"prose", s, re.IGNORECASE) and re.search(r"`none`|ci-evidence", s)
        and _NEG_DRIVING.search(s) and not _NEG_ALLOWED.search(s)
        for s in sents
    ), "one sentence must tie a prose-only observable to none/ci-evidence and exclude driving-test"


def test_triage_ships_the_122_worked_answer():
    text = _read(TRIAGE)
    sec = _slice(text, "## Worked answers", "## Hard rules")
    bullets = [b for b in _blocks(sec) if "#122" in b]
    assert len(bullets) == 1, "exactly one worked answer for #122"
    b = bullets[0]
    path = "skills/process-ticket/SKILL.md"
    assert path in b
    # the recommendation itself: a non-negated, non-rejected imperative sentence,
    # with the path bound to it (or to the untested-prose statement)
    rec = [x for x in _sentences(b) if _EXTRACT_IMPERATIVE.search(x)
           and not _NEGATION.search(x) and not _REJECTED.search(x)]
    assert rec, "the worked answer must recommend extracting the decidable half into a script"
    assert any(path in x for x in rec) or any(
        path in x and re.search(r"carries? no test|no test", x, re.IGNORECASE)
        for x in _sentences(b)
    ), "the path must be bound to the extraction recommendation or the untested-prose statement"
    m = re.search(r"carries? no test|no test", b, re.IGNORECASE)
    assert m, "the prose half must be declared untested"
    assert any(
        re.search(r"carries? no test|no test", s, re.IGNORECASE)
        and re.search(r"real run|reviewer", s, re.IGNORECASE)
        for s in _sentences(b)
    ), "the untested prose half must name its verification route in the same sentence"
    end = re.search(r"STATUS: ANSWERED", b)
    assert end and end.start() > m.start(), "STATUS: ANSWERED must close the answer"
    for s in _sentences(b):
        if "driving-test" in s:
            assert _NEG_DRIVING.search(s), f"worked answer must not recommend driving-test: {s!r}"


def test_triage_adds_no_new_status():
    text = _read(TRIAGE)
    statuses = set(re.findall(r"STATUS: ([A-Z]+)", text))
    assert statuses == {"ANSWERED", "ESCALATE"}, statuses
