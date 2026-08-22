#!/usr/bin/env python3
"""Tmux launcher/state CLI for the tmux-deliver skill.

Runs the `deliver` review-gated TDD loop with delivery agents and reviewers in
separate tmux windows, each backed by either Claude Code or the OpenAI Codex CLI.

Roles per work unit:
  implementer  - claude | codex, model/effort from `init` (or per-call override)
  qa           - always codex, gpt-5.6-sol / xhigh
  adversarial  - always codex, gpt-5.6-sol / xhigh

Integration model: one `tmux-deliver/<slug>` branch checked out in a dedicated
integration worktree. Each unit gets its own worktree branched from the
integration branch; `accept` merges the unit branch back with --no-ff. The
invoking user's working tree is never touched.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# --- runtime / model configuration -----------------------------------------

CODEX_MODELS = {
    "luna": "gpt-5.6-luna",
    "sol": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "gpt-5.5": "gpt-5.5",
    "gpt-5.4": "gpt-5.4",
    "gpt-5.4-mini": "gpt-5.4-mini",
}
CLAUDE_MODELS = {"opus", "sonnet", "haiku", "fable"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
CODEX_ONLY_EFFORTS = {"ultra"}  # gpt-5.6-sol only

DEFAULTS = {
    "codex": {"model": "luna", "effort": "max"},
    "claude": {"model": "opus", "effort": "medium"},
}

# Reviewers are pinned by policy: always Sol on xhigh, always Codex.
REVIEWER_RUNTIME = "codex"
REVIEWER_MODEL = "sol"
REVIEWER_EFFORT = "xhigh"

# Codex agent definitions whose personas the reviewer windows adopt.
CODEX_PERSONAS = {
    "qa": "~/.codex/agents/qa-code-reviewer.toml",
    "adversarial": "~/.codex/agents/adversarial-reviewer.toml",
}

ROLES = ("implementer", "qa", "adversarial")
REVIEW_ROLES = ("qa", "adversarial")

UNIT_STATUSES = {
    "queued",
    "launched",
    "delivering",
    "delivered",
    "reviewing",
    "reviewed",
    "changes-requested",
    "accepted",
    "blocked",
    "failed",
    "escalated",
    "cancelled",
}
UNIT_ACTIVE = {
    "launched",
    "delivering",
    "delivered",
    "reviewing",
    "reviewed",
    "changes-requested",
    "blocked",
}
UNIT_TERMINAL = {"accepted", "cancelled"}

# Role statuses. `stale` and `notified` exist so the recorded status can never
# claim work that was never dispatched:
#   stale    - the round moved on and NOTHING has been sent to this role since.
#   notified - the orchestrator dispatched to it; the agent has not acknowledged yet.
# Only `running` is ever written by an agent.
ROLE_STATUSES = {"launched", "running", "done", "blocked", "failed", "stale", "notified"}
# Statuses an agent is allowed to report. `stale`/`notified` are orchestrator-side,
# and `launched` is launcher-written — an agent that could report it would make
# "still at `launched` means the launch failed" stop being true.
ROLE_REPORTABLE = {"running", "done", "blocked", "failed"}
# Statuses that assert "this role has work in front of it right now" — the ones a
# dead or long-idle pane contradicts.
ROLE_ACTIVE = {"launched", "notified", "running"}
# Dispatching to a role clears these; `running` is left alone (the agent owns it).
ROLE_DISPATCH_CLEARS = {"stale", "done", "blocked"}
VERDICTS = {"pass", "concerns", "block"}

INTEGRATION_UNIT = "_integration"

# Exit codes for the launch-path failures that used to pass silently.
EXIT_PANE_NOT_READY = 6
EXIT_PASTE_INCOMPLETE = 7
EXIT_NOT_SUBMITTED = 8
EXIT_NOT_RUNNING = 9
EXIT_RECEIPT_MISMATCH = 10

# --- pane signatures --------------------------------------------------------
# Observed on codex-cli 0.147.0 and Claude Code 2.1.228. If a CLI changes its
# TUI, update these and references/runtimes.md together.

# The composer is drawn and accepting input.
PANE_READY_PATTERNS: dict[str, list[str]] = {
    "codex": [r"^›", r"⏎\s*send"],
    "claude": [r"bypass permissions on", r"\?\s+for shortcuts", r"^❯"],
}

# First-run dialogs that swallow a pasted prompt. Checked BEFORE the ready
# patterns, because both CLIs render dialog options with the same glyph as the
# composer prompt ("› 1. Yes, continue", "❯ 1. Yes, I trust this folder").
PANE_BLOCKED_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "codex": [
        (r"Do you trust the contents of this directory", "codex directory-trust dialog"),
        (r"Press enter to continue", "codex startup dialog waiting for Enter"),
    ],
    "claude": [
        (r"Is this a project you created or one you trust", "Claude Code folder-trust dialog"),
        (r"Yes, I trust this folder", "Claude Code folder-trust dialog"),
        (r"Enter to confirm · Esc to cancel", "Claude Code modal dialog waiting for input"),
    ],
}

# A busy indicator: the CLI accepted a submitted message and started work.
PANE_WORKING_PATTERNS = [r"(?i)esc to interrupt", r"(?i)ctrl\+c to (?:stop|interrupt)"]

# Where the composer starts. Everything from here to the bottom of the screen is
# discarded before fingerprinting, and that is deliberate: **composer text is
# never evidence of anything.** Both CLIs render rotating placeholder hints in it
# ("Explain this codebase", "Improve documentation in @filename"), and the footer
# line beneath it mutates on its own too — measured on Claude Code 2.1.229, it
# drops from "⏸ manual mode on · ? for shortcuts · ← for agents" to "⏸ manual mode
# on" and back with no agent involved. Treating any of that as output is what
# makes an abandoned pane look like a working agent.
#
# Agent output always appears ABOVE the composer in both TUIs, so nothing that
# matters is lost. A pane sitting in a startup dialog reads as idle, which is
# correct — it is stuck.
PANE_COMPOSER_PATTERNS = [r"^\s*(?:│\s*)?[›❯]", r"^\s*│\s*>\s"]

# Lines above the composer that change on their own. Removed line by line, so
# "the screen changed" means the agent produced output — not that a spinner
# ticked or a timer advanced.
PANE_VOLATILE_PATTERNS = [
    r"⏎\s*send",
    r"(?i)\?\s+for shortcuts",
    r"(?i)esc to interrupt",
    r"(?i)ctrl\+c to (?:stop|interrupt)",
    r"(?i)\b(?:working|thinking|esc|elapsed)\b.*\(\s*\d+\s*[sm]\b",  # "Working (12s …)"
    r"(?i)\b\d+(?:\.\d+)?k?\s+tokens?\b",  # token counters
    r"(?i)\btokens?\s+used\b",
    r"^\s*[✳✶✻✽✢*·∴⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s",  # spinner glyph lines
    r"(?i)^\s*(?:▌|▐|█)+\s*$",  # cursor blocks
]

# A pane recorded as active but neither busy nor changing for this long is a
# contradiction worth shouting about (seconds). Only counted while the pane shows
# no busy indicator, so a genuinely thinking agent never trips it.
IDLE_THRESHOLD = 120.0
LIVENESS_FILE = "liveness.json"

# Heading `extend-scope` writes into a brief. Reviewers are told to look for it.
SCOPE_AMENDMENT_HEADING = "## SCOPE AMENDMENT"

# How each TUI renders content it has collapsed into a paste blob.
PASTE_MARKER_CHARS = re.compile(r"Pasted Content (\d+) chars")
PASTE_MARKER_LINES = re.compile(r"Pasted text[^\]]*?\+(\d+) lines?")

# Paste defaults.
#
# `paste-buffer` MUST be given -p. Without it tmux replays the buffer as ordinary
# key input with newlines translated to carriage returns — i.e. as Enter presses —
# and the receiving TUI is left guessing, from timing alone, whether it is being
# pasted into or typed at. That guess is what breaks runs: measured on codex-cli
# 0.147.0, 15,869 characters pasted without -p arrived as
# "[Pasted Content 13312 chars]" (2,557 lost, from the middle), and a composer
# that has mis-parsed a paste cannot be cleared with C-u or C-c. With -p, tmux
# wraps the payload in bracketed-paste control codes, the TUI knows exactly where
# the paste starts and ends, and the same content arrives whole — verified
# lossless in a single shot at 15,870, 27,369, 64,809 and 136,810 characters.
#
# tmux only emits the control codes if the application has requested bracketed
# paste mode, so -p is safe to pass unconditionally; but we never assume it took
# effect, we verify and fall back.
PASTE_FALLBACK_CHUNK_CHARS = 600  # what survives when bracketed paste is unavailable
PASTE_CHUNK_SETTLE = 0.25
INLINE_MAX_CHARS = 1500
PROMPT_MODES = ("auto", "pointer", "inline")

RECEIPT_PREFIX = "TMUX-DELIVER-RECEIPT:"


# --- small helpers ----------------------------------------------------------


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def slug(value: str, max_len: int = 48) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (value or "").strip()).strip("-")
    cleaned = re.sub(r"-{2,}", "-", cleaned).lower()
    return (cleaned or "unit")[:max_len]


def state_path(raw: str | Path) -> Path:
    return Path(raw).expanduser().resolve()


def ensure_state_dirs(state_dir: Path) -> None:
    for name in ["units", "briefs", "prompts", "deliveries", "reviews", "messages"]:
        (state_dir / name).mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        die(f"invalid JSON in {path}: {exc}")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def append_event(state_dir: Path, event: dict[str, Any]) -> None:
    ensure_state_dirs(state_dir)
    with (state_dir / "events.jsonl").open("a") as handle:
        handle.write(json.dumps({"at": utc_now(), **event}, sort_keys=True) + "\n")


def meta_path(state_dir: Path) -> Path:
    return state_dir / "meta.json"


def unit_path(state_dir: Path, unit_id: str) -> Path:
    return state_dir / "units" / f"{unit_id}.json"


def load_meta(state_dir: Path) -> dict[str, Any]:
    meta = read_json(meta_path(state_dir), {})
    if not meta:
        die(f"no meta.json in {state_dir}; run `init` first")
    return meta


def load_unit(state_dir: Path, unit_id: str) -> dict[str, Any]:
    unit = read_json(unit_path(state_dir, unit_id), {})
    if not unit:
        die(f"unknown unit {unit_id!r} in {state_dir}; run `prepare-unit` first")
    return unit


def load_units(state_dir: Path) -> list[dict[str, Any]]:
    units = []
    for path in sorted((state_dir / "units").glob("*.json")):
        unit = read_json(path, {})
        if unit:
            units.append(unit)
    return units


def blank_role() -> dict[str, Any]:
    return {
        "status": None,
        "window": None,
        "pane": None,
        "window_name": None,
        "runtime": None,
        "model": None,
        "effort": None,
        "launched_at": None,
        "updated_at": None,
        "message": "",
        "verdict": None,
        "artifact": None,
        "receipt": None,
        "receipt_verified": None,
        # Last round this role was actually sent something. If it is behind the
        # unit's round, nothing has been dispatched since the round bumped.
        "dispatched_round": None,
        "dispatched_at": None,
    }


# --- tmux / git plumbing ----------------------------------------------------


def run_tmux(
    args: list[str], *, capture: bool = False, check: bool = True
) -> subprocess.CompletedProcess[str]:
    cmd = ["tmux", *args]
    try:
        return subprocess.run(
            cmd,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError:
        die("tmux is not installed or not on PATH")
    except subprocess.CalledProcessError as exc:
        if capture:
            detail = (exc.stderr or exc.stdout or "").strip() or "tmux command failed"
            die(f"{detail}: {' '.join(shlex.quote(p) for p in cmd)}")
        raise


def tmux_has_session(session: str) -> bool:
    return run_tmux(["has-session", "-t", f"={session}"], check=False, capture=True).returncode == 0


def tmux_display(target: str, fmt: str) -> str:
    return run_tmux(["display-message", "-p", "-t", target, fmt], capture=True).stdout.strip()


def tmux_target_alive(target: str) -> bool:
    """True only if tmux can still resolve `target` to something that exists.

    Must be `list-panes`, never `display-message`: `display-message` exits 0 on
    a target that no longer exists, expanding the format against nothing.
    """
    if not target:
        return False
    return run_tmux(["list-panes", "-t", target], check=False, capture=True).returncode == 0


def send_command(target: str, command: str) -> None:
    run_tmux(["send-keys", "-t", target, command, "C-m"])


def mirror_window_index(mirror_session: str, window_id: str) -> str | None:
    """Where `window_id` sits in the mirror session, if it is linked there."""
    listing = run_tmux(
        ["list-windows", "-t", f"={mirror_session}", "-F", "#{window_id} #{session_name}:#{window_index}"],
        check=False,
        capture=True,
    )
    if listing.returncode != 0:
        return None
    for line in (listing.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == window_id:
            return parts[1]
    return None


def mirror_free_index(mirror_session: str) -> int:
    listing = run_tmux(
        ["list-windows", "-t", f"={mirror_session}", "-F", "#{window_index}"], check=False, capture=True
    )
    indices = [int(v) for v in (listing.stdout or "").split() if v.isdigit()]
    return (max(indices) + 1) if indices else 1


def mirror_link(mirror_session: str | None, window_id: str) -> str:
    """Link an agent window into the session the user is actually looking at.

    Linking is not copying: it is the same window in two sessions, so the run is
    not disturbed and closing it in either place closes it in both.
    """
    if not mirror_session:
        return "not configured"
    if not tmux_has_session(mirror_session):
        return f"NOT linked — mirror session {mirror_session!r} does not exist"
    if mirror_window_index(mirror_session, window_id):
        return f"already linked into {mirror_session}"
    # Link at the end of the mirror session, so the user's existing windows keep
    # their indices and each new agent appears after the last. `-a` (insert after
    # the current window) is the fallback, and it renumbers.
    attempts = [
        ["link-window", "-d", "-s", window_id, "-t", f"={mirror_session}:{mirror_free_index(mirror_session)}"],
        ["link-window", "-d", "-a", "-s", window_id, "-t", f"={mirror_session}:"],
    ]
    for args in attempts:
        result = run_tmux(args, check=False, capture=True)
        if result.returncode == 0:
            break
    else:
        detail = (result.stderr or result.stdout or "").strip()
        return f"NOT linked ({detail or 'link-window failed'})"
    return f"linked into {mirror_session}:{(mirror_window_index(mirror_session, window_id) or '?').split(':')[-1]}"


def mirror_unlink(mirror_session: str | None, window_id: str) -> str:
    """Drop the mirror's copy of a link before the window itself is killed.

    Without this, killing the window yanks it out of the user's session with no
    explanation. Unlinking first is the same end state, arrived at deliberately.
    """
    if not mirror_session or not window_id:
        return "not mirrored"
    index = mirror_window_index(mirror_session, window_id)
    if not index:
        return "not mirrored"
    result = run_tmux(["unlink-window", "-t", index], check=False, capture=True)
    if result.returncode != 0:
        return f"unlink failed ({(result.stderr or result.stdout or '').strip()})"
    return f"unlinked from {index}"


def capture_pane(target: str, history: int = 0) -> str:
    """Return the pane's visible contents (plus `history` scrollback lines).

    Empty for a pane tmux cannot find — check `tmux_target_alive` first where
    that distinction matters.
    """
    args = ["capture-pane", "-p"]
    if history:
        args += ["-S", f"-{history}"]
    args += ["-t", target]
    result = run_tmux(args, capture=True, check=False)
    return result.stdout or ""


def pane_tail(target: str, lines: int = 30) -> str:
    return "\n".join(capture_pane(target).rstrip().splitlines()[-lines:])


def _squeeze(text: str) -> str:
    """Drop all whitespace, so a wrapped TUI line still matches its source."""
    return re.sub(r"\s+", "", text)


def pane_state(pane: str, runtime: str, extra_patterns: list[str] | None = None) -> tuple[str, str]:
    """Classify a captured pane as `blocked`, `ready`, or `unknown`."""
    for pattern, label in PANE_BLOCKED_PATTERNS.get(runtime, []):
        if re.search(pattern, pane, re.M):
            return "blocked", label
    for pattern in [*PANE_READY_PATTERNS.get(runtime, []), *(extra_patterns or [])]:
        if re.search(pattern, pane, re.M):
            return "ready", f"composer signature /{pattern}/"
    return "unknown", ""


def pane_is_busy(pane: str) -> bool:
    """True if the CLI is showing a busy indicator — the one honest liveness signal.

    Same patterns the launcher uses to confirm a submit was taken, deliberately:
    one definition of "this agent is working", used everywhere.
    """
    return any(re.search(pattern, pane) for pattern in PANE_WORKING_PATTERNS)


def pane_fingerprint(target: str, pane: str) -> str:
    """A hash that changes only when the agent actually produced output.

    The composer region is cut off entirely and volatile lines (spinner, timers,
    token counters) are stripped — see PANE_COMPOSER_PATTERNS for why the
    composer must never count. Scrollback size is folded in so output that has
    already scrolled off the visible screen still registers as a change.
    """
    lines = pane.splitlines()
    cut = None
    for index, line in enumerate(lines):
        if any(re.search(pattern, line) for pattern in PANE_COMPOSER_PATTERNS):
            cut = index
    if cut is not None:
        lines = lines[:cut]

    kept = []
    for line in lines:
        if not line.strip():
            continue
        if any(re.search(pattern, line) for pattern in PANE_VOLATILE_PATTERNS):
            continue
        kept.append(re.sub(r"\s+", " ", line.strip()))
    history = run_tmux(
        ["display-message", "-p", "-t", target, "#{history_size}"], check=False, capture=True
    ).stdout.strip()
    return hashlib.sha1(("\n".join(kept) + f"|history={history}").encode()).hexdigest()[:16]


def sample_pane_liveness(state_dir: Path, target: str) -> dict[str, Any]:
    """Observe a pane now, and say how long it has been unchanged.

    Idle time is measured against a small store (`liveness.json`) of previous
    observations, because tmux does not record when a pane last emitted anything.
    It is therefore bounded by how long we have been watching: `observed_for` is
    reported alongside so an idle reading taken seconds after the first sample
    cannot be mistaken for a long silence.
    """
    if not target:
        return {"pane": None, "alive": False, "busy": False, "idle": None, "observed_for": 0.0}

    store_path = state_dir / LIVENESS_FILE
    store = read_json(store_path, {})
    if not isinstance(store, dict):
        store = {}

    if not tmux_target_alive(target):
        # Prune rather than refresh: a stored `changed_at` is indistinguishable
        # from recent activity, so a dead pane would read back as "idle 0s".
        if store.pop(target, None) is not None:
            write_json(store_path, store)
        return {"pane": target, "alive": False, "busy": False, "idle": None, "observed_for": 0.0}

    pane = capture_pane(target)
    fingerprint = pane_fingerprint(target, pane)
    now = time.time()

    entry = store.get(target) or {}
    if entry.get("fingerprint") != fingerprint:
        entry = {
            "fingerprint": fingerprint,
            "changed_at": now,
            "first_seen": entry.get("first_seen", now),
        }
    entry["seen_at"] = now
    entry["changed_at_iso"] = dt.datetime.fromtimestamp(
        entry["changed_at"], dt.timezone.utc
    ).replace(microsecond=0).isoformat()
    store[target] = entry
    write_json(store_path, store)

    return {
        "pane": target,
        "alive": True,
        "busy": pane_is_busy(pane),
        "idle": max(0.0, now - float(entry["changed_at"])),
        "observed_for": max(0.0, now - float(entry["first_seen"])),
        "last_change": entry["changed_at_iso"],
    }


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def liveness_threshold(args: argparse.Namespace) -> float:
    """`--idle-threshold` from `args`, defaulting only when it was never set.

    Not `or IDLE_THRESHOLD`: 0 is a legitimate value meaning "flag any quiet
    pane immediately", and it is falsy, so `or` would silently restore 120.
    """
    threshold = getattr(args, "idle_threshold", None)
    return IDLE_THRESHOLD if threshold is None else threshold


def role_liveness(
    state_dir: Path,
    unit: dict[str, Any],
    role: str,
    *,
    threshold: float = IDLE_THRESHOLD,
) -> dict[str, Any]:
    """Recorded status vs what the pane is doing, and the contradiction between them.

    `flag` is set only when the bookkeeping claims something the pane denies. It
    is the whole point of this function: a status table that agrees with itself
    is not evidence, and the orchestrator should not have to know to go looking.
    """
    role_state = (unit.get("roles") or {}).get(role) or {}
    recorded = role_state.get("status")
    pane = role_state.get("pane")
    unit_round = unit.get("round") or 0
    dispatched = role_state.get("dispatched_round")

    info = sample_pane_liveness(state_dir, pane) if pane else {
        "pane": None, "alive": False, "busy": False, "idle": None, "observed_for": 0.0
    }
    info.update({"unit": unit["id"], "role": role, "recorded": recorded, "dispatched_round": dispatched})

    if unit.get("status") in UNIT_TERMINAL:
        # Acceptance closes the windows itself; a terminal unit's roles are
        # history, not a contradiction.
        info["flag"] = None
        info["flag_kind"] = None
        return info

    flag = None
    # A stable reason code alongside the human sentence. `watch` dedupes on this,
    # so an alert fires once per transition into a state — the sentence itself
    # carries a live duration and would otherwise re-fire on every poll.
    kind = None
    if pane and not info["alive"]:
        # Ahead of every other reading, whatever the recorded status: nothing can
        # be dispatched into a window that is gone, so advising a `send` or a
        # `re-review` here would point at an agent that cannot answer.
        kind = "dead-pane"
        flag = (
            f"DEAD PANE — recorded {recorded or '-'!r} and the pane is gone. No dispatch can land "
            "until you relaunch with `start-agent --relaunch`."
        )
    elif recorded == "stale":
        kind = "not-dispatched"
        told = (
            " It has been shown the round's change request, but that is a notice, not the work item."
            if role_state.get("notice_round") == unit_round
            else ""
        )
        nudge = (
            f"`re-review --unit {unit['id']}`"
            if role in REVIEW_ROLES
            else f"`send --unit {unit['id']} --role {role}`"
        )
        flag = (
            f"NOT DISPATCHED — round {unit_round} was opened and this role has not been given the "
            f"round's work item.{told} It will sit idle until you dispatch: {nudge}."
        )
    elif recorded in ROLE_ACTIVE and not pane:
        kind = "no-window"
        flag = f"NO WINDOW — recorded {recorded!r} but no pane was ever recorded for this role."
    elif recorded in ROLE_ACTIVE and info["alive"] and not info["busy"]:
        idle = info["idle"] or 0.0
        if idle >= threshold:
            detail = ""
            if dispatched is not None and unit_round and dispatched < unit_round:
                detail = (
                    f" Nothing has been sent to it since round {dispatched} "
                    f"(the unit is on round {unit_round})."
                )
            kind = "stalled"
            flag = (
                f"STALLED — recorded {recorded!r} but the pane has shown no output and no busy "
                f"indicator for {human_duration(idle)}.{detail}"
            )
    info["flag"] = flag
    info["flag_kind"] = kind
    return info


def collect_liveness(
    state_dir: Path, units: list[dict[str, Any]], *, threshold: float = IDLE_THRESHOLD
) -> list[dict[str, Any]]:
    rows = []
    for unit in units:
        for role in ROLES:
            role_state = (unit.get("roles") or {}).get(role) or {}
            if not role_state.get("status") and not role_state.get("pane"):
                continue
            rows.append(role_liveness(state_dir, unit, role, threshold=threshold))
    return rows


def format_liveness(row: dict[str, Any]) -> str:
    if row["pane"] is None:
        state = "no-window"
    elif not row["alive"]:
        state = "DEAD"
    else:
        state = "busy" if row["busy"] else f"idle {human_duration(row['idle'])}"
    label = f"{row['unit']}/{row['role']}"
    return (
        f"{label[:34]:<34} pane={(row['pane'] or '-'):<6} "
        f"recorded={(row['recorded'] or '-'):<9} pane_state={state}"
    )


def wait_for_pane_ready(
    target: str,
    runtime: str,
    *,
    floor: float,
    timeout: float,
    poll: float = 0.5,
    extra_patterns: list[str] | None = None,
) -> tuple[str, str, str]:
    """Poll a pane until its CLI is ready for input.

    Returns (state, detail, pane) where state is `ready`, `blocked` (a startup
    dialog is eating input), or `timeout`. A fixed sleep is not enough: both
    CLIs print banners, boot MCP servers, and may raise a first-run dialog
    before their composer exists, and anything pasted before then is dropped.

    Readiness requires a positive signature — never merely "the pane went
    quiet". A pane that has gone quiet because the CLI failed to start is a
    shell prompt, and pasting a prompt into a shell runs it as commands.
    """
    time.sleep(max(0.0, floor))
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        pane = capture_pane(target)
        state, detail = pane_state(pane, runtime, extra_patterns)
        if state in ("ready", "blocked"):
            return state, detail, pane
        if time.monotonic() >= deadline:
            return "timeout", "no composer signature before the deadline", pane
        time.sleep(poll)


def _chunk_text(text: str, size: int) -> list[str]:
    """Split on line boundaries into chunks of at most `size` characters."""
    if size <= 0 or len(text) <= size:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:size])
            line = line[size:]
        if current and len(current) + len(line) > size:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


def _paste_chunk(target: str, text: str, scratch_dir: Path, *, bracketed: bool) -> None:
    scratch_dir.mkdir(parents=True, exist_ok=True)
    path = scratch_dir / f"paste-{uuid.uuid4().hex[:12]}"
    path.write_text(text)
    buffer_name = f"tdlv-{uuid.uuid4().hex[:12]}"
    run_tmux(["load-buffer", "-b", buffer_name, str(path)])
    run_tmux(["paste-buffer", *(["-p"] if bracketed else []), "-t", target, "-b", buffer_name])
    path.unlink(missing_ok=True)


def send_line(target: str, text: str) -> None:
    """Type one line of literal text into a pane — no newlines, no buffer.

    The most robust way to get an instruction into a TUI: there is nothing for
    the composer to misinterpret, no paste heuristic involved, and no way for a
    newline to be read as a submit.
    """
    line = " ".join(text.split())
    run_tmux(["send-keys", "-l", "-t", target, line])


def respawn_pane(target: str, command: str) -> None:
    """Kill whatever is in the pane and start `command` in it, keeping the pane id.

    The only reliable way out of a composer that has mis-parsed input: C-u and
    C-c do not clear one. The pane id survives, so recorded unit state stays
    valid. Never do this to a pane holding an agent whose context matters.
    """
    run_tmux(["respawn-pane", "-k", "-t", target, command])


def _paste_marker_totals(pane: str) -> tuple[int, int]:
    chars = sum(int(m) for m in PASTE_MARKER_CHARS.findall(pane))
    lines = sum(int(m) for m in PASTE_MARKER_LINES.findall(pane))
    return chars, lines


def _tail_token(text: str, length: int = 48) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()[-length:]
    return ""


def _paste_landed(pane: str, payload: str, token: str) -> tuple[bool, str]:
    """Decide whether everything we pasted is actually sitting in the composer.

    Two independent signals, either of which is sufficient: the tail of the
    payload is visible (the TUI inserted it literally), or the TUI's own paste
    marker accounts for the payload's size.
    """
    if token and _squeeze(token) and _squeeze(token) in _squeeze(pane):
        return True, "payload tail visible in the composer"
    chars, lines = _paste_marker_totals(pane)
    if chars:
        # Codex reports paste sizes rounded down to 1 KiB blocks.
        allowance = 1024 + int(len(payload) * 0.01)
        if len(payload) - chars <= allowance:
            return True, f"paste marker accounts for {chars} of {len(payload)} chars"
        return False, f"paste marker shows {chars} of {len(payload)} chars ({len(payload) - chars} missing)"
    if lines:
        expected = len(payload.splitlines())
        if lines >= expected:
            return True, f"paste marker accounts for {lines} of {expected} lines"
        return False, f"paste marker shows {lines} of {expected} lines"
    return False, "neither the pasted tail nor a paste marker is visible in the pane"


def pointer_message(prompt_file: Path, *, kind: str = "prompt") -> str:
    """The one-line instruction typed in pointer mode.

    Single line by construction — it is typed with `send-keys -l`, so a newline
    in it would be an Enter press mid-sentence. It ends with the path, which
    doubles as the token the delivery check looks for in the pane.
    """
    receipt_note = (
        f"its final line is a receipt token (`{RECEIPT_PREFIX} ...`) that you must quote in your "
        "first report, and if that line is missing then what you have is incomplete, so report "
        "`blocked` rather than working from a partial brief; "
        if kind == "prompt"
        else ""
    )
    text = (
        f"Read this file in full right now, before anything else — it is your {kind}, "
        f"follow it exactly and use any report commands it gives you verbatim; {receipt_note}"
        f"the file is: {prompt_file}"
    )
    return " ".join(text.split())


def submit_and_verify(target: str, *, retries: int = 1, window: float = 6.0) -> tuple[str, str]:
    """Press Enter and confirm the CLI actually took the message.

    Returns (result, detail). `working` means a busy indicator appeared,
    `changed` means the pane redrew (the composer clearing), `unverified`
    means Enter apparently did nothing.
    """
    attempts = 0
    while True:
        before = capture_pane(target)
        # A busy indicator already on screen (Codex prints one while booting its
        # MCP servers) proves nothing about our Enter, so only count a new one.
        stale_busy = [p for p in PANE_WORKING_PATTERNS if re.search(p, before)]
        run_tmux(["send-keys", "-t", target, "C-m"])
        deadline = time.monotonic() + window
        changed = False
        while time.monotonic() < deadline:
            time.sleep(0.4)
            pane = capture_pane(target)
            for pattern in PANE_WORKING_PATTERNS:
                if pattern not in stale_busy and re.search(pattern, pane):
                    return "working", f"busy indicator /{pattern}/ appeared after Enter"
            if pane != before:
                changed = True
        if changed:
            return "changed", "pane redrew after Enter (no busy indicator seen)"
        if attempts >= retries:
            return "unverified", f"pane unchanged after {attempts + 1} Enter(s)"
        attempts += 1


def _await_landed(target: str, payload: str, token: str, delay: float) -> tuple[bool, str, str]:
    """Poll until the whole payload is accounted for in the pane, or time out.

    Deliberately goal-directed rather than "wait for the pane to go quiet": a
    large payload keeps arriving after the tmux command returns, and the screen
    can sit unchanged for a beat mid-arrival, which reads as settled and makes a
    complete paste look truncated. Waiting for the answer we actually want is
    both faster in the good case and correct in the slow one.
    """
    deadline = time.monotonic() + max(delay, 3.0, len(payload) / 4000)
    while True:
        pane = capture_pane(target)
        landed, detail = _paste_landed(pane, payload, token)
        if landed or time.monotonic() >= deadline:
            return landed, detail, pane
        time.sleep(0.4)


def _deliver_payload(
    target: str,
    payload: str,
    scratch: Path,
    *,
    mode: str,
    bracketed: bool,
    chunk_chars: int,
    chunk_settle: float,
) -> tuple[int, str]:
    if mode == "pointer":
        send_line(target, payload)
        return 1, "send-keys -l (one line, no newlines)"
    chunks = _chunk_text(payload, chunk_chars)
    for chunk in chunks:
        _paste_chunk(target, chunk, scratch, bracketed=bracketed)
        time.sleep(chunk_settle)
    how = "paste-buffer -p (bracketed)" if bracketed else "paste-buffer (unbracketed)"
    return len(chunks), f"{how} x{len(chunks)}"


def paste_and_submit(
    target: str,
    text: str,
    state_dir: Path,
    label: str,
    delay: float,
    *,
    mode: str = "auto",
    inline_max_chars: int = INLINE_MAX_CHARS,
    chunk_chars: int | None = None,
    chunk_settle: float = PASTE_CHUNK_SETTLE,
    bracketed: bool = True,
    pointer_path: Path | None = None,
    pointer_kind: str = "prompt",
    recover: Any = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Get `text` into a pane and submitted, and prove both happened.

    `pointer` mode types **one line** with `send-keys -l` telling the agent to
    read the file, instead of pasting the file itself. That is the default for
    anything large and it is the most robust option available: a single line has
    no newlines for a composer to read as Enter and no paste heuristic to
    misfire, so there is nothing left to truncate or mangle.

    `inline` mode pastes the text itself with `paste-buffer -p`, and refuses to
    press Enter unless the whole payload can be accounted for in the pane.

    If a delivery cannot be verified and `recover` is supplied, the pane is
    restarted and the delivery retried unbracketed and chunked — a composer that
    has mis-parsed input cannot be cleared any other way. `recover` must be None
    for a pane holding an agent whose context matters: restarting it throws that
    context away.
    """
    ensure_state_dirs(state_dir)
    stamp = utc_now().replace(":", "")
    message_path = state_dir / "messages" / f"{stamp}-{slug(label, 40)}.md"
    message_path.write_text(text.rstrip() + "\n")

    if mode not in PROMPT_MODES:
        die(f"unknown prompt mode {mode!r}; expected one of {', '.join(PROMPT_MODES)}")
    resolved_mode = mode
    if mode == "auto":
        resolved_mode = "pointer" if len(text) > inline_max_chars else "inline"
    if resolved_mode == "pointer":
        payload = pointer_message(pointer_path or message_path, kind=pointer_kind)
    else:
        payload = text.rstrip() + "\n"

    token = _tail_token(payload)
    scratch = state_dir / "messages" / ".paste"

    # Attempt 1 as configured; attempt 2 drops bracketed paste and chunks small,
    # for a TUI that never enabled bracketed paste mode. Both are verified.
    plans = [(bracketed, chunk_chars if chunk_chars is not None else (0 if bracketed else PASTE_FALLBACK_CHUNK_CHARS))]
    if bracketed and resolved_mode == "inline":
        plans.append((False, chunk_chars if chunk_chars is not None else PASTE_FALLBACK_CHUNK_CHARS))

    failures: list[str] = []
    for index, (use_brackets, size) in enumerate(plans):
        chunks, how = _deliver_payload(
            target,
            payload,
            scratch,
            mode=resolved_mode,
            bracketed=use_brackets,
            chunk_chars=size,
            chunk_settle=chunk_settle,
        )
        landed, paste_detail, _ = _await_landed(target, payload, token, delay)

        submitted, submit_detail = "not-attempted", "the payload never landed"
        if landed:
            submitted, submit_detail = submit_and_verify(target)
            if submitted != "unverified":
                return {
                    "message_file": message_path,
                    "mode": resolved_mode,
                    "chars": len(payload),
                    "chunks": chunks,
                    "delivery": how,
                    "attempts": index + 1,
                    "earlier_failures": failures,
                    "paste_check": f"ok ({paste_detail})",
                    "submitted": f"{submitted} ({submit_detail})",
                }

        failures.append(f"attempt {index + 1} via {how}: paste {paste_detail}; submit {submit_detail}")
        if not verify:
            return {
                "message_file": message_path,
                "mode": resolved_mode,
                "chars": len(payload),
                "chunks": chunks,
                "delivery": how,
                "attempts": index + 1,
                "earlier_failures": failures,
                "paste_check": f"{'ok' if landed else 'FAILED'} ({paste_detail})",
                "submitted": f"{submitted} ({submit_detail})",
            }

        if index == len(plans) - 1 or recover is None:
            break
        if not recover():
            failures.append("pane could not be restarted cleanly")
            break

    joined = "\n  ".join(failures)
    hint = (
        "The composer is now in an unknown state, and C-u / C-c will not clear it — restart the "
        f"pane with `tmux respawn-pane -k -t {target} '<launch command>'` before retrying. Note "
        "that this discards the agent's context."
        if recover is None
        else "The pane was restarted between attempts, so it is not left holding a partial prompt."
    )
    die(
        f"could not deliver the {pointer_kind} to {target}:\n  {joined}\n{hint}\n"
        f"Full text: {message_path}\n--- pane tail ---\n{pane_tail(target)}",
        EXIT_PASTE_INCOMPLETE if "never landed" in failures[-1] else EXIT_NOT_SUBMITTED,
    )
    raise AssertionError  # unreachable


def wait_for_role_status(
    state_dir: Path,
    unit_id: str,
    role: str,
    *,
    timeout: float,
    poll: float = 2.0,
    wanted: tuple[str, ...] = ("running", "done", "blocked", "failed"),
) -> tuple[str | None, dict[str, Any]]:
    """Block until an agent self-reports, so `launched` is never mistaken for `running`."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        unit = read_json(unit_path(state_dir, unit_id), {})
        role_state = (unit.get("roles") or {}).get(role) or {}
        status = role_state.get("status")
        if status in wanted:
            return status, unit
        if time.monotonic() >= deadline:
            return None, unit
        time.sleep(poll)


def _receipt_report(role_state: dict[str, Any]) -> str:
    verified = role_state.get("receipt_verified")
    if verified is True:
        return "verified (the agent quoted this launch's receipt token, so it has the whole prompt)"
    if verified is False:
        return "MISMATCH (the token the agent quoted is not this launch's — treat the prompt as truncated)"
    return (
        "not quoted (the agent reported without --receipt; ask it to quote the prompt's final "
        "line before trusting that it has the full brief)"
    )


def paste_options(args: argparse.Namespace, meta: dict[str, Any]) -> dict[str, Any]:
    """Paste settings for a command, defaulting to the run's recorded ones.

    Read with getattr so a command that does not expose the paste flags can still
    dispatch — the defaults are the same either way.
    """
    submit_delay = getattr(args, "submit_delay", None)
    return {
        "delay": submit_delay if submit_delay is not None else float(meta.get("submit_delay") or 2.0),
        "mode": getattr(args, "prompt_mode", None) or meta.get("prompt_mode") or "auto",
        "inline_max_chars": getattr(args, "inline_max_chars", None) or INLINE_MAX_CHARS,
        "chunk_chars": getattr(args, "paste_chunk_chars", None),
        "chunk_settle": getattr(args, "paste_settle", None) or PASTE_CHUNK_SETTLE,
        "bracketed": not getattr(args, "no_bracketed_paste", False),
    }


# Phrases that only ever belong in an implementer's change request. A reviewer
# window given one of these is being asked to do something its contract forbids,
# and a good reviewer refuses — costing a round-trip. Kept deliberately tight:
# a re-review prompt legitimately says "report done with a verdict", so generic
# instruction words are useless as markers.
IMPLEMENTER_ONLY_MARKERS = [
    r"(?i)apply these exact changes",
    r"(?i)\bcommit your work\b",
    r"(?i)write your delivery summary",
]


def check_role_appropriate(role: str, text: str, *, allow: bool) -> None:
    """Refuse to hand a reviewer a document written for the implementer.

    Each role gets the message its contract expects: the implementer gets a
    change list, reviewers get a re-review prompt. They are different documents,
    and sending one to the other is a new way to lose a round — the reviewer
    correctly refuses to touch the worktree and nothing moves.
    """
    if allow or role not in REVIEW_ROLES:
        return
    hits = [m for m in IMPLEMENTER_ONLY_MARKERS if re.search(m, text)]
    if not hits:
        return
    die(
        f"this message is addressed to the implementer ({len(hits)} implementer-only phrase(s), "
        f"e.g. /{hits[0]}/), but you are sending it to the {role} reviewer, whose contract forbids "
        "modifying the worktree. It will refuse, and the round will not move.\n"
        "Each role gets the document its contract expects: the implementer gets the change list, "
        "the reviewers get a re-review prompt naming the delta to judge.\n"
        f"Use `re-review --unit <unit>` (no --file sends a generated re-review prompt), or pass "
        "--anyway if you really mean to send this text here."
    )


def dispatch_to_role(
    state_dir: Path,
    unit: dict[str, Any],
    role: str,
    text: str,
    *,
    label: str,
    options: dict[str, Any],
    kind: str = "message from the orchestrator",
    clears_stale: bool = True,
) -> dict[str, Any]:
    """Send text to one role's window and record that it was actually sent.

    The recorded dispatch is the half that matters. `status` compares
    `dispatched_round` with the unit's round, so a role the orchestrator meant to
    task but never did shows up as un-dispatched instead of hiding behind
    `running`. State is written only after `paste_and_submit` has proved the
    delivery, so a failed send never leaves the table claiming otherwise.
    """
    role_state = (unit.get("roles") or {}).get(role) or {}
    target = role_state.get("pane") or role_state.get("window")
    if not target:
        die(f"unit {unit['id']} has no {role} window; start it with `start-agent` first")
    if not tmux_target_alive(target):
        die(f"{role} window for {unit['id']} is gone ({target}); relaunch with `start-agent --relaunch`")

    sent = paste_and_submit(
        target,
        text,
        state_dir,
        label,
        options["delay"],
        mode=options["mode"],
        inline_max_chars=options["inline_max_chars"],
        chunk_chars=options["chunk_chars"],
        chunk_settle=options["chunk_settle"],
        bracketed=options["bracketed"],
        pointer_kind=kind,
        # Never recover by restarting: this pane holds a live agent whose
        # round-N context is the reason it is still open.
        recover=None,
    )

    if clears_stale:
        role_state["dispatched_round"] = unit.get("round")
        role_state["dispatched_at"] = utc_now()
        if role_state.get("status") in ROLE_DISPATCH_CLEARS:
            role_state["status"] = "notified"
            role_state["verdict"] = None
    else:
        # An informational message is not a task. Recording it as one would put
        # the role back to claiming work it has not been given.
        role_state["notice_round"] = unit.get("round")
        role_state["notice_at"] = utc_now()
    role_state["updated_at"] = utc_now()
    role_state["round"] = unit.get("round")
    unit.setdefault("roles", {})[role] = role_state
    unit["updated_at"] = utc_now()
    write_json(unit_path(state_dir, unit["id"]), unit)
    append_event(
        state_dir,
        {
            "type": "dispatch" if clears_stale else "notice",
            "unit": unit["id"],
            "role": role,
            "round": unit.get("round"),
            "label": label,
            "message_file": str(sent["message_file"]),
        },
    )
    sent["target"] = target
    sent["role"] = role
    return sent


def run_git(args: list[str], *, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError:
        die("git is not installed or not on PATH")
    except subprocess.CalledProcessError as exc:
        detail = ((exc.stderr or "") + (exc.stdout or "")).strip()
        die(detail or f"git failed: {' '.join(shlex.quote(p) for p in ['git', *args])}")


def git_ok(args: list[str]) -> bool:
    return subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def repo_toplevel(path: Path) -> Path:
    out = run_git(["-C", str(path), "rev-parse", "--show-toplevel"]).stdout.strip()
    return Path(out).resolve()


def branch_exists(repo: Path, branch: str) -> bool:
    return git_ok(["-C", str(repo), "rev-parse", "--verify", f"refs/heads/{branch}"])


def worktree_is_clean(worktree: Path) -> tuple[bool, str]:
    out = run_git(["-C", str(worktree), "status", "--porcelain"]).stdout.strip()
    return (not out), out


def remove_worktree(repo_root: Path, worktree: Path) -> str:
    if not worktree.exists():
        return "already absent"
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(worktree)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return f"NOT removed: {(result.stderr or result.stdout).strip()}"
    return "removed"


# --- runtime resolution -----------------------------------------------------


def resolve_runtime(runtime: str, model: str | None, effort: str | None) -> dict[str, str]:
    """Validate a runtime/model/effort triple and return the resolved values."""
    if runtime not in DEFAULTS:
        die(f"unknown runtime {runtime!r}; expected 'claude' or 'codex'")
    model = model or DEFAULTS[runtime]["model"]
    effort = effort or DEFAULTS[runtime]["effort"]

    if runtime == "codex":
        if model in CODEX_MODELS:
            resolved_model = CODEX_MODELS[model]
        elif model in CODEX_MODELS.values():
            resolved_model = model
        else:
            die(
                f"model {model!r} is not a Codex model. "
                f"Choose one of: {', '.join(sorted(CODEX_MODELS))}"
            )
        allowed = EFFORTS | (CODEX_ONLY_EFFORTS if resolved_model == "gpt-5.6-sol" else set())
        if effort not in allowed:
            die(f"effort {effort!r} invalid for {resolved_model}; expected: {', '.join(sorted(allowed))}")
    else:
        if model in CODEX_MODELS or model in CODEX_MODELS.values():
            die(f"model {model!r} is a Codex model but runtime is 'claude'; use --codex or pick a Claude model")
        if not (model in CLAUDE_MODELS or model.startswith("claude-")):
            die(
                f"model {model!r} is not a Claude model. "
                f"Choose an alias ({', '.join(sorted(CLAUDE_MODELS))}) or a full claude-* name"
            )
        resolved_model = model
        if effort not in EFFORTS:
            die(f"effort {effort!r} invalid for Claude; expected: {', '.join(sorted(EFFORTS))}")

    return {"runtime": runtime, "model": resolved_model, "effort": effort}


def codex_trust_override(workspace: str | Path) -> str | None:
    """The `-c` override that pre-trusts `workspace` for Codex, if one exists.

    Codex splits a `-c` key on `.` and does **not** honour a quoted key segment.
    Verified on codex-cli 0.147.0: `projects."<path>".trust_level="trusted"` is
    silently ignored and the trust dialog still appears, while
    `projects.<path>.trust_level="trusted"` works. A path that itself contains a
    dot cannot be expressed either way — the launcher warns and the readiness
    check reports the dialog rather than pasting a prompt into it.
    """
    path = str(workspace)
    if "." in path:
        return None
    return f'projects.{path}.trust_level="trusted"'


def launch_command(runtime: str, model: str, effort: str, workspace: str | Path = ".") -> str:
    """Build the shell command that starts the agent CLI in a tmux pane.

    Both runtimes launch with approvals fully bypassed: these panes are
    unattended and must never block on a keypress.
    """
    if runtime == "codex":
        # A unit worktree is always a brand-new directory, so Codex would show its
        # "Do you trust the contents of this directory?" prompt on startup —
        # which --dangerously-bypass-approvals-and-sandbox does NOT suppress, and
        # which would swallow the pasted prompt and hang the pane. Pre-trust the
        # path with a -c override so no dialog ever appears (no config.toml edit).
        parts = [
            "codex",
            "--model",
            shlex.quote(model),
            "-c",
            shlex.quote(f'model_reasoning_effort="{effort}"'),
        ]
        trust = codex_trust_override(workspace)
        if trust:
            parts += ["-c", shlex.quote(trust)]
        parts += [
            "--dangerously-bypass-approvals-and-sandbox",
            "--no-alt-screen",
        ]
    else:
        parts = [
            "claude",
            "--model",
            shlex.quote(model),
            "--effort",
            shlex.quote(effort),
            "--dangerously-skip-permissions",
        ]
    return " ".join(parts)


def pre_trust_workspace(runtime: str, workspace: Path) -> str:
    """Suppress the CLI's first-run directory-trust dialog for `workspace`.

    A unit worktree is always a brand-new directory, so both CLIs would open a
    "do you trust this folder?" dialog on startup. Neither
    --dangerously-skip-permissions nor --dangerously-bypass-approvals-and-sandbox
    suppresses it, and the dialog swallows the pasted prompt and hangs the pane.

    Claude Code blocks on a per-directory trust dialog, persisted in
    ~/.claude.json as projects.<path>.hasTrustDialogAccepted. A unit worktree is
    always new, so that key cannot already exist — seed it here.

    Claude Code ALSO requires a one-time global acceptance of bypass-permissions
    mode (`bypassPermissionsModeAccepted`). This function deliberately does NOT
    set that: it is a global, permanent change to the user's own Claude Code
    behaviour, not something a delivery run should decide. We only require that it
    is already set, and fail fast if it is not.

    Codex needs neither — launch_command() passes a -c trust override.
    """
    if runtime != "claude":
        if codex_trust_override(workspace):
            return "codex: pre-trusted via -c projects override"
        return (
            "codex: NOT pre-trusted — the worktree path contains a '.', which a codex -c key "
            "cannot express, so expect the directory-trust dialog"
        )

    config = Path.home() / ".claude.json"
    if not config.exists():
        die(
            "~/.claude.json not found, so Claude Code's first-run dialogs cannot be "
            "pre-cleared and the agent pane would hang. Launch `claude "
            "--dangerously-skip-permissions` once by hand and accept the prompt, "
            "or use --codex."
        )

    try:
        data = json.loads(config.read_text())
    except json.JSONDecodeError:
        die("~/.claude.json is not valid JSON; refusing to touch it. Use --codex, or repair the file.")

    if data.get("bypassPermissionsModeAccepted") is not True:
        die(
            "Claude Code has not accepted bypass-permissions mode on this machine "
            "(`bypassPermissionsModeAccepted` is not set in ~/.claude.json). The agent "
            "pane would hang forever on that dialog with the prompt already pasted "
            "into it.\n"
            "This is a global, permanent change to your Claude Code setup, so "
            "tmux-deliver will not make it for you. Either run `claude "
            "--dangerously-skip-permissions` once by hand and accept the prompt, or "
            "use --codex (which needs no such acceptance)."
        )

    entry = data.setdefault("projects", {}).setdefault(str(workspace), {})
    if entry.get("hasTrustDialogAccepted") is True:
        return "worktree already trusted"

    entry["hasTrustDialogAccepted"] = True
    entry.setdefault("projectOnboardingSeenCount", 1)
    tmp = config.with_suffix(".json.tmux-deliver-tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(config)
    return "seeded worktree trust"


def role_runtime(meta: dict[str, Any], role: str) -> dict[str, str]:
    if role in REVIEW_ROLES:
        # Reviewers are pinned to Codex/Sol/xhigh by policy. A run may override
        # that ONLY by writing all three reviewer_* keys into its own meta.json
        # — used when the Codex provider is unavailable (e.g. an exhausted token
        # budget) and the alternative is not reviewing at all. Partial overrides
        # are ignored so a half-edited meta cannot silently produce an
        # unintended reviewer triple.
        override = (
            meta.get("reviewer_runtime"),
            meta.get("reviewer_model"),
            meta.get("reviewer_effort"),
        )
        if all(override) and override != (REVIEWER_RUNTIME, REVIEWER_MODEL, REVIEWER_EFFORT):
            return resolve_runtime(*override)
        return resolve_runtime(REVIEWER_RUNTIME, REVIEWER_MODEL, REVIEWER_EFFORT)
    return {
        "runtime": meta["runtime"],
        "model": meta["model"],
        "effort": meta["effort"],
    }


# --- prompt construction ----------------------------------------------------


def skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


# Both runtimes' agent contracts are called this. They differ by the tree they
# ship in, not by name: each CLI reads its own skills directory and finds exactly
# one contract there, written for it.
AGENT_SKILL_NAME = "tmux-deliver-agent"


def agent_skill_path(runtime: str) -> Path:
    """Where the agent contract for `runtime` is installed.

    Same name in both trees, different tree: the Claude contract ships beside
    this skill under `~/.claude/skills`, the Codex one under the Codex home,
    because that is where the Codex CLI looks for skills.

    One location per runtime, and deliberately no falling back to the other tree
    if it is missing. The two contracts share a name but are different documents,
    so a search that "prefers whatever exists" would hand a Codex agent the Claude
    contract whenever the Codex tree had not been installed yet — a wrong brief
    read as a right one. Better to name the correct path and let the agent fail
    loudly on a directory that is not there.
    """
    if runtime == "codex":
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        return codex_home / "skills" / AGENT_SKILL_NAME
    return skill_dir().parent / AGENT_SKILL_NAME


def skill_invocation(runtime: str) -> str:
    path = agent_skill_path(runtime)
    if runtime == "codex":
        # Codex resolves skills by an explicit path reference.
        return f"Use ${AGENT_SKILL_NAME} at {path}."
    return f"Use the `{AGENT_SKILL_NAME}` skill (at {path})."


def report_command(script: Path, state_dir: Path, unit_id: str, role: str, round_no: int) -> str:
    return (
        f"python3 {shlex.quote(str(script))} report"
        f" --state-dir {shlex.quote(str(state_dir))}"
        f" --unit {shlex.quote(unit_id)}"
        f" --role {role}"
        f" --round {round_no}"
    )


def build_implementer_prompt(
    *,
    script: Path,
    state_dir: Path,
    meta: dict[str, Any],
    unit: dict[str, Any],
    round_no: int,
    brief: str,
    context_files: list[Path],
    prompt_file: Path,
) -> str:
    runtime = meta["runtime"]
    base = report_command(script, state_dir, unit["id"], "implementer", round_no)
    delivery_file = state_dir / "deliveries" / f"{unit['id']}-r{round_no}.md"
    context_lines = "\n".join(f"- {p}" for p in context_files) or "- None"

    return f"""{skill_invocation(runtime)}

ROLE: implementer
UNIT: {unit['id']} — {unit['title']}
ROUND: {round_no}
WORKSPACE: {unit['worktree']}
BRANCH: {unit['branch']}
STATE DIR: {state_dir}
DELIVERY SUMMARY PATH: {delivery_file}
PROMPT FILE (this document, in full): {prompt_file}

PROOF OF RECEIPT: the final line of this prompt is `{RECEIPT_PREFIX} <token>`.
If you cannot see that line, what you are reading is truncated — the acceptance
criteria and the "do NOT touch" list are at the end, so working from a partial
copy is worse than not starting. Re-read the prompt file above; if the line is
still missing, report `blocked` with message "prompt truncated" and stop.

First actions, in order:
1. Run `pwd`. It MUST be `{unit['worktree']}`. If it is not, report `blocked`
   immediately and stop — do not cd, and do not touch any file.
2. Run `git status --short` and `git branch --show-current`. The branch MUST be
   `{unit['branch']}`. If not, report `blocked`.
3. Read every context file listed below, then read the assignment in full.
4. Report that you have started, quoting the receipt token verbatim:
   `{base} --status running --receipt <token from this prompt's final line> --message "Started; read the brief, planning test-first."`

Context files:
{context_lines}

Verification commands for this repo:
{meta.get('verification') or '- (none recorded; ask the orchestrator before assuming)'}

Your brief lives at {unit.get('brief_file') or '(inline below)'} and is reproduced
below. If the orchestrator widens your scope mid-round it appends a section headed
`{SCOPE_AMENDMENT_HEADING}` to that file — re-read it when told to, and treat such
a section as binding authorisation. If you are told in a message that extra files
are allowed but the brief does not say so, ask for the brief to be amended: the
reviewers judge you against the brief, not against your messages.

=== ASSIGNMENT ===
{brief.rstrip()}
=== END ASSIGNMENT ===

Strict TDD is mandatory (see the skill for the full contract):
  Red   → write the failing test FIRST; confirm it fails for the RIGHT reason.
  Green → minimum production code to pass.
  Refactor → clean up, tests stay green.
  Evidence → paste the actual red output then the actual green output into your
             delivery summary. Retrofitted tests are rejected.

When your work is done:
1. Commit your work on `{unit['branch']}` (the orchestrator merges commits, not
   dirty worktrees — an uncommitted worktree fails acceptance).
2. Write your delivery summary to `{delivery_file}`, including: what changed, the
   full file list, the red→green evidence, the verification commands you ran with
   their output, and anything the orchestrator must know.
3. Report delivered:
   `{base} --status done --artifact {shlex.quote(str(delivery_file))} --message "Delivered round {round_no}; red-green evidence and verification in the summary."`

Report `blocked` the moment you need orchestrator input, and `failed` if you
cannot proceed and have no useful next action. Then STOP and wait — the
orchestrator will send follow-up instructions into this window if changes are
required. Do not exit; do not start new work on your own initiative.
"""


def build_reviewer_prompt(
    *,
    script: Path,
    state_dir: Path,
    meta: dict[str, Any],
    unit: dict[str, Any],
    role: str,
    round_no: int,
    brief: str,
    delivery_file: Path | None,
    prompt_file: Path,
) -> str:
    base = report_command(script, state_dir, unit["id"], role, round_no)
    review_file = state_dir / "reviews" / f"{unit['id']}-r{round_no}-{role}.md"
    persona = CODEX_PERSONAS[role]
    label = "QA reviewer" if role == "qa" else "Adversarial reviewer"

    return f"""{skill_invocation('codex')}

ROLE: {role}
UNIT: {unit['id']} — {unit['title']}
ROUND: {round_no}
WORKSPACE: {unit['worktree']}
BRANCH: {unit['branch']} (branched from {meta['integration_branch']})
STATE DIR: {state_dir}
REVIEW OUTPUT PATH: {review_file}
PROMPT FILE (this document, in full): {prompt_file}

PROOF OF RECEIPT: the final line of this prompt is `{RECEIPT_PREFIX} <token>`.
If you cannot see that line, what you are reading is truncated — re-read the
prompt file above, and if the line is still missing report `blocked` with
message "prompt truncated" rather than reviewing against a partial brief.

You are the {label} for this unit. Adopt the persona and review process defined
in the `developer_instructions` of `{persona}` — read that file first.

First actions, in order:
1. Run `pwd`. It MUST be `{unit['worktree']}`. If not, report `blocked` and stop.
2. Read the persona file above, then the unit brief and the delivery summary.
3. Inspect the change: `git diff {meta['integration_branch']}...HEAD` and
   `git log {meta['integration_branch']}..HEAD`.
4. Report started, quoting the receipt token verbatim:
   `{base} --status running --receipt <token from this prompt's final line> --message "Started {role} review of round {round_no}."`

You MUST NOT modify, stage, commit, revert, or delete any source, test, or
config file in this worktree. You may run tests, linters, type-checks and builds
(you have unrestricted execution so that you can), but the working tree must be
byte-identical when you finish. The orchestrator verifies this with
`git status --porcelain` and will reject your review if you changed anything.

Delivery summary to review: {delivery_file or '(not recorded — read the diff and git log)'}

Verification commands for this repo:
{meta.get('verification') or '- (none recorded; derive from the repo conventions)'}

The brief file is {unit.get('brief_file') or '(inline below)'} and is reproduced
below. Before judging scope, re-read that file: a section headed
`{SCOPE_AMENDMENT_HEADING}` is an authorised widening of scope from the
orchestrator, and files it names are IN scope however they read against the
original list. Amendments carry a round and timestamp and are not retroactive —
judge each round against the brief as it stood when that round started.

=== UNIT BRIEF ===
{brief.rstrip()}
=== END UNIT BRIEF ===

Independently verify the TDD claim: the tests must have been written before the
production code, and the red→green evidence must be real. Say so explicitly if
the evidence looks retrofitted or fabricated.

Write your review to `{review_file}` with these sections:
- VERDICT: pass | concerns | block
- BLOCKING findings (each: file:line, why it is wrong, a concrete failure scenario)
- NON-BLOCKING findings
- TDD evidence assessment
- Verification commands you ran, and their results

Then report:
  `{base} --status done --verdict <pass|concerns|block> --artifact {shlex.quote(str(review_file))} --message "<one-line verdict>"`

After reporting, STOP and wait in this window. The orchestrator will send you the
delta to re-review in later rounds — keep your context so you can judge whether
your findings were actually addressed.
"""


# --- commands ---------------------------------------------------------------


def command_init(args: argparse.Namespace) -> None:
    repo = repo_toplevel(Path(args.repo).expanduser().resolve())
    state_dir = state_path(args.state_dir) if os.path.isabs(str(args.state_dir)) else (repo / args.state_dir).resolve()
    ensure_state_dirs(state_dir)

    runtime = "codex" if args.codex else "claude" if args.claude else args.runtime
    if not runtime:
        die("pick a runtime: --codex or --claude")
    resolved = resolve_runtime(runtime, args.model, args.effort)
    reviewer = resolve_runtime(REVIEWER_RUNTIME, REVIEWER_MODEL, REVIEWER_EFFORT)

    # --- tmux session ---
    target = args.target or os.environ.get("TMUX_PANE")
    if target and not args.session:
        session = tmux_display(target, "#{session_name}")
        # A bare-numeric session name makes `new-window -t <session>` resolve as a
        # window index, which breaks every agent window after the first.
        if re.fullmatch(r"\d+", session):
            new_name = slug(f"tmux-deliver-{args.slug or repo.name}", 40)
            run_tmux(["rename-session", "-t", target, new_name])
            session = new_name
            print(f"renamed_numeric_session={new_name}")
        orchestrator_target = target
        created = False
    else:
        session = args.session or slug(f"tmux-deliver-{args.slug or repo.name}", 40)
        if tmux_has_session(session):
            if not args.reuse:
                die(f"tmux session {session!r} already exists; pass --reuse to adopt it")
        else:
            run_tmux(["new-session", "-d", "-s", session, "-n", "tmux-deliver", "-c", str(repo)])
        orchestrator_target = None
        created = True

    # The session the user is actually looking at. Agent windows are linked into
    # it as they are created, because a window in a session nobody is attached to
    # is a window nobody sees.
    mirror_session = args.mirror_session
    if mirror_session == "auto":
        # The session this orchestrator is running in — i.e. the one the user is
        # attached to — when the run's windows are going somewhere else.
        mirror_session = tmux_display(target, "#{session_name}") if target else None
    if mirror_session and mirror_session == session:
        mirror_session = None  # windows already live there
    mirror_note = "not configured"
    if mirror_session:
        mirror_note = (
            f"agent windows will be linked into {mirror_session}"
            if tmux_has_session(mirror_session)
            else f"WARNING: mirror session {mirror_session!r} does not exist yet; linking will be skipped"
        )

    # --- integration branch + worktree ---
    plan_slug = slug(args.slug or "delivery", 40)
    integration_branch = args.integration_branch or f"tmux-deliver/{plan_slug}"
    worktree_root = (
        Path(args.worktree_root).expanduser().resolve()
        if args.worktree_root
        else repo.parent / f"{repo.name}-worktrees"
    )
    integration_worktree = worktree_root / f"{plan_slug}{'' if plan_slug.startswith('_') else '-integration'}"

    base_ref = args.base or run_git(["-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    base_sha = run_git(["-C", str(repo), "rev-parse", base_ref]).stdout.strip()

    worktree_root.mkdir(parents=True, exist_ok=True)
    if integration_worktree.exists():
        if not args.reuse:
            die(f"integration worktree already exists: {integration_worktree} (pass --reuse)")
    elif branch_exists(repo, integration_branch):
        run_git(["-C", str(repo), "worktree", "add", str(integration_worktree), integration_branch], capture=False)
    else:
        run_git(
            ["-C", str(repo), "worktree", "add", "-b", integration_branch, str(integration_worktree), base_ref],
            capture=False,
        )

    meta = {
        "created_at": read_json(meta_path(state_dir), {}).get("created_at", utc_now()),
        "updated_at": utc_now(),
        "script_path": str(Path(__file__).resolve()),
        "state_dir": str(state_dir),
        "repo": str(repo),
        "session": session,
        "session_created": created,
        "orchestrator_target": orchestrator_target,
        "mirror_session": mirror_session,
        "runtime": resolved["runtime"],
        "model": resolved["model"],
        "effort": resolved["effort"],
        "reviewer_runtime": reviewer["runtime"],
        "reviewer_model": reviewer["model"],
        "reviewer_effort": reviewer["effort"],
        "concurrency": args.concurrency,
        "max_rounds": args.max_rounds,
        "plan": args.plan,
        "plan_slug": plan_slug,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "integration_branch": integration_branch,
        "integration_worktree": str(integration_worktree),
        "worktree_root": str(worktree_root),
        "verification": args.verification,
        "submit_delay": args.submit_delay,
        "prompt_mode": args.prompt_mode,
    }
    write_json(meta_path(state_dir), meta)
    append_event(state_dir, {"type": "init", "session": session, "integration_branch": integration_branch})

    print(f"state_dir={state_dir}")
    print(f"repo={repo}")
    print(f"session={session}")
    print(f"mirror_session={mirror_session or '-'} ({mirror_note})")
    print(f"implementer={resolved['runtime']}:{resolved['model']}/{resolved['effort']}")
    print(f"reviewers={reviewer['runtime']}:{reviewer['model']}/{reviewer['effort']}")
    print(f"base_ref={base_ref}")
    print(f"integration_branch={integration_branch}")
    print(f"integration_worktree={integration_worktree}")
    print(f"concurrency={args.concurrency} max_rounds={args.max_rounds}")
    print(f"attach_command=tmux attach -t {shlex.quote(session)}")


def command_prepare_unit(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    repo = Path(meta["repo"])
    unit_id = slug(args.unit or args.title)
    path = unit_path(state_dir, unit_id)

    existing = read_json(path, {})
    if existing and not args.reuse:
        die(f"unit {unit_id!r} already exists; pass --reuse to re-prepare it")

    deps = [slug(d) for d in args.depends_on]
    unmet = []
    for dep in deps:
        dep_unit = read_json(unit_path(state_dir, dep), {})
        if not dep_unit:
            unmet.append(f"{dep} (not prepared)")
        elif dep_unit.get("status") != "accepted":
            unmet.append(f"{dep} ({dep_unit.get('status')})")
    if unmet and not args.ignore_deps:
        die(
            f"unit {unit_id!r} depends on unaccepted unit(s): {', '.join(unmet)}. "
            "Accept them first so this worktree inherits their commits, or pass --ignore-deps."
        )

    # Flat sibling of the integration branch, not nested under it: git refs are
    # paths, so `tmux-deliver/<slug>` existing as a branch forbids
    # `tmux-deliver/<slug>/<unit>` as a namespace directory.
    branch = args.branch or f"tmux-deliver/{meta['plan_slug']}-{unit_id}"
    worktree = Path(meta["worktree_root"]) / f"{meta['plan_slug']}-{unit_id}"

    if worktree.exists():
        if not args.reuse:
            die(f"worktree already exists: {worktree} (pass --reuse)")
        created = "already_exists"
    else:
        Path(meta["worktree_root"]).mkdir(parents=True, exist_ok=True)
        if branch_exists(repo, branch):
            run_git(["-C", str(repo), "worktree", "add", str(worktree), branch], capture=False)
        else:
            # Branch from the integration branch so this unit inherits every
            # already-accepted unit's work.
            run_git(
                ["-C", str(repo), "worktree", "add", "-b", branch, str(worktree), meta["integration_branch"]],
                capture=False,
            )
        created = "created"

    unit = existing or {}
    unit.update(
        {
            "id": unit_id,
            "title": args.title,
            "status": "queued",
            "round": 0,
            "created_at": unit.get("created_at", utc_now()),
            "updated_at": utc_now(),
            "depends_on": deps,
            "branch": branch,
            "worktree": str(worktree),
            "brief_file": None,
            "roles": unit.get("roles") or {role: blank_role() for role in ROLES},
            "history": unit.get("history", []),
        }
    )
    write_json(path, unit)
    append_event(state_dir, {"type": "prepare-unit", "unit": unit_id, "branch": branch, "worktree": str(worktree)})

    print(f"unit={unit_id}")
    print(f"branch={branch}")
    print(f"worktree={worktree}")
    print(f"worktree_state={created}")
    print(f"forked_from={meta['integration_branch']}")


def _read_brief(args: argparse.Namespace, state_dir: Path, unit: dict[str, Any]) -> tuple[str, Path | None]:
    if args.brief_file:
        p = Path(args.brief_file).expanduser().resolve()
        if not p.exists():
            die(f"brief file does not exist: {p}")
        return p.read_text(), p
    if unit.get("brief_file"):
        p = Path(unit["brief_file"])
        if p.exists():
            return p.read_text(), p
    default = state_dir / "briefs" / f"{unit['id']}.md"
    if default.exists():
        return default.read_text(), default
    die(f"no brief found; write one to {default} or pass --brief-file")
    raise AssertionError  # unreachable


def command_start_agent(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    unit = load_unit(state_dir, slug(args.unit))
    role = args.role
    if role not in ROLES:
        die(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")

    round_no = args.round if args.round is not None else max(1, unit.get("round") or 1)
    brief, brief_path = _read_brief(args, state_dir, unit)
    if brief_path:
        unit["brief_file"] = str(brief_path)

    workspace = Path(unit["worktree"])
    if not workspace.exists():
        die(f"unit worktree missing: {workspace}; run prepare-unit")

    cfg = role_runtime(meta, role)
    if role == "implementer" and (args.model or args.effort):
        cfg = resolve_runtime(cfg["runtime"], args.model or cfg["model"], args.effort or cfg["effort"])
    elif role in REVIEW_ROLES and (args.model or args.effort):
        die("reviewer model/effort is fixed by policy at gpt-5.6-sol / xhigh and cannot be overridden")

    context_files = [Path(p).expanduser().resolve() for p in args.context_file]
    missing = [str(p) for p in context_files if not p.exists()]
    if missing:
        die(f"context file(s) missing: {', '.join(missing)}")

    script = Path(meta.get("script_path") or Path(__file__).resolve())
    prompt_path = state_dir / "prompts" / f"{unit['id']}-r{round_no}-{role}.md"
    receipt = uuid.uuid4().hex[:16]
    if role == "implementer":
        prompt = build_implementer_prompt(
            script=script,
            state_dir=state_dir,
            meta=meta,
            unit=unit,
            round_no=round_no,
            brief=brief,
            context_files=context_files,
            prompt_file=prompt_path,
        )
    else:
        delivery = state_dir / "deliveries" / f"{unit['id']}-r{round_no}.md"
        prompt = build_reviewer_prompt(
            script=script,
            state_dir=state_dir,
            meta=meta,
            unit=unit,
            role=role,
            round_no=round_no,
            brief=brief,
            delivery_file=delivery if delivery.exists() else None,
            prompt_file=prompt_path,
        )

    # The receipt token goes last and appears nowhere else, so quoting it is
    # proof the agent read the prompt all the way to the end.
    prompt = f"{prompt.rstrip()}\n\n=== END OF PROMPT ===\n{RECEIPT_PREFIX} {receipt}\n"
    prompt_path.write_text(prompt)

    if args.dry_run:
        print("dry_run=true")
        print(f"unit={unit['id']} role={role} round={round_no}")
        print(f"launch_command={launch_command(**cfg, workspace=workspace)}")
        print(f"prompt_file={prompt_path}")
        print(f"prompt_chars={len(prompt)}")
        print(f"receipt={receipt}")
        return

    if role == "implementer" and not args.ignore_concurrency:
        limit = int(meta.get("concurrency") or 3)
        active = sum(
            1
            for u in load_units(state_dir)
            if u["id"] != unit["id"] and u.get("status") in UNIT_ACTIVE
        )
        if active >= limit:
            die(f"concurrency limit reached: {active}/{limit} units already active (pass --ignore-concurrency)")

    existing_pane = (unit["roles"].get(role) or {}).get("pane")
    if existing_pane and tmux_target_alive(existing_pane) and not args.relaunch:
        die(
            f"{role} window for unit {unit['id']} is still alive ({existing_pane}). "
            "Use `send` to continue it, or pass --relaunch to replace it."
        )
    if existing_pane and args.relaunch:
        run_tmux(["kill-window", "-t", existing_pane], check=False, capture=True)

    # Clear the CLI's first-run dialogs before creating the window: this can fail
    # hard, and failing after `new-window` would leave an orphan pane behind.
    trust_result = pre_trust_workspace(cfg["runtime"], workspace)

    window_name = slug(f"{unit['id']}-{role}", 40)
    result = run_tmux(
        [
            "new-window", "-d", "-P", "-F", "#{window_id}",
            "-t", f"={meta['session']}",
            "-n", window_name,
            "-c", str(workspace),
        ],
        capture=True,
    )
    window_id = result.stdout.strip()
    pane_id = tmux_display(window_id, "#{pane_id}")
    mirror_result = mirror_link(meta.get("mirror_session"), window_id)

    unit["roles"][role] = {
        **blank_role(),
        "status": "launched",
        "dispatched_round": round_no,
        "dispatched_at": utc_now(),
        "window": window_id,
        "pane": pane_id,
        "window_name": window_name,
        "runtime": cfg["runtime"],
        "model": cfg["model"],
        "effort": cfg["effort"],
        "launched_at": utc_now(),
        "updated_at": utc_now(),
        "round": round_no,
        "prompt_file": str(prompt_path),
        "receipt": receipt,
    }
    unit["round"] = round_no
    unit["status"] = "delivering" if role == "implementer" else "reviewing"
    unit["updated_at"] = utc_now()
    write_json(unit_path(state_dir, unit["id"]), unit)
    append_event(
        state_dir,
        {"type": "start-agent", "unit": unit["id"], "role": role, "round": round_no, "pane": pane_id},
    )

    launch = launch_command(**cfg, workspace=workspace)
    send_command(pane_id, launch)

    def pane_ready_now() -> tuple[str, str, str]:
        return wait_for_pane_ready(
            pane_id,
            cfg["runtime"],
            floor=args.startup_wait,
            timeout=args.ready_timeout,
            extra_patterns=args.ready_pattern,
        )

    def restart_pane() -> bool:
        """Recovery between delivery attempts. Safe here and only here: the CLI
        has just started, so restarting it throws away no agent context."""
        respawn_pane(pane_id, launch)
        return pane_ready_now()[0] == "ready"

    if args.no_wait_ready:
        ready_state, ready_detail = "skipped", f"--no-wait-ready; slept {args.startup_wait:g}s"
        time.sleep(args.startup_wait)
    else:
        ready_state, ready_detail, ready_pane = pane_ready_now()
        if ready_state != "ready":
            unit["roles"][role]["status"] = "failed"
            unit["roles"][role]["message"] = f"pane never became ready: {ready_detail}"
            unit["roles"][role]["updated_at"] = utc_now()
            unit["status"] = "failed"
            unit["updated_at"] = utc_now()
            write_json(unit_path(state_dir, unit["id"]), unit)
            append_event(
                state_dir,
                {"type": "launch-failed", "unit": unit["id"], "role": role, "reason": ready_detail},
            )
            hint = (
                "Answer or clear it in the pane, then re-run with --relaunch."
                if ready_state == "blocked"
                else "Raise --ready-timeout if the CLI is just slow, or check the pane for a stalled launch."
            )
            die(
                f"{cfg['runtime']} in {pane_id} is not ready for input: {ready_detail}. "
                f"Nothing was pasted, so no agent is running against a half-delivered prompt.\n{hint}\n"
                f"window={window_id} pane={pane_id} prompt_file={prompt_path}\n"
                f"--- pane tail ---\n{chr(10).join(ready_pane.rstrip().splitlines()[-25:])}",
                EXIT_PANE_NOT_READY,
            )

    paste = paste_and_submit(
        pane_id,
        prompt,
        state_dir,
        f"{unit['id']}-r{round_no}-{role}",
        args.submit_delay if args.submit_delay is not None else float(meta.get("submit_delay") or 2.0),
        mode=args.prompt_mode or meta.get("prompt_mode") or "auto",
        inline_max_chars=args.inline_max_chars,
        chunk_chars=args.paste_chunk_chars,
        chunk_settle=args.paste_settle,
        bracketed=not args.no_bracketed_paste,
        pointer_path=prompt_path,
        recover=restart_pane,
    )

    print(f"unit={unit['id']}")
    print(f"role={role}")
    print(f"round={round_no}")
    print(f"runtime={cfg['runtime']}:{cfg['model']}/{cfg['effort']}")
    print(f"workspace_trust={trust_result}")
    print(f"window={window_id}")
    print(f"pane={pane_id}")
    print(f"mirror={mirror_result}")
    print(f"prompt_file={prompt_path}")
    print(f"prompt_chars={len(prompt)}")
    print(f"receipt={receipt}")
    print(f"pane_ready={ready_state} ({ready_detail})")
    print(f"prompt_mode={paste['mode']} delivery={paste['delivery']} chars={paste['chars']}")
    if paste["attempts"] > 1:
        print(f"delivery_attempts={paste['attempts']} (pane was restarted and the prompt re-delivered)")
        for failure in paste["earlier_failures"]:
            print(f"  earlier: {failure}")
    print(f"paste_check={paste['paste_check']}")
    print(f"submitted={paste['submitted']}")

    wait_running = 0.0 if args.no_wait_running else args.wait_running
    if wait_running <= 0:
        print("agent_status=launched (NOT verified — `launched` only means a window exists;")
        print("  confirm with `await-running` or the watcher before trusting this launch)")
        return

    status, latest = wait_for_role_status(state_dir, unit["id"], role, timeout=wait_running)
    if status is None:
        print(f"agent_status=TIMEOUT (no self-report within {wait_running:g}s)")
        print(f"action=run `await-running --unit {unit['id']} --role {role}` to keep waiting, or")
        print(f"  inspect the pane: tmux capture-pane -p -t {pane_id} | tail -40")
        print(f"--- pane tail ---\n{pane_tail(pane_id, 25)}")
        raise SystemExit(EXIT_NOT_RUNNING)

    print(f"agent_status={status}")
    print(f"receipt_check={_receipt_report((latest.get('roles') or {}).get(role) or {})}")


def command_report(args: argparse.Namespace) -> None:
    if args.status not in ROLE_REPORTABLE:
        die(
            f"invalid status {args.status!r}; expected one of {', '.join(sorted(ROLE_REPORTABLE))} "
            "(`launched` is written by the launcher; `stale` and `notified` by the orchestrator)"
        )
    if args.verdict and args.verdict not in VERDICTS:
        die(f"invalid verdict {args.verdict!r}; expected one of {', '.join(sorted(VERDICTS))}")
    if args.role not in ROLES:
        die(f"invalid role {args.role!r}; expected one of {', '.join(ROLES)}")

    state_dir = state_path(args.state_dir)
    unit = load_unit(state_dir, slug(args.unit))
    role_state = unit["roles"].get(args.role) or blank_role()

    artifact = None
    if args.artifact:
        artifact_path = Path(args.artifact).expanduser().resolve()
        if not artifact_path.exists():
            die(f"artifact does not exist: {artifact_path}")
        artifact = str(artifact_path)

    # Proof of receipt: the agent quotes the token from its prompt's final line,
    # which it can only do if the prompt reached it whole.
    receipt_note = "not-provided"
    if args.receipt:
        given = args.receipt.strip().removeprefix(RECEIPT_PREFIX).strip().strip("`")
        expected = role_state.get("receipt")
        if not expected:
            receipt_note = "no-receipt-recorded-for-this-launch"
        elif given == expected:
            role_state["receipt_verified"] = True
            receipt_note = "verified"
        else:
            role_state["receipt_verified"] = False
            receipt_note = "MISMATCH"

    role_state.update(
        {
            "status": args.status,
            "message": args.message or "",
            "updated_at": utc_now(),
            "verdict": args.verdict or role_state.get("verdict"),
            "artifact": artifact or role_state.get("artifact"),
            "round": args.round if args.round is not None else role_state.get("round"),
        }
    )
    unit["roles"][args.role] = role_state
    unit["updated_at"] = role_state["updated_at"]
    unit.setdefault("history", []).append(
        {
            "at": role_state["updated_at"],
            "role": args.role,
            "round": role_state.get("round"),
            "status": args.status,
            "verdict": args.verdict,
            "message": args.message or "",
            "artifact": artifact,
        }
    )

    # Roll the unit-level status up from the role reports.
    if args.status in ("blocked", "failed"):
        unit["status"] = args.status
    elif args.role == "implementer" and args.status == "done":
        unit["status"] = "delivered"
    elif args.role in REVIEW_ROLES and args.status == "done":
        others = [r for r in REVIEW_ROLES if r != args.role]
        both_done = all((unit["roles"].get(o) or {}).get("status") == "done" for o in others)
        unit["status"] = "reviewed" if both_done else "reviewing"
    elif args.status == "running" and unit.get("status") == "launched":
        unit["status"] = "delivering" if args.role == "implementer" else "reviewing"

    write_json(unit_path(state_dir, unit["id"]), unit)
    append_event(
        state_dir,
        {
            "type": "report",
            "unit": unit["id"],
            "role": args.role,
            "status": args.status,
            "verdict": args.verdict,
            "message": args.message or "",
        },
    )

    print(f"unit={unit['id']}")
    print(f"role={args.role}")
    print(f"status={args.status}")
    if args.verdict:
        print(f"verdict={args.verdict}")
    print(f"unit_status={unit['status']}")
    print(f"receipt={receipt_note}")
    if receipt_note == "MISMATCH":
        die(
            "the receipt token you quoted is not the one at the end of your prompt, so what you "
            "read is not the prompt this window was launched with — most likely it was truncated. "
            "Re-read your prompt file in full and report `blocked` if its final "
            f"`{RECEIPT_PREFIX}` line is missing. Do not start work.",
            EXIT_RECEIPT_MISMATCH,
        )


def command_status(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    units = load_units(state_dir)
    threshold = liveness_threshold(args)
    want_liveness = not getattr(args, "no_liveness", False)
    rows = collect_liveness(state_dir, units, threshold=threshold) if want_liveness else []

    if args.json:
        print(
            json.dumps(
                {
                    "meta": read_json(meta_path(state_dir), {}),
                    "units": units,
                    "liveness": rows,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    meta = read_json(meta_path(state_dir), {})
    if meta:
        print(
            f"session={meta.get('session')} "
            f"impl={meta.get('runtime')}:{meta.get('model')}/{meta.get('effort')} "
            f"rev={meta.get('reviewer_model')}/{meta.get('reviewer_effort')} "
            f"integration={meta.get('integration_branch')}"
        )
    if not units:
        print(f"No units found in {state_dir}")
        return

    flagged = [row for row in rows if row.get("flag")]
    if flagged:
        print(
            f"!! {len(flagged)} role(s) recorded as having work in front of them that their panes "
            "contradict — see LIVENESS below."
        )

    print()
    print(f"{'UNIT':<24} {'STATUS':<18} {'RND':<4} {'IMPL':<10} {'QA':<14} {'ADV':<14} TITLE")
    for unit in units:
        roles = unit.get("roles") or {}

        def cell(role: str, width: int) -> str:
            r = roles.get(role) or {}
            st = r.get("status") or "-"
            if role in REVIEW_ROLES and r.get("verdict"):
                st = f"{st}/{r['verdict']}"
            return f"{st[:width]:<{width}}"

        print(
            f"{unit['id'][:24]:<24} "
            f"{(unit.get('status') or ''):<18} "
            f"{str(unit.get('round') or 0):<4} "
            f"{cell('implementer', 10)} "
            f"{cell('qa', 14)} "
            f"{cell('adversarial', 14)} "
            f"{unit.get('title', '')}"
        )

    if not want_liveness:
        print()
        print("liveness=skipped (--no-liveness): the statuses above are bookkeeping only.")
        return
    if not rows:
        return

    print()
    print(f"LIVENESS (pane truth, idle threshold {human_duration(threshold)})")
    for row in rows:
        print(f"  {format_liveness(row)}")
        if row.get("flag"):
            print(f"      !! {row['flag']}")
    if not flagged:
        print("  no contradictions: every recorded status matches what its pane is doing.")


def command_send(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)

    if args.file:
        text = Path(args.file).expanduser().resolve().read_text()
    elif args.message:
        text = args.message
    else:
        die("provide --file or --message")

    options = paste_options(args, meta)

    if args.target:
        # Raw target: no unit record to update, so nothing is recorded as dispatched.
        sent = paste_and_submit(
            args.target,
            text,
            state_dir,
            f"send-{slug(args.target)}",
            options["delay"],
            mode=options["mode"],
            inline_max_chars=options["inline_max_chars"],
            chunk_chars=options["chunk_chars"],
            chunk_settle=options["chunk_settle"],
            bracketed=options["bracketed"],
            pointer_kind="message from the orchestrator",
            # No recovery here: restarting this pane would kill a live agent and
            # the round-N context that makes its next answer worth anything.
            recover=None,
        )
        target = args.target
        dispatch_note = "not recorded (--target bypasses unit state; prefer --unit/--role)"
    else:
        if not args.unit or not args.role:
            die("provide --unit and --role (or an explicit --target)")
        check_role_appropriate(args.role, text, allow=getattr(args, "anyway", False))
        unit = load_unit(state_dir, slug(args.unit))
        sent = dispatch_to_role(
            state_dir,
            unit,
            args.role,
            text,
            label=f"send-{unit['id']}-{args.role}",
            options=options,
        )
        target = sent["target"]
        role_state = unit["roles"][args.role]
        dispatch_note = f"round {role_state['dispatched_round']} at {role_state['dispatched_at']}"

    print(f"sent_to={target}")
    print(f"message_file={sent['message_file']}")
    print(f"mode={sent['mode']} delivery={sent['delivery']} chars={sent['chars']}")
    print(f"paste_check={sent['paste_check']}")
    print(f"submitted={sent['submitted']}")
    print(f"dispatch_recorded={dispatch_note}")


def command_next_round(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    unit = load_unit(state_dir, slug(args.unit))
    max_rounds = int(meta.get("max_rounds") or 3)
    new_round = int(unit.get("round") or 1) + 1

    if new_round > max_rounds and not args.force:
        unit["status"] = "escalated"
        unit["updated_at"] = utc_now()
        unit.setdefault("history", []).append(
            {"at": utc_now(), "role": "orchestrator", "status": "escalated",
             "message": f"{max_rounds} rejected rounds reached", "round": unit.get("round")}
        )
        write_json(unit_path(state_dir, unit["id"]), unit)
        append_event(state_dir, {"type": "escalate", "unit": unit["id"], "round": unit.get("round")})
        print(f"unit={unit['id']}")
        print("status=escalated")
        print(f"reason={max_rounds} rejected rounds reached; stop and escalate to the user")
        print("hint=pass --force only if the user explicitly authorises another round")
        raise SystemExit(2)

    feedback_path = None
    if args.feedback_file:
        feedback_path = Path(args.feedback_file).expanduser().resolve()
        if not feedback_path.exists():
            die(f"feedback file does not exist: {feedback_path}")

    unit["round"] = new_round
    unit["status"] = "changes-requested"
    unit["updated_at"] = utc_now()
    # Every role that had finished is now behind the round. It is marked `stale`,
    # NOT `running`: nothing has been sent to it, and a status that claims
    # otherwise is how a reviewer ends up waiting forever for work that was never
    # dispatched. `stale` is dropped only by an actual dispatch.
    for role in ROLES:
        role_state = unit["roles"].get(role) or blank_role()
        if role_state.get("status") == "done":
            role_state["status"] = "stale"
            role_state["verdict"] = None
            role_state["round"] = new_round
            role_state["updated_at"] = utc_now()
            unit["roles"][role] = role_state
    unit.setdefault("history", []).append(
        {"at": utc_now(), "role": "orchestrator", "status": "changes-requested",
         "round": new_round, "message": args.message or ""}
    )
    write_json(unit_path(state_dir, unit["id"]), unit)
    append_event(state_dir, {"type": "next-round", "unit": unit["id"], "round": new_round})

    print(f"unit={unit['id']}")
    print(f"round={new_round}")
    print(f"rounds_remaining={max_rounds - new_round}")
    print("status=changes-requested")

    dispatched: list[str] = []
    if feedback_path:
        options = paste_options(args, meta)
        sent = dispatch_to_role(
            state_dir,
            unit,
            "implementer",
            feedback_path.read_text(),
            label=f"{unit['id']}-r{new_round}-feedback",
            options=options,
            kind=f"round {new_round} change request",
        )
        dispatched.append("implementer")
        print(f"implementer_dispatch={sent['submitted']} message_file={sent['message_file']}")

        if not args.no_notify_reviewers:
            notice = _reviewer_round_notice(unit, new_round, feedback_path)
            for role in REVIEW_ROLES:
                role_state = unit["roles"].get(role) or {}
                if not role_state.get("pane") or not tmux_target_alive(role_state.get("pane")):
                    print(f"{role}_notice=SKIPPED (no live window)")
                    continue
                sent = dispatch_to_role(
                    state_dir,
                    unit,
                    role,
                    notice,
                    label=f"{unit['id']}-r{new_round}-{role}-round-notice",
                    options=options,
                    kind=f"round {new_round} notice",
                    # Informational only: the delta does not exist yet, so this
                    # role is still owed a real dispatch and must stay `stale`.
                    clears_stale=False,
                )
                dispatched.append(f"{role} (notice only)")
                print(f"{role}_notice={sent['submitted']}")

    stale = [r for r in ROLES if (unit["roles"].get(r) or {}).get("status") == "stale"]
    print(f"dispatched={', '.join(dispatched) or 'NOTHING'}")
    print(f"awaiting_dispatch={', '.join(stale) or 'none'}")
    if not feedback_path:
        print()
        print("!! WARNING: the round was bumped but NOTHING was sent to any agent.")
        print("!! Every role above is `stale` and will sit idle until you dispatch to it.")
        print("!! Do one of:")
        print(f"!!   ... next-round --unit {unit['id']} --feedback-file <md>   (one command, does the lot)")
        print(f"!!   ... send --unit {unit['id']} --role implementer --file <md>")
        print(f"!!   ... re-review --unit {unit['id']} --file <md>             (both reviewers)")
    elif stale:
        print()
        print(f"!! {', '.join(stale)} still `stale`: they have the change request but not the delta.")
        print(f"!! When the implementer reports done, run: ... re-review --unit {unit['id']} --file <delta md>")


def _reviewer_round_notice(unit: dict[str, Any], round_no: int, feedback_path: Path) -> str:
    brief = unit.get("brief_file") or "(no brief recorded)"
    return f"""ROUND {round_no} OPENED — unit {unit['id']} ({unit.get('title', '')})

I rejected the previous round and have sent the implementer one consolidated list
of required changes. Read it now so you know what was asked for:

  {feedback_path}

Then WAIT. The implementer is working; I will send you the delta when it reports
done. At that point re-review against your own previous findings and say, finding
by finding, whether each was actually addressed.

Re-read the unit brief before you judge anything:

  {brief}

If it now carries a section headed `{SCOPE_AMENDMENT_HEADING}`, that is an
authorised change of scope from me and it is binding — files listed there are in
scope, however they read against the original list.

Do not modify the worktree. Do not start re-reviewing until I send the delta.
"""


def command_re_review(args: argparse.Namespace) -> None:
    """Send the delta to both reviewer windows — the dispatch that keeps getting forgotten."""
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    unit = load_unit(state_dir, slug(args.unit))
    round_no = unit.get("round") or 1

    if args.file:
        text = Path(args.file).expanduser().resolve().read_text()
    elif args.message:
        text = args.message
    else:
        delivery = state_dir / "deliveries" / f"{unit['id']}-r{round_no}.md"
        text = _default_re_review_message(unit, round_no, delivery)

    roles = args.roles or list(REVIEW_ROLES)
    for role in roles:
        check_role_appropriate(role, text, allow=getattr(args, "anyway", False))

    options = paste_options(args, meta)
    results = []
    missing = []
    for role in roles:
        if role not in ROLES:
            die(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")
        # Check first, dispatch second: dying part-way through would leave one
        # reviewer tasked and the other silently not, which is the failure this
        # command exists to prevent.
        pane = (unit["roles"].get(role) or {}).get("pane")
        if not pane or not tmux_target_alive(pane):
            missing.append(role)
            results.append(f"{role}=NO LIVE WINDOW")
            continue
        sent = dispatch_to_role(
            state_dir,
            unit,
            role,
            text,
            label=f"{unit['id']}-r{round_no}-{role}-rereview",
            options=options,
            kind=f"round {round_no} re-review request",
        )
        results.append(f"{role}={sent['submitted']}")

    unit = load_unit(state_dir, unit["id"])
    if unit.get("status") in ("changes-requested", "delivered"):
        unit["status"] = "reviewing"
        unit["updated_at"] = utc_now()
        write_json(unit_path(state_dir, unit["id"]), unit)

    print(f"unit={unit['id']}")
    print(f"round={round_no}")
    print(f"dispatched={' '.join(results)}")
    still_stale = [r for r in ROLES if (unit["roles"].get(r) or {}).get("status") == "stale"]
    print(f"awaiting_dispatch={', '.join(still_stale) or 'none'}")
    if missing:
        print(
            f"!! {', '.join(missing)} has no live window, so it was NOT tasked. Relaunch with "
            f"`start-agent --unit {unit['id']} --role <role> --relaunch` (a fresh process loses "
            "the earlier round's context, so re-brief it), then re-run this command."
        )
        raise SystemExit(EXIT_NOT_RUNNING)


def _default_re_review_message(unit: dict[str, Any], round_no: int, delivery: Path) -> str:
    return f"""ROUND {round_no} RE-REVIEW — unit {unit['id']} ({unit.get('title', '')})

The implementer has delivered again. Review the delta now.

  Delivery summary: {delivery}
  Unit brief:       {unit.get('brief_file') or '(none recorded)'}
  Diff:             git diff <integration-branch>...HEAD in {unit['worktree']}

Go through your previous findings one by one and state, for each, whether it was
actually addressed — not whether the code now looks acceptable in general. Then
write your review to the round-{round_no} review path and report `done` with a
verdict, exactly as you did last round.

If the brief carries a section headed `{SCOPE_AMENDMENT_HEADING}`, that is an
authorised scope change from me and is binding — do not raise files it names as
out-of-scope.

Do not modify the worktree.
"""


def command_extend_scope(args: argparse.Namespace) -> None:
    """Amend a unit's scope in the one artefact all three roles share: the brief.

    Authorising extra files by `send`ing the implementer alone is invisible to the
    reviewers, who then block the delivery for going outside scope — correctly,
    given what they can see. The amendment goes into the brief on disk, under a
    standard heading, and all three roles are told to re-read it.
    """
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    unit = load_unit(state_dir, slug(args.unit))
    round_no = unit.get("round") or 1

    if args.file:
        body = Path(args.file).expanduser().resolve().read_text().rstrip()
    elif args.message:
        body = args.message.rstrip()
    else:
        die("provide --file or --message with the amendment text")

    brief_file = unit.get("brief_file") or str(state_dir / "briefs" / f"{unit['id']}.md")
    brief_path = Path(brief_file)
    if not brief_path.exists():
        die(f"unit brief not found at {brief_path}; write the brief before amending its scope")

    stamp = utc_now()
    section = (
        f"\n\n{SCOPE_AMENDMENT_HEADING} — round {round_no}, {stamp}\n\n"
        "Authorised by the orchestrator, binding on implementer and reviewers alike, and\n"
        "effective from this timestamp. It amends the scope above; where the two disagree,\n"
        "this section wins.\n\n"
        f"{body}\n"
    )
    with brief_path.open("a") as handle:
        handle.write(section)

    unit["brief_file"] = str(brief_path)
    unit.setdefault("scope_amendments", []).append({"at": stamp, "round": round_no, "body": body})
    unit.setdefault("history", []).append(
        {"at": stamp, "role": "orchestrator", "status": unit.get("status"), "round": round_no,
         "message": f"scope amendment appended to {brief_path}"}
    )
    unit["updated_at"] = stamp
    write_json(unit_path(state_dir, unit["id"]), unit)
    append_event(
        state_dir, {"type": "extend-scope", "unit": unit["id"], "round": round_no, "brief": str(brief_path)}
    )

    print(f"unit={unit['id']}")
    print(f"brief={brief_path}")
    print(f"heading={SCOPE_AMENDMENT_HEADING} — round {round_no}, {stamp}")

    if args.no_notify:
        print("notified=NOBODY (--no-notify)")
        print("!! The reviewers cannot see an amendment they were not told to re-read.")
        return

    notice = f"""SCOPE AMENDMENT — unit {unit['id']}, round {round_no}

I have amended this unit's scope. The amendment is appended to the unit brief,
under the heading `{SCOPE_AMENDMENT_HEADING} — round {round_no}, {stamp}`:

  {brief_path}

Re-read that section now. It is authorised by me and binding on every role:
the implementer may change what it names, and reviewers must NOT treat those
changes as out of scope. It takes effect from its timestamp and is not
retroactive — earlier rounds are judged against the brief as it stood then.

The amendment text, verbatim:

{body}
"""
    options = paste_options(args, meta)
    roles = args.roles or list(ROLES)
    for role in roles:
        if role not in ROLES:
            die(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")
        role_state = unit["roles"].get(role) or {}
        pane = role_state.get("pane")
        if not pane or not tmux_target_alive(pane):
            print(f"{role}=NOT NOTIFIED (no live window)")
            continue
        sent = dispatch_to_role(
            state_dir,
            unit,
            role,
            notice,
            label=f"{unit['id']}-r{round_no}-{role}-scope-amendment",
            options=options,
            kind="scope amendment",
            # A scope change is not the round's work item; it must not clear a
            # pending dispatch obligation.
            clears_stale=False,
        )
        print(f"{role}={sent['submitted']}")


def command_accept(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    unit = load_unit(state_dir, slug(args.unit))
    repo = Path(meta["repo"])
    integration_worktree = Path(meta["integration_worktree"])
    worktree = Path(unit["worktree"])

    if not integration_worktree.exists():
        die(f"integration worktree missing: {integration_worktree}")
    if not worktree.exists():
        die(f"unit worktree missing: {worktree}")

    clean, dirty = worktree_is_clean(worktree)
    if not clean and not args.allow_dirty:
        die(
            f"unit worktree {worktree} has uncommitted changes:\n{dirty}\n"
            "Have the implementer commit before acceptance (or pass --allow-dirty to merge only committed work)."
        )

    ahead = run_git(
        ["-C", str(worktree), "rev-list", "--count", f"{meta['integration_branch']}..{unit['branch']}"]
    ).stdout.strip()
    if ahead == "0" and not args.allow_empty:
        die(
            f"branch {unit['branch']} has no commits beyond {meta['integration_branch']}; "
            "nothing to merge (pass --allow-empty for a genuinely no-op unit)"
        )

    message = args.message or f"tmux-deliver: accept unit {unit['id']} — {unit.get('title', '')}".strip()
    merge = subprocess.run(
        ["git", "-C", str(integration_worktree), "merge", "--no-ff", "-m", message, unit["branch"]],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if merge.returncode != 0:
        detail = ((merge.stdout or "") + (merge.stderr or "")).strip()
        unit["status"] = "blocked"
        unit["updated_at"] = utc_now()
        unit.setdefault("history", []).append(
            {"at": utc_now(), "role": "orchestrator", "status": "blocked",
             "message": f"merge conflict into {meta['integration_branch']}"}
        )
        write_json(unit_path(state_dir, unit["id"]), unit)
        die(
            f"merge of {unit['branch']} into {meta['integration_branch']} failed:\n{detail}\n"
            f"Resolve in {integration_worktree} (or `git -C {integration_worktree} merge --abort`), then re-run accept."
        )

    merged_sha = run_git(["-C", str(integration_worktree), "rev-parse", "HEAD"]).stdout.strip()

    window_results = []
    mirrored = 0
    if not args.keep_windows:
        for role in ROLES:
            role_state = unit["roles"].get(role) or {}
            window = role_state.get("window")
            if window:
                # Unlink before killing: the mirrored copy is the same window, so
                # closing it here removes it from the user's session too.
                if mirror_unlink(meta.get("mirror_session"), window).startswith("unlinked"):
                    mirrored += 1
                r = run_tmux(["kill-window", "-t", window], check=False, capture=True)
                window_results.append(f"{role}={'closed' if r.returncode == 0 else 'already gone'}")
                role_state["status"] = role_state.get("status") or "done"
                role_state["window"] = None
                role_state["pane"] = None
                unit["roles"][role] = role_state

    worktree_result = "kept"
    if not args.keep_worktree:
        worktree_result = remove_worktree(repo, worktree)

    unit["status"] = "accepted"
    unit["accepted_at"] = utc_now()
    unit["updated_at"] = utc_now()
    unit["merged_sha"] = merged_sha
    unit.setdefault("history", []).append(
        {"at": utc_now(), "role": "orchestrator", "status": "accepted",
         "round": unit.get("round"), "message": message}
    )
    write_json(unit_path(state_dir, unit["id"]), unit)
    append_event(state_dir, {"type": "accept", "unit": unit["id"], "merged_sha": merged_sha})

    print(f"unit={unit['id']}")
    print("status=accepted")
    print(f"merged_into={meta['integration_branch']}")
    print(f"merge_commit={merged_sha}")
    print(f"commits_merged={ahead}")
    print(f"windows={' '.join(window_results) or 'kept'}")
    if mirrored:
        print(
            f"mirror={mirrored} window(s) removed from {meta.get('mirror_session')} — "
            "tell the user their mirrored view just lost this unit's windows; that is acceptance, not a fault"
        )
    print(f"worktree={worktree_result}")


def command_verify_readonly(args: argparse.Namespace) -> None:
    """Confirm a reviewer left the unit worktree untouched."""
    state_dir = state_path(args.state_dir)
    unit = load_unit(state_dir, slug(args.unit))
    worktree = Path(unit["worktree"])
    if not worktree.exists():
        die(f"unit worktree missing: {worktree}")
    clean, dirty = worktree_is_clean(worktree)
    print(f"unit={unit['id']}")
    print(f"worktree={worktree}")
    if clean:
        print("reviewer_side_effects=none")
        return
    print("reviewer_side_effects=DETECTED")
    print(dirty)
    print("action=reject the review; the reviewer contract forbids modifying the worktree")
    raise SystemExit(3)


def command_await_running(args: argparse.Namespace) -> None:
    """Block until an agent self-reports, closing the `launched` vs `running` gap."""
    state_dir = state_path(args.state_dir)
    unit = load_unit(state_dir, slug(args.unit))
    role = args.role
    if role not in ROLES:
        die(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")
    pane = (unit["roles"].get(role) or {}).get("pane")

    status, latest = wait_for_role_status(state_dir, unit["id"], role, timeout=args.timeout, poll=args.poll)
    role_state = (latest.get("roles") or {}).get(role) or {}
    print(f"unit={unit['id']}")
    print(f"role={role}")
    print(f"pane={pane or '-'}")
    if status is None:
        print(f"agent_status=TIMEOUT (no self-report within {args.timeout:g}s)")
        if pane:
            alive = "alive" if tmux_target_alive(pane) else "DEAD"
            print(f"pane_state={alive}")
            print(f"--- pane tail ---\n{pane_tail(pane, 25)}")
        print("action=an agent that never reports has not received its prompt; re-read the pane, "
              "then relaunch with `start-agent --relaunch`")
        raise SystemExit(EXIT_NOT_RUNNING)
    print(f"agent_status={status}")
    print(f"message={role_state.get('message', '')}")
    print(f"receipt_check={_receipt_report(role_state)}")


def command_watch(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    seen: dict[str, str] = {}
    flagged: dict[str, str] = {}
    threshold = liveness_threshold(args)
    liveness_interval = max(getattr(args, "liveness_interval", None) or 15.0, args.interval)
    next_liveness = 0.0
    if getattr(args, "no_liveness", False):
        # A watch that prints nothing looks the same whether every pane is
        # healthy or no pane is being looked at at all. Say which, once.
        print(
            "liveness=skipped (--no-liveness): status changes below are bookkeeping only; "
            "dead and stalled panes will NOT be reported",
            flush=True,
        )
    while True:
        try:
            units = load_units(state_dir)
        except SystemExit:
            time.sleep(args.interval)
            continue

        # Liveness on its own, slower cadence: it shells out to tmux per pane, and
        # a stall is a minutes-scale event, not a seconds-scale one.
        if not getattr(args, "no_liveness", False) and time.monotonic() >= next_liveness:
            next_liveness = time.monotonic() + liveness_interval
            for row in collect_liveness(state_dir, units, threshold=threshold):
                key = f"{row['unit']}/{row['role']}"
                flag = row.get("flag")
                kind = row.get("flag_kind")
                # One event per transition, never one per poll: the alert is only
                # worth anything if it is rare enough to be read. Dedupe on the
                # reason code, because the sentence carries a growing duration.
                if flag and flagged.get(key) != kind:
                    print(
                        f"ALERT unit={row['unit']} role={row['role']} recorded={row['recorded']} "
                        f"pane={row['pane'] or '-'} alive={row['alive']} busy={row['busy']} "
                        f"idle={human_duration(row['idle'])} :: {flag}",
                        flush=True,
                    )
                    flagged[key] = kind
                elif not flag and key in flagged:
                    print(
                        f"CLEARED unit={row['unit']} role={row['role']} recorded={row['recorded']} "
                        f"pane={row['pane'] or '-'} — the pane and the recorded status agree again",
                        flush=True,
                    )
                    flagged.pop(key, None)

        for unit in units:
            roles = unit.get("roles") or {}
            fingerprint = json.dumps(
                {
                    "s": unit.get("status"),
                    "r": unit.get("round"),
                    "roles": {k: [(v or {}).get("status"), (v or {}).get("verdict")] for k, v in roles.items()},
                },
                sort_keys=True,
            )
            if seen.get(unit["id"]) == fingerprint:
                continue
            seen[unit["id"]] = fingerprint
            role_bits = " ".join(
                f"{r}={(roles.get(r) or {}).get('status') or '-'}"
                + (f"/{(roles.get(r) or {}).get('verdict')}" if (roles.get(r) or {}).get("verdict") else "")
                for r in ROLES
            )
            last = (unit.get("history") or [{}])[-1]
            print(
                f"unit={unit['id']} status={unit.get('status')} round={unit.get('round')} "
                f"{role_bits} title={unit.get('title', '')} message={last.get('message', '')}",
                flush=True,
            )
            stale = [r for r in ROLES if (roles.get(r) or {}).get("status") == "stale"]
            if stale:
                print(
                    f"  ACTION unit={unit['id']} awaiting_dispatch={','.join(stale)} — round "
                    f"{unit.get('round')} was opened but nothing has been sent to these roles; "
                    f"they will sit idle until you `send` or `re-review`",
                    flush=True,
                )
        time.sleep(args.interval)


def command_cleanup(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    repo = Path(meta["repo"])
    units = load_units(state_dir)

    active = [u for u in units if u.get("status") in UNIT_ACTIVE]
    if active and not args.force:
        detail = ", ".join(f"{u['id']} ({u.get('status')})" for u in active)
        die(f"cannot clean up: {len(active)} unit(s) still active — {detail} (pass --force to override)")

    for unit in units:
        results = []
        for role in ROLES:
            window = (unit["roles"].get(role) or {}).get("window")
            if window:
                mirror_unlink(meta.get("mirror_session"), window)
                r = run_tmux(["kill-window", "-t", window], check=False, capture=True)
                results.append(f"{role}={'closed' if r.returncode == 0 else 'gone'}")
        worktree = Path(unit["worktree"]) if unit.get("worktree") else None
        wt = remove_worktree(repo, worktree) if worktree else "none"
        print(f"{unit['id']}: {' '.join(results) or 'no windows'} worktree={wt}")

    if args.all:
        wt = remove_worktree(repo, Path(meta["integration_worktree"]))
        print(f"integration worktree: {wt}")
        print(f"integration branch {meta['integration_branch']} PRESERVED")
    else:
        print(f"integration worktree kept: {meta['integration_worktree']}")

    run_git(["-C", str(repo), "worktree", "prune"], check=False)
    if meta.get("mirror_session"):
        print(f"mirror session {meta['mirror_session']}: linked agent windows are gone with the windows themselves")
    print("done")


def command_mirror(args: argparse.Namespace) -> None:
    """Link every live agent window into a session the user is attached to.

    Retrofit for a run started without `--mirror-session`, and the way to change
    or drop the mirror mid-run.
    """
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    if args.session:
        if args.session == meta.get("session"):
            die(f"{args.session!r} is the run's own session; agent windows already live there")
        meta["mirror_session"] = args.session
        meta["updated_at"] = utc_now()
        write_json(meta_path(state_dir), meta)
    mirror_session = meta.get("mirror_session")
    if not mirror_session:
        die("no mirror session recorded; pass --session <name> (or re-init with --mirror-session)")
    if not tmux_has_session(mirror_session):
        die(f"tmux session {mirror_session!r} does not exist")

    print(f"run_session={meta.get('session')}")
    print(f"mirror_session={mirror_session}")
    for unit in load_units(state_dir):
        for role in ROLES:
            window = (unit["roles"].get(role) or {}).get("window")
            if not window or not tmux_target_alive(window):
                continue
            action = mirror_unlink if args.unlink else mirror_link
            print(f"{unit['id']}/{role}: {action(mirror_session, window)}")
    if args.unlink:
        print("note=the windows still exist in the run's own session; only the mirrored view was removed")


def command_finish(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    units = load_units(state_dir)
    integration_worktree = Path(meta["integration_worktree"])

    print(f"integration_branch={meta['integration_branch']}")
    print(f"integration_worktree={integration_worktree}")
    print(f"base_ref={meta['base_ref']}")
    print(f"base_sha={meta['base_sha']}")
    if integration_worktree.exists():
        head = run_git(["-C", str(integration_worktree), "rev-parse", "HEAD"]).stdout.strip()
        count = run_git(
            ["-C", str(integration_worktree), "rev-list", "--count", f"{meta['base_sha']}..HEAD"]
        ).stdout.strip()
        stat = run_git(
            ["-C", str(integration_worktree), "diff", "--stat", f"{meta['base_sha']}..HEAD"]
        ).stdout.rstrip()
        print(f"integration_head={head}")
        print(f"commits_ahead_of_base={count}")
        print("--- diffstat vs base ---")
        print(stat or "(no changes)")

    print("--- units ---")
    for unit in units:
        print(f"{unit['id']}: {unit.get('status')} rounds={unit.get('round')} merged={unit.get('merged_sha', '-')}")
    outstanding = [u["id"] for u in units if u.get("status") not in UNIT_TERMINAL]
    print(f"outstanding={', '.join(outstanding) or 'none'}")
    print(f"merge_hint=git merge --no-ff {meta['integration_branch']}")


def command_recover(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    print(f"state_dir={state_dir}")
    print(f"session={meta.get('session')}")
    print(f"attach_command=tmux attach -t {shlex.quote(str(meta.get('session')))}")
    print(f"mirror_session={meta.get('mirror_session') or '-'}")
    print(f"integration_branch={meta.get('integration_branch')}")
    print()
    for unit in load_units(state_dir):
        for role in ROLES:
            role_state = unit["roles"].get(role) or {}
            pane = role_state.get("pane")
            if pane:
                live = sample_pane_liveness(state_dir, pane)
                where = "DEAD" if not live["alive"] else ("busy" if live["busy"] else f"idle {human_duration(live['idle'])}")
                receipt = {True: "verified", False: "MISMATCH"}.get(role_state.get("receipt_verified"), "not-quoted")
                print(
                    f"{unit['id']}/{role}: pane={pane} {where} "
                    f"status={role_state.get('status') or '-'} "
                    f"dispatched_round={role_state.get('dispatched_round') if role_state.get('dispatched_round') is not None else '-'} "
                    f"receipt={receipt}"
                )
    print()
    command_status(
        argparse.Namespace(state_dir=str(state_dir), json=False, no_liveness=False, idle_threshold=IDLE_THRESHOLD)
    )
    print()
    print("Treat `delivered` and `reviewed` as awaiting orchestrator judgement, never as accepted.")
    print("A role still at `launched` has never acknowledged anything — that is a failed launch,")
    print("not a slow agent. Check the pane, then relaunch with `start-agent --relaunch`.")
    print("A role at `stale` is owed a dispatch: it has been told nothing since the round bumped.")
    print("A role marked DEAD lost its pane — usually a tmux server restart took every agent window")
    print("with it. Relaunch with `start-agent --relaunch` before dispatching anything to it.")


# --- parser -----------------------------------------------------------------


def add_paste_args(parser: argparse.ArgumentParser) -> None:
    """Flags shared by every command that pastes into a pane."""
    parser.add_argument(
        "--prompt-mode",
        choices=list(PROMPT_MODES),
        help="pointer: paste a short pointer to the file; inline: paste the text itself; "
        "auto (default): pointer for anything over --inline-max-chars",
    )
    parser.add_argument("--inline-max-chars", type=int, default=INLINE_MAX_CHARS)
    parser.add_argument(
        "--paste-chunk-chars",
        type=int,
        help="inline mode: chunk size in characters (0 = one shot). Default: one shot with "
        f"bracketed paste, {PASTE_FALLBACK_CHUNK_CHARS} without it",
    )
    parser.add_argument("--paste-settle", type=float, default=PASTE_CHUNK_SETTLE)
    parser.add_argument(
        "--no-bracketed-paste",
        action="store_true",
        help="do not pass -p to paste-buffer. Without -p tmux replays the buffer as keystrokes "
        "with newlines as Enter, which is what silently truncates and mangles prompts",
    )


def add_liveness_args(parser: argparse.ArgumentParser) -> None:
    """Flags shared by the commands that compare recorded status against panes."""
    parser.add_argument(
        "--idle-threshold",
        type=float,
        default=IDLE_THRESHOLD,
        help=f"seconds a pane may be quiet (and show no busy indicator) before a role recorded "
        f"as active is called out as stalled (default {IDLE_THRESHOLD:g})",
    )
    parser.add_argument(
        "--no-liveness",
        action="store_true",
        help="do not sample panes; report only what the state files say",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="tmux-deliver: run the deliver loop across tmux windows.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create the session, integration branch and integration worktree.")
    init.add_argument("--state-dir", default=".tmux-deliver")
    init.add_argument("--repo", default=".")
    init.add_argument("--runtime", choices=["claude", "codex"])
    init.add_argument("--claude", action="store_true", help="shorthand for --runtime claude")
    init.add_argument("--codex", action="store_true", help="shorthand for --runtime codex")
    init.add_argument("--model", help="alias (luna|sol|terra / opus|sonnet|haiku|fable) or full model name")
    init.add_argument("--effort", help="low|medium|high|xhigh|max (ultra: gpt-5.6-sol only)")
    init.add_argument("--slug", help="short slug for the plan; used in branch and worktree names")
    init.add_argument("--plan", help="path to the plan document, recorded in meta")
    init.add_argument("--base", help="base ref for the integration branch (default: current HEAD branch)")
    init.add_argument("--integration-branch")
    init.add_argument("--worktree-root")
    init.add_argument("--session", help="tmux session to create/adopt (default: reuse the current one)")
    init.add_argument(
        "--mirror-session",
        help="link every agent window into this session as it is created, so a user attached "
        "there can watch a run whose windows live elsewhere. 'auto' means the orchestrator's "
        "own session. Only useful alongside --session",
    )
    init.add_argument("--target", help="tmux pane of the orchestrator (default: $TMUX_PANE)")
    init.add_argument("--concurrency", type=int, default=3)
    init.add_argument("--max-rounds", type=int, default=3)
    init.add_argument("--verification", help="verification commands, recorded in every agent prompt")
    init.add_argument("--submit-delay", type=float, default=2.0)
    init.add_argument(
        "--prompt-mode",
        choices=list(PROMPT_MODES),
        default="auto",
        help="default paste mode for this run: pointer (paste a pointer to the prompt file), "
        "inline (paste the text), auto (pointer for anything large)",
    )
    init.add_argument("--reuse", action="store_true")
    init.set_defaults(func=command_init)

    prep = sub.add_parser("prepare-unit", help="Create a unit's branch and worktree off the integration branch.")
    prep.add_argument("--state-dir", default=".tmux-deliver")
    prep.add_argument("--unit")
    prep.add_argument("--title", required=True)
    prep.add_argument("--branch")
    prep.add_argument("--depends-on", action="append", default=[])
    prep.add_argument("--ignore-deps", action="store_true")
    prep.add_argument("--reuse", action="store_true")
    prep.set_defaults(func=command_prepare_unit)

    start = sub.add_parser("start-agent", help="Launch an implementer/qa/adversarial window for a unit.")
    start.add_argument("--state-dir", default=".tmux-deliver")
    start.add_argument("--unit", required=True)
    start.add_argument("--role", required=True, choices=list(ROLES))
    start.add_argument("--round", type=int)
    start.add_argument("--brief-file")
    start.add_argument("--context-file", action="append", default=[])
    start.add_argument("--model", help="implementer only; reviewers are pinned to gpt-5.6-sol/xhigh")
    start.add_argument("--effort", help="implementer only; reviewers are pinned to gpt-5.6-sol/xhigh")
    start.add_argument(
        "--startup-wait",
        type=float,
        default=4.0,
        help="minimum settle time after launching the CLI, before readiness polling starts",
    )
    start.add_argument(
        "--ready-timeout",
        type=float,
        default=90.0,
        help="how long to wait for the CLI's composer to appear before failing (default 90s)",
    )
    start.add_argument(
        "--ready-pattern",
        action="append",
        default=[],
        help="extra regex that means 'composer is up', for a CLI whose TUI has changed",
    )
    start.add_argument(
        "--no-wait-ready",
        action="store_true",
        help="skip readiness detection and paste after --startup-wait (the old, racy behaviour)",
    )
    start.add_argument("--submit-delay", type=float)
    add_paste_args(start)
    start.add_argument(
        "--wait-running",
        type=float,
        default=90.0,
        help="block until the agent self-reports; exits non-zero if it never does (default 90s)",
    )
    start.add_argument(
        "--no-wait-running",
        action="store_true",
        help="return as soon as the prompt is submitted, without waiting for the agent to report",
    )
    start.add_argument("--relaunch", action="store_true", help="kill an existing window for this role first")
    start.add_argument("--ignore-concurrency", action="store_true")
    start.add_argument("--dry-run", action="store_true")
    start.set_defaults(func=command_start_agent)

    report = sub.add_parser("report", help="Agent-side status report (called by the agents themselves).")
    report.add_argument("--state-dir", default=".tmux-deliver")
    report.add_argument("--unit", required=True)
    report.add_argument("--role", required=True, choices=list(ROLES))
    report.add_argument("--status", required=True)
    report.add_argument("--round", type=int)
    report.add_argument("--verdict", choices=sorted(VERDICTS))
    report.add_argument("--artifact", help="delivery summary or review file")
    report.add_argument("--message", default="")
    report.add_argument(
        "--receipt",
        help="the token on the final line of your prompt; proves the prompt reached you whole",
    )
    report.set_defaults(func=command_report)

    status = sub.add_parser("status", help="Print the unit/role status table plus pane liveness.")
    status.add_argument("--state-dir", default=".tmux-deliver")
    status.add_argument("--json", action="store_true")
    add_liveness_args(status)
    status.set_defaults(func=command_status)

    send = sub.add_parser("send", help="Paste a follow-up message into an agent window and submit it.")
    send.add_argument("--state-dir", default=".tmux-deliver")
    send.add_argument("--unit")
    send.add_argument("--role", choices=list(ROLES))
    send.add_argument("--target")
    send.add_argument("--message")
    send.add_argument("--file")
    send.add_argument("--submit-delay", type=float)
    send.add_argument(
        "--anyway",
        action="store_true",
        help="send even if the text reads as a change request addressed to the implementer "
        "and the target is a reviewer",
    )
    add_paste_args(send)
    send.set_defaults(func=command_send)

    await_running = sub.add_parser(
        "await-running", help="Block until an agent self-reports; non-zero if it never does."
    )
    await_running.add_argument("--state-dir", default=".tmux-deliver")
    await_running.add_argument("--unit", required=True)
    await_running.add_argument("--role", required=True, choices=list(ROLES))
    await_running.add_argument("--timeout", type=float, default=300.0)
    await_running.add_argument("--poll", type=float, default=3.0)
    await_running.set_defaults(func=command_await_running)

    nxt = sub.add_parser(
        "next-round",
        help="Reject the current round, bump the counter, and dispatch the change request.",
    )
    nxt.add_argument("--state-dir", default=".tmux-deliver")
    nxt.add_argument("--unit", required=True)
    nxt.add_argument("--message", default="")
    nxt.add_argument(
        "--feedback-file",
        help="markdown change request; sent to the implementer, and (unless --no-notify-reviewers) "
        "shown to both reviewers as a round-opened notice. Without this nothing is dispatched and "
        "every finished role is left `stale`",
    )
    nxt.add_argument(
        "--no-notify-reviewers",
        action="store_true",
        help="with --feedback-file: dispatch to the implementer only",
    )
    nxt.add_argument("--submit-delay", type=float)
    add_paste_args(nxt)
    nxt.add_argument("--force", action="store_true", help="exceed max-rounds (needs explicit user authorisation)")
    nxt.set_defaults(func=command_next_round)

    rereview = sub.add_parser(
        "re-review",
        help="Send the delta to both reviewer windows and clear their `stale` status.",
    )
    rereview.add_argument("--state-dir", default=".tmux-deliver")
    rereview.add_argument("--unit", required=True)
    rereview.add_argument("--file", help="the delta/instructions to send (default: a generated re-review request)")
    rereview.add_argument("--message")
    rereview.add_argument(
        "--roles", action="append", choices=list(ROLES), help="default: both reviewers"
    )
    rereview.add_argument("--submit-delay", type=float)
    rereview.add_argument(
        "--anyway",
        action="store_true",
        help="send even if the text reads as a change request addressed to the implementer",
    )
    add_paste_args(rereview)
    rereview.set_defaults(func=command_re_review)

    extend = sub.add_parser(
        "extend-scope",
        help="Append an authorised scope change to a unit's brief and tell all three roles.",
    )
    extend.add_argument("--state-dir", default=".tmux-deliver")
    extend.add_argument("--unit", required=True)
    extend.add_argument("--file", help="markdown amendment text")
    extend.add_argument("--message", help="amendment text inline")
    extend.add_argument("--roles", action="append", choices=list(ROLES), help="default: all three")
    extend.add_argument(
        "--no-notify",
        action="store_true",
        help="write the amendment to the brief without telling anyone (rarely what you want)",
    )
    extend.add_argument("--submit-delay", type=float)
    add_paste_args(extend)
    extend.set_defaults(func=command_extend_scope)

    accept = sub.add_parser("accept", help="Accept a unit: merge into the integration branch, close windows, drop worktree.")
    accept.add_argument("--state-dir", default=".tmux-deliver")
    accept.add_argument("--unit", required=True)
    accept.add_argument("--message")
    accept.add_argument("--keep-windows", action="store_true")
    accept.add_argument("--keep-worktree", action="store_true")
    accept.add_argument("--allow-dirty", action="store_true")
    accept.add_argument("--allow-empty", action="store_true")
    accept.set_defaults(func=command_accept)

    verify = sub.add_parser("verify-readonly", help="Confirm reviewers left a unit worktree untouched.")
    verify.add_argument("--state-dir", default=".tmux-deliver")
    verify.add_argument("--unit", required=True)
    verify.set_defaults(func=command_verify_readonly)

    watch = sub.add_parser(
        "watch",
        help="Print a line whenever any unit or role changes state, plus stall alerts.",
    )
    watch.add_argument("--state-dir", default=".tmux-deliver")
    watch.add_argument("--interval", type=float, default=3.0)
    watch.add_argument(
        "--liveness-interval",
        type=float,
        default=15.0,
        help="how often to sample panes for the stall check (default 15s; never below --interval)",
    )
    add_liveness_args(watch)
    watch.set_defaults(func=command_watch)

    mirror = sub.add_parser(
        "mirror", help="Link every live agent window into a session the user is attached to."
    )
    mirror.add_argument("--state-dir", default=".tmux-deliver")
    mirror.add_argument("--session", help="mirror session to use from now on (recorded in meta.json)")
    mirror.add_argument("--unlink", action="store_true", help="remove the mirrored links instead")
    mirror.set_defaults(func=command_mirror)

    finish = sub.add_parser("finish", help="Print the final integration report.")
    finish.add_argument("--state-dir", default=".tmux-deliver")
    finish.set_defaults(func=command_finish)

    cleanup = sub.add_parser("cleanup", help="Close unit windows and remove unit worktrees.")
    cleanup.add_argument("--state-dir", default=".tmux-deliver")
    cleanup.add_argument("--all", action="store_true", help="also remove the integration worktree (branch is kept)")
    cleanup.add_argument("--force", action="store_true")
    cleanup.set_defaults(func=command_cleanup)

    recover = sub.add_parser("recover", help="Print recovery info and which panes are still alive.")
    recover.add_argument("--state-dir", default=".tmux-deliver")
    recover.set_defaults(func=command_recover)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
