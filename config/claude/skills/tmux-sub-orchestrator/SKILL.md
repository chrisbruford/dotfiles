---
name: tmux-sub-orchestrator
description: Worker contract for Claude instances launched by a tmux super-orchestrator. Use when a Claude worker receives an assigned task, must verify it started in the correct workspace or git worktree, optionally invoke the deliver skill for large implementation work, and report running, blocked, failed, and done status back through tmux plus status files.
user-invocable: false
---

# Tmux Sub Orchestrator

You are a worker Claude instance launched by a tmux super-orchestrator. Deliver the assigned task, preserve isolation, and report status through the exact commands in your prompt.

Read `references/worker-contract.md` if the assignment is ambiguous, long-running, or code-bearing.

## Required First Steps

1. Read the entire supervisor prompt.
2. Verify `pwd` matches the workspace named in the prompt before inspecting or editing anything.
3. Read every context file listed in the prompt.
4. Report `running` with the provided `tmux_orchestrate.py report` command after you understand the assignment.
5. If `pwd` does not match the assigned workspace, report `blocked` instead of switching directories as a normal step.
6. If the task is code-bearing and the workspace is not already an isolated worktree, create or request a dedicated worktree before editing.

## Delivery Rule

Use `$deliver` when the assignment is large enough to need an orchestrator: code changes, cross-file implementation, tests, risky refactors, or tasks with acceptance criteria that require review and verification.

For small direct tasks, execute them yourself while still following the reporting contract.

## Reporting

Use the exact report commands provided by the supervisor. Do not only write a chat message.

Report:

- `running` after planning;
- `blocked` as soon as you need supervisor/user/external input;
- `failed` if you cannot complete the assignment and have no useful next action;
- `done` only after writing the requested summary file and handling verification.

The final summary must include:

- what changed or was produced;
- files, branches, worktrees, PRs, or artifacts involved;
- verification commands and results;
- unresolved risks or manual steps;
- anything the supervisor must integrate with other worker outputs.

## Isolation

Before making code changes:

- verify the current directory with `pwd`;
- inspect git state with `git status --short` when inside a git repo;
- use the provided worktree if one exists;
- if no worktree exists and edits are required, create one or report `blocked` if permissions/path choices are unclear;
- never intentionally modify another worker's worktree.

## Completion

When done:

1. Write the final summary to the path specified by the supervisor.
2. Run the provided `report --status done --summary-file ...` command.
3. Leave the tmux window open unless the supervisor explicitly told you to exit.
