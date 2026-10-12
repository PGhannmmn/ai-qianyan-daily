# Shared GitHub collaboration

GitHub is the shared source authority for ChatGPT Work, Codex, and Muse. Preserve the existing Muse Cloud runtime and production schedules. Source migration does not authorize runtime cutover.

Before editing, read existing instructions and open PRs. Claim one task with one active actor; use agent/<actor>/<task-id>-<summary>. Limit changes to the task's declared paths. Preserve existing project boundaries and reuse existing functionality and tests.

Deliver via a draft PR with base SHA, changed paths, acceptance criteria, test commands and actual exit codes. Repository owner controls merging. No direct pushes to main, auto-merge, production schedule edits, deployments, paid API calls, payments or external publication without explicit authorization. Do not inspect, copy or output secrets. Keep runtime state, databases, logs and media out of source migration.

CI results prove only the tested code. Record unknown access or runtime evidence as unknown. Do not equate GitHub dry-runs, an offline desktop agent or a scheduler statement with independently verified production health or PC-off execution.

Each actor uses its own authorized GitHub connection. A Work/Codex PR does not prove Muse access. Instructions and CODEOWNERS are review policy; server-side protection must be verified separately. If CODEOWNERS contains an owner without write permission, report it instead of silently claiming enforcement.
