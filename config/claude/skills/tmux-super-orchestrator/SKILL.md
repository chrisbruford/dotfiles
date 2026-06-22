---
name: tmux-super-orchestrator
description: Coordinate large projects across multiple Claude sub-orchestrators in tmux. Use when a user wants one AI to decompose a large plan, Linear project, Linear issue list, Markdown plan, or mixed source documents into orchestrator-sized tasks, launch Claude workers in tmux windows, manage concurrency and git worktree isolation, and track status via file-based reporting with a background watcher.
---

# Tmux Super Orchestrator

Act as the supervisor for a suite of Claude orchestrators running in tmux. Hold the whole project context, decompose the work into independently deliverable units, start one Claude worker per ready unit, and integrate the results.

Use the bundled CLI for all tmux/status operations:

```bash
python3 ~/.claude/skills/tmux-super-orchestrator/scripts/tmux_orchestrate.py --help
```

Read these references only when needed:

- `references/task-intake.md` for turning Linear projects, issue lists, Markdown plans, and mixed docs into a worker-ready task graph.
- `references/status-protocol.md` for the persisted state files, report statuses, and recovery expectations.

## Operating Model

- You are the only agent that holds the full project plan, milestones, dependencies, and source-document context.
- Workers receive sharply scoped task briefs with only the context required for their assignment.
- Workers report by updating task state files only — they do not send text into the supervisor window.
- Keep at most three active workers by default unless the user explicitly changes the concurrency cap.
- Prefer a dedicated git worktree per code-bearing worker. For code-bearing tasks, start the worker with worktree isolation enabled: create or select the task worktree first, then launch the tmux worker session with that worktree as its initial workspace. The worker should verify `pwd`, not switch directories as a normal step.
- Hard-code worker launch to `claude`; do not switch to another agent runtime unless the user asks for a future revision.
- Use the existing `deliver` skill when a worker assignment is large enough to require its own orchestrator loop. For small non-code tasks, direct execution by the worker is acceptable.

## Workflow

1. **Collect sources.** Gather every source the user provides: Linear project, Linear issues, Markdown files, specs, PRDs, ticket exports, architecture docs, and repository context.
2. **Build a context pack.** Create an orchestration state directory and write a concise global context pack there: goals, milestones, task graph, source links or file paths, constraints, out-of-scope items, verification commands, and integration risks.
3. **Register or create the supervisor.**
   - If you are already running inside the tmux pane that should receive worker reports, run `register-supervisor`.
   - If not, run `init-session` to create a tmux session with a `supervisor` window, launch Claude there, and hand off to that session.
4. **Start the watcher.** Immediately after setup, use the `Monitor` tool to run `watch` in the background. Each line it emits is a task state change — handle it by running `status` and deciding the next action.
5. **Slice work.** Produce task briefs that are large enough to need an orchestrator but small enough to finish independently. Capture dependencies explicitly.
6. **Start workers.** Start one worker session for each ready task, observing the concurrency cap. Give each worker its workspace or worktree, source context, acceptance criteria, expected verification, and report commands.
7. **Monitor and steer.** The watcher notifies you of state changes. Run `status` to see the full picture, then send follow-up instructions, unblock workers, or pause for user input as needed.
7. **Integrate.** When workers finish, inspect their summaries and diffs, verify against the global plan, run project-level checks, resolve conflicts, and produce the final project report.

## Supervisor Setup

If already inside the tmux pane that should receive updates:

```bash
python3 ~/.claude/skills/tmux-super-orchestrator/scripts/tmux_orchestrate.py register-supervisor \
  --state-dir .tmux-orchestration \
  --concurrency 3
```

If not inside tmux, create a new session and launch a supervisor Claude window:

```bash
python3 ~/.claude/skills/tmux-super-orchestrator/scripts/tmux_orchestrate.py init-session \
  --session project-supervisor \
  --state-dir .tmux-orchestration \
  --root . \
  --concurrency 3
```

After `init-session`, attach with:

```bash
tmux attach -t project-supervisor
```

## Worker Start Pattern

Write each worker brief to a file in the state directory, then start a worker from that file:

```bash
python3 ~/.claude/skills/tmux-super-orchestrator/scripts/tmux_orchestrate.py start-worker \
  --state-dir .tmux-orchestration \
  --task-id payments-api-001 \
  --title "Deliver payment retry API changes" \
  --workspace /path/to/worktree-or-project \
  --assignment-file .tmux-orchestration/briefs/payments-api-001.md \
  --context-file .tmux-orchestration/global-context.md \
  --context-file .tmux-orchestration/task-graph.md
```

The helper creates a tmux window with the workspace as the initial cwd, starts `claude` there, pastes a worker prompt that invokes `$tmux-sub-orchestrator`, and records the task state. If not using the helper, perform the same operation directly with tmux: start the worker window in the chosen project directory or git worktree.

Use `prepare-worktree` before starting a worker when you need a new isolated git worktree:

```bash
python3 ~/.claude/skills/tmux-super-orchestrator/scripts/tmux_orchestrate.py prepare-worktree \
  --repo /path/to/repo \
  --task-id payments-api-001 \
  --base main
```

Use the printed worktree path as the worker's initial workspace.

## Worker Brief Requirements

Every assignment file must include:

- Task ID and title.
- Source task, Linear issue URL or key, Markdown section, or source-file path.
- Required workspace and expected git worktree or branch isolation.
- Objective and non-goals.
- Relevant global context excerpt, not the whole project if it is not needed.
- Acceptance criteria.
- Verification commands and expected evidence.
- Files or areas that must not be touched.
- Dependency assumptions and blockers to report immediately.
- Whether the worker should use `deliver`, and if left to worker judgment, the rule for deciding.

## Monitoring

Start the background watcher using the `Monitor` tool immediately after setup:

```bash
python3 ~/.claude/skills/tmux-super-orchestrator/scripts/tmux_orchestrate.py watch \
  --state-dir .tmux-orchestration
```

Each line emitted is a task state change: `task_id=... status=... title=... message=...`. When notified, run `status` to see the full picture and decide the next action.

Check persisted status at any time:

```bash
python3 ~/.claude/skills/tmux-super-orchestrator/scripts/tmux_orchestrate.py status \
  --state-dir .tmux-orchestration
```

Send follow-up instructions to a worker window:

```bash
python3 ~/.claude/skills/tmux-super-orchestrator/scripts/tmux_orchestrate.py send \
  --state-dir .tmux-orchestration \
  --task-id payments-api-001 \
  --message "Please rerun the integration test after rebasing onto the latest base worktree."
```

When all tasks are complete and the user is ready to finish, clean up all worker windows and their git worktrees:

```bash
python3 ~/.claude/skills/tmux-super-orchestrator/scripts/tmux_orchestrate.py cleanup \
  --state-dir .tmux-orchestration
```

This closes every worker tmux window and removes its git worktree. It refuses to run if any task is still in an active state (`launched`, `running`, `blocked`). The supervisor window is left open.

Recover after interruption:

```bash
python3 ~/.claude/skills/tmux-super-orchestrator/scripts/tmux_orchestrate.py recover \
  --state-dir .tmux-orchestration
```

## Acceptance Bar

Do not mark the overall project complete until:

- Every required task is `done`, deliberately cancelled, or explicitly deferred with user approval.
- Worker summaries and diffs have been reviewed against the global plan.
- Cross-task conflicts and dependency assumptions have been reconciled.
- Project-level verification has run or any unrun command is documented with the reason.
- The user receives a final report with delivered work, remaining risks, manual steps, and verification evidence.
