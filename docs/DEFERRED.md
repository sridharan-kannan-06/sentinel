# What was not built, and why

Every entry here is a decision rather than an oversight. Where something was cut
for time, that is said plainly. Where something was cut because it was the wrong
thing to build, the reasoning is given.

---

## Agent Gateway — deliberately not used

Agent Gateway is the Gemini Enterprise Agent Platform component for unified
routing and policy enforcement, and it is the obvious thing to reach for on this
track. It is in Private Preview. Access was not available within the build
window, and designing around a component that could not be exercised would have
produced a diagram that was true and a system that was not.

**What was built instead:** a Policy Enforcement Point in
`services/engine/policy.py`, governed by `policy/policy.yaml`.

Writing it rather than adopting it forced three decisions that a managed gateway
would have made invisibly, and each turned out to matter:

**Deny by default in three separate layers.** An undeclared agent has no
permissions, an undeclared action is refused, and a declared action outside that
agent's allow list is refused. A gateway configured by exception tends to allow
by default with a deny list bolted on, and the difference only shows up the day
somebody adds a tool and forgets the rule.

**T3 is evaluated before the allow list.** Every agent is refused a clinical
write for the same reason, and that reason — Sentinel has no clinical authority
at all — is more true and more useful than the "wrong department" answer the
allow list would have produced. Ordering the checks was a choice; a gateway would
have made it for us.

**The decision is an artefact, not an outcome.** Every call returns a decision
id, the policy version, and the hash of the exact bytes that decided. Those land
in the ledger, so a refusal recorded three days ago can be tied to the
configuration that was in force at the time rather than to whatever the file says
today.

The enforcement point is also duplicated on purpose: each agent re-checks its own
allow list on every action even though the coordinator already checked. A
coordinator bug, a replayed request, or a caller that skipped the coordinator
entirely still meets a closed door. A single gateway is a single place to be
wrong.

**If this were going to production**, Agent Gateway would be worth revisiting for
the routing and quota concerns it handles well. The policy model would stay where
it is. Authority is domain logic, it is the thing this system is actually for,
and it belongs in the repository next to the tests that prove it.

---

## Memory Bank — cut, and the rule it would have needed is already enforced

Vertex AI Memory Bank would have held soft operational knowledge scoped to a
role: *this insurer's pre-auth desk answers email within six hours and ignores
portal messages*, *this consultant acknowledges on mobile and not on email*.

It was cut for time. It is genuinely useful and it is genuinely not load-bearing.

The rule it would have had to obey is worth stating anyway, because the
architecture already enforces it:

> Memory may change how the agent acts. It may never change whether an obligation
> closes.

Memory poisoning is the risk Google's own Memory Bank documentation names first.
In this system a poisoned memory could at most cause a badly chosen notification
channel. It could not close anything, because closure requires an `Evidence`
record that passes five deterministic checks and `CLOSED` has exactly one legal
predecessor. There is no code path from a remembered preference to a status
change. That separation was designed in before Memory Bank was cut, which is why
cutting it changed nothing else.

The nearest thing that did ship is `already_attempted` on the coordinator's
input: the actions tried on an obligation so far, read from the ledger. It exists
because without it the coordinator re-read the same status on every wake and the
obligation never advanced. That is memory in the only place it was actually
needed, and it comes from the append-only ledger rather than from a store that
could be poisoned.

---

## Google Chat, Calendar and Sheets — blocked by account type, not by time

The plan called for a Google Chat message landing in a space as the primary
notification. **Chat incoming webhooks require a Google Workspace account** and
this project runs on a consumer account. That is not a limitation that more time
would have solved.

`services/engine/notify.py` therefore ships three implementations behind one
interface, so the channel is configuration rather than code:

- `gmail` — real email through the Gmail API, using a refresh token the account
  owner granted once and which lives in Secret Manager. This is what runs.
- `sheets` — appends to a spreadsheet shared with the service account. Written
  and working, not configured, because email is the stronger channel here.
- `log` — records the notification and returns a reference that names itself as a
  log entry, so nothing downstream can mistake it for evidence a person was
  reached.

Calendar events were dropped for the same reason: a service account cannot write
to a consumer calendar without the calendar being shared with it first, which is
a manual step that adds a moving part to a demonstration without adding a
capability the system needs.

---

## Agent Identity — service accounts, not GEAP identities

`agentidentity.googleapis.com` is enabled on the project. The fleet runs as seven
ordinary service accounts rather than as formal Agent Identity principals.

The property that matters was achievable without it: each agent runs as a
distinct identity, IAM separates what each can reach, per-secret bindings mean
only `sentinel-ingest` and `sentinel-reid` can unwrap the tokenisation key, and
each container verifies at boot that the role it declares matches the identity it
actually holds, read from the metadata server.

Adopting the formal identity model would have added a layer whose benefit — a
richer identity envelope for cross-organisation agent calls — is not exercised by
a single-tenant system. It was not worth the risk of a half-configured identity
layer sitting under the part of the system that decides who may do what.

---

## Gemma in-boundary triage — attempted last, cut if it does not work

The plan was a small open model on Cloud Run CPU inside the trust boundary,
doing first-pass event classification so that only structured, de-identified
output reaches Gemini. It is architecturally justified and it is a genuine second
Google model rather than a decorative one.

It is scheduled last precisely because it is the kind of thing that quietly eats
an afternoon. If it is not working as a real code path in the running system, it
will be cut rather than shipped as a decoration, and this section will say so.

---

## The engine is publicly reachable

`sentinel-engine`, `sentinel-ingest`, the three agents and the board are deployed
with `--allow-unauthenticated`. Only `sentinel-reid` is closed.

This was a deliberate trade on the first day, taken so the canary could start
that evening rather than after an afternoon of auth plumbing, and it has not been
reversed. The reasoning it rests on: the public services hold no PHI, every
identifier they handle is already a token, and the one service that can reverse a
token is the one that is closed and reachable only by the board's identity.

The Pub/Sub subscription and the Cloud Scheduler job already authenticate with
OIDC tokens as `sentinel-tasks`, and `sentinel-tasks` already holds `run.invoker`
on the engine. Closing the engine is therefore a one-flag change rather than a
rewrite, and it is the first thing that should happen if this ran anywhere real.

---

## Explicitly out of scope from the start

Multi-tenancy. Login and signup. Roles beyond operator and approver. A mobile
app. Voice. A chat interface. A graph visualisation library. A hospital simulator
with hundreds of fabricated patients. An HL7 or FHIR parser beyond a single
fixture. Kubernetes.

The fixture set is twelve subjects. A board showing a hundred and twenty-eight
fabricated workflows is the fastest way to lose a technical audience: the first
row anybody clicks is hollow, and nothing on the screen is believed afterwards.
Twelve subjects that each trace end to end are worth more than any number of
plausible-looking rows.
