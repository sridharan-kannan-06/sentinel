# CLAUDE.md

Standing rules for this repository. Read this file at the start of every session
before touching any code.

## What this is

Sentinel is an autonomous continuity layer for hospital operations. It watches for
work that did not happen, determines who owes it, chases it across days, and refuses
to close anything without authoritative proof.

Built solo for the All Things Agentic Hackathon, Fortified Enterprise Fleet track.
Submission deadline 31 August 2026, 17:00 PT. Feature freeze 29 August 23:59 IST.

## The three invariants

These are not aspirations. They are properties the code must have at all times. If a
change would violate one, stop and say so rather than working around it.

1. **The system reacts to the absence of events.** Obligations carry timers. Nothing
   in the system depends on a human noticing something. Timers can be lost, so a
   reconciliation sweep treats the obligation as the source of truth, never the timer.

2. **The model may propose closure. Only an authoritative external fact may close.**
   Gemini is a proposer. It never commits. The Evidence Gate is deterministic code
   with no model call inside it. Any code path that lets a model response set status
   to CLOSED is a defect regardless of how well it performs.

3. **The model never sees a patient.** Protected health information is tokenised at
   the trust boundary before anything reaches Gemini. Re-identification happens in one
   place, in a separate service, for an authenticated human, and is logged every time.
   No PHI in logs, traces, error messages, test fixtures, or committed files.

## Stack

- Python 3.12 in the container. Local development runs 3.14, which resolves the
  whole dependency tree including `grpcio` on native `cp314` wheels. Keep the code
  compatible with both rather than relying on 3.14-only syntax.
- FastAPI, google-adk, google-genai
- Model: `gemini-3.5-flash` on Agent Platform, formerly Vertex AI
- Firestore native mode for the ledger, Cloud Tasks for timers, Cloud Scheduler for
  the reconciliation sweep, Pub/Sub for event fan-out
- Sensitive Data Protection for deterministic tokenisation, Cloud KMS for the wrapped
  key, Model Armor for injection and PII screening
- OpenTelemetry to Cloud Trace, structured JSON logs to Cloud Logging
- Next.js 15 App Router, TypeScript, and Carbon Design System for the Continuity
  Board. Carbon vanilla, Gray 100 dark theme, IBM Plex Mono for all numerics.
  Tailwind is deliberately not used: two styling systems in one eight-day build
  is a cost with no return.
- Everything on Cloud Run, `min-instances=0`, `max-instances=2`, region `us-central1`

Gemini 3.x notes that matter: do not set `temperature`, `top_p`, or `top_k`. Use
`thinking_level` rather than the deprecated `thinking_budget`. Function call responses
must echo matching `id` and `name` fields and the same count as the preceding calls.

**`gemini-3.5-flash` is served only from the `global` location, never from
`us-central1`.** Every other service in this project is regional. Calling the
regional endpoint returns a 404 saying the publisher model does not exist, which
reads like the model name is wrong when it is the location that is wrong. Firestore,
Cloud Tasks, Cloud Run, KMS, and Model Armor all stay in `us-central1`.

## Repository layout

```
services/ingest      trust boundary, screening, tokenisation, publishes to Pub/Sub
services/engine      interpreter, ledger, policy engine, evidence gate, timers
services/agents      three department agents, one Cloud Run service each
services/reid        re-identification, separate service account, human callers only
web                  Continuity Board
policy               versioned YAML policy config, hashed at boot
infra                deploy scripts and provisioning
eval                 replay harness and published numbers
fixtures             synthetic events, twelve subjects, no real data
docs                 architecture diagram and notes
```

## Code conventions

**No emoji anywhere.** Not in code, log lines, UI text, commit messages,
documentation, or console output. This is an operations tool.

**Comments are plain sentences.** No banner comments, no separator lines, no rows of
dashes or equals signs, no decorative dividers. A comment explains why a thing is the
way it is, not what the next line does.

Correct:

```python
# Cloud Tasks can drop a task silently, so the sweep re-derives the checkpoint
# from the obligation rather than trusting the timer to have fired.
```

Wrong:

```python
# ---------- Reconciliation ----------
# ===== Setup =====
# Loop over obligations
```

Other rules:

- Type hints on every function signature. Pydantic models for anything crossing a
  service boundary.
- Structured logging only. Every log line carries `obligation_id` and `trace_id`
  where one exists. No bare `print`.
- No secrets, keys, tokens, or project-specific credentials in the repository. Config
  comes from environment variables, secrets from Secret Manager.
- No `TODO` or `FIXME` in committed code. Either do it or record it in
  `docs/DEFERRED.md` with a reason.
- Fixtures are synthetic and obviously so. Twelve subjects, not a hundred. Never
  fabricate a metric, a benchmark number, or a hospital quote.

## Windows and PowerShell

The shell here is PowerShell. When a command fails with a message about scripts being
disabled on this system, that is the execution policy blocking a `.ps1` shim. Fix it
once, at user scope, without administrator rights:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Confirm with `Get-ExecutionPolicy -List`.

If that still fails or the policy is locked by group policy, call the `.cmd` shims
directly instead, because they are not governed by the execution policy:

```powershell
npm.cmd install
npx.cmd create-next-app@latest
```

Per-command escape hatch when neither is possible:

```powershell
powershell -ExecutionPolicy Bypass -Command "npm install"
```

Other Windows rules:

- Use forward slashes in Python path handling, and `pathlib` rather than string
  concatenation.
- Do not assume `make`, `bash`, `curl`, or GNU coreutils are present. Write helper
  scripts as `.ps1`.
- Long-running commands like `gcloud run deploy` can take four minutes. Wait for them
  rather than assuming failure and retrying.

## Commits

Commit at every verified checkpoint, not at the end of a session. A checkpoint is
verified when the acceptance check for that step has actually passed, not when the
code looks finished.

- Conventional Commits: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`,
  `infra:`. Scope where useful, for example `feat(ledger):`.
- Subject line under 72 characters, imperative mood, no trailing period, no emoji.
- Body explains why the change was made and what was verified. Two to five lines.
- No AI attribution trailers, no `Co-Authored-By` lines, no generated-with footers.
  AI assistance is disclosed once in the README, not on every commit.

**Never run `git push`.** Pushing is done manually. Never run `git commit --amend`,
`git rebase`, `git reset --hard`, or any force operation on existing history. If a
commit was wrong, add a new commit that fixes it.

Example:

```
feat(evidence): reject closure without a matching authoritative source

The gate now checks source allowlist, external reference id, acceptance
window, and subject token before permitting CLOSED. Verified against the
five negative fixtures in eval/fixtures/evidence_reject.
```

## Working agreement

- Never claim a step is done without running the acceptance check and showing its
  output. "Should work" is not done.
- Never invent a Google Cloud API name, IAM role, gcloud flag, or SDK method. If you
  are not certain it exists, say so and check the documentation before writing it.
- Ask before deleting files, changing the policy YAML schema, or altering the ledger
  state machine. Those three are load-bearing.
- When something in the spec turns out to be impossible or much more expensive than
  it looked, say so immediately rather than building a weaker version quietly.
- Prefer boring code that a judge can read in thirty seconds over clever code.

## Out of scope

Do not build, and do not propose: multi-tenancy, login or signup, roles beyond
operator and approver, a mobile app, voice, a chat interface, a graph visualisation
library, a hospital simulator with hundreds of fake patients, an HL7 or FHIR parser
beyond a single fixture, Kubernetes, or Agent Gateway.
