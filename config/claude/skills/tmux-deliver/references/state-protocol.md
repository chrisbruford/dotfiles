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
  prompts/<unit>-r<N>-<role>.md          the agent's prompt; by default the pane gets a
                                         pointer to this file, not the text itself
  deliveries/<unit>-r<N>.md              implementer summary + red→green evidence
  reviews/<unit>-r<N>-qa.md              QA review
  reviews/<unit>-r<N>-adversarial.md     adversarial review
  messages/*.md                          everything pasted through a tmux buffer
  liveness.json                          per-pane fingerprint + when it last changed,
                                         so "idle for 14m" can be said at all
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

| Status | Written by | Meaning |
|---|---|---|
| `launched` | launcher | a window exists and the prompt was submitted; no agent has acknowledged |
| `notified` | orchestrator | a message was dispatched to this role for the **current** round; not yet acknowledged |
| `running` | **the agent** | the agent has read its prompt and is working |
| `done` | the agent | delivered / reviewed; reviewers also carry a `verdict` |
| `blocked` | the agent | needs orchestrator input |
| `failed` | the agent | cannot proceed |
| `stale` | orchestrator | **the round moved on and nothing has been sent to this role since** |

Only `running`, `done`, `blocked` and `failed` may be reported by an agent;
`report` rejects the other two outright. `stale` and `notified` are new — a
consumer that does not know them should treat `stale` as "not working, owed a
dispatch" and `notified` as "launched-equivalent".

**`launched` is not evidence of anything.** The launcher writes it; no agent has
acknowledged anything at that point. Only `running` is a report from the agent
itself. A role sitting at `launched` is a failed launch, not a slow agent —
`start-agent` blocks for `--wait-running` seconds (default 90) and exits non-zero
if the report never comes, and `await-running` waits longer when an agent is
legitimately slow to boot.

### `stale`, and why `next-round` no longer writes `running`

`next-round` used to reset every finished role to `running`. That status was a
lie in the one case that matters: nothing had been sent to those roles, so unless
the orchestrator remembered to `send` each of them the delta, the reviewers sat
idle **forever** while the table reported them working. It is a design defect, not
a lapse — it was hit twice in one session, on consecutive rounds, by an
orchestrator that had already recognised the failure once.

Now:

- `next-round` sets finished roles to **`stale`**, which is visibly not `running`.
- `--feedback-file` makes `next-round` do the whole transition: it dispatches the
  change request to the implementer (clearing its `stale`) and shows both
  reviewers a round-opened notice. Reviewers **stay `stale`**, because the delta
  they must actually review does not exist yet — they are still owed a dispatch.
- `re-review` is that dispatch. It sends both reviewer windows the delta and
  clears their `stale`.
- Any `send --unit … --role …` also clears it, and records `dispatched_round` /
  `dispatched_at` on the role.
- With no `--feedback-file`, `next-round` still bumps the round (exit 0, as
  before) but prints a loud warning naming every role it left un-dispatched.

The invariant: **a role is never recorded `running` unless the agent itself said
so, and a role that is owed a dispatch says `stale` until it gets one.**
`dispatched_round` behind the unit's `round` is the machine-checkable form of the
same fact, and `status` reports it.

A purely informational message (the round-opened notice, a scope amendment) is
recorded as `notice_round` and deliberately does **not** clear `stale` — telling
an agent something is not the same as giving it work.

## Liveness — recorded status vs what the pane is doing

`status` samples every recorded pane and prints a `LIVENESS` block: alive/dead,
busy/idle, and time since the pane last changed. Where the two disagree it says
so, at the top and again in the block:

```
!! 2 role(s) recorded as having work in front of them that their panes contradict

LIVENESS (pane truth, idle threshold 2m)
  greet/implementer  pane=%62 recorded=running  pane_state=idle 14m
      !! STALLED — recorded 'running' but the pane has shown no output and no busy
         indicator for 14m. Nothing has been sent to it since round 1 (the unit is on round 2).
  greet/qa           pane=%25 recorded=stale    pane_state=DEAD
      !! DEAD PANE — recorded 'stale' and the pane is gone. No dispatch can land until
         you relaunch with `start-agent --relaunch`.
```

- **Dead** comes first, because "does this pane still exist" precedes every other
  question. A recorded pane id tmux can no longer resolve prints as `DEAD` and is
  always flagged, whatever the recorded status — including `done` and `stale`.
  Distinct from `no-window`, which means no pane was ever recorded for the role.
- **Busy** is the CLI's own busy indicator (`esc to interrupt`) — the same signal
  the launcher uses to confirm a submit was taken. One definition, used everywhere.
- **Idle** is time since the pane's fingerprint last changed. The fingerprint
  **drops the composer line entirely**, because both CLIs rotate placeholder hints
  there (`Explain this codebase`, `Improve documentation in @filename`) with no
  agent involved. Composer text is never evidence. Spinners, elapsed timers and
  token counters are dropped for the same reason; scrollback size is folded in so
  output that has scrolled away still counts.
- Idle time is bounded by how long we have been sampling — it lives in
  `liveness.json` and starts at the first observation, so it can only under-report,
  never over-report. `watch` samples every `--liveness-interval` (default 15s), so
  a run with the watcher up accrues real history. A pane that has died is
  **pruned** from `liveness.json` rather than refreshed, so its last-known
  `changed_at` can never resurface as recent activity.
- `--idle-threshold` (default 120s) is how long a pane may be quiet, **while
  showing no busy indicator**, before an active-looking role is called out. A
  thinking agent shows the indicator, so it never trips.
- `--no-liveness` skips sampling; the table then says so rather than implying it
  checked.

`watch` emits `ALERT` on entry into a flagged state — dead pane as well as stall —
and `CLEARED` on exit, one event per transition, not per poll, plus an `ACTION`
line whenever a unit has `stale` roles. Under `--no-liveness` it says so on the
first line instead of quietly reporting nothing.

## Proof of receipt

Every prompt ends with `TMUX-DELIVER-RECEIPT: <token>`, recorded as
`roles.<role>.receipt` in the unit file. The agent's **first** report must quote
it back with `--receipt`, which it can only do if the prompt reached it whole:

| `report --receipt` | `receipt_verified` | Meaning |
|---|---|---|
| matches | `true` | the agent has the entire prompt, tail included |
| differs | `false` | `report` exits 10 and tells the agent to stop — treat the prompt as truncated |
| omitted | `null` | no proof either way; ask the agent to quote the final line before trusting it |

This is the only check that covers the whole path — paste, submit, and the
agent's own reading of the file — and it is what catches a brief truncated in its
final third, where the acceptance criteria and the "do NOT touch" list live.
`recover` prints the receipt state per role.

## Exit codes on the launch path

| Code | Meaning |
|---|---|
| 6 | the CLI never reached its composer, or a startup dialog is eating input — nothing was pasted |
| 7 | the paste did not land in full — **Enter was not pressed**, so nothing ran against a truncated prompt |
| 8 | Enter did not submit; the text may still be sitting in the composer |
| 9 | the agent never self-reported within `--wait-running` / `await-running --timeout`, or `re-review` found a reviewer with no live window (the others were still dispatched) |
| 10 | an agent quoted a receipt token that is not its launch's |

Codes 2 (`next-round` escalation) and 3 (`verify-readonly` side effects) are
unchanged.

## Watching

```bash
python3 ~/.claude/skills/tmux-deliver/scripts/tmux_deliver.py watch --state-dir .tmux-deliver
```

Run this under the **Monitor** tool. Each emitted line is a change:

```
unit=retry-policy status=reviewed round=1 implementer=done qa=done/concerns adversarial=done/block title=... message=...
```

plus, from the liveness sampler:

```
  ACTION unit=retry-policy awaiting_dispatch=qa,adversarial — round 2 was opened but nothing
         has been sent to these roles; they will sit idle until you `send` or `re-review`
ALERT unit=retry-policy role=qa recorded=running pane=%13 alive=True busy=False idle=5m ::
      STALLED — recorded 'running' but the pane has shown no output and no busy indicator for 5m.
CLEARED unit=retry-policy role=qa recorded=running pane=%13 — the pane and the recorded status agree again
```

Trust these events — but only as far as they go. The watcher reports what agents
report; it cannot report a launch that never delivered a prompt, because a unit
stuck at `launched` emits nothing at all. That gap is `start-agent`'s job, not the
watcher's. Open a pane whenever `start-agent` exits non-zero, a unit reports
`blocked`, a role sits at `launched`, or an `ALERT` fires:

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

A dead pane counts as a contradiction, so `recover` will not print
`no contradictions` while any role's recorded pane is missing — which is the
signature of the interruption it exists to report.

## Known tmux pitfalls this CLI handles

- **`paste-buffer` without `-p`.** The defect behind the whole class. Unbracketed,
  tmux replays the buffer as keystrokes with newlines as Enter, the TUI has to
  guess whether it is a paste, and when it guesses wrong the prompt is truncated
  and spliced with literal typed text (measured: 15,869 in, 13,312 arrived). With
  `-p` the same payload arrives whole in one shot, up to at least 136 KB. Large
  payloads are delivered as a one-line **pointer** to the prompt file by default,
  which sidesteps pasting entirely. See `runtimes.md`.
- **`display-message` does not fail on a dead pane.** A tmux server restart takes
  every agent window with it while the unit files still name the old pane ids, so
  "does this pane exist" has to be answered correctly. `display-message -p -t %24
  '#{pane_id}'` exits **0** on a pane that is gone (measured on tmux 3.4; `-t %24
  'X#{pane_id}X'` prints `XX`) — it expands the format against nothing. Trusting
  that made every pane lost to a restart report `alive`, `idle 0s`, and the hash
  of an empty capture, under the line `no contradictions`. `list-panes -t %24`
  exits **1** with `can't find pane: %24`, so liveness probes with that.
- **Un-submitted prompts.** Enter is sent only once the payload is fully accounted
  for in the pane, and the launcher then confirms the CLI actually took the message
  (a busy indicator, or at minimum a redraw), retries Enter once, and fails with
  exit 8 if it still cannot tell. Never send Enter yourself in the same shell
  pipeline as a launch.
- **Composers cannot be un-mangled.** `C-u` and `C-c` do not clear a composer that
  has mis-parsed input. `tmux respawn-pane -k -t <pane> '<launch cmd>'` restarts
  the process and keeps the pane id, so unit state stays valid. `start-agent` uses
  it between delivery attempts; `send` never does, because that pane holds a live
  agent's context.
- **Pasting before the CLI exists.** `start-agent` polls for the composer instead
  of sleeping a fixed `--startup-wait`. Anything pasted while a CLI is still
  booting, or while a first-run dialog is up, is silently swallowed.
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
  `workspace_trust=`; if a dialog appears anyway, the readiness check names it and
  exits 6 without pasting. The Codex `-c` key must not quote the path or the
  pre-trust silently does nothing. See `runtimes.md`.
- **Git ref namespace collision.** Unit branches are flat siblings of the
  integration branch (`tmux-deliver/<slug>-<unit>`), never nested beneath it — a
  branch named `tmux-deliver/<slug>` makes `tmux-deliver/<slug>/<unit>` an illegal
  ref, because git refs are filesystem paths.
