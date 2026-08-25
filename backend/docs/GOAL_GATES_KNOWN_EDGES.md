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

**Corrected three times. Read the history, it is the point of the entry.**

1. "A flash, not a dead end." Review reproduced it and the turn kept "No
   response was generated for this message". Too light.
2. **"PERMANENT / unreachable / must start again."** Too heavy, in the other
   direction: review did the one thing that version called impossible —
   switched tabs and came back — and the gate rebuilt. There is a green test
   named "switching away and back restores again" that says so.
3. The severity in (2) was fixed but the MECHANISM was still wrong. It said
   "the restore does not fire on that mount", which review then falsified by
   reading the effect: it fires, and the entry's own next clause — the panel
   points at a live run — is only true BECAUSE it fires.

Three passes, and the first two corrections each left something wrong. The
lesson stays in the file rather than being tidied out of it: a claim about
behaviour needs reproducing whether it is about severity or about mechanism,
and "I reasoned about the code" is how all three versions got here.

**What actually happens.** Reload during `resolving_goal` — the second or so
between sending a goal and the question appearing. The `pending` gate is
stripped on save and on load. The restore effect in `ChatScreen` DOES run: it
calls `goalAnalysisApi.list()`, finds this thread's run, and does
`setContent({ goalRunId: mine.id })`, which is why the panel opens on a live
run at all. It then stops one step short, at

```
if (mine.status !== "awaiting_confirmation"
    && mine.status !== "awaiting_approval") return
```

because a run still resolving its goal is at neither gate yet. So the panel
points at the run, and the turn keeps the no-reply line with no card to answer.

**Recovery.** Switching to another chat tab and back re-runs the effect — and
by then the run has reached `awaiting_confirmation`, so the status check passes
and the gate is rebuilt and appended. The recovery works because the RUN moved,
not because the effect started running. The run is NOT lost. The appended card
lands below the stale no-reply line, which is not replaced.

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

**Not to be confused with the panel's `pollKey`.** `GoalAnalysisTab` does have
a re-arm again — the gate poll stops itself at a ceiling, and the "Check again"
button in the error banner restarts it. That is a poll restarting inside one
component, not a marker written onto a thread turn and persisted, which is what
this section retired and what §8 is about.

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
as before. It refuses and says so instead. The goal is not started, which is
the honest outcome: a run that cannot be brought back to its own chat is worth
less than a retry.

**The window was two seconds, and that was a bet rather than a bound.** Two
seconds is a guess about how fast a conversation insert lands; a cold backend
or a slow link loses that bet and the reader is refused for a save that was
about to arrive. It is ten seconds now, the loop still exits the instant the
row appears, and both the refusal and the widening have tests
(`ChatScreen.goal-restore.dom.test.tsx`, "a chat that never saves") — the
second one lands the row at three seconds specifically so that reverting the
window to two goes red. The copy was wrong too: it told the reader to "send a
message first", which is what they had just done.

**Still not right structurally.** The wait polls `tabsRef` for a value another
code path is producing, rather than awaiting the conversation-create promise
itself. Ten seconds is a better guess than two; it is still a guess. §8 removes
the need for it.

## 7c. A second run's gate is silently dead while the first one resolves

**What happens.** `goalGateBusyTurnRef` is one ref for the whole screen, so a
confirm or approve anywhere blocks every other gate card. The busy INDICATOR is
per-turn, though, so the second card looks live: the button is enabled and
clicking it does nothing at all.

**How to reach it.** Two chats, each with a run parked at a gate, and answer
one while the other is on screen.

**Severity.** Rare and self-clearing — the lock releases as soon as the first
call returns, and the second click works. It leaves no bad state; it just
ignores a click without saying why.

**Why deferred.** The honest fix is to scope the lock to the turn it belongs
to, which means the busy state stops being a screen-level ref — the same
refactor §8 describes. Papering over it by disabling the second button would
trade a silent no-op for a control that is dead with no reason given.

## 7d. Unmounting inside the conversation wait discards the goal

**What happens.** If the panel unmounts during the wait in §7b — the reader
closes the tab or navigates — `startGoalAnalysis` returns before starting
anything, and the goal text is gone.

**Before this, the run already existed** and could be restored, so this is a
narrow regression. It is deliberate: the alternative is starting a run that
cannot find its way back to a chat nobody is looking at any more. Nothing is
stranded server-side, which is the trade — a lost sentence rather than an
unreachable run.

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
