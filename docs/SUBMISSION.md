# Submission

Everything the forms ask for, ready to copy. Devpost fields first, then YouTube.

---

## Devpost

### Category

Fortified Enterprise Fleet

### Elevator pitch

> An autonomous continuity layer for hospitals. It watches for the work that
> didn't happen, works out who owes it, chases it across days, and refuses to
> close anything without proof.

### Try it out links

| Label | URL |
|---|---|
| Continuity Board (live) | `https://sentinel-web-sqr3nw6srq-uc.a.run.app/` |
| Source code | `https://github.com/<your-account>/sentinel` |
| Demo video | your YouTube link |

Cloud Run serves the board on two hostnames. Submit the one above. The other
form, `sentinel-web-60712078658.us-central1.run.app`, resolves on most networks
but not all.

### Built with

25 tags, in order of how much they matter to the project.

```
gemini, google-adk, vertex-ai, gemma, cloud-run, firestore, cloud-tasks,
cloud-scheduler, pub-sub, sensitive-data-protection, model-armor, cloud-kms,
secret-manager, agent-registry, opentelemetry, cloud-trace, cloud-monitoring,
python, fastapi, pydantic, nextjs, typescript, carbon-design-system, ollama,
docker
```

---

## About the project

Paste the whole of this section into the story field. It is already Markdown.

### Inspiration

A critical potassium result comes back at two in the afternoon. The doctor who
ordered it is in theatre. The result was delivered, the system worked, and nobody
acts on it for six hours.

Hospitals do not fail because staff do not know their jobs. They fail at the
seams. Between shifts, between departments, between a result arriving and someone
noticing. An insurer asks for one document and the request sits in a shared
inbox. A discharge waits on a pharmacy sign off that nobody chased.

Every one of those failures has the same shape. Nothing happened.

Every agent I have seen reacts to events. Something arrives, the agent wakes up,
the agent does something. But you cannot subscribe to "nothing happened", and it
turns out that detecting silence is a genuinely different engineering problem
from reacting to noise. That gap is what Sentinel is built for.

### What it does

Sentinel turns hospital events into **obligations**: commitments the workflow has
implicitly made and has not yet discharged. Each one knows who owes it, when it is
due, and what would prove it was done.

It then does three things nothing else does.

**It notices when nothing happens.** Every obligation carries its own timer with
an explicit wake up time, because cron cannot express "wake up about this
particular obligation at four o'clock". A sweep runs every fifteen minutes and
re-arms any timer that was lost, because the obligation is the source of truth
and the timer is not.

**It refuses to close without proof.** A model can look at a message and say "this
looks done". Closing an obligation instead requires five deterministic checks with
no model call inside them: an authoritative source for that obligation type, an
external reference number, a timestamp inside the acceptance window, a matching
subject, and an assertion that describes what was actually required.

**It never sees a patient.** Identifiers are replaced at the trust boundary before
anything reaches Gemini. The replacement is deterministic, so the same person maps
to the same token across weeks, which is what makes chasing something for six days
possible without holding a name anywhere.

One obligation was opened on 24 August and left running. It woke on the 26th and
nudged the owner, woke on the 28th and escalated to a breach, and woke on the 30th
and escalated to a supervisor. Three real emails, across six days, from timers set
once. No clock was simulated and nothing was fast forwarded.

### How I built it

Eight services on Cloud Run, each with its own identity.

**The trust boundary** (`services/ingest`) screens inbound text with Model Armor,
then replaces every identifier using Sensitive Data Protection with a key wrapped
by Cloud KMS. Only the de-identified text is ever stored or published.

**The engine** (`services/engine`) holds the obligation ledger in Firestore, with
transactional writes guarded by a version field and an append only history. It
runs two Gemini agents, both built with no tools at all: an interpreter that turns
one event into obligation proposals, and a coordinator that decides which
department owes the next move. Neither can write anything. Deterministic code
takes every proposal and checks it before it becomes real.

**The fleet** is one container image deployed three times, as three service
accounts, with disjoint tool allow lists. Each agent re-checks its own permissions
even though the coordinator already checked, and each container verifies at start
up that the role it claims matches the identity it actually holds.

**The policy engine** is versioned YAML rather than code. Its hash is logged at
boot, returned with every decision, and shown on every page of the interface, so a
refusal recorded three days ago can be tied to the exact configuration that
produced it.

**Re-identification** lives in its own service, and it is the only one closed to
the internet. It records who asked, for which token, and why, before it answers.

**The interface** is Next.js and IBM Carbon, ordered by risk of breach rather than
by deadline, with a "why is this stuck" answer that walks the dependency graph to
the thing that actually has to happen first.

### Challenges I ran into

**Gemini 3.5 Flash is served only from the global endpoint.** Every other service
in the project is regional. Calling the regional endpoint returns a 404 saying the
publisher model does not exist, which reads exactly like a wrong model name. That
cost an hour before I thought to try a different location.

**Google's edge intercepts `/healthz`.** On a `run.app` hostname it answers with
its own 404 page before the request ever reaches the container. Every other path
routes normally. A service with a `/healthz` health check looks identical to a
service that failed to deploy. I renamed every health endpoint to `/health`.

**A newer version of `google-api-core` breaks every Firestore transaction on Cloud
Run** while working perfectly on a laptop. It percent encodes the database name,
so the error reads `Invalid database id %28default%29`, and the rollback path then
masks it with a second error about a missing transaction id. It is pinned in all
four Python services now.

**Cloud Run throttles CPU to near zero once a request returns**, so a batched
OpenTelemetry span processor may never flush before the instance is reclaimed. The
exporter reports no errors and the trace console stays empty. Span export is
synchronous here.

**Cloud Run serves two hostnames per service**, and some networks resolve only one
of them. I lost time believing a deployment was broken when it was a DNS problem
on my own machine.

**Turbopack cannot resolve Carbon's internal Sass imports**, and Carbon writes its
design tokens under class selectors rather than at the document root. The result
was a page that compiled cleanly and rendered black text on a black background.

### What I learned

**Timers alone do not solve absence detection.** A dropped timer is
indistinguishable from a task that was never due. Nothing errors. The obligation
simply stops being tracked. What fixes it is inverting which thing is
authoritative: the obligation carries its own next checkpoint, and a sweep
re-derives it. Losing a timer then costs fifteen minutes instead of costing the
obligation. Measured across twelve obligations with real timers: 100 per cent
detected, median 5.6 seconds after the deadline.

**The Evidence Gate ablation was less dramatic and more interesting than I
expected.** Over thirty pieces of evidence: zero per cent false closure for the
deterministic gate, 16 per cent for Gemini judging the same corpus. Sixteen per
cent is good. The model caught every wrong patient case and every plainly
irrelevant assertion. What it missed was all one kind: whether a source is
authoritative for that particular obligation type, and whether a timestamp falls
inside a window. Judgements made of a lookup table and a subtraction, where the
model had nothing to reason from and reasoned from plausibility instead.

**Prompt injection detection is diluted by legitimate text.** An injected
paragraph is blocked on its own at 320 characters. The byte identical paragraph
inside a plausible 1318 character insurer letter is not blocked. Only the
surrounding correspondence changed. Screening in overlapping windows finds it, but
a mitigation you discover by accident is not a defence you should rely on, so I
tested the failure mode. Fed the injection with screening removed entirely, the
agent ignored the instruction and asked for the document the letter was genuinely
chasing. It has no tools and no vocabulary for closing anything, so it could not
have obeyed.

**Asking a model to tier an action returns the seriousness of the subject, not the
authority the action needs.** Asked to assign a risk tier to a critical potassium
result, Gemini returned the tier reserved for writing to a clinical record, which
is denied always with no approval path. It was reading how serious the situation
was rather than what the system would have to do about it, which was send a
message. That one word would have made the whole critical lab workflow impossible
to discharge, and it would have failed silently as working as designed.

**An evaluation that credits a fallback is not an evaluation.** My first
measurement of the Gemma classifier reported 100 per cent accuracy. Four of the
sixteen calls had timed out and quietly taken a default label that happened to be
correct for those four. The classifier was being credited for answers it never
gave. A fallback now sets a flag and the harness refuses to count it. The true
figure is 75 per cent.

The through line, for me, is that the useful question is not how to stop a model
hallucinating. It is what a decision is actually made of. If it is judgement about
messy language, the model beats anything I would write. If it is a lookup table
and a timestamp comparison, the model is guessing at something I could simply
know, and it will guess well enough, often enough, to hide the problem until it
matters.

### What is missing

I did not talk to a hospital. The intent was to ground the three workflows in
questions to working clinical staff about what actually gets forgotten. That did
not happen inside the build window, so the workflows are modelled on documented
failure modes rather than on anything a member of staff said. It is the weakest
part of the project and it is better said than discovered.

I also make no claim about a status quo baseline. The honest comparison would be
against how long the same lapse currently goes unnoticed in a real hospital. I
have no such measurement, and quoting a plausible figure would have fabricated the
most important number in the evaluation.

### What is next

Closing the public services behind IAM, which is a one flag change rather than a
rewrite. Advisory memory scoped to a role, under the rule that memory may change
how the agent acts but never whether an obligation closes. And the six questions,
to an actual hospital.

---

## YouTube

### Title

```
Sentinel: an AI agent that reacts to the absence of events
```

### Description

```
Most AI agents react to events. Hospitals fail when nothing happens.

Sentinel is an autonomous continuity layer for hospital operations. It watches
for the work that didn't happen, works out who owes it, chases it across days,
and refuses to close anything without authoritative proof.

Built solo in eight days on Gemini 3.5 Flash, Google ADK and Google Cloud.

I created this piece of content for the purposes of entering the All Things
Agentic Hackathon.

Chapters
0:00 The problem
0:25 Reacting to the absence of events
0:45 Six days unattended, three escalations, no simulated clock
1:40 The Evidence Gate refuses to close without proof
2:30 A prompt injection, and why the filter is not the defence
3:15 Running on Google Cloud
3:35 Closing it on the only thing that could have closed it

Measured results
100% detection across 12 obligations, median 5.6 seconds after the deadline.
0% false closure with the deterministic Evidence Gate, 16% when the model
judged the same evidence.

Source code: https://github.com/<your-account>/sentinel
Live board: https://sentinel-web-sqr3nw6srq-uc.a.run.app/

Built with Gemini 3.5 Flash, Google ADK, Gemma, Cloud Run, Firestore, Cloud
Tasks, Cloud Scheduler, Pub/Sub, Sensitive Data Protection, Model Armor, Cloud
KMS, Agent Registry, OpenTelemetry, Next.js and IBM Carbon.

#AllThingsAgenticHackathon
```

Adjust the chapter timings to your actual cut before pasting. YouTube only turns
them into clickable chapters if the first one is `0:00` and there are at least
three.

The hackathon sentence is there on purpose. It is the wording the bonus for
published content requires, and including it costs nothing.

Set the video to **Public**, not Unlisted. Unlisted does not qualify.

---

## Before submitting

- [ ] Video uploaded to YouTube and set to Public
- [ ] Chapter timings corrected to match the final cut
- [ ] Repository pushed and public, or shared with `testing@devpost.com` and `cloudhackathons@google.com`
- [ ] Category set to Fortified Enterprise Fleet
- [ ] Hosted URL opened once to confirm it answers
- [ ] Elevator pitch, story, tags and links filled from the sections above
