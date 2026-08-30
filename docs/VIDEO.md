# Recording the demo

Keep this open on a second screen while you record. Every command is one line.
Nothing here needs anything typed by hand except the commands themselves.

---

## Before you start

Open two windows side by side.

**Left: a terminal.** Open PowerShell and type:

```
cd C:\dev\sentinel
```

**Right: a browser** at this address:

```
https://sentinel-web-sqr3nw6srq-uc.a.run.app/
```

Use that address, not the other one. Your home network cannot look up the
`us-central1` version. Both are the same site.

**Warm it up.** Load the page once, a minute before you start recording. The
first load after a quiet period takes about ten seconds because the server was
asleep. After that it is instant. You do not want that pause on camera.

Also open, in other browser tabs, ready to switch to:

- your Gmail inbox
- `https://console.cloud.google.com/run?project=sentinel-506512`

---

## What is on the screen

The board shows **one row**. That row is real. It has been open since
24 August and the system has been chasing it, on its own, ever since.

Across the top is a strip of grey labels. That is the trust panel. It shows
which rulebook is loaded, that the Evidence Gate is **ON**, and that the model
is `gemini-3.5-flash`, marked *proposes only, never commits*.

**Click the row.** You get the detail page. Three things to point at:

1. **"Why is this stuck"** near the top, in plain English.
2. **The timeline** down the left. Real dates. Look at the gaps between them —
   26 August, then 28 August. Nobody was at a keyboard on those days.
3. **"What the model saw"** on the right: `PT-a94f`. A token, not a name.

The button top right says **"Show everything the agent did and why"**. Click it
and the timeline expands to the full record.

---

## The four beats

### Beat 1 — it has been working while nobody watched

Stay on the detail page. Point at the timeline:

```
08-24 14:42   obligation created
08-26 05:30   nudge sent
08-28 05:30   breach sent
```

Say: *this obligation has been open for six days. The system woke up twice on
its own and sent two emails. No clock was simulated and nothing was
fast-forwarded.*

Then **switch to your Gmail tab** and show the two emails. They arrived on the
26th and the 28th. That is the whole argument in one screen.

### Beat 2 — it refuses to close without proof

In the terminal:

```
.venv\Scripts\python.exe infra\demo_evidence.py
```

It prints four sections, slowly, on its own. You do not have to do anything.

- An agent says the work is done and has nothing to show. **Refused.**
- A real record from the real lab system, but for **the wrong patient**.
  **Refused** — and only one of the five checks fails, which is the point.
- The real record for the right patient. **Closed.**

Say: *no model was asked any of those times. Five rules decided.*

### Beat 3 — someone tries to talk it into closing

```
.venv\Scripts\python.exe infra\demo_injection.py
```

It shows an insurer letter, then the instruction hidden inside it telling the
agent to close the claim. Then it shows the letter being blocked.

The strong part is the last section. It removes the guard entirely and feeds the
instruction straight to the model. The model **ignores it** and asks for the
document the letter was genuinely chasing.

Say: *the filter is not what protects this. The agent has no tools and no way to
say "closed". It could not obey the instruction even with nothing in the way.*

### Beat 4 — it is really on Google Cloud

Switch to the Cloud Run tab. Eight services, all green. Point at the address bar
so the `.run.app` URL is visible.

The rules require you to show this. Do not skip it.

---

## If something goes wrong while recording

**The page takes ages to load.** The server went to sleep. Load it once and wait,
then start again.

**A command prints a wall of red.** Check your internet. Everything talks to
Google Cloud.

**You want to run a demo twice.** You can. Each run makes a fresh obligation, so
the board will show extra rows.

**Tidy up afterwards:**

```
.venv\Scripts\python.exe infra\cleanup_test_data.py --apply
```

That removes everything the demos made and leaves the six-day obligation alone.
Run it before your final take so the board is clean.

---

## Timing

Four minutes total. Roughly:

| | |
|---|---|
| The problem, in your own words | 0:00 - 0:30 |
| Beat 1, the board and the two emails | 0:30 - 1:30 |
| Beat 2, the Evidence Gate | 1:30 - 2:20 |
| Beat 3, the injection | 2:20 - 3:10 |
| Beat 4, Cloud Run | 3:10 - 3:30 |
| "Built solo in eight days" | 3:30 - 4:00 |

Record it more than once. The second take is always better than the first.
