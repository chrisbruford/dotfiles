# Runtimes, models, effort

## Argument parsing

```
/tmux-deliver <plan path> [--codex|--claude] [--model <alias>] [--effort <level>] [--concurrency N]
```

Examples:

```
/tmux-deliver docs/plan.md --codex
/tmux-deliver docs/plan.md --codex --model terra --effort xhigh
/tmux-deliver docs/plan.md --claude
/tmux-deliver docs/plan.md --claude --model sonnet --effort high --concurrency 2
```

The runtime flag selects the CLI for **delivery agents only**. Reviewers are always
Codex on `gpt-5.6-sol` at `xhigh` — that is policy, not a default, and there is no
override flag. `start-agent --role qa --model ...` is rejected by the CLI.

## Resolution table

| Runtime | Model aliases | Resolved to | Default model | Default effort |
|---|---|---|---|---|
| `codex` | `luna`, `sol`, `terra` | `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra` | `luna` | `max` |
| `codex` | `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini` | as given | — | — |
| `claude` | `opus`, `sonnet`, `haiku`, `fable`, or any `claude-*` | as given | `opus` | `medium` |
| reviewers | fixed | `gpt-5.6-sol` | — | `xhigh` |

Effort levels: `low`, `medium`, `high`, `xhigh`, `max`. `ultra` exists **only** on
`gpt-5.6-sol`. Cross-family models are a hard error — `--claude --model luna` and
`--codex --model opus` both fail fast rather than silently falling back.

## Exact launch commands

Codex delivery agent (default luna/max):

```bash
codex --model gpt-5.6-luna \
      -c 'model_reasoning_effort="max"' \
      -c 'projects.<worktree>.trust_level="trusted"' \
      --dangerously-bypass-approvals-and-sandbox \
      --no-alt-screen
```

Claude delivery agent (default opus/medium):

```bash
claude --model opus --effort medium --dangerously-skip-permissions
```

Reviewer (both roles, both runtimes):

```bash
codex --model gpt-5.6-sol \
      -c 'model_reasoning_effort="xhigh"' \
      -c 'projects.<worktree>.trust_level="trusted"' \
      --dangerously-bypass-approvals-and-sandbox \
      --no-alt-screen
```

Notes on why each flag is there:

- **Codex has no `--effort` flag.** Reasoning effort is a config override:
  `-c model_reasoning_effort="<level>"`. The value is parsed as TOML, so it must be
  quoted — bare `max` is not valid TOML and only works by literal-string fallback.
- **`--no-alt-screen`** keeps the Codex TUI inline so `tmux capture-pane -p`
  returns useful scrollback when a pane needs debugging.
- **Approvals are fully bypassed** in every pane. These processes are unattended;
  a permission prompt is a deadlock. The containment boundary is the per-unit git
  worktree, not the sandbox.
- **The directory-trust dialog is a separate problem, and it bites.** A unit
  worktree is always a brand-new directory, so both CLIs open a "do you trust the
  contents of this directory?" dialog on startup. **Neither
  `--dangerously-bypass-approvals-and-sandbox` nor `--dangerously-skip-permissions`
  suppresses it** (verified), and the dialog *swallows the pasted prompt* — the
  pane sits forever at "Press enter to continue" and the unit never reaches
  `running`. The launcher pre-empts it per runtime:
  - **Codex** — `-c projects.<worktree>.trust_level="trusted"` on the command
    line. No config file is modified. **The path must not be quoted.** Codex
    splits a `-c` key on `.` and does not honour a quoted key segment: on
    codex-cli 0.147.0, `projects."<path>".trust_level="trusted"` is silently
    ignored and the dialog still appears, while the unquoted form works
    (A/B verified on two fresh directories). The consequence of getting this
    wrong is not a visible error — it is a dialog that eats the first ~1.5 KB of
    the pasted prompt and then answers itself when the launcher presses Enter,
    leaving a truncated brief sitting unsubmitted in the composer.
    A worktree path that itself contains a `.` cannot be expressed as a `-c` key
    at all; `start-agent` says so in `workspace_trust=` and the readiness check
    reports the dialog rather than pasting into it.
  - **Claude** — Claude Code has **two** such dialogs, both persisted in
    `~/.claude.json`, and they are handled differently on purpose:
    - `projects."<worktree>".hasTrustDialogAccepted` — per-directory trust.
      `start-agent` seeds this (atomic read-modify-write). A freshly created
      worktree can never already be trusted, so there is nothing to preserve.
    - `bypassPermissionsModeAccepted` — a **global, permanent** acknowledgement of
      bypass-permissions mode. `start-agent` **never sets this**. It is a standing
      change to the user's own Claude Code behaviour everywhere, which a delivery
      run has no business making. Instead it is treated as a **machine
      precondition**: if the key is not already `true`, `start-agent` exits with an
      error explaining how to accept the dialog by hand, before creating any tmux
      window. `--codex` has no equivalent requirement.

  `start-agent` prints `workspace_trust=` (`seeded worktree trust`,
  `worktree already trusted`, or `codex: handled by -c projects override`) so the
  effect is visible. If you ever launch a window by hand, do the same, or the pane
  will hang forever on a dialog having silently eaten the pasted prompt.
- **`claude --effort`** accepts the same `low|medium|high|xhigh|max` vocabulary, so
  the user-facing `--effort` flag means the same thing in both runtimes.

## Launch readiness — what "the composer is up" looks like

`start-agent` does not sleep and hope. It polls `tmux capture-pane -p` until the
runtime's composer is on screen, and refuses to paste anything until it is.
`--startup-wait` is now a floor before polling starts, not the whole story;
`--ready-timeout` (default 90s) bounds the wait.

The signatures live in `PANE_READY_PATTERNS` / `PANE_BLOCKED_PATTERNS` in
`scripts/tmux_deliver.py`. As observed on codex-cli 0.147.0 and Claude Code
2.1.228:

| Runtime | Composer is up | A dialog is eating input |
|---|---|---|
| codex | a line starting `›`, or the `⏎ send` hint | `Do you trust the contents of this directory`, `Press enter to continue` |
| claude | `bypass permissions on`, `? for shortcuts`, or a line starting `❯` | `Is this a project you created or one you trust`, `Yes, I trust this folder`, `Enter to confirm · Esc to cancel` |

The blocked patterns are checked **first** and deliberately so: both CLIs render
dialog options with the same glyph as their composer (`› 1. Yes, continue`,
`❯ 1. Yes, I trust this folder`), so a naive composer check reads a trust dialog
as a ready prompt and pastes the brief straight into it.

There is no "the pane went quiet, it must be ready" fallback, and there must not
be one: a pane that has gone quiet because the CLI failed to start is a **shell
prompt**, and pasting a prompt into a shell runs it as commands. If a CLI changes
its TUI, the launch fails loudly with the pane tail attached; fix it by adding the
new signature above, or pass `--ready-pattern '<regex>'` for a one-off.

## Getting a prompt into a pane intact

**`paste-buffer` must be given `-p`.** This is the single most important line in
this file. Without `-p`, tmux replays the buffer as ordinary key input and
translates newlines to carriage returns — Enter presses — leaving the receiving
TUI to work out from *timing alone* whether it is being pasted into or typed at.
When that guess goes wrong the prompt is silently mutilated: content is lost, the
composer ends up holding several partial `[Pasted Content N chars]` blobs with
the middle of the prompt spliced in as literal typed text, and the trailing Enter
submits nothing. Measured on codex-cli 0.147.0, without `-p`:

| Delivery | Sent | Arrived |
|---|---|---|
| one `paste-buffer` | 15,869 | 13,312 across two blobs — 2,557 lost from the middle |
| 1,200-char chunks | 15,869 | 12,802 |
| 400-char chunks | 15,869 | complete (small enough to read as typing) |

With `-p`, tmux wraps the payload in bracketed-paste control codes, the TUI knows
exactly where the paste begins and ends, and the guessing stops. Verified
lossless in a **single shot** at 15,870 / 27,369 / 64,809 / 136,810 characters —
one blob, exact character count, every time. Claude Code behaves the same.

tmux only emits the control codes if the application asked for bracketed paste
mode, so `-p` is safe to pass unconditionally — but the launcher never assumes it
took effect. It verifies, and falls back to unbracketed 600-char chunks if the
payload cannot be accounted for.

`--prompt-mode auto` (the default) then means:

- **pointer** (anything over `--inline-max-chars`, default 1500): **one line**,
  typed with `send-keys -l`, telling the agent to read
  `prompts/<unit>-r<N>-<role>.md` in full. No buffer, no newlines, no paste
  heuristic, nothing for a composer to misread — the most robust option there is,
  and it makes prompt size irrelevant.
- **inline** (small messages, or `--prompt-mode inline`): the text itself via
  `paste-buffer -p`, in one shot. `--paste-chunk-chars` forces chunking;
  `--no-bracketed-paste` forces the old unbracketed behaviour, which then chunks
  at 600 by default because that is what survives it.

Either way the launcher waits until the payload is fully accounted for in the
pane — the tail visible, or the TUI's paste marker matching the payload size —
**before** pressing Enter, and refuses to submit if it never gets there. An
unsubmitted prompt is a nuisance; a silently truncated one is a delivery agent
working from a brief with no acceptance criteria and no "do NOT touch" list.

### Recovering a mangled composer

A composer that has mis-parsed input **cannot be cleared** — neither `C-u` nor
`C-c` does it. The only reliable recovery is to restart the process in place:

```bash
tmux respawn-pane -k -t <pane> '<launch command>'
```

The pane id survives, so recorded unit state stays valid. `start-agent` does this
automatically between delivery attempts, where it costs nothing because the CLI
has only just started. **`send` never does it**: that pane holds a live agent, and
restarting it throws away the round-N context that makes its next answer worth
anything. A `send` that cannot be verified fails loudly and leaves the decision
to you.

## Watching a run you are not attached to

With `--session`, agent windows land in a session the user is not attached to, so
they are invisible to them. Doing it by hand, per window, does not survive
contact: it was forgotten for two of the agents in a real run, and the user
noticed the gap before the orchestrator did. So it is automated.

```bash
... init --session tmux-deliver-payments --mirror-session auto      # your own session
... init --session tmux-deliver-payments --mirror-session my-work   # a named one
```

`mirror_session` is recorded in `meta.json`, and every window `start-agent`
creates is linked into it as it is created — `start-agent` prints
`mirror=linked into <session>:<idx>`. Windows are appended at the end of the
mirror session, so the user's own windows keep their indices.

For a run already in flight, or to change or remove the mirror:

```bash
... mirror --session my-work    # link every live agent window, and record the session
... mirror                      # re-link anything missing from the recorded mirror
... mirror --unlink             # drop the mirrored view only; the run keeps its windows
```

Linking is non-destructive — it is the same window in two places, not a copy. That
is also the sharp edge: **`accept` closes the unit's three windows, so they
disappear from the mirrored session too.** The user's view thins out as units are
accepted, with no explanation unless you give one. `accept` unlinks before it
kills and says how many windows the mirror lost; pass that on.

`cleanup` behaves the same way for every remaining unit.

The manual equivalents, if you ever need them:

```bash
tmux link-window -d -s <window-id> -t <user-session>:<idx>   # show it
tmux unlink-window -t <user-session>:<idx>                   # put it back
```

## Reading a pane: what is evidence and what is not

The liveness check in `status` / `watch` (see `state-protocol.md`) rests on two
signals, and deliberately excludes a third:

| Signal | Verdict |
|---|---|
| busy indicator (`esc to interrupt`) | **evidence** — the CLI is working |
| output above the composer changing | **evidence** — the agent produced something |
| composer line contents | **never evidence** |

Both CLIs render rotating placeholder hints in the composer (`Explain this
codebase`, `Improve documentation in @filename`), so an abandoned pane can look
busy to anything that reads that line. The footer beneath it mutates on its own
too: measured on Claude Code 2.1.229, it flips between `⏸ manual mode on · ? for
shortcuts · ← for agents` and `⏸ manual mode on` with no agent involved.

`pane_fingerprint()` therefore cuts the pane at the composer and hashes only what
is above it, minus spinner, timer and token-counter lines. Verified against a live
Claude Code pane: typing a full placeholder-style hint into the composer leaves
the fingerprint byte-identical. If a CLI moves its composer, update
`PANE_COMPOSER_PATTERNS` — the failure mode is an idle agent that never gets
flagged, which is silent.

## Reviewer personas

Reviewer windows are plain Codex sessions, so they do not inherit the configured
subagent personas automatically. Their prompt points them at the existing Codex
agent definitions and tells them to adopt the `developer_instructions` within:

- QA → `~/.codex/agents/qa-code-reviewer.toml`
- Adversarial → `~/.codex/agents/adversarial-reviewer.toml`

If either file is missing, the reviewer will say so — install or restore it rather
than letting a reviewer run with a generic persona, which is what `deliver` warns
against when it says not to silently substitute a general agent.

## Window layout

One tmux session holds every agent window. Per unit, three windows:

```
<unit-id>-implementer     codex|claude, chosen model/effort
<unit-id>-qa              codex gpt-5.6-sol / xhigh
<unit-id>-adversarial     codex gpt-5.6-sol / xhigh
```

At the default concurrency of 3 units that is 9 windows at peak. Reviewer windows
stay alive across rounds so they can judge the delta; `accept` closes all three
(and unlinks them from the mirror session first).
