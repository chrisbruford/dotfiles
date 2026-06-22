#!/usr/bin/env python3
"""Tmux helper CLI for Claude super-orchestrator skills."""

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


CLAUDE_COMMAND = "claude --model default"
VALID_STATUSES = {
    "queued",
    "launched",
    "running",
    "blocked",
    "failed",
    "done",
    "cancelled",
}
ACTIVE_STATUSES = {"launched", "running", "blocked"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def slug(value: str, max_len: int = 48) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-")
    cleaned = re.sub(r"-{2,}", "-", cleaned).lower()
    return (cleaned or "task")[:max_len]


def state_path(raw: str | Path) -> Path:
    return Path(raw).expanduser().resolve()


def ensure_state_dirs(state_dir: Path) -> None:
    for name in ["tasks", "prompts", "briefs", "summaries", "messages"]:
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
    event = {"at": utc_now(), **event}
    with (state_dir / "events.jsonl").open("a") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def meta_path(state_dir: Path) -> Path:
    return state_dir / "meta.json"


def task_path(state_dir: Path, task_id: str) -> Path:
    return state_dir / "tasks" / f"{task_id}.json"


def load_meta(state_dir: Path) -> dict[str, Any]:
    meta = read_json(meta_path(state_dir), {})
    if not meta:
        die(f"no meta.json found in {state_dir}; run register-supervisor or init-session first")
    return meta


def load_task(state_dir: Path, task_id: str) -> dict[str, Any]:
    task = read_json(task_path(state_dir, task_id), {})
    if not task:
        die(f"unknown task id {task_id!r} in {state_dir}")
    return task


def run_tmux(args: list[str], *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
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
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            detail = stderr or stdout or "tmux command failed"
            die(f"{detail}: {' '.join(shlex.quote(part) for part in cmd)}")
        raise


def tmux_has_session(session: str) -> bool:
    result = run_tmux(["has-session", "-t", session], check=False, capture=True)
    return result.returncode == 0


def tmux_display(target: str, fmt: str) -> str:
    result = run_tmux(["display-message", "-p", "-t", target, fmt], capture=True)
    return result.stdout.strip()


def send_text_to_tmux(target: str, text: str, state_dir: Path, label: str) -> Path:
    ensure_state_dirs(state_dir)
    message_path = state_dir / "messages" / f"{utc_now().replace(':', '')}-{slug(label, 30)}.md"
    message_path.write_text(text.rstrip() + "\n")
    buffer_name = f"oorch-{uuid.uuid4().hex[:12]}"
    run_tmux(["load-buffer", "-b", buffer_name, str(message_path)])
    run_tmux(["paste-buffer", "-t", target, "-b", buffer_name])
    time.sleep(0.5)
    run_tmux(["send-keys", "-t", target, "C-m"])
    return message_path


def send_command(target: str, command: str) -> None:
    run_tmux(["send-keys", "-t", target, command, "C-m"])


def build_bootstrap_prompt(state_dir: Path, root: Path) -> str:
    script = Path(__file__).resolve()
    return f"""Use $tmux-super-orchestrator.

You are the tmux super-orchestrator for this workspace.

State directory: {state_dir}
Project root: {root}

Begin by reading any user-provided project sources, then build the global context pack and task graph before starting workers. Use the bundled tmux_orchestrate.py CLI for worker startup, status, reporting, and recovery.

Worker notifications:
Workers update task state files directly — they do NOT send text into this window.
Start the watcher immediately using the Monitor tool so you are notified when any task changes state:

  python3 {script} watch --state-dir {state_dir}

Each line the watcher emits (task_id=... status=... title=... message=...) is a notification that a task has changed. When you receive one, run `status` to see the full picture and decide the next action.
"""


def build_worker_prompt(
    *,
    script_path: Path,
    state_dir: Path,
    task_id: str,
    title: str,
    workspace: Path,
    assignment: str,
    context_files: list[Path],
    use_deliver: str,
) -> str:
    report_base = (
        f"python3 {shlex.quote(str(script_path))} report "
        f"--state-dir {shlex.quote(str(state_dir))} "
        f"--task-id {shlex.quote(task_id)}"
    )
    summary_path = state_dir / "summaries" / f"{task_id}.md"
    context_lines = "\n".join(f"- {path}" for path in context_files) or "- None"
    deliver_rule = {
        "always": "Use the deliver skill for this assignment after you understand the brief.",
        "never": "Do not invoke deliver skill unless the supervisor later tells you to.",
        "auto": "Use the deliver skill if the assignment is code-bearing, cross-file, risky, or otherwise large enough to require an orchestrator loop. For small direct tasks, execute directly.",
    }[use_deliver]

    return f"""Use $tmux-sub-orchestrator.

You are a Claude sub-orchestrator launched by a tmux super-orchestrator.

Task ID: {task_id}
Title: {title}
Workspace: {workspace}
State directory: {state_dir}

First actions:
1. Verify `pwd` is `{workspace}`. The supervisor launched this tmux window with that initial workspace.
2. If `pwd` is not `{workspace}`, report `blocked` before inspecting or editing anything.
3. Read this full prompt and any context files listed below.
4. Report that you are running with:
   `{report_base} --status running --message "Started; reading assignment and planning."`

Deliver skill rule:
{deliver_rule}

Context files:
{context_lines}

Assignment:
{assignment.rstrip()}

Reporting contract:
- Report blockers immediately:
  `{report_base} --status blocked --message "Blocked because ..."`
- Report failures with the last useful state:
  `{report_base} --status failed --message "Failed because ..."`
- When complete, write your final summary to:
  `{summary_path}`
- Then report done:
  `{report_base} --status done --summary-file {shlex.quote(str(summary_path))} --message "Completed; summary and verification evidence are ready."`

Do not mark yourself done until the assignment acceptance criteria and verification requirements have been handled or clearly documented as impossible.
"""


def command_init_session(args: argparse.Namespace) -> None:
    root = Path(args.root).expanduser().resolve()
    state_dir = state_path(args.state_dir)
    ensure_state_dirs(state_dir)

    if tmux_has_session(args.session):
        if not args.reuse:
            die(f"tmux session {args.session!r} already exists; pass --reuse to use it")
    else:
        run_tmux(["new-session", "-d", "-s", args.session, "-n", "supervisor", "-c", str(root)])

    supervisor_target = f"{args.session}:supervisor"
    pane_id = tmux_display(supervisor_target, "#{pane_id}")
    meta = {
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "session": args.session,
        "root": str(root),
        "state_dir": str(state_dir),
        "supervisor_target": pane_id,
        "concurrency": args.concurrency,
        "claude_command": CLAUDE_COMMAND,
        "script_path": str(Path(__file__).resolve()),
    }
    write_json(meta_path(state_dir), meta)
    append_event(state_dir, {"type": "init-session", "session": args.session, "target": pane_id})

    if not args.no_launch_supervisor:
        send_command(pane_id, CLAUDE_COMMAND)
        time.sleep(args.startup_wait)
        prompt = Path(args.supervisor_prompt_file).read_text() if args.supervisor_prompt_file else build_bootstrap_prompt(state_dir, root)
        send_text_to_tmux(pane_id, prompt, state_dir, "supervisor-bootstrap")

    print(f"state_dir={state_dir}")
    print(f"session={args.session}")
    print(f"supervisor_target={pane_id}")


def command_register_supervisor(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    ensure_state_dirs(state_dir)
    target = args.target or os.environ.get("TMUX_PANE")
    if not target:
        die("no --target provided and TMUX_PANE is not set")

    session = tmux_display(target, "#{session_name}")
    root = Path(args.root).expanduser().resolve() if args.root else Path.cwd().resolve()
    meta = {
        "created_at": read_json(meta_path(state_dir), {}).get("created_at", utc_now()),
        "updated_at": utc_now(),
        "session": session,
        "root": str(root),
        "state_dir": str(state_dir),
        "supervisor_target": target,
        "concurrency": args.concurrency,
        "claude_command": CLAUDE_COMMAND,
        "script_path": str(Path(__file__).resolve()),
    }
    write_json(meta_path(state_dir), meta)
    append_event(state_dir, {"type": "register-supervisor", "session": session, "target": target})
    print(f"state_dir={state_dir}")
    print(f"session={session}")
    print(f"supervisor_target={target}")


def active_task_count(state_dir: Path) -> int:
    count = 0
    for path in (state_dir / "tasks").glob("*.json"):
        task = read_json(path, {})
        if task.get("status") in ACTIVE_STATUSES:
            count += 1
    return count


def command_start_worker(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    ensure_state_dirs(state_dir)
    meta = load_meta(state_dir)
    task_id = slug(args.task_id or args.title)
    task_file = task_path(state_dir, task_id)
    if task_file.exists() and not args.reuse:
        die(f"task {task_id!r} already exists; pass --reuse to update and relaunch intentionally")

    if args.assignment_file:
        assignment_path = Path(args.assignment_file).expanduser().resolve()
        assignment = assignment_path.read_text()
    elif args.assignment:
        assignment_path = None
        assignment = args.assignment
    else:
        die("provide --assignment-file or --assignment")

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists():
        die(f"workspace does not exist: {workspace}")

    context_files = [Path(path).expanduser().resolve() for path in args.context_file]
    missing_context = [str(path) for path in context_files if not path.exists()]
    if missing_context:
        die(f"context file(s) do not exist: {', '.join(missing_context)}")

    prompt = build_worker_prompt(
        script_path=Path(meta.get("script_path") or Path(__file__).resolve()),
        state_dir=state_dir,
        task_id=task_id,
        title=args.title,
        workspace=workspace,
        assignment=assignment,
        context_files=context_files,
        use_deliver=args.use_deliver,
    )
    prompt_path = state_dir / "prompts" / f"{task_id}.md"

    if args.dry_run:
        prompt_path.write_text(prompt)
        print(f"dry_run=true")
        print(f"task_id={task_id}")
        print(f"prompt_file={prompt_path}")
        return

    limit = int(meta.get("concurrency") or 3)
    current = active_task_count(state_dir)
    if current >= limit and not args.ignore_concurrency:
        die(f"concurrency limit reached: {current}/{limit} active tasks")

    session = meta["session"]
    window_name = slug(f"task-{task_id}", 40)
    result = run_tmux(
        ["new-window", "-d", "-P", "-F", "#{window_id}", "-t", session, "-n", window_name, "-c", str(workspace)],
        capture=True,
    )
    window_id = result.stdout.strip()
    pane_id = tmux_display(window_id, "#{pane_id}")

    prompt_path.write_text(prompt)
    task = {
        "id": task_id,
        "title": args.title,
        "status": "launched",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "workspace": str(workspace),
        "assignment_file": str(assignment_path) if assignment_path else None,
        "context_files": [str(path) for path in context_files],
        "prompt_file": str(prompt_path),
        "summary_file": str(state_dir / "summaries" / f"{task_id}.md"),
        "tmux_window": window_id,
        "tmux_pane": pane_id,
        "window_name": window_name,
        "history": [],
    }
    write_json(task_file, task)
    append_event(state_dir, {"type": "worker-launched", "task_id": task_id, "window": window_id, "pane": pane_id})

    send_command(pane_id, CLAUDE_COMMAND)
    time.sleep(args.startup_wait)
    send_text_to_tmux(pane_id, prompt, state_dir, f"prompt-{task_id}")
    print(f"task_id={task_id}")
    print(f"window={window_id}")
    print(f"pane={pane_id}")
    print(f"prompt_file={prompt_path}")


def command_report(args: argparse.Namespace) -> None:
    if args.status not in VALID_STATUSES:
        die(f"invalid status {args.status!r}; expected one of {', '.join(sorted(VALID_STATUSES))}")
    state_dir = state_path(args.state_dir)
    ensure_state_dirs(state_dir)
    task = load_task(state_dir, args.task_id)

    summary_text = ""
    summary_file = args.summary_file
    if summary_file:
        summary_path = Path(summary_file).expanduser().resolve()
        if not summary_path.exists():
            die(f"summary file does not exist: {summary_path}")
        summary_text = summary_path.read_text().strip()
        task["summary_file"] = str(summary_path)

    report = {
        "at": utc_now(),
        "status": args.status,
        "message": args.message or "",
        "summary_file": task.get("summary_file"),
    }
    task.setdefault("history", []).append(report)
    task["status"] = args.status
    task["updated_at"] = report["at"]
    task["last_message"] = args.message or ""
    write_json(task_path(state_dir, args.task_id), task)
    append_event(state_dir, {"type": "report", "task_id": args.task_id, "status": args.status, "message": args.message or ""})

    print(f"task_id={args.task_id}")
    print(f"status={args.status}")
    print(f"state_file={task_path(state_dir, args.task_id)}")


def load_tasks(state_dir: Path) -> list[dict[str, Any]]:
    tasks = []
    for path in sorted((state_dir / "tasks").glob("*.json")):
        task = read_json(path, {})
        if task:
            tasks.append(task)
    return tasks


def command_status(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    tasks = load_tasks(state_dir)
    if args.json:
        print(json.dumps(tasks, indent=2, sort_keys=True))
        return
    if not tasks:
        print(f"No tasks found in {state_dir}")
        return
    print(f"{'STATUS':<10} {'TASK':<28} {'UPDATED':<22} TITLE")
    for task in tasks:
        print(
            f"{task.get('status', ''):<10} "
            f"{task.get('id', '')[:28]:<28} "
            f"{task.get('updated_at', '')[:22]:<22} "
            f"{task.get('title', '')}"
        )


def command_send(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    if args.task_id:
        task = load_task(state_dir, args.task_id)
        target = task.get("tmux_pane") or task.get("tmux_window")
        if not target:
            die(f"task {args.task_id!r} has no tmux target")
    elif args.target:
        target = args.target
    else:
        die("provide --task-id or --target")

    if args.file:
        text = Path(args.file).expanduser().resolve().read_text()
    elif args.message:
        text = args.message
    else:
        die("provide --file or --message")
    path = send_text_to_tmux(target, text, state_dir, f"send-{args.task_id or slug(target)}")
    print(f"sent_to={target}")
    print(f"message_file={path}")


def command_watch(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    interval = args.interval
    last_states: dict[str, tuple[str, str]] = {}
    while True:
        try:
            tasks = load_tasks(state_dir)
        except SystemExit:
            time.sleep(interval)
            continue
        for task in tasks:
            tid = task["id"]
            current = (task.get("status", ""), task.get("updated_at", ""))
            if last_states.get(tid) != current:
                last_states[tid] = current
                print(
                    f"task_id={tid} status={task.get('status', '')} "
                    f"title={task.get('title', '')} message={task.get('last_message', '')}",
                    flush=True,
                )
        time.sleep(interval)


def remove_worktree(worktree_path: Path) -> tuple[bool, str]:
    if not worktree_path.exists():
        return True, "already absent"
    result = subprocess.run(
        ["git", "-C", str(worktree_path), "rev-parse", "--git-common-dir"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return False, "could not determine main repo (not a git worktree?)"
    git_common = Path(result.stdout.strip())
    if not git_common.is_absolute():
        git_common = (worktree_path / git_common).resolve()
    main_repo = git_common.parent
    result = subprocess.run(
        ["git", "-C", str(main_repo), "worktree", "remove", "--force", str(worktree_path)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, "removed"


def command_cleanup(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    tasks = load_tasks(state_dir)

    active = [t for t in tasks if t.get("status") in ACTIVE_STATUSES]
    if active:
        details = ", ".join(f"{t['id']} ({t.get('status')})" for t in active)
        die(f"cannot clean up: {len(active)} task(s) still active — {details}")

    if not tasks:
        print("no tasks to clean up")
        return

    for task in tasks:
        task_id = task.get("id", "?")
        window = task.get("tmux_window")
        workspace = task.get("workspace")

        window_result = "skipped (no window recorded)"
        if window:
            r = run_tmux(["kill-window", "-t", window], check=False, capture=True)
            window_result = "closed" if r.returncode == 0 else "already gone"

        worktree_result = "skipped (no workspace recorded)"
        if workspace:
            ok, msg = remove_worktree(Path(workspace))
            worktree_result = msg

        print(f"{task_id}: window={window_result} worktree={worktree_result}")

    print("done")


def command_recover(args: argparse.Namespace) -> None:
    state_dir = state_path(args.state_dir)
    meta = load_meta(state_dir)
    print(f"state_dir={state_dir}")
    print(f"session={meta.get('session')}")
    print(f"supervisor_target={meta.get('supervisor_target')}")
    print(f"attach_command=tmux attach -t {shlex.quote(str(meta.get('session')))}")
    print()
    command_status(argparse.Namespace(state_dir=str(state_dir), json=False))
    print()
    print("Treat done tasks as ready for supervisor review, not automatically accepted.")


def run_git(args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError:
        die("git is not installed or not on PATH")
    except subprocess.CalledProcessError as exc:
        detail = ""
        if capture:
            detail = (exc.stderr or exc.stdout or "").strip()
        die(detail or f"git command failed: {' '.join(shlex.quote(part) for part in ['git', *args])}")


def command_prepare_worktree(args: argparse.Namespace) -> None:
    repo = Path(args.repo).expanduser().resolve()
    if not repo.exists():
        die(f"repo path does not exist: {repo}")
    root_result = run_git(["-C", str(repo), "rev-parse", "--show-toplevel"], capture=True)
    repo_root = Path(root_result.stdout.strip()).resolve()
    repo_name = repo_root.name
    task_slug = slug(args.task_id)
    worktree_root = (
        Path(args.worktree_root).expanduser().resolve()
        if args.worktree_root
        else repo_root.parent / f"{repo_name}-worktrees"
    )
    worktree_path = worktree_root / task_slug
    branch = args.branch or f"tmux/{task_slug}"

    branch_exists = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", f"refs/heads/{branch}"],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    if branch_exists:
        command = ["git", "-C", str(repo_root), "worktree", "add", str(worktree_path), branch]
    else:
        command = ["git", "-C", str(repo_root), "worktree", "add", "-b", branch, str(worktree_path), args.base]

    if args.print_only:
        print(" ".join(shlex.quote(part) for part in command))
        print(f"worktree_path={worktree_path}")
        print(f"branch={branch}")
        return

    worktree_root.mkdir(parents=True, exist_ok=True)
    if worktree_path.exists():
        print(f"worktree_path={worktree_path}")
        print(f"branch={branch}")
        print("already_exists=true")
        return
    subprocess.run(command, check=True)
    print(f"worktree_path={worktree_path}")
    print(f"branch={branch}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Claude tmux super-orchestration sessions.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-session", help="Create/register a tmux supervisor session.")
    init.add_argument("--session", required=True)
    init.add_argument("--state-dir", default=".tmux-orchestration")
    init.add_argument("--root", default=".")
    init.add_argument("--concurrency", type=int, default=3)
    init.add_argument("--startup-wait", type=float, default=3.0)
    init.add_argument("--supervisor-prompt-file")
    init.add_argument("--reuse", action="store_true")
    init.add_argument("--no-launch-supervisor", action="store_true")
    init.set_defaults(func=command_init_session)

    register = sub.add_parser("register-supervisor", help="Register the current or specified tmux pane as supervisor.")
    register.add_argument("--state-dir", default=".tmux-orchestration")
    register.add_argument("--target")
    register.add_argument("--root")
    register.add_argument("--concurrency", type=int, default=3)
    register.set_defaults(func=command_register_supervisor)

    start_worker = sub.add_parser("start-worker", help="Launch a Claude worker in a new tmux window.")
    start_worker.add_argument("--state-dir", default=".tmux-orchestration")
    start_worker.add_argument("--task-id")
    start_worker.add_argument("--title", required=True)
    start_worker.add_argument("--workspace", required=True)
    start_worker.add_argument("--assignment-file")
    start_worker.add_argument("--assignment")
    start_worker.add_argument("--context-file", action="append", default=[])
    start_worker.add_argument("--use-deliver", choices=["auto", "always", "never"], default="auto")
    start_worker.add_argument("--startup-wait", type=float, default=3.0)
    start_worker.add_argument("--reuse", action="store_true")
    start_worker.add_argument("--ignore-concurrency", action="store_true")
    start_worker.add_argument("--dry-run", action="store_true")
    start_worker.set_defaults(func=command_start_worker)

    report = sub.add_parser("report", help="Persist and optionally notify a worker status report.")
    report.add_argument("--state-dir", default=".tmux-orchestration")
    report.add_argument("--task-id", required=True)
    report.add_argument("--status", required=True)
    report.add_argument("--message", default="")
    report.add_argument("--summary-file")
    report.set_defaults(func=command_report)

    status = sub.add_parser("status", help="Print task status.")
    status.add_argument("--state-dir", default=".tmux-orchestration")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    send = sub.add_parser("send", help="Paste a follow-up message into a tmux target or task pane.")
    send.add_argument("--state-dir", default=".tmux-orchestration")
    send.add_argument("--task-id")
    send.add_argument("--target")
    send.add_argument("--message")
    send.add_argument("--file")
    send.set_defaults(func=command_send)

    watch = sub.add_parser("watch", help="Poll task files and print a line to stdout whenever a task changes state.")
    watch.add_argument("--state-dir", default=".tmux-orchestration")
    watch.add_argument("--interval", type=float, default=3.0)
    watch.set_defaults(func=command_watch)

    cleanup = sub.add_parser("cleanup", help="Close all worker tmux windows and remove their git worktrees. Refuses if any task is still active.")
    cleanup.add_argument("--state-dir", default=".tmux-orchestration")
    cleanup.set_defaults(func=command_cleanup)

    recover = sub.add_parser("recover", help="Print recovery information for a tmux orchestration state directory.")
    recover.add_argument("--state-dir", default=".tmux-orchestration")
    recover.set_defaults(func=command_recover)

    worktree = sub.add_parser("prepare-worktree", help="Create or print an isolated git worktree for a task.")
    worktree.add_argument("--state-dir", default=".tmux-orchestration")
    worktree.add_argument("--repo", required=True)
    worktree.add_argument("--task-id", required=True)
    worktree.add_argument("--base", default="HEAD")
    worktree.add_argument("--branch")
    worktree.add_argument("--worktree-root")
    worktree.add_argument("--print-only", action="store_true")
    worktree.set_defaults(func=command_prepare_worktree)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
