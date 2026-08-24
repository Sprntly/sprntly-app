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

## 1. A reloaded turn briefly reads "No response was generated"

**What happens.** A `pending` gate is stripped from the thread on save and on
load (`_thawThread`), because the poll that would replace it died with the page.
That leaves the reader's own message with no card under it until the restore
appends a rebuilt one, and in that window the ordinary no-reply ladder renders
"No response was generated for this message."

**How to reach it.** Reload while a run is still `resolving_goal` — the second
or so between sending a goal and the definition question appearing.

**Why deferred.** It is a flash, not a dead end: the gate appears immediately
after. Two attempts to suppress it (a marker on the turn, then a synthetic
"interrupted" record) each produced worse bugs than the flash — see §7.

**Fix direction.** Do not add a field to the turn. Derive the whole gate from
the server row (§8) and the window disappears with the shadow state.

---

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

## 6. `goalGateResolved: undefined` in the re-arm path

**What happens.** The restore clears a settled record when it attaches a rebuilt
gate. Review could not construct a path where that destroys a real record, but
nothing prevents it either — it is an undocumented invariant, not a live bug.

---

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
