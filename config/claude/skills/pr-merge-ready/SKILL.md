---
name: pr-merge-ready
description: >
  Review a GitHub PR with a skeptical eye, address review comments that have merit,
  fix CI failures (reporting on ones that can't be fixed), update the branch if it
  is behind base, and verify the PR is in a mergeable state. Accepts --monitor to
  continuously poll for new failures and comments in the background.
argument-hint: "<pr-url-or-number> [--monitor] [--interval <minutes>m]"
---

# PR Merge Readiness

Bring a pull request to a mergeable state: update the branch, fix CI, address meritorious review comments, and report on anything that needs human intervention.

## Input

Arguments: `$ARGUMENTS`

Parse these arguments:

- **PR reference** (required): a GitHub PR URL (`https://github.com/owner/repo/pull/123`) or a bare PR number (uses the current repo)
- **`--monitor`** flag: after the initial fix pass, set up background polling
- **`--interval <N>m`** flag: polling interval for monitor mode (default `10m`)

If no PR reference is given, try `gh pr view --json url` to get the current branch's PR. If that also fails, ask the user.

Derive `OWNER`, `REPO`, `PR_NUMBER` from the PR reference.

---

## Phase 1 — Gather PR State

Run these in parallel:

```bash
gh pr view "$PR_NUMBER" --repo "$OWNER/$REPO" \
  --json number,title,body,state,mergeable,mergeStateStatus,baseRefName,headRefName,\
author,isDraft,reviewDecision,statusCheckRollup,files,additions,deletions,commits \
  2>&1

gh pr checks "$PR_NUMBER" --repo "$OWNER/$REPO" 2>&1

gh api "repos/$OWNER/$REPO/pulls/$PR_NUMBER/reviews" 2>&1

gh api "repos/$OWNER/$REPO/pulls/$PR_NUMBER/comments" 2>&1

gh api "repos/$OWNER/$REPO/issues/$PR_NUMBER/comments" 2>&1
```

From the results, build a mental model of:

- **Mergeability**: `mergeable` field + `mergeStateStatus` (`CLEAN`, `BEHIND`, `BLOCKED`, `DIRTY`, `UNSTABLE`, `UNKNOWN`)
- **Branch delta**: is head behind base?
- **CI state**: which checks are failing, which are pending, which passed
- **Open review threads**: unresolved comments awaiting a reply or code fix
- **Issue-level comments**: general discussion comments on the PR

---

## Phase 2 — Update Branch if Behind Base

If `mergeStateStatus` is `BEHIND` or the branch is behind its base branch:

1. Check out the PR branch locally:

   ```bash
   gh pr checkout "$PR_NUMBER" --repo "$OWNER/$REPO"
   ```

2. Fetch and merge (prefer merge over rebase to avoid force-push churn; use rebase only if the repo's CLAUDE.md or contributing guide explicitly requires it):

   ```bash
   git fetch origin
   git merge "origin/$(gh pr view $PR_NUMBER --repo $OWNER/$REPO --json baseRefName -q .baseRefName)"
   ```

3. If there are merge conflicts: attempt to resolve them. If you cannot safely resolve a conflict, stop and report it — do not push a broken merge.

4. Push if the merge was clean:
   ```bash
   git push
   ```

Report: "Branch updated — merged N commit(s) from base."

---

## Phase 3 — Address Review Comments

Fetch all review comments and issue-level comments (already done in Phase 1). For each **unresolved thread**:

### Skepticism filter — apply all three tests before acting

A comment has merit if **at least two** of the following are true:

- It identifies a concrete bug, incorrect behaviour, or security risk. Always check for correctness, don't just agree
- It cites a project convention (CLAUDE.md, README, contributing guide) that the code violates
- It asks a clarifying question whose answer would change the implementation

A comment does **not** have merit if it is:

- Purely stylistic with no convention backing
- A drive-by opinion ("I'd prefer…") without a concrete defect
- Already addressed in a later commit on the same thread
- Resolved/outdated (the code it refers to no longer exists in the diff)

### Action for comments that pass the filter

1. Read the referenced file and line(s).
2. Make the smallest change that satisfies the concern.
3. Do not gold-plate: fix only what the comment identifies.
4. Commit the fix with a message like: `address review: <brief description>`.

### Responding to comments you do NOT fix

For comments you judge to lack merit, post a polite, specific rebuttal explaining why no change is needed. Use:

```bash
gh api "repos/$OWNER/$REPO/pulls/$PR_NUMBER/comments/$COMMENT_ID/replies" \
  -X POST -f body="<your reply>"
```

For issue-level comments:

```bash
gh api "repos/$OWNER/$REPO/issues/$PR_NUMBER/comments" \
  -X POST -f body="<your reply>"
```

### Summarise comment work

After processing all threads, report:

- N comments addressed with code changes
- N comments rebutted with explanation
- N comments skipped (already resolved / duplicate)

---

## Phase 4 — Fix CI Failures

Re-fetch the check status after any branch updates:

```bash
gh pr checks "$PR_NUMBER" --repo "$OWNER/$REPO" 2>&1
```

For each **failing** check:

### Step A — Read the failure log

```bash
gh run view <run-id> --log-failed 2>&1
```

If the run ID is not directly available, find it:

```bash
gh api "repos/$OWNER/$REPO/commits/$(git rev-parse HEAD)/check-runs" \
  --jq '.check_runs[] | select(.conclusion=="failure") | {id:.id,name:.name,html_url:.html_url}'
```

### Step B — Categorise the failure

| Category                                                                | Can fix?    | Action                                      |
| ----------------------------------------------------------------------- | ----------- | ------------------------------------------- |
| Test failure — test body points to a real bug in changed code           | Yes         | Fix the production code                     |
| Lint / format error                                                     | Yes         | Run the formatter/linter and commit the fix |
| Type error                                                              | Yes         | Fix the type issue                          |
| Test failure — test itself is wrong after a deliberate behaviour change | Yes         | Update the test                             |
| Build error in changed files                                            | Yes         | Fix the compilation error                   |
| Flaky / infrastructure test (network, timeout, random seed)             | No — report | Note it and suggest a re-run                |
| Failure in unchanged legacy code unrelated to this PR                   | No — report | Note it; do not touch unrelated code        |
| Missing secret / environment variable                                   | No — report | Explain what is missing and who can add it  |
| Required status from an external service (e.g. Snyk, CodeCov gate)      | No — report | Explain the gate and how to satisfy it      |

### Step C — Reproduce the failure locally

Before changing any code, reproduce the failing check locally. Mandatory — do not skip.

1. Identify the exact command CI ran (check `.github/workflows/` if the log is ambiguous).
2. Run that command locally, matching CI's working directory, env vars, and arguments:

   ```bash
   # examples — adapt to the actual tech stack and the actual CI invocation
   npm test -- --testPathPattern="failing-file"
   dotnet test --filter "FullyQualifiedName~FailingTestName"
   npm run lint
   pytest path/to/test_file.py::test_name -x
   ```

3. Confirm the local run produces the same failure (same error, same exit code). If it does not:
   - Close the gap (env var, tool version, flags, OS-specific behaviour).
   - If you still cannot reproduce after a reasonable attempt, make a best-effort fix based on the CI log and note in the final report that the fix was not verified locally.

### Step D — Fix, verify locally, then push

1. Make the minimal change.
2. Re-run the exact local command from Step C and confirm it passes. If you reproduced the failure locally, do not push until the local command is green.
3. If the fix could affect more than the failing check (lint autofix reformatting, type signature changes), run the broader local suite (`npm test`, `dotnet test`, `npm run lint`).
4. Commit and push:

   ```bash
   git add -A
   git commit -m "fix ci: <describe what was broken>"
   git push
   ```

If the local command still fails after the fix, stop and report.

### Step E — Wait for re-run

After pushing fixes, optionally wait for CI to re-run:

```bash
gh run watch $(gh run list --limit 1 --json databaseId -q '.[0].databaseId') 2>&1
```

Only wait if the user is in an interactive session and the suite is short (< 3 min). Otherwise report and let CI run asynchronously.

---

## Phase 5 — Final Mergeable State Report

After all phases, report a concise summary:

```
## PR #<number> — <title>

### Status: READY TO MERGE / BLOCKED / NEEDS ATTENTION

**Branch**: up to date with <base> ✓   (or: merged N commits ✓)

**CI**
- ✓ <check-name>
- ✓ <check-name>
- ✗ <check-name> — <cannot-fix reason>   (if any unfixable failures remain)

**Review comments**
- Addressed: N (code changed)
- Rebutted: N (no change needed — explanation posted)
- Remaining: N (not yet addressed — see below)

**Remaining blockers** (if any)
- <description of each thing that needs human action>

**Next step**: <one clear sentence on what to do next>
```

---

## Phase 6 — Monitor Mode (only if `--monitor` flag was passed)

After completing phases 1–5, start a background monitor using the **Monitor tool**.

Determine the polling interval in seconds: default `600` (10 min), or convert the `--interval` argument (e.g. `5m` → `300`).

Write and start the following polling script via the Monitor tool. The script emits a structured line whenever something changes; Claude reacts to each line as it arrives.

```bash
#!/usr/bin/env bash
# pr-monitor: polls PR state and emits change events on stdout
OWNER="<owner>"
REPO="<repo>"
PR="<number>"
INTERVAL=<interval_seconds>

prev_checks=""
prev_comment_count=""
prev_mergeable=""

while true; do
  # PR lifecycle — stop monitoring if PR is closed or merged
  pr_state=$(gh pr view "$PR" --repo "$OWNER/$REPO" --json state -q .state 2>/dev/null)
  if [ "$pr_state" = "MERGED" ]; then
    echo "PR_CLOSED state=MERGED"
    exit 0
  fi
  if [ "$pr_state" = "CLOSED" ]; then
    echo "PR_CLOSED state=CLOSED"
    exit 0
  fi

  # CI status
  checks=$(gh pr checks "$PR" --repo "$OWNER/$REPO" 2>/dev/null | awk '{print $1,$2}' | sort)
  if [ "$checks" != "$prev_checks" ]; then
    failing=$(gh pr checks "$PR" --repo "$OWNER/$REPO" 2>/dev/null | grep -c "fail" || true)
    echo "CI_CHANGED failing=$failing"
    prev_checks="$checks"
  fi

  # Review comment count (new threads)
  comment_count=$(gh api "repos/$OWNER/$REPO/pulls/$PR/comments" --jq 'length' 2>/dev/null)
  if [ "$comment_count" != "$prev_comment_count" ] && [ -n "$prev_comment_count" ]; then
    echo "COMMENTS_CHANGED count=$comment_count"
    prev_comment_count="$comment_count"
  fi
  [ -z "$prev_comment_count" ] && prev_comment_count="$comment_count"

  # Branch behind base
  mergeable=$(gh pr view "$PR" --repo "$OWNER/$REPO" --json mergeStateStatus -q .mergeStateStatus 2>/dev/null)
  if [ "$mergeable" != "$prev_mergeable" ] && [ -n "$prev_mergeable" ]; then
    echo "STATE_CHANGED mergeStateStatus=$mergeable"
  fi
  prev_mergeable="$mergeable"

  sleep "$INTERVAL"
done
```

**React to each emitted line:**

| Line prefix                             | Action                                                  |
| --------------------------------------- | ------------------------------------------------------- |
| `CI_CHANGED failing=0`                  | All checks now green — report to user                   |
| `CI_CHANGED failing=N`                  | Re-run phases 4 (fix CI) and 5 (report)                 |
| `COMMENTS_CHANGED`                      | Re-run phase 3 (address new comments) then phase 5      |
| `STATE_CHANGED mergeStateStatus=BEHIND` | Re-run phase 2 (update branch) then phase 5             |
| `STATE_CHANGED mergeStateStatus=CLEAN`  | Report to user: PR is now mergeable                     |
| `STATE_CHANGED mergeStateStatus=DIRTY`  | Report merge conflict — needs human resolution          |
| `PR_CLOSED state=MERGED`                | Report PR merged; monitor has exited — no action needed |
| `PR_CLOSED state=CLOSED`                | Report PR closed without merge; monitor has exited      |

Announce to the user:

```
Monitor mode active — watching PR #<number> every <interval>.
I will interject here whenever CI, comments, or merge state changes.
Ask me to "stop monitoring" to cancel.
```

---

## Guardrails

- **Never force-push** unless the repo's contributing guide explicitly says the project squashes or rebases PRs. Prefer merge commits for branch updates.
- **Never modify files outside the PR's changed file set** to fix a CI failure — report instead.
- **Never approve, merge, or close the PR** — that is always a human decision.
- **Never post a review on the PR** — only reply to individual comment threads and issue-level comments.
- **Do not manufacture fixes**: if you cannot understand what a CI failure requires, categorise it as "report" and explain what you saw.
- **Draft PRs**: still run all phases but note in the final report that the PR is a draft and cannot be merged until marked ready.
- **If the working directory is not the PR's repo**: checkout the correct repo first or use `--repo` flags throughout.
