#!/usr/bin/env python3
"""Regression tests for the dotfiles-managed Claude Code configuration.

Stdlib only, no network:

    python3 -m unittest discover -s tests -v

These cover the Stop-hook interpreter contract. The hook shipped as
`python ~/.claude/hooks/langfuse_hook.py`, but the Coder workspace image
installs `python3`/`python3-pip` and no `python-is-python3`, so every Stop
raised `command not found: python`.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SETTINGS_TEMPLATE = REPO / "config" / "claude" / "settings.json"
HOOK_SCRIPT = REPO / "config" / "claude" / "hooks" / "langfuse_hook.py"
MERGE_SCRIPT = REPO / "scripts" / "merge-claude-settings.py"

PLUGIN_KEY = "langfuse-observability@langfuse-observability"

# `\bpython\b` does not match `python3` -- the trailing `\b` needs a non-word
# character after `python`, and `3` is a word character.
BARE_PYTHON = re.compile(r"\bpython\b")

PEP_723_BLOCK = re.compile(r"^# /// script$(.*?)^# ///$", re.MULTILINE | re.DOTALL)


def load_merge_module():
    """Import scripts/merge-claude-settings.py, whose name is not importable."""
    spec = importlib.util.spec_from_file_location("merge_claude_settings", MERGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hook_commands(settings, event=None):
    events = settings.get("hooks", {})
    for name, groups in events.items():
        if event is not None and name != event:
            continue
        for group in groups:
            for hook in group.get("hooks", []):
                if "command" in hook:
                    yield hook["command"]


class HookInterpreterTests(unittest.TestCase):
    """The bug: a hook naming an interpreter the target machines do not have."""

    def setUp(self):
        self.settings = json.loads(SETTINGS_TEMPLATE.read_text())

    def test_no_hook_invokes_bare_python(self):
        commands = list(hook_commands(self.settings))
        self.assertTrue(commands, "settings template declares no hook commands")
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(
                    BARE_PYTHON.search(command),
                    "hook invokes `python`, which does not exist on the Coder "
                    "workspace image (python3 only) -- use `python3` or "
                    "`uv run --script`",
                )

    def test_stop_hook_prefers_uv_and_falls_back_to_python3(self):
        # uv provisions langfuse from the hook's inline metadata on machines
        # that have it; python3 keeps the hook runnable on machines that do not.
        # Both halves matter because these dotfiles are installed on personal
        # workstations as well as Coder workspaces.
        commands = list(hook_commands(self.settings, event="Stop"))
        self.assertTrue(commands, "settings template declares no Stop hook")
        joined = "\n".join(commands)
        self.assertIn("uv run", joined)
        self.assertIn("python3", joined)

    def test_hook_paths_are_absolute_after_home_substitution(self):
        # setup-claude.sh renders __HOME__ before merging. Relying on the shell
        # to expand `~` instead couples the hook to how Claude Code spawns it.
        for command in hook_commands(self.settings):
            with self.subTest(command=command):
                self.assertNotIn("~/", command)


class HookScriptTests(unittest.TestCase):
    def test_declares_langfuse_dependency_inline(self):
        block = PEP_723_BLOCK.search(HOOK_SCRIPT.read_text())
        self.assertIsNotNone(
            block,
            "hook has no PEP 723 inline metadata block, so `uv run --script` "
            "cannot provision langfuse and the hook silently no-ops",
        )
        self.assertIn("langfuse", block.group(1))


class PluginGuardTests(unittest.TestCase):
    """The hook must stand down when the langfuse-observability plugin is live.

    The Coder workspace image installs that plugin, which registers its own Stop
    hook over the same transcript. Personal workstations do not have it. Without
    a guard a workspace session is traced twice.
    """

    def _run_hook(self, *, plugin_installed, plugin_enabled=True):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            claude_dir = tmp / ".claude"
            (claude_dir / "plugins").mkdir(parents=True)

            plugins = {"version": 2, "plugins": {}}
            settings = {}
            if plugin_installed:
                plugins["plugins"][PLUGIN_KEY] = [{"scope": "user", "version": "1.0.0"}]
                settings["enabledPlugins"] = {PLUGIN_KEY: plugin_enabled}
            (claude_dir / "plugins" / "installed_plugins.json").write_text(
                json.dumps(plugins)
            )
            (claude_dir / "settings.json").write_text(json.dumps(settings))

            # Stub the SDK so the test never reaches PyPI or the network. It
            # records having been imported, and that is the signal the tests
            # assert on: the guard runs *before* the langfuse import precisely
            # so the plugin case costs no dependency resolution.
            marker = tmp / "sdk-imported"
            stub_dir = tmp / "stub"
            stub_dir.mkdir()
            (stub_dir / "langfuse.py").write_text(
                textwrap.dedent(
                    f"""
                    from pathlib import Path
                    Path({str(marker)!r}).write_text("imported")

                    class Langfuse:
                        def __init__(self, **kwargs):
                            raise AssertionError("test must not build a client")

                    def propagate_attributes(**kwargs):
                        raise AssertionError("test must not emit a trace")
                    """
                )
            )

            env = {
                **os.environ,
                "HOME": str(tmp),
                "CLAUDE_CONFIG_DIR": str(claude_dir),
                "PYTHONPATH": str(stub_dir),
                "TRACE_TO_LANGFUSE": "true",
                "CC_LANGFUSE_PUBLIC_KEY": "pk-test",
                "CC_LANGFUSE_SECRET_KEY": "sk-test",
            }
            proc = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT)],
                input="{}",
                text=True,
                capture_output=True,
                env=env,
                timeout=60,
            )
            return proc, marker.exists()

    def test_no_ops_when_plugin_owns_the_stop_hook(self):
        proc, sdk_imported = self._run_hook(plugin_installed=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(
            sdk_imported,
            "hook loaded the langfuse SDK even though the plugin already "
            "traces this session",
        )

    def test_runs_when_plugin_is_absent(self):
        proc, sdk_imported = self._run_hook(plugin_installed=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(
            sdk_imported,
            "hook stood down on a machine with no langfuse plugin, so nothing "
            "would trace the session",
        )

    def test_runs_when_plugin_is_installed_but_disabled(self):
        # entrypoint.sh disables the plugin in task workspaces; a disabled
        # plugin registers no hook, so ours is not a duplicate.
        proc, sdk_imported = self._run_hook(
            plugin_installed=True, plugin_enabled=False
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(sdk_imported)


class MergeHooksTests(unittest.TestCase):
    """merge_hooks appended and never removed, so a fixed template would land
    *beside* the broken hook in every already-provisioned workspace rather than
    replacing it -- two Stop hooks, one of them still erroring."""

    def setUp(self):
        self.merge = load_merge_module()
        self.stale = {
            "hooks": [
                {"type": "command", "command": "python ~/.claude/hooks/langfuse_hook.py"}
            ]
        }
        self.current = {
            "hooks": [
                {
                    "type": "command",
                    "command": (
                        "if command -v uv >/dev/null 2>&1; then exec uv run --quiet "
                        "--script /home/engineer/.claude/hooks/langfuse_hook.py; "
                        "else exec python3 "
                        "/home/engineer/.claude/hooks/langfuse_hook.py; fi"
                    ),
                }
            ]
        }

    def test_stale_langfuse_group_is_replaced_not_duplicated(self):
        merged = self.merge.merge_hooks({"Stop": [self.stale]}, {"Stop": [self.current]})
        self.assertEqual(merged["Stop"], [self.current])

    def test_stale_group_is_pruned_from_events_the_template_no_longer_declares(self):
        merged = self.merge.merge_hooks(
            {"SessionEnd": [self.stale]}, {"Stop": [self.current]}
        )
        self.assertNotIn("SessionEnd", merged)
        self.assertEqual(merged["Stop"], [self.current])

    def test_unrelated_user_hook_groups_are_preserved(self):
        user_group = {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "/opt/claude-config/hooks/scan.sh"}],
        }
        merged = self.merge.merge_hooks(
            {"PreToolUse": [user_group], "Stop": [self.stale]},
            {"Stop": [self.current]},
        )
        self.assertEqual(merged["PreToolUse"], [user_group])
        self.assertEqual(merged["Stop"], [self.current])

    def test_user_hooks_on_the_same_event_survive_the_prune(self):
        user_group = {
            "hooks": [{"type": "command", "command": "notify-send done"}]
        }
        merged = self.merge.merge_hooks(
            {"Stop": [self.stale, user_group]}, {"Stop": [self.current]}
        )
        self.assertEqual(merged["Stop"], [user_group, self.current])

    def test_merge_is_idempotent(self):
        once = self.merge.merge_hooks({"Stop": [self.stale]}, {"Stop": [self.current]})
        twice = self.merge.merge_hooks(once, {"Stop": [self.current]})
        self.assertEqual(once, twice)


class RenderedTemplateTests(unittest.TestCase):
    """End-to-end: render the template the way setup-claude.sh does, merge it
    over a settings.json carrying the broken hook, and check what lands."""

    def test_sync_heals_a_workspace_that_already_has_the_broken_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rendered = tmp / "rendered.json"
            rendered.write_text(
                SETTINGS_TEMPLATE.read_text().replace("__HOME__", "/home/engineer")
            )

            target = tmp / "settings.json"
            target.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python ~/.claude/hooks/langfuse_hook.py",
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                )
            )

            subprocess.run(
                [sys.executable, str(MERGE_SCRIPT), str(rendered), str(target)],
                check=True,
                capture_output=True,
            )

            merged = json.loads(target.read_text())
            commands = list(hook_commands(merged, event="Stop"))
            self.assertEqual(len(commands), 1, f"expected one Stop hook, got {commands}")
            self.assertIsNone(BARE_PYTHON.search(commands[0]), commands[0])


if __name__ == "__main__":
    unittest.main()
