"""
Behaviour tests for `scripts/gatekeeper/prose-lane-available.py`: whether
`agent-autonomous-prompt-engineer` is enabled for a project, read from the
settings files a package session (a fresh `claude -p` in a worktree) will
actually see. The plugin is an optional lower plugin, not a dependency, so
this check is what stands between a prose package and a session that starts
a skill that does not exist.

HOME / USERPROFILE point into tmp_path, so the machine's real user settings
never leak into a verdict.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gatekeeper" / "prose-lane-available.py"
KEY = "agent-autonomous-prompt-engineer@agent-marketplace"


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    return {"home": home, "project": project}


def write(path, plugins):
    path.write_text(json.dumps({"enabledPlugins": plugins}), encoding="utf-8")


def check(world, *args):
    env = dict(os.environ, HOME=str(world["home"]), USERPROFILE=str(world["home"]))
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(args or [str(world["project"])])],
        capture_output=True, text=True, env=env,
    )


def test_enabled_in_the_project_settings_is_available(world):
    write(world["project"] / ".claude" / "settings.json",
          {"agent-autonomous-developer@agent-marketplace": True, KEY: True})
    result = check(world)
    assert result.returncode == 0
    assert result.stdout.splitlines()[0] == "prose_lane: available"


def test_only_the_developer_enabled_is_unavailable(world):
    write(world["project"] / ".claude" / "settings.json",
          {"agent-autonomous-developer@agent-marketplace": True})
    result = check(world)
    assert result.returncode == 2
    assert result.stdout.splitlines()[0] == "prose_lane: unavailable"
    assert "source: none" in result.stdout


def test_no_settings_files_at_all_is_unavailable_not_an_error(world):
    result = check(world)
    assert result.returncode == 2
    assert "prose_lane: unavailable" in result.stdout


def test_enabled_at_user_level_counts(world):
    write(world["home"] / ".claude" / "settings.json", {KEY: True})
    assert check(world).returncode == 0


def test_project_false_overrides_user_true(world):
    write(world["home"] / ".claude" / "settings.json", {KEY: True})
    write(world["project"] / ".claude" / "settings.json", {KEY: False})
    result = check(world)
    assert result.returncode == 2
    assert str(world["project"]) in result.stdout, "the deciding file is named"


def test_any_marketplace_counts(world):
    write(world["project"] / ".claude" / "settings.json",
          {"agent-autonomous-prompt-engineer@some-fork": True})
    assert check(world).returncode == 0


def test_a_similarly_named_plugin_does_not_count(world):
    write(world["project"] / ".claude" / "settings.json",
          {"agent-autonomous-prompt-engineer-extras@agent-marketplace": True})
    assert check(world).returncode == 2


def test_settings_local_json_is_not_read(world):
    """Untracked, so a worktree cut from the branch does not contain it."""
    write(world["project"] / ".claude" / "settings.local.json", {KEY: True})
    assert check(world).returncode == 2


def test_a_broken_settings_file_is_skipped(world):
    (world["project"] / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    write(world["home"] / ".claude" / "settings.json", {KEY: True})
    assert check(world).returncode == 0


def test_missing_argument_or_directory_is_unusable(world):
    env = dict(os.environ, HOME=str(world["home"]), USERPROFILE=str(world["home"]))
    no_arg = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env)
    assert no_arg.returncode == 1
    assert check(world, str(world["project"] / "nope")).returncode == 1
