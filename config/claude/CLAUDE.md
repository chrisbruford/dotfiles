## Tools

### md-to-pdf
`md-to-pdf` is globally available (via `npm link` from `~/repos/md-to-pdf`).
Use it whenever asked to convert a Markdown file to PDF — especially when the document contains Mermaid diagrams.
Do not use it unless explicitely asked to convert to PDF. I do not want PDFs by default.

```sh
md-to-pdf <input.md>                          # PDF saved alongside input file
md-to-pdf <input.md> -o <output.pdf>          # explicit output path
md-to-pdf <input.md> --theme forest           # Mermaid theme: default|forest|dark|neutral
md-to-pdf <input.md> --page-size Letter       # A4 (default) or Letter
md-to-pdf <input.md> --title "My Doc"         # custom header title
md-to-pdf <input.md> --no-footer              # omit page numbers
```

Requires system Chrome (auto-detected). Uses `puppeteer-core` + Mermaid.js v10 CDN.

---

## Deployment & Secrets

When deploying via 1Password, use the exact secret/item names the user provides and do not re-check sign-in status or add `--yes` flags to coder commands.

---

## Git Workflow

Before any destructive git operation (rebuild, force-push, rebase), always fetch from remote first and confirm the branch base.

---

## PR & Testing

For PR-merge-ready and deployment tasks, run TDD: add a regression test for every bug fix, verify CI is green before reporting done, and clearly flag the remaining human-approval blocker.

---

## TODOs

- At the start of each session, read the files in ~/.claude/todos/ and display active tasks (in-progress and todo) for this project.
- When completing work that relates to a TODO, suggest updating its status with /todo-done.
