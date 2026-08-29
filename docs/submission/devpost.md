# Devpost submission

**Category:** Fortified Enterprise Fleet

**Tagline:** Sentinel is an autonomous continuity layer for hospitals. It watches
for the work that didn't happen, works out who owes it, chases it across days, and
refuses to close anything without proof.

---

## Inspiration

Hospitals don't fail because staff don't know their jobs. They fail at the seams —
between shifts, between departments, between a result arriving and someone
noticing.

A critical potassium result comes back and the ordering clinician is in theatre. An
insurer asks for one document and the request sits in a shared inbox. A discharge
waits on a pharmacy sign-off nobody chased. In every case the system worked, the
message was delivered, and the work still didn't happen.

Every agent I've seen reacts to events. **You cannot subscribe to "nothing
happened."** That gap is what Sentinel is built for.

---

## Features and functionality

**Absence detection.** Every obligation carries its own Cloud Tasks timer with an
explicit `scheduleTime`, because cron cannot express *wake up about this
particular obligation at 16:00*. Timers get lost, so a Cloud Scheduler sweep runs
every fifteen minutes and re-derives every checkpoint from the obligation record
itself. The obligation is authoritative; the timer never is. Verified by deleting
a live Cloud Task on purpose and watching the sweep recover it.

**An obligation ledger with a proved invariant.** Firestore, transactional,
version-guarded, append-only history. `CLOSED` has exactly one legal predecessor
in the state machine, and an assertion derives that from the transition table at
import — if anyone adds an edge that makes closure reachable another way, the
service refuses to start.

**The Evidence Gate.** Closing requires five deterministic checks with no model
call inside: authoritative source *for that obligation type*, external reference
id, timestamp inside the acceptance window and after the obligation opened,
matching subject token, and an assertion that describes the requirement.

**A trust boundary the model never crosses.** Model Armor screens inbound text,
then Sensitive Data Protection replaces every identifier with a deterministic
token under a KMS-wrapped key. `Meena Raghavan` becomes `PT-8119` and stays
`PT-8119` across weeks, which is what makes multi-day correlation possible without
holding a name anywhere. Only de-identified text is ever persisted.

**A fleet with real identity separation.** A coordinator holding zero tools routes
to three department agents running as three service accounts with disjoint tool
allowlists. Each agent re-checks its own allowlist even though the coordinator
already checked, and each container verifies at boot that the role it declares
matches the identity it actually runs as.

**A policy engine that is data, not code.** Versioned YAML, deny-by-default in
three layers, four risk tiers. Its hash is logged at boot, returned with every
decision, and shown on every page of the UI, so a refusal recorded three days ago
ties to the configuration that produced it.

**Human approval for anything leaving the hospital.** T2 actions park with the
exact outbound payload, a named human, and a mandatory reason. Approving twice
returns the first decision rather than acting again.

**The Continuity Board.** Obligations ordered by risk of breach rather than by
deadline. A "why is this stuck" answer that walks the dependency graph to the
obligation that actually has to happen first, not the one immediately in front. A
trust panel carrying every config hash in force.

**Re-identification as a separate, closed service.** The only code that can
reverse a token, the only service not reachable from the internet, callable only
by the board's identity. It records who asked, which token, and why *before* it
answers, and resolves one token at a time with no way to enumerate.

**The canary.** One obligation opened on 24 August and left running, with real
Cloud Tasks checkpoints on 26, 28 and 30 August. No clock is simulated and nothing
is fast-forwarded.

---

## Technologies used

**Google AI:** Gemini 3.5 Flash via Vertex AI (interpreter and coordinator, both
constructed with zero tools). Gemma 3 running on CPU inside the trust boundary for
first-pass triage — a second Google model as a real code path, not a decoration.

**Agent frameworks:** Google ADK. Agent Registry for the fleet catalogue, with
three A2A agent cards whose skills are generated from the policy allowlist so the
catalogue cannot advertise what policy would refuse.

**Google Cloud:** Cloud Run (eight services, `min-instances=0`), Firestore, Cloud
Tasks, Cloud Scheduler, Pub/Sub, Sensitive Data Protection, Cloud KMS, Secret
Manager, Model Armor, Cloud Trace, Cloud Logging, Cloud Monitoring, Artifact
Registry, Cloud Build, Gmail API.

**Application:** Python 3.12, FastAPI, Pydantic. Next.js 16, TypeScript, and IBM
Carbon Design System for the board. OpenTelemetry for the reasoning-chain traces.

---

## Data sources

**All synthetic, and obviously so.** Twelve invented subjects across five
obligation types, in `fixtures/`. The names exist in exactly one file, which is
the input to the trust boundary — nothing downstream of ingest has ever seen them.

Twelve rather than a hundred and twenty-eight on purpose. A board showing a
hundred fabricated workflows is the fastest way to lose a technical audience: the
first row anybody clicks is hollow and nothing on the screen is believed
afterwards.

No real patient data was used at any point.

---

## Findings and learnings

**1. Detecting absence is a different engineering problem from reacting to
presence, and timers alone do not solve it.** A dropped timer is
indistinguishable from a task that was never due — nothing errors, the obligation
just quietly stops being tracked. What fixes it is inverting which thing is
authoritative: the obligation carries its own next checkpoint and a sweep
re-derives it. Losing a timer then costs fifteen minutes instead of costing the
obligation. Measured: 100% detection over twelve obligations, median 5.6 seconds
after the deadline, with real timers and real waiting.

**2. The Evidence Gate ablation was less dramatic and more interesting than
expected.** Over thirty samples: 0% false closure for the deterministic gate, 16%
for `gemini-3.5-flash` judging the same corpus. I expected worse from the model.
Sixteen percent is *good* — it caught every wrong-patient case and every plainly
irrelevant assertion. What it missed was all one kind: whether a source is
authoritative *for that particular obligation type*, and whether a timestamp falls
inside an acceptance window. Judgements made of a lookup table and an arithmetic
comparison, where the model had nothing to reason from and reasoned from
plausibility instead. Both arms closed 100% of what genuinely qualified, so the
gate is specific rather than merely conservative.

**3. Model Armor's prompt-injection detection is diluted by surrounding
legitimate text.** The injected paragraph in our fixture is blocked on its own at
320 characters. The byte-identical paragraph inside a plausible 1318-character
insurer letter is not blocked. Screening in windows finds it at 300 characters per
window and misses it at 400 and 600. This was measured, not assumed, and it
changed the design: long documents are now screened whole and again in overlapping
windows. But a mitigation found by accident is not a defence to rely on, so we
tested the failure mode — fed the injection with screening bypassed, the
interpreter produced an ordinary obligation for the document the letter was
genuinely asking for and kept chasing it. The defence that held was structural,
not filtering.

**4. Asking a model to tier an action gets you the seriousness of the subject, not
the authority the action needs.** Asked to assign a risk tier to a critical
potassium result, the model returned T3 — the tier reserved for writing to a
clinical record, denied always with no approval path. It was reading how serious
the situation was rather than what the system would have to *do* about it, which
was send a message. That one word would have made the entire critical-lab workflow
permanently undischargeable, and it would have failed silently as "working as
designed". The same division now appears three times: the model proposes an SLA
and code computes the dates, the model proposes a route and code decides if it is
permitted, the model proposes closure and code decides if it happened.

**5. What is missing from this list is the hospital.** The plan called for six
questions to working clinical staff about what actually gets forgotten. That
conversation did not happen inside the build window. The workflows here are
modelled on documented failure modes rather than on anything a person said, and
that is the weakest part of the submission. Stating it is better than presenting
three plausible workflows as though they were validated.

**No status-quo baseline is claimed.** The honest comparison would be against how
long the same lapse currently goes unnoticed in a real hospital. No such
measurement was obtained, and quoting a plausible "next morning ward round" figure
would have fabricated the most important number on the page.

---

## Built solo

Between 24 and 31 August 2026, by one person. Developed with AI assistance
(Claude); every architectural decision, measured number, and finding above was
verified against the running system rather than asserted.
