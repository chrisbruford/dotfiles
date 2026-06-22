# Worker Contract

This reference expands the sub-orchestrator behavior expected by the tmux supervisor.

## Understand Scope Before Acting

Identify:

- task objective;
- acceptance criteria;
- relevant source documents;
- expected workspace;
- whether the directory is an existing worktree;
- verification commands;
- files or areas that are out of scope;
- dependencies on other workers.

If the prompt lacks enough detail to avoid risky guessing, report `blocked` with the exact missing information.

## Workspace

The supervisor should launch the tmux worker window in the assigned workspace, usually a task-specific git worktree for code-bearing work. Verify `pwd` before inspecting or editing files. If the directory is wrong, report `blocked` instead of silently switching directories; a wrong cwd means the launch contract failed and the supervisor should correct or relaunch the task.

## When To Use `$deliver`

Use `$deliver` for work that benefits from a local orchestrator loop:

- implementation in a codebase;
- new or changed tests;
- multi-file edits;
- behavior changes with regression risk;
- review-gated work where QA/adversarial checks are useful.

Do not use `$deliver` just to read files, summarize docs, update a simple checklist, or report that a dependency is blocked.

## Git Worktree Expectations

If the supervisor provides a worktree path, use it.

If you are in a git repo and must edit code without an isolated worktree:

1. Stop before editing.
2. Prefer creating a task-specific worktree from the intended base branch.
3. If the base branch or destination path is unclear, report `blocked`.

Never run destructive git commands unless the supervisor explicitly asked for that operation.

## Reports

Keep reports short but operationally useful. The supervisor needs enough information to update the global plan without reading the full worker transcript.

Good blocker report:

```bash
python3 /path/to/tmux_orchestrate.py report --state-dir /path/to/.tmux-orchestration --task-id TASK --status blocked --message "Blocked: issue ABC-123 requires the API contract from task payments-api-001 before tests can be written."
```

Good done report:

```bash
python3 /path/to/tmux_orchestrate.py report --state-dir /path/to/.tmux-orchestration --task-id TASK --status done --summary-file /path/to/.tmux-orchestration/summaries/TASK.md --message "Completed with unit and integration checks passing."
```
