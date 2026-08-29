# Social post

For LinkedIn or X. The hashtag is required for the bonus; the rest is written to
be worth reading on its own rather than to be an announcement.

---

## LinkedIn

Every AI agent I've seen this month reacts to events. Hospitals don't fail that
way. They fail when nothing happens.

A critical lab result comes back and the ordering clinician is in theatre. An
insurer asks for one document and the request sits in a shared inbox. The system
worked, the message was delivered, and the work still didn't happen.

You cannot subscribe to "nothing happened." So I spent the last week building an
agent that reacts to the absence of events, for the All Things Agentic Hackathon.

Three things I measured that surprised me:

**A dropped timer is indistinguishable from a task that was never due.** Nothing
errors. The obligation just quietly stops being tracked. The fix was inverting
which thing is authoritative — the obligation record carries its own next
checkpoint, and a sweep re-derives it every fifteen minutes. Losing a timer now
costs fifteen minutes instead of costing the obligation. I tested it by deleting a
live task on purpose.

**Asked "is this obligation complete?", Gemini was wrong 16% of the time. A
deterministic gate was wrong 0%.** Both accepted 100% of genuinely valid evidence,
so the gate wasn't just being conservative. The model's failures were all one
kind: whether a source counts *for that specific obligation type*, and whether a
timestamp falls inside a window. Judgements made of a lookup table and a
subtraction, where the model had nothing to reason from — so it reasoned from
plausibility.

**Prompt-injection detection is diluted by legitimate text.** An injected
paragraph on its own: blocked. The byte-identical paragraph inside a plausible
one-page insurer letter: not blocked. Only the surrounding context changed. The
mitigation is screening in overlapping windows — but a mitigation you find by
accident isn't a defence you should rely on, so I tested what happens when the
filter fails. The agent ignored the injected instruction and kept chasing the real
missing document, because it had no tools and no vocabulary for "close this". The
defence that held was structural, not filtering.

The through-line, for me: the useful question isn't "how do I stop the model
hallucinating." It's **what is this decision actually made of?** If it's judgement
about messy language, the model beats anything I'd write. If it's a lookup table
and a timestamp comparison, the model is guessing at something I could simply
know — and it'll guess well enough, often enough, to hide the problem until it
matters.

Built solo on Gemini 3.5 Flash, Google ADK and Google Cloud. Every number above
came off a running system, and the harness that produces them is in the repo.

#AllThingsAgenticHackathon

---

## X / shorter version

Every agent reacts to events. Hospitals fail when *nothing* happens.

Spent the week building one that reacts to absence.

Measured, and surprised by all three:

→ A dropped timer is indistinguishable from a task that was never due. Nothing
errors. The fix was making the obligation authoritative and the timer disposable.

→ Asked "is this complete?" Gemini was wrong 16% of the time. A deterministic gate:
0%. Both accepted 100% of valid evidence — so the gate was specific, not just
strict.

→ Prompt injection detection is diluted by legitimate text. Same paragraph:
blocked alone, missed inside a full letter.

The lesson isn't "stop the model hallucinating". It's *what is this decision made
of?* Judgement about messy language → use the model. A lookup table and a
timestamp comparison → the model is guessing at something you could know.

Built solo on Gemini 3.5 Flash + ADK + Google Cloud.

#AllThingsAgenticHackathon
