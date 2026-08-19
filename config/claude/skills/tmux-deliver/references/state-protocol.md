# State protocol

The orchestrator and all agents share a state directory, by default
`.tmux-deliver/` at the repo root. Agents never send text into the orchestrator's
window — they write state files, and the orchestrator learns about changes from
`watch`.

## Layout

```
.tmux-deliver/
  meta.json                              session, runtime/model/effort, integration branch,
                                         concurrency, max-rounds, verification commands
  units/<unit-id>.json                   per-unit state incl. per-role window/pane/status
  briefs/<unit-id>.md                    orchestrator-authored unit brief
  prompts/<unit>-r<N>-<role>.md          exact prompt pasted into each window
  deliveries/<unit>-r<N>.md              implementer summary + red→green evidence
  reviews/<unit>-r<N>-qa.md              QA review
  reviews/<unit>-r<N>-adversarial.md     adversarial review
  messages/*.md                          everything pasted through a tmux buffer
  events.jsonl                           append-only event log
```

## Unit statuses

| Status | Meaning |
|---|---|
| `queued` | worktree and branch prepared, no window launched |
| `launched` | a window exists, agent has not acknowledged yet |
| `delivering` | implementer is working |
| `delivered` | implementer reported done; awaiting review |
| `reviewing` | one or both reviewers running |
| `reviewed` | both reviewers reported done; awaiting orchestrator judgement |
| `changes-requested` | orchestrator rejected the round; round counter bumped |
| `blocked` | an agent needs input, or an acceptance merge conflicted |
| `failed` | an agent could not complete and has no useful next action |
| `escalated` | max rounds (3) exhausted — stop and go to the user |
| `accepted` | merged into the integration branch; windows closed, worktree removed |
| `cancelled` | deliberately dropped |

`delivered` and `reviewed` mean **awaiting your judgement** — never treat either as
accepted. Only `accept` produces `accepted`, and only after you have read the diff
yourself and run the verification commands.

## Role statuses

Each unit tracks `implementer`, `qa`, `adversarial` independently:
`launched` → `running` → `done` (or `blocked` / `failed`). Reviewers additionally
carry a `verdict` of `pass` | `concerns` | `block`.

## Watching

```bash
python3 ~/.claude/skills/tmux-deliver/scripts/tmux_deliver.py watch --state-dir .tmux-deliver
```

Run this under the **Monitor** tool. Each emitted line is a change:

```
unit=retry-policy status=reviewed round=1 implementer=done qa=done/concerns adversarial=done/block title=... message=...
```

Trust these events. Agents submit their own prompts and clear their own permission
prompts, so a unit that has not reached `running` within a few minutes is the only
reason to open a pane:

```bash
tmux capture-pane -p -t <pane-id> | tail -40
```

## Git model

```
user's branch              (never moves, never checked out by us)
  └─ tmux-deliver/<slug>   integration branch, in its own worktree
       ├─ tmux-deliver/<slug>-<unit-a>    unit worktree
       ├─ tmux-deliver/<slug>-<unit-b>    unit worktree (forked after unit-a merged)
       └─ ...
```

`prepare-unit` forks each unit from the **current** integration branch tip, so a
unit prepared after its dependency was accepted inherits that work. `accept` merges
back with `--no-ff`, so every unit is one identifiable merge commit.

Acceptance requires a **clean** unit worktree — the implementer commits its own
work. `accept` refuses on uncommitted changes rather than merging a partial unit.

## Recovery

```bash
python3 ~/.claude/skills/tmux-deliver/scripts/tmux_deliver.py recover --state-dir .tmux-deliver
```

Reports the session, the attach command, the integration branch, which panes are
still `alive` vs `DEAD`, and the full status table. For a dead pane, relaunch that
role with `start-agent --relaunch` (a fresh process, so it loses prior-round
context — re-brief it with the accumulated feedback in the new prompt).

## Known tmux pitfalls this CLI handles

- **Un-submitted prompts.** Large prompts are pasted via `load-buffer`/`paste-buffer`,
  then Enter is sent after a settle delay (`--submit-delay`, default 2s). Never send
  Enter yourself in the same shell pipeline as a launch.
- **Bare-numeric session names.** `new-window -t 1` resolves `1` as a *window index*,
  which breaks the second and later windows. `init` renames a numeric session first.
- **Ambiguous session targeting.** All session lookups use exact-match `=<session>`.
- **Stray keystrokes.** Only `send` writes to panes, and it always targets one pane
  by id. Broadcasting Enter can re-trigger a finished agent into duplicate work.
- **Worktree/task-record ordering.** `prepare-unit` creates both the worktree and
  the unit record in one step, so `start-agent` never collides with a pre-created
  record.
- **Directory-trust dialog.** A fresh worktree triggers a trust dialog in both CLIs
  that the dangerous-bypass flags do *not* suppress, and it swallows the pasted
  prompt. `start-agent` pre-trusts the path per runtime and reports
  `workspace_trust=`. See `runtimes.md`.
- **Git ref namespace collision.** Unit branches are flat siblings of the
  integration branch (`tmux-deliver/<slug>-<unit>`), never nested beneath it — a
  branch named `tmux-deliver/<slug>` makes `tmux-deliver/<slug>/<unit>` an illegal
  ref, because git refs are filesystem paths.
