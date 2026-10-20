# dotfiles

Personal shell and AI-tooling configuration for Coder engineer workspaces (Linux) and local macOS machines.

`install.sh` is idempotent: it runs on every Coder workspace start and is safe to re-run by hand to pick up changes.

## Install

```sh
git clone git@github.com:chrisbruford/dotfiles.git ~/repos/dotfiles
~/repos/dotfiles/install.sh
```

In Coder, point the workspace's dotfiles URI at this repo and it runs automatically.

### Requirements

- `git`, `curl`, `sudo`
- Linux: `apt-get` (Ubuntu/Debian)
- macOS: [Homebrew](https://brew.sh)
- `python3` and `rsync` for the Claude Code / Codex steps
- Optional: `claude` and `codex` CLIs — their setup is skipped if not on `PATH`

## What it does

| Step | Script | Result |
| --- | --- | --- |
| Git identity & defaults | `install.sh` | Global `user.name`/`email`, `main` default branch, `push.autoSetupRemote` |
| Zsh | `scripts/setup-zsh.sh` | Installs zsh, tmux, oh-my-zsh, powerlevel10k, zsh-autosuggestions; sets zsh as login shell |
| Shell config | `install.sh` | Copies `config/.zshrc`, `config/.zsh_aliases`, `.p10k.zsh` to `$HOME`; adds bash fallback aliases and an `exec zsh` hook to `~/.bashrc` |
| Vim | `install.sh` | Writes a minimal `~/.vimrc` |
| Claude Code | `scripts/setup-claude.sh` | Syncs `config/claude/` into `~/.claude/` |
| Claude plugins | `scripts/setup-claude-plugins.sh` | Adds marketplaces and installs plugins listed in `config/claude/plugins.json` |
| Codex CLI | `scripts/setup-codex.sh` | Syncs `config/codex/` into `~/.codex/` |

> Files copied into `$HOME` (`.zshrc`, `.zsh_aliases`, `.p10k.zsh`, `.vimrc`, `~/.claude/CLAUDE.md`, agents, skills) are **overwritten** on each run. Make changes here, not in the live copies.

## Layout

```
install.sh                  Entry point
.p10k.zsh                   Powerlevel10k prompt config
config/
  .zshrc, .zsh_aliases      Zsh config
  claude/                   Claude Code user config (~/.claude)
    CLAUDE.md               Global instructions
    settings.json           Settings template (__HOME__ is substituted at install)
    plugins.json            Plugin marketplaces + plugins to install
    agents/                 Subagent definitions
    skills/                 Skills (deliver, tmux-*, pr-merge-ready, tracey, …)
    hooks/langfuse_hook.py  Stop hook for Langfuse tracing
    statusline.sh           Status line script
    statusline-pricing.json Seed pricing table for the status line
  codex/                    Codex CLI user config (~/.codex)
scripts/
  setup-*.sh                Per-tool setup steps
  merge-claude-settings.py  Merges the settings template into the live settings.json
tests/                      Regression tests for the Claude config
```

## Claude Code settings merge

Claude Code rewrites `~/.claude/settings.json` at runtime, so the template is merged rather than copied (`scripts/merge-claude-settings.py`):

- **Enforced/unioned every run:** `env`, `permissions`, `hooks`, `statusLine`, `enabledPlugins`, `extraKnownMarketplaces`, `$schema`
- **Seeded once, then left alone:** everything else (e.g. `model`, `effortLevel`)
- Hook groups that invoke dotfiles-owned scripts (`langfuse_hook.py`) are replaced, not appended, so command-line changes propagate.

`statusline-pricing.json` is only seeded if missing; refresh it with the `statusline-pricing-refresh` skill.

## Codex

Codex gets its own `AGENTS.md`, agent `.toml` files and Codex-specific skills. Skills shared with Claude Code (`azure-ad-sso`, `liberis-brand`, `pr-merge-ready`, `tracey`) are symlinked from `~/.claude/skills/` rather than duplicated.

## Tests

Stdlib-only and offline:

```sh
python3 -m unittest discover -s tests -v
```

CI (`.github/workflows/check-code.yaml`) runs the tests on Python 3.12 (matching the Coder image) and validates that `settings.json` and `plugins.json` parse.
