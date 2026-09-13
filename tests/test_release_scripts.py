"""
Driving tests for the two standalone bash scripts extracted from
`.github/workflows/release.yml` (agent-ticket-orchestrator#15):

- `.github/scripts/prev-release-tag.sh` — resolves the previous release tag
  for changelog generation, with an explicit semver ordering (never
  `sort -V`, which gets release-vs-prerelease precedence backwards) and a
  `--validate <version>` mode that is the one place the semver grammar lives.
- `.github/scripts/marketplace-payload.sh` — builds the `agent-marketplace`
  dispatch JSON payload with `jq -n`, byte-for-byte preserving a hostile
  changelog and omitting the `changelog` key entirely when it is empty.

Neither script exists yet at the time these tests are written (phase=tests,
RED only). Both are invoked as `bash <script>`, never relying on the exec
bit (Git-for-Windows does not preserve it) and never resolving a bare
`bash` from PATH on Windows, where it can resolve to the WSL stub instead
of Git Bash (agent-comfy CI round 1 incident, see AGENTS.md).
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / ".github" / "scripts"
PREV_RELEASE_TAG = SCRIPTS_DIR / "prev-release-tag.sh"
MARKETPLACE_PAYLOAD = SCRIPTS_DIR / "marketplace-payload.sh"

# --- bash resolution --------------------------------------------------------
#
# On win32 we require Git Bash at its well-known absolute path. We do NOT
# pytest.skip() when it is absent: a skip here would let the Windows leg of
# the lint.yml CI matrix report false-green without ever exercising these
# scripts (this is a deliberate deviation from "skip if absent" per
# plan-critic feedback). A missing Git Bash on win32 is a real environment
# problem, so we let it fail loudly instead, as a normal assertion error.
if sys.platform == "win32":
    _BASH_PATH = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
    assert _BASH_PATH.is_file(), (
        f"Git Bash not found at {_BASH_PATH}. This is required to run "
        "the release scripts' tests on Windows; a bare 'bash' from PATH "
        "can resolve to the WSL stub instead (agent-comfy CI round 1), "
        "and skipping here would let the Windows CI leg report "
        "false-green without ever exercising these scripts."
    )
    BASH = str(_BASH_PATH)
else:
    BASH = "/bin/bash"


def run_prev_release_tag(tag_being_created, tags):
    """`git tag -l | bash prev-release-tag.sh "$TAG"` — tags fed on stdin,
    one per line; no git repo needed."""
    stdin = "".join(t + "\n" for t in tags)
    return subprocess.run(
        [BASH, str(PREV_RELEASE_TAG), tag_being_created],
        input=stdin,
        capture_output=True,
        text=True,
    )


def run_prev_release_tag_validate(version):
    return subprocess.run(
        [BASH, str(PREV_RELEASE_TAG), "--validate", version],
        input="",
        capture_output=True,
        text=True,
    )


DEFAULT_PAYLOAD_ENV = {
    "NAME": "agent-ticket-orchestrator",
    "DESC": "Pure skill + agents plugin for the ticket pipeline.",
    "REPO": "Seretos/agent-ticket-orchestrator",
    "VERSION": "0.1.5",
    "TAG": "agent-ticket-orchestrator--v0.1.5",
    "CHANGELOG": "* some change (#1)",
}


def run_marketplace_payload(env_overrides=None):
    env = dict(os.environ)
    env.update(DEFAULT_PAYLOAD_ENV)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [BASH, str(MARKETPLACE_PAYLOAD)],
        input="",
        capture_output=True,
        text=True,
        env=env,
    )


def expected_icon(repo, tag):
    return f"https://raw.githubusercontent.com/{repo}/{tag}/assets/icon.png"


def expected_description_url(repo, tag):
    return f"https://raw.githubusercontent.com/{repo}/{tag}/description.md"


# =============================================================================
# prev-release-tag.sh
# =============================================================================


def test_prev_release_tag_three_way_numeric_ordering_not_lexicographic():
    # 0.1.10 > 0.1.4 numerically; a lexicographic/`sort -V`-style bug could
    # get this wrong in either direction. Pin the exact three-way winner.
    result = run_prev_release_tag(
        "agent-x--v0.3.0",
        ["agent-x--v0.2.0", "agent-x--v0.1.10", "agent-x--v0.1.4"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "agent-x--v0.2.0"


def test_prev_release_tag_numeric_not_lexicographic_pairwise():
    # Isolate the 0.1.10 vs 0.1.4 comparison: lexicographically "0.1.10" <
    # "0.1.4" (because '1' < '4' at that char position), the wrong answer.
    result = run_prev_release_tag(
        "agent-x--v0.3.0",
        ["agent-x--v0.1.10", "agent-x--v0.1.4"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "agent-x--v0.1.10"


def test_prev_release_tag_release_beats_prerelease_of_same_version():
    result = run_prev_release_tag(
        "agent-x--v1.0.1",
        ["agent-x--v1.0.0", "agent-x--v1.0.0-rc.3"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "agent-x--v1.0.0"


def test_prev_release_tag_prerelease_numeric_identifiers_compare_numerically():
    # rc.10 must beat rc.2 numerically, not lexicographically (which would
    # put "rc.10" before "rc.2").
    result = run_prev_release_tag(
        "agent-x--v1.0.1",
        ["agent-x--v1.0.0-rc.10", "agent-x--v1.0.0-rc.2"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "agent-x--v1.0.0-rc.10"


def test_prev_release_tag_three_way_numeric_ordering_winner_not_first():
    # Same three-way comparison as above, but the winner is listed LAST on
    # stdin -- a `head -1`-after-filtering script (no real comparison) would
    # print the wrong tag here even though it happens to pass the sibling
    # test above where the winner is first.
    result = run_prev_release_tag(
        "agent-x--v0.3.0",
        ["agent-x--v0.1.4", "agent-x--v0.1.10", "agent-x--v0.2.0"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "agent-x--v0.2.0"


def test_prev_release_tag_release_beats_prerelease_winner_not_first():
    # Same as test_prev_release_tag_release_beats_prerelease_of_same_version
    # but with the prerelease listed first, so a script that just returns
    # the first surviving line would fail this one.
    result = run_prev_release_tag(
        "agent-x--v1.0.1",
        ["agent-x--v1.0.0-rc.3", "agent-x--v1.0.0"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "agent-x--v1.0.0"


def test_prev_release_tag_prerelease_numeric_identifiers_winner_not_first():
    # Same as test_prev_release_tag_prerelease_numeric_identifiers_compare_numerically
    # but with rc.10 listed second, so a first-line-wins script would fail.
    result = run_prev_release_tag(
        "agent-x--v1.0.1",
        ["agent-x--v1.0.0-rc.2", "agent-x--v1.0.0-rc.10"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "agent-x--v1.0.0-rc.10"


def test_prev_release_tag_excludes_the_tag_being_created_even_if_listed():
    result = run_prev_release_tag(
        "agent-x--v0.1.5",
        ["agent-x--v0.1.4", "agent-x--v0.1.5"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "agent-x--v0.1.4"


def test_prev_release_tag_excludes_a_different_plugin_prefix():
    result = run_prev_release_tag(
        "agent-x--v0.2.0",
        ["other-plugin--v9.9.9", "agent-x--v0.1.4"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "agent-x--v0.1.4"


def test_prev_release_tag_excludes_src_star_tags():
    result = run_prev_release_tag(
        "agent-x--v0.2.0",
        ["src/agent-x--v0.1.4", "agent-x--v0.1.3"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "agent-x--v0.1.3"


def test_prev_release_tag_src_star_never_treated_as_a_release_tag_even_alone():
    # Only a src/* marker present (no real release tag) -> no candidate.
    result = run_prev_release_tag(
        "agent-x--v0.2.0",
        ["src/agent-x--v0.1.9"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_prev_release_tag_empty_stdin_is_first_release_not_an_error():
    result = run_prev_release_tag("agent-x--v0.1.0", [])
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_prev_release_tag_no_matching_prefix_is_first_release_not_an_error():
    result = run_prev_release_tag(
        "agent-x--v0.1.0",
        ["other-plugin--v9.9.9"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_prev_release_tag_only_the_tag_being_created_in_list_yields_empty():
    result = run_prev_release_tag(
        "agent-x--v0.1.0",
        ["agent-x--v0.1.0"],
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_prev_release_tag_excludes_candidates_newer_than_the_tag_being_created():
    # Out-of-order/backport release: v0.1.4 already exists and we are now
    # cutting v0.1.3. The candidate filter must exclude not just the exact
    # tag being created but also anything numerically >= it -- otherwise
    # PREV_TAG would resolve to v0.1.4 (newer than the release being cut),
    # reversing the notes baseline instead of finding the true predecessor
    # v0.1.2. PREV_TAG must never equal v0.1.4 or v0.1.3, only v0.1.2.
    result = run_prev_release_tag(
        "agent-x--v0.1.3",
        ["agent-x--v0.1.4", "agent-x--v0.1.2"],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "agent-x--v0.1.2"


def test_prev_release_tag_handles_arbitrarily_long_numeric_identifiers():
    # The script's own --validate grammar (`(0|[1-9][0-9]*)`) puts no
    # digit-count limit on a numeric field, so a syntactically valid major
    # version can be long enough to overflow bash's fixed-width `((...))`
    # arithmetic (typically 64-bit signed) and silently produce a wrong
    # ordering. 100000000000000000000 (21 digits, 10^20) is one digit longer
    # than 99999999999999999999 (20 digits, 10^20 - 1) and must still
    # compare as the greater version.
    result = run_prev_release_tag(
        "agent-x--v100000000000000000001.0.0",
        [
            "agent-x--v99999999999999999999.0.0",
            "agent-x--v100000000000000000000.0.0",
        ],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "agent-x--v100000000000000000000.0.0"


def test_prev_release_tag_skips_malformed_garbage_and_blank_lines():
    result = run_prev_release_tag(
        "agent-x--v0.2.0",
        ["", "garbage-not-a-tag", "agent-x--vNOTSEMVER", "agent-x--v0.1.2"],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "agent-x--v0.1.2"


@pytest.mark.parametrize(
    "version",
    ["0.1.4", "0.1.4-rc.1", "0.0.1", "10.20.30", "1.0.0-alpha.1"],
)
def test_prev_release_tag_validate_accepts_strict_semver(version):
    result = run_prev_release_tag_validate(version)
    assert result.returncode == 0, (
        f"expected {version!r} to be accepted: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


@pytest.mark.parametrize(
    "version",
    [
        "01.2.3",  # leading zero
        "1.2",  # missing patch
        "v1.2.3",  # tag prefix, not a bare version
        "1.2.3+build",  # build metadata not allowed per this plan's grammar
    ],
)
def test_prev_release_tag_validate_rejects_invalid_versions(version):
    result = run_prev_release_tag_validate(version)
    assert result.returncode != 0, (
        f"expected {version!r} to be rejected but got exit 0: "
        f"stdout={result.stdout!r}"
    )
    # A nonzero exit alone is not enough evidence: bash also exits 127 when
    # the script file itself is missing, which would make this assertion
    # pass vacuously regardless of whether validation logic exists at all.
    # Rule that out explicitly so this test is RED (for the right reason)
    # while the script doesn't exist, and GREEN only once real validation
    # rejects the version.
    assert "No such file or directory" not in result.stderr, (
        f"script not found, not a validation rejection: {result.stderr!r}"
    )


# =============================================================================
# marketplace-payload.sh
# =============================================================================

HOSTILE_CHANGELOG = (
    'Line with a "double quote" and a \'single quote\'.\n'
    "Backtick line: `echo hi`\n"
    "Dollar var: $HOME and ${SOME_VAR}\n"
    "Backslash: C:\\Users\\arnev\\file.txt\n"
    "EOF\n"
    "Trailing line after a literal EOF marker."
)


def test_marketplace_payload_round_trips_hostile_changelog_byte_for_byte():
    result = run_marketplace_payload({"CHANGELOG": HOSTILE_CHANGELOG})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["client_payload"]["changelog"] == HOSTILE_CHANGELOG


def test_marketplace_payload_round_trips_a_trailing_newline_byte_for_byte():
    # HOSTILE_CHANGELOG above never ends in a newline; a script that reads
    # CHANGELOG via unguarded `$(...)` command substitution silently strips
    # trailing newlines, and this case is the only one that would catch it.
    changelog = "* did a thing (#1)\n* did another thing (#2)\n"
    result = run_marketplace_payload({"CHANGELOG": changelog})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["client_payload"]["changelog"] == changelog


def test_marketplace_payload_omits_changelog_key_when_empty():
    result = run_marketplace_payload({"CHANGELOG": ""})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "changelog" not in payload["client_payload"]


def test_marketplace_payload_whitespace_only_changelog_is_not_treated_as_empty():
    result = run_marketplace_payload({"CHANGELOG": "   "})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "changelog" in payload["client_payload"]
    assert payload["client_payload"]["changelog"] == "   "


@pytest.mark.parametrize(
    "env",
    [
        {
            "NAME": "agent-ticket-orchestrator",
            "DESC": "Pure skill + agents plugin for the ticket pipeline.",
            "REPO": "Seretos/agent-ticket-orchestrator",
            "VERSION": "0.1.5",
            "TAG": "agent-ticket-orchestrator--v0.1.5",
            "CHANGELOG": "* did a thing (#42)",
        },
        # Different values on every varying field -- a script that
        # hardcoded the five literals from the case above would pass that
        # one and fail this one, since the output no longer tracks input.
        {
            "NAME": "agent-worktree",
            "DESC": "A completely different plugin description.",
            "REPO": "Seretos/agent-worktree",
            "VERSION": "2.3.4-beta.1",
            "TAG": "agent-worktree--v2.3.4-beta.1",
            "CHANGELOG": "* something else entirely (#7)",
        },
    ],
)
def test_marketplace_payload_populates_fields_from_env(env):
    result = run_marketplace_payload(env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["event_type"] == "plugin-release"
    cp = payload["client_payload"]
    assert cp["name"] == env["NAME"]
    assert cp["description"] == env["DESC"]
    assert cp["repo"] == env["REPO"]
    assert cp["version"] == env["VERSION"]
    assert cp["ref"] == env["TAG"]
    assert cp["category"] == "skill"
    assert cp["icon"] == expected_icon(env["REPO"], env["TAG"])
    assert cp["description_url"] == expected_description_url(env["REPO"], env["TAG"])


def test_marketplace_payload_icon_and_description_url_survive_special_tag_chars():
    # A tag containing '+'/'.' characters must still produce the same
    # well-formed URL shape as today's inline logic (or, at minimum, not
    # crash) — this is a lossless extraction, not a redesign.
    env = {
        "NAME": "agent-ticket-orchestrator",
        "DESC": "desc",
        "REPO": "Seretos/agent-ticket-orchestrator",
        "VERSION": "0.1.5-rc.1+build.5",
        "TAG": "agent-ticket-orchestrator--v0.1.5-rc.1+build.5",
        "CHANGELOG": "notes",
    }
    result = run_marketplace_payload(env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    cp = payload["client_payload"]
    # The equality checks above already pin the exact URL string (scheme,
    # host and both dynamic segments included); a separate regex-shape
    # assertion here would be logically implied by them and could never
    # independently fail, so it is omitted (YAGNI).
    assert cp["icon"] == expected_icon(env["REPO"], env["TAG"])
    assert cp["description_url"] == expected_description_url(env["REPO"], env["TAG"])
