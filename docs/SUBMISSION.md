# Submission

Everything the Devpost form asks for, in the order it asks for it. Copy each
section into the matching field.

---

## Category

**Fortified Enterprise Fleet**

---

## Hosted project URL

```
https://sentinel-web-sqr3nw6srq-uc.a.run.app/
```

Cloud Run serves this service on two hostnames. The one above is the one to
submit. The alternative form,
`https://sentinel-web-60712078658.us-central1.run.app/`, resolves on most
networks but not all.

The board reads live state. Everything on it is real: the obligations, the
timestamps, the audit trail.

---

## Repository URL

```
https://github.com/<your-account>/sentinel
```

Replace with the real URL after pushing. If the repository is private, share it
with `testing@devpost.com` and `cloudhackathons@google.com`.

Spin-up instructions are in `README.md` under **Spin-up**, covering both a local
run and a full deploy from a clean clone. They were verified by following them in
a fresh clone rather than from memory.

---

## Architecture diagram

`docs/architecture.svg` in the repository, and rendered inline at the top of
`docs/architecture.md`. It shows the trust boundary, where identifiers are
replaced, where Gemini is called and what it is not permitted to do, the ledger,
the timers, the department fleet, and the observability path.

---

## Demo video

Upload `docs/demo.mp4` to YouTube and set it to **Public**, not Unlisted, then
paste the link here.

---

## Text description

### Features and functionality

**Absence detection.** Every obligation carries its own Cloud Tasks timer with an
explicit `scheduleTime`, because cron cannot express *wake up about this
particular obligation at 16:00*. Timers can be lost, so a Cloud Scheduler sweep
runs every fifteen minutes and re-derives every checkpoint from the obligation
record itself. The obligation is authoritative; the timer never is. Verified by
deleting a live Cloud Task and watching the sweep recover the obligation.

**An obligation ledger with a proved invariant.** Firestore, transactional,
version-guarded, append-only history. `CLOSED` has exactly one legal predecessor
in the state machine, and an assertion derives that from the transition table at
import time — if anyone adds an edge that makes closure reachable another way,
the service refuses to start.

**The Evidence Gate.** Closing an obligation requires five deterministic checks
with no model call inside: an authoritative source *for that obligation type*, an
external reference id, a timestamp inside the acceptance window and after the
obligation opened, a matching subject token, and an assertion that describes the
requirement.

**A trust boundary the model never crosses.** Model Armor screens inbound text,
then Sensitive Data Protection replaces every identifier with a deterministic
token under a KMS-wrapped key. The same person maps to the same token across
weeks, which is what makes multi-day correlation possible without holding a name
anywhere. Only de-identified text is ever persisted.

**A fleet with real identity separation.** A coordinator holding zero tools
routes to three department agents running as three service accounts with disjoint
tool allowlists. Each agent re-checks its own allowlist even though the
coordinator already checked, and each container verifies at boot that the role it
declares matches the identity it actually runs as.

**A policy engine that is data, not code.** Versioned YAML, deny-by-default in
three layers, four risk tiers. Its hash is logged at boot, returned with every
decision, and shown on every page of the UI, so a refusal recorded three days ago
ties to the configuration that produced it.

**Human approval for anything leaving the hospital.** Tier-two actions park with
the exact outbound payload, a named human, and a mandatory reason. Approving
twice returns the first decision rather than acting again.

**The Continuity Board.** Obligations ordered by risk of breach rather than by
deadline. A "why is this stuck" answer that walks the dependency graph to the
obligation that actually has to happen first, not the one immediately in front.

**Re-identification as a separate, closed service.** The only code that can
reverse a token, and the only service not reachable from the internet. It records
who asked, which token, and why *before* it answers, and resolves one token at a
time with no way to enumerate.

**Long-running proof.** One obligation was opened on 24 August and left running.
It woke on the 26th and nudged the owner, woke on the 28th and escalated to a
breach, and woke on the 30th and escalated to a supervisor — three real emails,
across six days, from timers set once. No clock was simulated.

### Technologies used

**Google AI.** Gemini 3.5 Flash via Vertex AI for the interpreter and the
coordinator, both constructed with zero tools. Gemma 3 running on CPU inside the
trust boundary for first-pass triage.

**Agent frameworks.** Google ADK. Agent Registry for the fleet catalogue, with
three A2A agent cards whose skills are generated from the policy allowlist so the
catalogue cannot advertise what policy would refuse.

**Google Cloud.** Cloud Run (eight services, `min-instances=0`), Firestore, Cloud
Tasks, Cloud Scheduler, Pub/Sub, Sensitive Data Protection, Cloud KMS, Secret
Manager, Model Armor, Cloud Trace, Cloud Logging, Cloud Monitoring, Artifact
Registry, Cloud Build, Gmail API.

**Application.** Python 3.12, FastAPI, Pydantic. Next.js 16, TypeScript and IBM
Carbon Design System for the board. OpenTelemetry for the reasoning-chain traces.

### Other data sources used

All synthetic, and obviously so. Twelve invented subjects across five obligation
types, in `fixtures/`. The names exist in exactly one file, which is the input to
the trust boundary — nothing downstream of ingest has ever seen them.

Twelve rather than a hundred and twenty-eight on purpose. A board showing a
hundred fabricated workflows is the fastest way to lose a technical reader: the
first hollow row anybody opens discredits every row above it.

No real patient data was used at any point.

### Findings and learnings

**1. Detecting absence is a different engineering problem from reacting to
presence, and timers alone do not solve it.** A dropped timer is
indistinguishable from a task that was never due — nothing errors, the obligation
simply stops being tracked. What fixes it is inverting which thing is
authoritative: the obligation carries its own next checkpoint and a sweep
re-derives it. Losing a timer then costs fifteen minutes instead of costing the
obligation. Measured: 100% detection over twelve obligations, median 5.6 seconds
after the deadline, with real timers and real waiting.

**2. The Evidence Gate ablation was less dramatic and more interesting than
expected.** Over thirty samples: 0% false closure for the deterministic gate, 16%
for Gemini 3.5 Flash judging the same corpus. Sixteen percent is *good* — the
model caught every wrong-patient case and every plainly irrelevant assertion.
What it missed was all one kind: whether a source is authoritative *for that
particular obligation type*, and whether a timestamp falls inside an acceptance
window. Judgements made of a lookup table and an arithmetic comparison, where the
model had nothing to reason from and reasoned from plausibility instead. Both
arms closed 100% of what genuinely qualified, so the gate is specific rather than
merely conservative.

**3. Model Armor's prompt-injection detection is diluted by surrounding
legitimate text.** An injected paragraph is blocked on its own at 320 characters.
The byte-identical paragraph inside a plausible 1318-character insurer letter is
not blocked. Screening in windows finds it at 300 characters per window and
misses it at 400 and 600. This was measured, not assumed, and it changed the
design: long documents are now screened whole and again in overlapping windows.
But a mitigation found by accident is not a defence to rely on, so the failure
mode was tested — fed the injection with screening bypassed, the interpreter
produced an ordinary obligation for the document the letter was genuinely asking
for and kept chasing it. The defence that held was structural, not filtering.

**4. Asking a model to tier an action returns the seriousness of the subject, not
the authority the action needs.** Asked to assign a risk tier to a critical
potassium result, the model returned T3 — the tier reserved for writing to a
clinical record, denied always with no approval path. It was reading how serious
the situation was rather than what the system would have to *do* about it, which
was send a message. That one word would have made the entire critical-lab
workflow permanently undischargeable, and it would have failed silently as
"working as designed". The same division now appears three times: the model
proposes an SLA and code computes the dates, the model proposes a route and code
decides if it is permitted, the model proposes closure and code decides if it
happened.

**5. An evaluation that credits a fallback is not an evaluation.** The first
measurement of the Gemma triage classifier reported 100% accuracy. Four of the
sixteen calls had timed out and silently taken a default label that happened to
be correct for those four, so the classifier was being credited for answers it
never gave. A fallback now sets a `degraded` flag and the harness refuses to
count a degraded result as correct. The true figure is 75%. It was only visible
because the raw model output was printed next to the verdict.

**6. What is missing from this list is the hospital.** The intent was to ground
the three workflows in questions to working clinical staff about what actually
gets forgotten. That did not happen inside the build window, so the workflows are
modelled on documented failure modes rather than on anything a member of staff
said. It is stated here rather than left for a reader to discover.

**No status-quo baseline is claimed.** The honest comparison would be against how
long the same lapse currently goes unnoticed in a real hospital. No such
measurement was obtained, and quoting a plausible figure would have fabricated
the most important number in the evaluation.

---

## Before submitting

- [ ] Video uploaded to YouTube, set to **Public** (not Unlisted)
- [ ] Repository pushed and public, or shared with the two addresses above
- [ ] Category set to Fortified Enterprise Fleet
- [ ] Hosted URL pasted and opened once to confirm it answers
- [ ] Text description fields filled from the sections above
