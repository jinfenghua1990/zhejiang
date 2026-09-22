# AGENTS.md

## Scope

These instructions apply to the entire `ecommerce-workspace` repository.

This repository is maintained through frequent, user-directed incremental changes. GitHub Actions minutes are limited, so every coding agent must minimize unnecessary commits, pushes, and CI executions while preserving correctness.

## Mandatory Git / GitHub efficiency rules

### 1. One user change round = one commit / one push by default

For one coherent user request or one continuous revision round:

- Inspect all relevant files first.
- Plan the complete change set before writing.
- Apply all related file changes together.
- Create **one commit** for the whole change set.
- Update/push the branch **once**.

Do **not** commit or push file-by-file.

Bad pattern:

```text
edit A -> commit/push
edit B -> commit/push
edit C -> commit/push
```

Required pattern:

```text
inspect A/B/C
edit A/B/C
validate
one commit
one push
```

### 2. Batch multi-file GitHub API writes

When working through GitHub APIs/tools, avoid sequential `create_file` / `update_file` operations when each operation creates its own commit.

For multi-file changes, prefer a Git-data batching flow when available:

1. Read the target branch HEAD/tree.
2. Create/update all required blobs.
3. Create one tree containing all changes.
4. Create one commit.
5. Move/update the branch ref once.

The goal is one branch update for the entire change round.

### 3. Never use remote CI as an editing loop

Do not make a small change, push, wait for CI, then make another small change repeatedly.

Before the push:

- inspect affected call sites and dependencies;
- run or reason through the narrowest relevant checks available;
- combine fixes found during review into the same pending change set;
- only then push.

If a first push has already happened and another correction is unavoidable, batch all remaining corrections into one follow-up commit rather than multiple micro-commits.

### 4. Be aware of duplicate workflow triggers

This repository may trigger workflows from both `push` and `pull_request`. A single branch update can therefore start multiple workflow runs.

Before writing to a branch, assume that each push may be expensive.

Especially avoid repeated pushes to `develop` while a `develop -> main` pull request is open.

### 5. Heavy CI is for validation, not exploration

Do not intentionally trigger full backend tests, frontend production builds, Docker image builds, database migration checks, backup/restore drills, or release workflows just to discover basic editing mistakes.

Use repository inspection and targeted validation first.

### 6. Do not create temporary Actions workflows for one-off code patches

Do not add disposable `.github/workflows/*.yml` files merely to execute a patch, script, or one-time edit.

If a one-off workflow is truly necessary, it must be justified, narrowly scoped, and removed promptly. Prefer direct repository edits and normal code review.

### 7. CI-skip policy

For documentation-only or agent-instruction-only commits that do not affect runtime behavior, tests, builds, deployment, migrations, security, or dependencies, a recognized GitHub Actions skip token such as `[skip ci]` may be used in the commit message to avoid wasting Actions minutes.

Do **not** use CI skipping for:

- application code changes;
- backend/frontend logic;
- database models or migrations;
- dependency changes;
- Docker/deployment changes;
- security-sensitive changes;
- workflow changes that need validation.

### 8. Preserve release safety

Reducing Actions usage must not mean bypassing important release checks.

The preferred model is:

- frequent development edits: batch locally/in one commit;
- PR/merge/release boundary: run the appropriate complete validation;
- production release: use the tested/promoted artifact path.

### 9. Avoid unnecessary branch churn

Do not create extra branches for tiny follow-up edits when the current task branch is appropriate.

Do not repeatedly merge/rebase solely to trigger checks.

### 10. Final reporting

After repository changes, report:

- branch changed;
- commit SHA;
- whether the change was intentionally CI-skipped;
- whether a workflow run is expected;
- any remaining validation that belongs at PR/release time.

## Repository-specific reason

On 2026-09-20, the repository had accumulated hundreds of workflow runs in the month, with repeated CI executions caused by high-frequency pushes and overlapping `push` / `pull_request` triggers. The operating rule above exists to prevent a single UI or business-logic revision from expanding into many redundant GitHub Actions runs.

The user frequently makes iterative UI and business-logic requests. Preserve that fast interaction style; optimize the implementation process behind it instead of asking the user to bundle requests manually.
