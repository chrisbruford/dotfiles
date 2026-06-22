# Status Protocol

The supervisor and workers share a state directory, normally `.tmux-orchestration`.

## Files

- `meta.json`: session, supervisor target, root path, concurrency limit, and script path.
- `tasks/<task-id>.json`: persisted task state.
- `prompts/<task-id>.md`: exact prompt sent to the worker.
- `briefs/<task-id>.md`: supervisor-authored assignment brief.
- `summaries/<task-id>.md`: worker-authored final summary.
- `messages/*.md`: prompts and reports pasted through tmux buffers.
- `events.jsonl`: append-only event log.

## Status Values

- `queued`: task has been recorded but not launched.
- `launched`: tmux window was created and Claude prompt was sent.
- `running`: worker has acknowledged the task and is actively working.
- `blocked`: worker cannot continue without supervisor/user/external input.
- `failed`: worker attempted the task and could not complete it.
- `done`: worker believes the task is complete and has written a summary.
- `cancelled`: supervisor deliberately stopped or superseded the task.

## Worker Report Expectations

Workers should report:

- after reading the assignment and establishing their plan;
- before waiting on a blocker;
- after major verification milestones if the task is long-running;
- when done, with a summary file and verification evidence;
- when failed, with the exact reason and the last useful state.

## Recovery

On resume, run `recover` and read `status`. Treat `done` as "ready for supervisor review", not automatically accepted. Inspect worker summaries, diffs, and verification evidence before updating the global plan.
