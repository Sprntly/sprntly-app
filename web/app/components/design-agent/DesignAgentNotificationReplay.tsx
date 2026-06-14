"use client"

/**
 * P6-05 (#8) — completion-notification replay, hoisted to the authed AppShell.
 *
 * The P5-09 ready-toast replay used to live in a mount effect inside
 * `DesignAgentDrawerView`, which only renders on the PRD's Design section. A
 * hard reload landing on Home / No-draft never mounted the drawer, so a
 * persisted-but-unacknowledged completion toast never re-showed. This component
 * mounts once in `AppShell` (inside `NavigationProvider`, on EVERY authed page),
 * so the replay fires regardless of which page the reload lands on. Renders null.
 *
 * Decision-D(b) (LOCKED 2026-06-04 — ack-on-toast-clear, ZERO
 * `NavigationContext.tsx` touch): the toast is NOT auto-acked on first show
 * (`replayCompletedNotifications` no longer calls `acknowledge`). Instead the
 * replay acks its OWN last-shown id when that toast clears. The single-slot
 * toast (`NavigationContext`) auto-hides after 5500ms; we observe the slot
 * transition non-null → null and ack ONLY when the cleared toast matches the
 * replay's last emission (`shouldAckOnClear`) — so a competing feature's toast
 * (or a newer replay show that supplanted the slot) never acks the wrong id.
 *
 * Accepted imprecision (AC11 escape clause, recorded for the P6-01 handoff):
 * the single-slot toast exposes no per-show "this toast closed" callback, so the
 * replay infers its own clear by matching the previous slot value's {title, sub}
 * against its recorded last emission. In a multi-toast page-load where a
 * non-replay toast carries an identical {title, sub} to the replay's last show
 * (both "Prototype ready" + same sub), a clear of that look-alike could ack the
 * replay's still-pending id. This is bounded (same title AND same sub) and
 * acceptable under (b); the higher-fidelity fix (a single-line append-only
 * dismiss callback on `NavigationContext` carrying the cleared toast's identity)
 * is option (a), deferred to the P6-01 handoff doc — NOT taken here.
 */

import { useEffect, useRef } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { replayCompletedNotifications } from "./DesignAgentDrawer"
import {
  acknowledge,
  getLastReplayShow,
  shouldAckOnClear,
} from "./notificationStore"

export function DesignAgentNotificationReplay() {
  const { showToast, toast } = useNavigation()

  // Replay on mount. The per-page-load guard inside `replayCompletedNotifications`
  // dedupes within the same load, so AppShell re-mounting the replay across
  // authed-route navigations does not re-fire the toast. `showToast` is a stable
  // useCallback, so this runs once per page-load.
  useEffect(() => {
    replayCompletedNotifications(showToast)
  }, [showToast])

  // Decision-D(b): ack the replay's OWN last-shown id when its toast clears.
  // Track the previous slot value; when it transitions non-null → null and the
  // cleared toast matches the replay's last emission, acknowledge that id.
  const prevToast = useRef(toast)
  useEffect(() => {
    const ackId = shouldAckOnClear(prevToast.current, toast, getLastReplayShow())
    if (ackId != null) acknowledge(ackId)
    prevToast.current = toast
  }, [toast])

  return null
}
