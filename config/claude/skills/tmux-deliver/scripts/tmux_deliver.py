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

ROLE_STATUSES = {"launched", "running", "done", "blocked", "failed"}
VERDICTS = {"pass", "concerns", "block"}

INTEGRATION_UNIT = "_integration"


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
    if not target:
        return False
    return run_tmux(["display-message", "-p", "-t", target, "#{pane_id}"], check=False, capture=True).returncode == 0


def send_command(target: str, command: str) -> None:
    run_tmux(["send-keys", "-t", target, command, "C-m"])


def paste_and_submit(target: str, text: str, state_dir: Path, label: str, delay: float) -> Path:
    """Paste text via a tmux buffer, wait for the paste to land, then submit.

    Sending Enter too eagerly (or in the same shell pipeline as the launch)
    leaves large prompts un-submitted, so the delay is deliberate and tunable.
    """
    ensure_state_dirs(state_dir)
    stamp = utc_now().replace(":", "")
    message_path = state_dir / "messages" / f"{stamp}-{slug(label, 40)}.md"
    message_path.write_text(text.rstrip() + "\n")
    buffer_name = f"tdlv-{uuid.uuid4().hex[:12]}"
    run_tmux(["load-buffer", "-b", buffer_name, str(message_path)])
    run_tmux(["paste-buffer", "-t", target, "-b", buffer_name])
    time.sleep(delay)
    run_tmux(["send-keys", "-t", target, "C-m"])
    return message_path


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
            "-c",
            shlex.quote(f'projects."{workspace}".trust_level="trusted"'),
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
        return "codex: handled by -c projects override"

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
        return resolve_runtime(REVIEWER_RUNTIME, REVIEWER_MODEL, REVIEWER_EFFORT)
    return {
        "runtime": meta["runtime"],
        "model": meta["model"],
        "effort": meta["effort"],
    }


# --- prompt construction ----------------------------------------------------


def skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def agent_skill_path(runtime: str) -> Path:
    return skill_dir().parent / f"tmux-deliver-agent-{runtime}"


def skill_invocation(runtime: str) -> str:
    path = agent_skill_path(runtime)
    if runtime == "codex":
        # Codex resolves skills by an explicit path reference.
        return f"Use $tmux-deliver-agent-codex at {path}."
    return f"Use the `tmux-deliver-agent-claude` skill (at {path})."


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

First actions, in order:
1. Run `pwd`. It MUST be `{unit['worktree']}`. If it is not, report `blocked`
   immediately and stop — do not cd, and do not touch any file.
2. Run `git status --short` and `git branch --show-current`. The branch MUST be
   `{unit['branch']}`. If not, report `blocked`.
3. Read every context file listed below, then read the assignment in full.
4. Report that you have started:
   `{base} --status running --message "Started; read the brief, planning test-first."`

Context files:
{context_lines}

Verification commands for this repo:
{meta.get('verification') or '- (none recorded; ask the orchestrator before assuming)'}

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

You are the {label} for this unit. Adopt the persona and review process defined
in the `developer_instructions` of `{persona}` — read that file first.

First actions, in order:
1. Run `pwd`. It MUST be `{unit['worktree']}`. If not, report `blocked` and stop.
2. Read the persona file above, then the unit brief and the delivery summary.
3. Inspect the change: `git diff {meta['integration_branch']}...HEAD` and
   `git log {meta['integration_branch']}..HEAD`.
4. Report started:
   `{base} --status running --message "Started {role} review of round {round_no}."`

You MUST NOT modify, stage, commit, revert, or delete any source, test, or
config file in this worktree. You may run tests, linters, type-checks and builds
(you have unrestricted execution so that you can), but the working tree must be
byte-identical when you finish. The orchestrator verifies this with
`git status --porcelain` and will reject your review if you changed anything.

Delivery summary to review: {delivery_file or '(not recorded — read the diff and git log)'}

Verification commands for this repo:
{meta.get('verification') or '- (none recorded; derive from the repo conventions)'}

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
    }
    write_json(meta_path(state_dir), meta)
    append_event(state_dir, {"type": "init", "session": session, "integration_branch": integration_branch})

    print(f"state_dir={state_dir}")
    print(f"repo={repo}")
    print(f"session={session}")
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
    if role == "implementer":
        prompt = build_implementer_prompt(
            script=script,
            state_dir=state_dir,
            meta=meta,
            unit=unit,
            round_no=round_no,
            brief=brief,
            context_files=context_files,
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
        )

    prompt_path = state_dir / "prompts" / f"{unit['id']}-r{round_no}-{role}.md"
    prompt_path.write_text(prompt)

    if args.dry_run:
        print("dry_run=true")
        print(f"unit={unit['id']} role={role} round={round_no}")
        print(f"launch_command={launch_command(**cfg, workspace=workspace)}")
        print(f"prompt_file={prompt_path}")
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

    unit["roles"][role] = {
        **blank_role(),
        "status": "launched",
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
    }
    unit["round"] = round_no
    unit["status"] = "delivering" if role == "implementer" else "reviewing"
    unit["updated_at"] = utc_now()
    write_json(unit_path(state_dir, unit["id"]), unit)
    append_event(
        state_dir,
        {"type": "start-agent", "unit": unit["id"], "role": role, "round": round_no, "pane": pane_id},
    )

    send_command(pane_id, launch_command(**cfg, workspace=workspace))
    time.sleep(args.startup_wait)
    paste_and_submit(
        pane_id,
        prompt,
        state_dir,
        f"{unit['id']}-r{round_no}-{role}",
        args.submit_delay if args.submit_delay is not None else float(meta.get("submit_delay") or 2.0),
    )

    print(f"unit={unit['id']}")
    print(f"role={role}")
    print(f"round={round_no}")
    print(f"runtime={cfg['runtime']}:{cfg['model']}/{cfg['effort']}")
    print(f"workspace_trust={trust_result}")
    print(f"window={window_id}")
    print(f"pane={pane_id}")
    print(f"prompt_file={prompt_path}")


def command_report(args: argparse.Namespace) -> None:
    if args.status not in ROLE_STATUSES:
        die(f"invalid status {args.status!r}; expected one of {', '.join(sorted(ROLE_STATUSES))}")
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


def command_status(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    units = load_units(state_dir)
    if args.json:
        print(json.dumps({"meta": read_json(meta_path(state_dir), {}), "units": units}, indent=2, sort_keys=True))
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


def command_send(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    if args.target:
        target = args.target
        label = f"send-{slug(args.target)}"
    else:
        if not args.unit or not args.role:
            die("provide --unit and --role (or an explicit --target)")
        unit = load_unit(state_dir, slug(args.unit))
        role_state = unit["roles"].get(args.role) or {}
        target = role_state.get("pane") or role_state.get("window")
        if not target:
            die(f"unit {unit['id']} has no {args.role} window; start it first")
        if not tmux_target_alive(target):
            die(f"{args.role} window for {unit['id']} is gone ({target}); relaunch with start-agent --relaunch")
        label = f"send-{unit['id']}-{args.role}"

    if args.file:
        text = Path(args.file).expanduser().resolve().read_text()
    elif args.message:
        text = args.message
    else:
        die("provide --file or --message")

    delay = args.submit_delay if args.submit_delay is not None else float(meta.get("submit_delay") or 2.0)
    path = paste_and_submit(target, text, state_dir, label, delay)
    print(f"sent_to={target}")
    print(f"message_file={path}")


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

    unit["round"] = new_round
    unit["status"] = "changes-requested"
    unit["updated_at"] = utc_now()
    for role in ROLES:
        role_state = unit["roles"].get(role) or blank_role()
        if role_state.get("status") == "done":
            role_state["status"] = "running"
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
    if not args.keep_windows:
        for role in ROLES:
            role_state = unit["roles"].get(role) or {}
            window = role_state.get("window")
            if window:
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


def command_watch(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    seen: dict[str, str] = {}
    while True:
        try:
            units = load_units(state_dir)
        except SystemExit:
            time.sleep(args.interval)
            continue
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
    print("done")


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
    print(f"integration_branch={meta.get('integration_branch')}")
    print()
    for unit in load_units(state_dir):
        for role in ROLES:
            role_state = unit["roles"].get(role) or {}
            pane = role_state.get("pane")
            if pane:
                alive = "alive" if tmux_target_alive(pane) else "DEAD"
                print(f"{unit['id']}/{role}: pane={pane} {alive}")
    print()
    command_status(argparse.Namespace(state_dir=str(state_dir), json=False))
    print()
    print("Treat `delivered` and `reviewed` as awaiting orchestrator judgement, never as accepted.")


# --- parser -----------------------------------------------------------------


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
    init.add_argument("--target", help="tmux pane of the orchestrator (default: $TMUX_PANE)")
    init.add_argument("--concurrency", type=int, default=3)
    init.add_argument("--max-rounds", type=int, default=3)
    init.add_argument("--verification", help="verification commands, recorded in every agent prompt")
    init.add_argument("--submit-delay", type=float, default=2.0)
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
    start.add_argument("--startup-wait", type=float, default=4.0)
    start.add_argument("--submit-delay", type=float)
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
    report.set_defaults(func=command_report)

    status = sub.add_parser("status", help="Print the unit/role status table.")
    status.add_argument("--state-dir", default=".tmux-deliver")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    send = sub.add_parser("send", help="Paste a follow-up message into an agent window and submit it.")
    send.add_argument("--state-dir", default=".tmux-deliver")
    send.add_argument("--unit")
    send.add_argument("--role", choices=list(ROLES))
    send.add_argument("--target")
    send.add_argument("--message")
    send.add_argument("--file")
    send.add_argument("--submit-delay", type=float)
    send.set_defaults(func=command_send)

    nxt = sub.add_parser("next-round", help="Reject the current round and bump the round counter.")
    nxt.add_argument("--state-dir", default=".tmux-deliver")
    nxt.add_argument("--unit", required=True)
    nxt.add_argument("--message", default="")
    nxt.add_argument("--force", action="store_true", help="exceed max-rounds (needs explicit user authorisation)")
    nxt.set_defaults(func=command_next_round)

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

    watch = sub.add_parser("watch", help="Print a line whenever any unit or role changes state.")
    watch.add_argument("--state-dir", default=".tmux-deliver")
    watch.add_argument("--interval", type=float, default=3.0)
    watch.set_defaults(func=command_watch)

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
