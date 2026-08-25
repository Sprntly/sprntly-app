# Goal Analysis in-thread gates — known edges, deferred on purpose

The happy path is landed: type a goal, answer the definition question in the
chat, approve the plan in the chat, get the report in the panel.

Everything below is a KNOWN gap, found by adversarial review across six rounds
and deliberately not fixed in the landing PR. Each entry says what breaks, how
to reach it, and why it was judged safe to defer. None of them is reachable on
the path a PM actually walks; all of them are reload, multi-tab, or
mid-flight-interruption cases. The feature is allowlist-only behind
`feature_flags.crucible`, so the blast radius is the companies we name.

Sorted by what I would fix first.

---

## 1. Reloading before the first question leaves a stranded turn until you switch tabs

**Corrected twice. Read the history, it is the point of the entry.**

The first version called this "a flash, not a dead end". Review reproduced it:
the restore does not run on that path, `goalAnalysisApi.get` is called zero
times, and the turn keeps "No response was generated for this message." That
was wrong, so it was rewritten as **"PERMANENT / unreachable / must start
again"**.

That was wrong too, in the other direction. Review did the one thing the entry
called impossible — switched tabs and came back — and the gate rebuilt. There
is a green test named "switching away and back restores again" that says so.

An entry written to stop an edge being mispriced managed to misprice it twice,
in both directions. The lesson is in the file rather than tidied out of it: a
severity claim is a claim, and it needs the same reproduction as a code claim.

**What actually happens.** Reload during `resolving_goal` — the second or so
between sending a goal and the question appearing. The `pending` gate is
stripped on save and load, and the restore does not fire on that mount, so the
turn sits with the no-reply line and the panel points at a live run with no
card to answer it.

**Recovery.** Switching to another chat tab and back re-runs the restore
effect, which rebuilds the gate and appends it. The run is NOT lost. The
appended card lands below the stale no-reply line, which is not replaced.

**Severity.** Confusing and undiscoverable, not terminal. A reader who does not
happen to switch tabs will believe the run failed.

**Why deferred.** It needs a reload inside a one-second window on an
allowlist-only feature, and the recovery — though nobody would guess it — costs
one click.

**Fix direction.** §8. Two attempts to paper over this window (§7) each
produced worse bugs than the window itself.

## 2. The rebuilt gate appends instead of returning to its own turn

**What happens.** After a reload the restored card is appended as a new turn
rather than re-attached to the message that was carrying it, so it can sit a
message or two below the goal it belongs to.

**Why deferred.** Purely cosmetic. The mechanism needed to do it properly was a
per-turn marker, which generated five separate Criticals in one review round.
Appending has no state of its own to get wrong.

---

## 3. Two tabs on one conversation can both restore the same gate

**What happens.** The restore effect keys on the conversation, and two tabs can
share one. The guard scans every tab for a live gate on that run, so the second
tab usually declines — but the tab that declined still shows the panel pointing
at a chat that has no card in it.

**How to reach it.** Open the same conversation in two chat tabs with a run
sitting at a gate.

**Why deferred.** Needs two tabs on one conversation on an allowlist-only
feature. Under §8 both tabs would render the same truth from the same row.

---

## 4. A dead run's turn keeps its last live wording

**What happens.** `awaitGoalRun` gives up on unmount and after a ten-minute
ceiling, returning null. Some paths then leave whatever the turn last said.

**Why deferred.** Reachable only by leaving a run mid-gate for ten minutes with
the tab closed.

---

## 5. A reload before the run row exists loses the message

**What happens.** If the page dies between pressing send and the start POST
returning, there is no run — the turn is emitted, then nothing.

**Why deferred.** Correct behaviour, arguably: there is no run to restore and
the user retypes one sentence. Explicitly accepted rather than fixed.

---

## 6. (retired) — the re-arm path this described no longer exists

Kept as a numbered heading so the section numbers in commit messages and review
comments still line up. `goalGateResolved: undefined` was deleted along with
the whole re-arm marker; there is no such write anywhere now.

## 7. Two fixes that were tried and reverted — do not retry as written

Recorded so the next person does not re-derive them.

* **A per-turn re-arm marker.** As a boolean it captured whichever run restored
  next, rendering one goal's question under another goal's message. Keyed to the
  run it was empty at the moment it mattered, because the pending gate is
  emitted BEFORE the start POST returns the id. Cleared on one exit out of
  several, it orphaned permanently blank turns into sessionStorage.
* **Stamping a thawed turn as `failed` with "interrupted, reopening it…".**
  Cleared for only two of the run's nine statuses, so a perfectly healthy run
  reported itself stopped, permanently, in storage.

Both were attempts to hide §1. Neither is worth repeating against the current
shape; both disappear under §8.

---

## 7b. A goal as the FIRST message of a brand-new chat — FIXED, with a residual

Listed because this file missed it and review found it: the run was started
before the conversation row existed, so it carried no `conversation_id` and was
orphaned from its own chat forever — the restore matches runs by conversation,
so it could never return to the thread it came from. Now the start waits
briefly for the tab's `dbConvId`. Recorded rather than quietly fixed, because
"the doc listed every known edge" was itself one of the claims that was wrong.

**RESIDUAL, stated because "FIXED" on its own was another over-claim.** The
wait can fail: if the conversation row never arrives — create failed, offline —
the run would have started with no `conversation_id` anyway, orphaned exactly
as before, after a two-second stall nobody could explain. It now refuses and
says so instead. The goal is not started, which is the honest outcome: a run
that cannot be brought back to its own chat is worth less than a retry.

## 8. The structural fix, when this is worth doing properly

The server owns a nine-status run lifecycle. The client currently shadows it as
three optional sibling fields on the thread turn (`goalGate`,
`goalGateResolved`, `goalGateError`), written from many places and read by
several predicates over different subsets. Most representable combinations are
illegal, and the rules keeping them consistent live only in comments — which is
why repairs to one cell kept breaking another.

**Persist one field: the run id.** Derive the gate, the settled record and the
error from the server row through a single total function. The turn stops
carrying state that can disagree with the run, and §1, §2, §3, §4 and §6 stop
being separate problems.
