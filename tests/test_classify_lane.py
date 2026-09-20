"""
Behaviour tests for `scripts/gatekeeper/classify-lane.py` (#38): a pure
stdin-JSON -> stdout-verdict helper the gatekeeper's Step 2 pipes each
ticket's footprint paths into, so the lane of a package (`code` / `prose` /
`mixed`) is decided from paths by a program, not by a model's opinion.

Same harness as tests/test_relation_readback.py: module-level path constant,
`subprocess.run` with `sys.executable`, real path lists in, real stdout out.
"""

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CLASSIFY_LANE = REPO_ROOT / "scripts" / "gatekeeper" / "classify-lane.py"


def run_classifier(payload, raw=None):
    return subprocess.run(
        [sys.executable, str(CLASSIFY_LANE)],
        input=raw if raw is not None else json.dumps(payload),
        capture_output=True,
        text=True,
    )


def verdict(result):
    return result.stdout.splitlines()[0]


def path_lines(result):
    """{path: (lane, role)} from the `path: <lane> <role> <path>` lines."""
    out = {}
    for line in result.stdout.splitlines():
        if line.startswith("path: "):
            lane, role, path = line[len("path: "):].split(" ", 2)
            out[path] = (lane, role)
    return out


# --- single-lane verdicts --------------------------------------------------

def test_scripts_tests_workflows_and_manifests_are_code():
    result = run_classifier([
        "scripts/gatekeeper/relation-readback.py",
        "tests/test_relation_readback.py",
        ".github/workflows/lint.yml",
        ".claude-plugin/plugin.json",
        "src/pkg/module.py",
    ])
    assert result.returncode == 0
    assert verdict(result) == "lane: code"
    assert {lane for lane, _ in path_lines(result).values()} == {"code"}


def test_model_executed_files_are_prose():
    result = run_classifier([
        "skills/run/SKILL.md",
        "skills/run/references/merge-table.md",
        "agents/triage.md",
        "AGENTS.md",
        "CLAUDE.md",
        "commands/review.md",
        "prompts/planner.txt",
        "scripts/critic/plan-critic-system-prompt.txt",
        "scripts/critic/test-critic-constraints.md",
    ])
    assert result.returncode == 0
    assert verdict(result) == "lane: prose"
    assert {lane for lane, _ in path_lines(result).values()} == {"prose"}


def test_docs_that_accompany_code_are_code_not_prose():
    """README / docs / description are read by humans, not executed by a
    model -- the table's `everything else` half."""
    result = run_classifier(["README.md", "docs/architecture.md", "description.md", "CHANGELOG.md"])
    assert result.returncode == 0
    assert verdict(result) == "lane: code"


def test_nested_plugin_and_dot_claude_paths_match_like_root_paths():
    result = run_classifier([
        "plugins/agent-x/skills/run/SKILL.md",
        ".claude/agents/reviewer.md",
        ".claude/skills/deploy/SKILL.md",
        "packages/api/AGENTS.md",
    ])
    assert verdict(result) == "lane: prose"


def test_windows_separators_and_dot_slash_are_normalised():
    result = run_classifier(["skills\\run\\SKILL.md", "./agents/bundler.md"])
    assert verdict(result) == "lane: prose"
    assert set(path_lines(result)) == {"skills/run/SKILL.md", "agents/bundler.md"}


def test_a_python_file_named_like_a_skill_directory_is_still_code():
    """Only the markdown a model executes is prose; a script that happens to
    live under skills/ is a program."""
    result = run_classifier(["skills/run/helper.py", "agents/__init__.py"])
    assert verdict(result) == "lane: code"


# --- mixed: the two incident shapes ----------------------------------------

def test_122_shape_script_plus_skill_edit_is_mixed():
    """agent-autonomous-developer#122: a new stagnation-check script plus the
    SKILL.md that calls it."""
    result = run_classifier([
        "scripts/stagnation-check.py",
        "tests/test_stagnation_check.py",
        "skills/process-developer/SKILL.md",
    ])
    assert result.returncode == 0, "mixed is a decided verdict, not an error"
    assert verdict(result) == "lane: mixed"
    lanes = path_lines(result)
    assert lanes["scripts/stagnation-check.py"][0] == "code"
    assert lanes["skills/process-developer/SKILL.md"][0] == "prose"


def test_35_shape_agent_file_plus_its_contract_test_is_mixed():
    """This repo's #35: agents/triage.md plus tests/test_pipeline_contract.py."""
    result = run_classifier(["agents/triage.md", "tests/test_pipeline_contract.py"])
    assert result.returncode == 0
    assert verdict(result) == "lane: mixed"


# --- role hints -------------------------------------------------------------

def test_accompanying_agents_md_note_does_not_turn_a_code_ticket_mixed():
    result = run_classifier([
        {"path": "scripts/start-package-session.sh"},
        {"path": "tests/test_start_package_session.py", "role": "deliverable"},
        {"path": "AGENTS.md", "role": "accompanying"},
    ])
    assert verdict(result) == "lane: code"
    assert "deliverables: 2" in result.stdout.splitlines()
    # the accompanying path is still classified and listed, it just does not vote
    assert path_lines(result)["AGENTS.md"] == ("prose", "accompanying")


def test_accompanying_readme_does_not_turn_a_prose_ticket_mixed():
    result = run_classifier([
        {"path": "agents/clarifier.md"},
        {"path": "README.md", "role": "accompanying"},
    ])
    assert verdict(result) == "lane: prose"


def test_roles_do_not_hide_a_real_mixed_ticket():
    result = run_classifier([
        {"path": "scripts/check.py", "role": "deliverable"},
        {"path": "skills/run/SKILL.md", "role": "deliverable"},
        {"path": "README.md", "role": "accompanying"},
    ])
    assert verdict(result) == "lane: mixed"


def test_no_deliverable_at_all_lets_every_path_vote_and_says_so():
    """The shape nobody can argue from the paths: both halves are marked
    accompanying. The verdict is still printed, and `deliverables: none`
    tells the gatekeeper this is the Question case, not a split."""
    result = run_classifier([
        {"path": "README.md", "role": "accompanying"},
        {"path": "AGENTS.md", "role": "accompanying"},
    ])
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[0] == "lane: mixed"
    assert lines[1] == "deliverables: none"


def test_output_lists_paths_in_input_order():
    paths = ["z/last.py", "agents/a.md", "b/mid.py"]
    result = run_classifier(paths)
    listed = [l.split(" ", 3)[3] for l in result.stdout.splitlines() if l.startswith("path: ")]
    assert listed == paths


# --- unusable input ----------------------------------------------------------

def test_empty_list_is_unusable():
    result = run_classifier([])
    assert result.returncode == 2
    assert "lane:" not in result.stdout


def test_non_json_is_unusable():
    result = run_classifier(None, raw="skills/run/SKILL.md")
    assert result.returncode == 2
    assert "lane:" not in result.stdout


def test_an_object_instead_of_a_list_is_unusable():
    result = run_classifier({"paths": ["a.py"]})
    assert result.returncode == 2


def test_entry_without_a_path_is_unusable():
    result = run_classifier([{"role": "deliverable"}])
    assert result.returncode == 2


def test_unknown_role_is_unusable_and_named():
    result = run_classifier([{"path": "a.py", "role": "maybe"}])
    assert result.returncode == 2
    assert "maybe" in result.stdout


# --- cross-file contract: names a program or another file reads mechanically ---
# Not wording pins: each assertion ties a token one file WRITES to the file
# that READS it (label written by the gatekeeper / read by run; lane flag
# passed by run / parsed by the script; entries named in run / started by
# the script), so a rename on one side alone fails here.

START_SCRIPT = REPO_ROOT / "scripts" / "start-package-session.sh"
GATEKEEPER = REPO_ROOT / "skills" / "gatekeeper" / "SKILL.md"
RUN = REPO_ROOT / "skills" / "run" / "SKILL.md"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"


def _text(path):
    return path.read_text(encoding="utf-8")


def _script_entries():
    import re
    return dict(re.findall(r'^\s*(code|prose)\)\s+ENTRY="([^"]+)"', _text(START_SCRIPT), re.M))


def test_run_names_exactly_the_entries_the_script_starts():
    entries = _script_entries()
    assert set(entries) == {"code", "prose"}
    step_2b = _text(RUN).split("**b. Start the package session", 1)[1].split("**c. Read the ticket", 1)[0]
    for entry in entries.values():
        assert entry in step_2b, f"run's dispatch step must name {entry}"
    assert "--lane prose" in step_2b


def test_the_lane_label_is_the_same_token_on_both_sides():
    assert 'labels_add=["lane:prose"]' in _text(GATEKEEPER)
    assert "`lane:prose`" in _text(RUN)


def test_every_started_lower_plugin_is_a_declared_dependency():
    deps = {d["name"] for d in json.loads(_text(PLUGIN_JSON))["dependencies"]}
    for entry in _script_entries().values():
        plugin = entry.lstrip("/").split(":", 1)[0]
        assert plugin in deps, f"{plugin} is started by the script but not declared in plugin.json"


def test_both_candidate_enumerations_carry_the_ignore_filter():
    """#37: the exclusion lives in Step 1's list_tickets calls and nowhere
    else -- both columns, same label token as the report-only calls."""
    import re
    step1 = _text(GATEKEEPER).split("## Step 1", 1)[1].split("## Step 2", 1)[0]
    calls = re.findall(r"^list_tickets\(.*\)$", step1, re.M)
    excluded = [c for c in calls if 'not_labels=["gatekeeper-ignore"]' in c]
    counted = [c for c in calls if ' labels=["gatekeeper-ignore"]' in c]
    for group in (excluded, counted):
        assert sorted(re.search(r'column="(\w+)"', c).group(1) for c in group) == ["Backlog", "Question"]
    assert len(calls) == 4
    later = _text(GATEKEEPER).split("## Step 2", 1)[1].split("## Hard rules", 1)[0]
    assert "gatekeeper-ignore" not in later.replace("ignored (gatekeeper-ignore)", ""), (
        "no per-step label check after Step 1"
    )
