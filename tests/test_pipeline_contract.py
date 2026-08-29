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
form is the "Two worked frames" section in `agents/clarifier.md`, and
`test_clarifier_ships_the_two_worked_frames` below is the one test that
checks it — it is not a substitute for actually running the clarifier against
a real ticket.
"""

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = REPO_ROOT / "skills" / "run" / "SKILL.md"
GATEKEEPER = REPO_ROOT / "skills" / "gatekeeper" / "SKILL.md"
AGENTS_DIR = REPO_ROOT / "agents"
TRIAGE = AGENTS_DIR / "triage.md"
BUNDLER = AGENTS_DIR / "bundler.md"
CLARIFIER = AGENTS_DIR / "clarifier.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"
README = REPO_ROOT / "README.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
LINT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "lint.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _slice(text: str, start: str, end: str) -> str:
    """The text between the first occurrence of `start` and the first
    occurrence of `end` after it — used to scope an assertion to one
    section instead of the whole file."""
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


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
    text = _read(RELEASE_WORKFLOW)
    assert "jq -n" in text
    # the old unquoted `-d @- <<EOF ... ${VAR} ...` pattern must be gone for
    # the dispatch step specifically -- a multi-line changelog would break it.
    # A real heredoc use is an unindented `<<EOF` starting a shell line, not
    # this pattern mentioned in an explanatory comment (`# ... <<EOF ...`).
    dispatch_step = text.split("Dispatch to agent-marketplace", 1)[1]
    assert not re.search(r"^\s*[^#\n]*<<EOF", dispatch_step, re.MULTILINE)
    assert '-d "$PAYLOAD"' in dispatch_step


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


def test_run_defines_resolved_as_done_column_or_closed_off_board():
    text = _read(RUN)
    assert "### When is a blocker resolved" in text
    section = _slice(text, "### When is a blocker resolved", "### 2. Per package, sequentially")
    assert "custom_fields" in section
    assert "Done" in section
    assert "closed" in section
    assert "Closes #<n>" in section


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
    section = _slice(text, "## When STATUS: CLEAR is not available", "## Two worked frames")
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
    section = _slice(text, "## When STATUS: CLEAR is not available", "## Two worked frames")
    for category in ("refactor", "docs", "ci"):
        assert category in section.lower()


def test_clarifier_escape_hatch_is_closed_for_bug_tickets():
    text = _read(CLARIFIER)
    section = _slice(text, "## When STATUS: CLEAR is not available", "## Two worked frames")
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
    section = _slice(text, "## Two worked frames", "## Hard rules")
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


def test_gatekeeper_posts_the_frame_comment_when_the_ac_was_rewritten():
    text = _read(GATEKEEPER)
    assert "## Frame (gatekeeper)" in text
    assert "as-filed" in text
    assert "context-extractor" in text


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


# --- cross-cutting: LF only (Claude Code silently ignores CRLF) ------------

def test_every_parsed_markdown_file_is_lf_only():
    """lint.yml only checks skills/*/SKILL.md and agents/*.md for CRLF; this
    closes the gap for AGENTS.md, CLAUDE.md and README.md too, and matters
    concretely here because these edits were made on Windows."""
    paths = [AGENTS_MD, CLAUDE_MD, README, RUN, GATEKEEPER, BUNDLER, CLARIFIER, TRIAGE]
    offenders = [str(p) for p in paths if b"\r\n" in p.read_bytes()]
    assert offenders == []
