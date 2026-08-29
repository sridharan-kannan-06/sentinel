# Building an agent that reacts to the absence of events

*I created this piece of content for the purposes of entering the All Things
Agentic Hackathon.*

---

Every agent I have seen this month reacts to events. Something happens, the agent
wakes up, the agent does something. That is the shape of almost every agent
tutorial, and it is the shape of almost every agent product.

Hospitals do not fail that way. They fail when nothing happens.

A critical potassium result comes back at 14:40 and the ordering clinician is in
theatre. An insurer asks for one document and the request sits in a shared inbox.
A discharge waits on a pharmacy sign-off that nobody chased. In every case the
system worked, the message was delivered, and the work still did not happen.

You cannot subscribe to *nothing happened*. That turns out to be an engineering
problem with a surprising amount in it, and it is what I spent the hackathon on.

---

## The naive version, and why it loses obligations

The obvious design is a timer per task. Something is due at 16:00, set a timer for
16:00, wake up and check.

I built that first. It is wrong in a specific way: **a dropped timer is
indistinguishable from a task that was never due.** Cloud Tasks is reliable, but
"reliable" is not "guaranteed", and the failure is silent. Nothing errors. The
obligation simply sits there, and the entire premise of the system — that nothing
gets forgotten — quietly stops being true for that one row.

What fixes it is inverting which thing is authoritative. The obligation record
carries its own next checkpoint. A sweep runs every fifteen minutes, queries for
anything whose checkpoint is in the past and has not fired, and re-arms it.

```python
for obligation in ledger.overdue_checkpoints(now):
    checkpoint = obligation.next_checkpoint
    attempt = ledger.count_entries(obligation.id, "sweep.repaired") + 1
    timers.schedule_checkpoint(obligation.id, checkpoint, attempt=attempt)
```

The timer became disposable. Losing one costs fifteen minutes instead of costing
the obligation.

I tested this the only way that means anything: I deleted a live Cloud Task on
purpose and watched what happened. The audit trail:

```
02:41:34  obligation.created
02:43:07  sweep.repaired      timer was lost; the obligation was not
02:43:07  notification.sent
02:43:07  checkpoint.nudge    → AT_RISK
```

Measured over twelve obligations with real timers and real waiting: **100%
detected, median 5.6 seconds after the deadline.**

---

## The part where the model is not allowed to decide

The second problem is more interesting, because it is about what a language model
is for.

An agent chasing a task eventually wants to close it. Something arrives that looks
like completion, and the agent says: done. And it is *usually* right.

Usually right is a catastrophic property for the step that decides whether a
hospital obligation is finished. A false close is not an error message. It is a
row that disappears from the board and a piece of work that nobody will ever look
at again.

So closure does not go through the model. It goes through five deterministic
checks:

1. the source is on the authoritative list **for this obligation type**
2. an external reference id is present
3. the timestamp is inside the acceptance window and after the obligation opened
4. the subject token matches the obligation's subject
5. the assertion describes the required evidence

No model call anywhere inside it. And `CLOSED` has exactly one legal predecessor
in the state machine, so there is no path around it:

```python
def assert_closed_is_gated() -> None:
    predecessors = {src for src, dst in LEGAL_TRANSITIONS.items() if CLOSED in dst}
    if predecessors != {PENDING_EVIDENCE}:
        raise AssertionError(...)
```

That runs at import. If anyone ever adds an edge that makes `CLOSED` reachable
another way, the service does not start.

### So how wrong is the model, actually?

I wanted a number rather than an intuition, so I built a corpus: thirty pieces of
evidence across five obligation types, twenty-five of which should not close
anything. Then I ran both arms over identical input — the deterministic gate, and
`gemini-3.5-flash` asked "is this obligation discharged?"

| | Evidence Gate | Model judgement |
|---|---|---|
| **False closure rate** | **0.0%** | **16.0%** |
| True closure rate | 100% | 100% |

I had expected something more dramatic. Sixteen percent is *good*. The model
caught every case where the evidence named the wrong patient, and every case where
the assertion plainly described something else.

The four it let through are the interesting part. They were all the same kind of
judgement:

- a real record from a real system, describing work done *before this obligation
  existed*
- evidence from a source that is authoritative in general but not **for this
  obligation type**
- "confirmed with the department over the phone" — which reads exactly like
  completion and leaves nothing anybody can audit

These are not reading-comprehension failures. They are failures of a judgement
that needs a *rule*: a lookup table of which sources count for which obligation
type, and an arithmetic comparison of two timestamps. The model had nothing to
reason from, so it reasoned from plausibility, and plausibility is what an
attacker and a mistake both look like.

Both arms closed 100% of what genuinely qualified, so the gate is not just being
conservative. It is being *specific*.

---

## The finding I did not expect

I built a prompt-injection fixture: a realistic third-party-administrator letter
about a claim, with an instruction buried in the middle telling any automated
agent to mark the claim closed and record a fake approval reference.

Model Armor is Google's inline guardrail for exactly this. I expected to
demonstrate it catching the injection. Instead:

| What was screened | Length | Verdict |
|---|---|---|
| The injected paragraph on its own | 320 chars | **blocked** |
| A short bare injection | 139 chars | **blocked** |
| The identical paragraph inside the full letter | 1318 chars | **not blocked** |

The injection is byte-identical across those rows. The only thing that changed is
how much ordinary correspondence surrounds it.

**Prompt-injection detection is diluted by legitimate context.** I do not think
this is a bug so much as a property of any classifier scoring a whole document:
one hostile paragraph in a page of genuine business correspondence does not move
the aggregate much.

The mitigation is straightforward once you know: screen long documents whole *and*
again in overlapping windows.

```python
WINDOW_CHARS = 300
WINDOW_OVERLAP = 150
```

At 300 characters per window it is caught, in window 6 of 9. At 400 and at 600 it
is missed. The overlap matters too — an injection straddling a boundary would
otherwise be split into two harmless halves.

But here is the part that actually matters. **A mitigation you discovered by
accident is not a defence you should rely on.** An injection spread thinly enough
would still get through, and I have no way to know how thin is thin enough.

So I tested what happens when the filter fails. I fed the injection straight to
the interpreter with screening bypassed entirely.

It produced an ordinary pre-authorisation obligation, requiring the consultant's
justification note — the document the letter was *genuinely* asking for. It
ignored the instruction and kept chasing the real work.

That is not the model being clever. It is the model having nothing to work with:

- the interpreter is constructed with `tools=[]`
- "close" is not in its vocabulary; it can propose five obligation types and
  nothing else
- closure is not an action any agent can request
- `CLOSED` has one legal predecessor

The injection asked it to do something it had no way to express.

---

## The one that nearly broke a whole workflow

A smaller finding, and my favourite, because it is so easy to miss.

Obligations carry a risk tier. T0 and T1 are automatic, T2 needs human approval,
T3 is denied always — writing to a clinical record, expressing a clinical
opinion. I let the model propose the tier.

Asked to tier a critical potassium result, `gemini-3.5-flash` returned **T3**.

It is not wrong, exactly. A critical potassium result *is* the most serious thing
in the message. But the tier is not about how serious the situation is. It is
about what authority *the system* needs to do something about it — and all this
system was going to do was send a message to a clinician.

T3 is a permanent denial with no approval path. That one word would have made the
entire critical-lab workflow undischargeable, and it would have failed as
"working as designed" rather than as an error.

Risk tier is now derived in code from the obligation type. The model's suggestion
is kept, displayed, and ignored.

The same pattern showed up three separate times:

- the model proposes an SLA in hours; **code computes the dates**
- the model proposes a route; **code decides if it is permitted**
- the model proposes closure; **code decides if it happened**

Which is, I think, the actual lesson. The useful question is not "how do I stop
the model hallucinating". It is **"what is this decision made of?"** If it is made
of judgement about messy language, the model is genuinely better than the code you
would write. If it is made of a lookup table and a timestamp comparison, the model
is guessing at something you could simply *know* — and it will guess well enough,
often enough, to hide the problem until it matters.

---

## What I would do differently

I did not talk to a hospital. The plan called for six questions to working
clinical staff about what actually gets forgotten, and it did not happen inside
the build window. So the three workflows here are modelled on documented failure
modes rather than on anything a person said, and that is the weakest part of the
project. Every architectural decision I am pleased with is downstream of a problem
I inferred rather than one I was told about.

Everything above is measured against a running system on Google Cloud rather than
asserted — the harness is in the repository and the numbers regenerate with one
command.

---

*Sentinel is an autonomous continuity layer for hospital operations, built solo on
Gemini 3.5 Flash, Google ADK, and Google Cloud. Repository and full evaluation
method in the links below.*
