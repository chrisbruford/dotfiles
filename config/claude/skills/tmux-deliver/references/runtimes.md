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
      -c 'projects."<worktree>".trust_level="trusted"' \
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
      -c 'projects."<worktree>".trust_level="trusted"' \
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
  - **Codex** — `-c 'projects."<worktree>".trust_level="trusted"'` on the command
    line. No config file is modified.
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
stay alive across rounds so they can judge the delta; `accept` closes all three.
