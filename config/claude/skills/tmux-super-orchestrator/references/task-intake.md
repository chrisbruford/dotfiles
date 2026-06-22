# Task Intake

Use this reference when turning large source material into a worker-ready tmux plan.

## Source Types

### Linear Project

When Linear access is available, fetch:

- project name, status, target date, description, and milestones;
- issues in the project, including identifier, title, status, assignee, priority, labels, estimate, parent/sub-issue links, dependencies, and comments if relevant;
- linked documents or specs referenced by project or issue descriptions.

If Linear access is unavailable, ask the user for a project export, issue list, or Markdown copy. Do not invent issue details.

### Linear Issue List

For a loose issue list, group issues by dependency and milestone rather than by display order. Preserve issue identifiers in every worker brief so results can be mapped back.

### Markdown Plan

Read the full file. If it links to sibling specs, ADRs, checklists, or tickets, read those too. Extract explicit tasks first; only infer tasks where the source material leaves work implicit.

### Mixed Docs Folder

Build a manifest of files, then read selectively:

- start with README, PRD, roadmap, implementation plan, architecture docs, and task lists;
- search for "TODO", "Milestone", "Acceptance", "Out of scope", "Do not", "Dependencies", and "Verification";
- preserve source paths and headings in the task graph.

## Task Graph

Write `task-graph.md` in the state directory with:

- milestones and their exit criteria;
- tasks in dependency order;
- tasks safe to run in parallel;
- likely file or system ownership for each task;
- expected workspace, worktree, or repo for each task;
- verification commands;
- integration checkpoints.

## Task Sizing

Dispatch a task to a sub-orchestrator when it has enough complexity to require planning, validation, or review. Good worker-sized tasks usually have one clear objective, one primary repo or directory, and an independent verification path.

Split tasks that:

- touch unrelated systems;
- require different repositories or directories;
- have separate deploy or manual steps;
- would force one worker to hold too much global context.

Merge tasks that:

- cannot be tested independently;
- repeatedly modify the same files in conflicting ways;
- are small doc or configuration edits under one acceptance criterion.

## Branch Naming in Briefs

Do not mandate a specific branch name or prefix in worker briefs. Instead, tell the worker the objective and let it choose a short descriptive name appropriate to the work. Workers are better placed to avoid conflicts with existing branches in the target repo.

If a naming convention must be stated, describe the intent ("use a short descriptive branch name, e.g. `fix-auth-timeout`") rather than a structural pattern like `test/` or `feature/` — slash-namespaced names conflict with any existing branch that shares the prefix without the slash.

If a worker reports a branch naming conflict, approve whatever flat name they propose rather than requiring a specific format.

## Context Pack

Write `global-context.md` with only stable global information:

- project objective;
- user priorities;
- source links and source-file paths;
- conventions, commands, and constraints;
- shared architecture decisions;
- cross-task risks.

Do not put every source document into every worker prompt. Link to source files and include excerpts that matter for the task.
