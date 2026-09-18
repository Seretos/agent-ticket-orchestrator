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
# caps a `collision` package at one `large` ticket and owes a `recut` for an
# overlapping large pair the cap rejects (R3), (b) has the gatekeeper reject
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

    assert "#9" in section
    assert "#14" in section
    assert "a second step after #9" in section

    # which verdict it names -- bound with proximity + negation so a section
    # reaching the OPPOSITE verdict (bundle as one epic / collision) does not
    # pass by mere co-presence of the same words.
    assert "depends_on" in section
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
    assert re.search(
        r"\b(not|never|instead of|rather than|no|n't)\b[^.\n]{0,80}\b(collision|epic)\b"
        r"|\b(collision|epic)\b[^.\n]{0,80}\b(not|never|instead of|rather than|no|n't)\b",
        verdict_sentence, re.IGNORECASE,
    ), (
        "expected the collision/epic reading to be explicitly negated/"
        f"rejected in the verdict sentence, not merely mentioned: {verdict_sentence!r}"
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
    assert not re.search(r'"tickets":\s*\[\s*<id>', text), (
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

    # polarity guard: the cap is on `collision` only -- a sentence stating
    # 'at most one ... large' must never also rope in `effort`, which keeps
    # its own independent ~5-ticket cap.
    for sentence in re.split(r"\.\s+", text):
        if "at most one" in sentence.lower():
            assert "effort" not in sentence.lower(), (
                f"the collision-only cap sentence must not mention 'effort': {sentence!r}"
            )

    # edge case: effort's own cap sentence must still be present, untouched.
    assert "~5" in text


def test_bundler_owes_a_recut_for_two_large_overlapping_tickets():
    text = _read(BUNDLER)
    sentences = re.split(r"\.\s+", text)

    overlap_sentence = None
    for s in sentences:
        low = s.lower()
        if ("two" in low or "both" in low) and "large" in low and "overlap" in low:
            overlap_sentence = s
            break
    assert overlap_sentence, (
        "expected a sentence binding two/both + large + overlap -- the "
        "two-large-and-overlapping trigger"
    )
    assert re.search(r"\bmust\b|\balways\b", overlap_sentence, re.IGNORECASE), (
        f"expected an obligation verb (must/always) in: {overlap_sentence!r}"
    )
    assert "recut" in overlap_sentence.lower()

    # negation guard: the obligation must not be softened back into an option.
    for softener in ("optional", "may", "can"):
        assert not re.search(rf"\b{softener}\b", overlap_sentence, re.IGNORECASE), (
            f"the overlap sentence must not contain {softener!r}, which "
            f"would soften the duty back into an option: {overlap_sentence!r}"
        )

    # F3 fix (test-critic round 1): "must" + "recut" co-occurring is also
    # satisfied by a sentence stating the INVERSE duty ("must NOT emit a
    # recut"). Reject any prohibition phrasing outright -- the obligation
    # must be to EMIT a recut on overlap, never to withhold one.
    assert not re.search(r"\bmust\s+not\b|\bnever\b", overlap_sentence, re.IGNORECASE), (
        "the overlap sentence must not contain a prohibition ('must not' / "
        f"'never'), which would state the inverse (forbidden) duty: {overlap_sentence!r}"
    )

    # a separate, opposite-polarity check: the non-overlapping rejected-pair
    # case must be stated as owing NO recut, so an implementation that
    # demands a recut for every rejected large pair (overlapping or not)
    # also fails this test.
    non_overlap_sentence = None
    for s in sentences:
        low = s.lower()
        if "non-overlap" in low or "no overlap" in low or "not overlap" in low:
            non_overlap_sentence = s
            break
    assert non_overlap_sentence, (
        "expected a sentence stating that the non-overlapping rejected pair "
        "emits no recut"
    )
    assert re.search(
        r"\bno\b[^.\n]{0,40}\brecut\b|\brecut\b[^.\n]{0,40}\bno\b",
        non_overlap_sentence, re.IGNORECASE,
    ), f"expected 'no recut' bound in: {non_overlap_sentence!r}"


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
    assert re.search(r'labels=\["?epic"?\]|\bepic\b', materialise_section, re.IGNORECASE), (
        "expected the epic-materialisation body to still name 'epic'"
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
    assert re.search(
        r"(absent|empty)[^.\n]{0,120}changed_by[^.\n]{0,200}previous cut stand"
        r"|changed_by[^.\n]{0,120}(absent|empty)[^.\n]{0,200}previous cut stand",
        section, re.IGNORECASE,
    ), "expected the absent/empty changed_by branch to resolve to 'the previous cut stands'"
    assert re.search(
        r"changed_by[^.\n]{0,120}non-empty[^.\n]{0,200}accept"
        r"|non-empty[^.\n]{0,120}changed_by[^.\n]{0,200}accept",
        section, re.IGNORECASE,
    ), "expected the non-empty changed_by branch to resolve to accepting the new cut"

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
    assert re.search(
        r"gap[^.\n]{0,150}\b(not|never|does not|no longer)\b[^.\n]{0,60}\bmove\w*\b[^.\n]{0,60}\bPlanned\b"
        r"|\b(not|never|does not|no longer)\b[^.\n]{0,60}\bmove\w*\b[^.\n]{0,60}\bPlanned\b[^.\n]{0,150}gap",
        section, re.IGNORECASE,
    ), "expected a sentence binding the 'gap' verdict, a negation of 'move', and 'Planned' together"
    assert not re.search(
        r"\b(not|never|does not|no longer)\b[^.\n]{0,60}\bwithhold\w*\b[^.\n]{0,60}\bPlanned\b",
        section, re.IGNORECASE,
    ), (
        "found the inverted phrasing 'does not withhold ... Planned', "
        "which states the opposite of the gate (the gap NOT blocking Planned)"
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
