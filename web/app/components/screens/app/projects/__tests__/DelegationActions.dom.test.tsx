// @vitest-environment jsdom
//
// DelegationActions — the shared party/state-aware action affordance. Proves
// AC-3 (only party- and state-appropriate buttons, NEVER an illegal-edge or
// wrong-party button) and AC-4 (a click emits the right event; Decline passes
// a note). `test_legal_actions_map_matches_transition_edges` pins the client
// `LEGAL_ACTIONS` mirror against the server graph
// (`backend/app/db/delegation_events.py`'s `TRANSITIONS`/`EVENT_PARTY`),
// transcribed here as the reference edge list — every client button must be a
// legal server edge emittable by that party.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { DelegationActions, LEGAL_ACTIONS, type ViewerParty } from "../DelegationActions"

afterEach(() => cleanup())

// ── Reference: the server transition graph (verbatim) ─────────────────────
// backend/app/db/delegation_events.py — the sole authority. The client map is
// a SUBSET of these edges (never a superset), filtered to the party.
const TRANSITIONS: Record<string, string[]> = {
  assigned: ["accepted", "in_progress", "declined", "cancelled"],
  accepted: ["in_progress", "completed", "declined", "cancelled"],
  in_progress: ["completed", "declined", "cancelled"],
  completed: ["reopened"],
  declined: ["reopened", "cancelled"],
  cancelled: ["reopened"],
  reopened: ["accepted", "in_progress", "declined", "cancelled"],
}
const EVENT_PARTY: Record<string, ViewerParty> = {
  accepted: "assignee",
  in_progress: "assignee",
  completed: "assignee",
  declined: "assignee",
  cancelled: "assigner",
  reopened: "assigner",
}

function events(party: ViewerParty, status: string): string[] {
  render(
    React.createElement(DelegationActions, {
      delegationId: 1,
      status,
      viewerParty: party,
      onEmit: vi.fn(),
    }),
  )
  const btns = screen.queryAllByTestId(/^delegation-action-/)
  return btns.map((b) => (b.getAttribute("data-testid") ?? "").replace("delegation-action-", ""))
}

describe("DelegationActions — assignee visibility (AC3)", () => {
  it("test_assignee_assigned_shows_accept_decline_only — Accept + Decline; no Mark done/Cancel/Reopen", () => {
    expect(new Set(events("assignee", "assigned"))).toEqual(new Set(["accepted", "declined"]))
  })

  it("test_assignee_accepted_shows_markdone_decline — Mark done + Decline (+ In progress)", () => {
    expect(new Set(events("assignee", "accepted"))).toEqual(new Set(["in_progress", "completed", "declined"]))
  })

  it("test_assignee_in_progress_shows_markdone_decline — Mark done + Decline, no In progress edge", () => {
    expect(new Set(events("assignee", "in_progress"))).toEqual(new Set(["completed", "declined"]))
  })

  it("test_assignee_closed_shows_no_buttons — completed/declined/cancelled → none", () => {
    for (const status of ["completed", "declined", "cancelled"]) {
      cleanup()
      expect(events("assignee", status)).toEqual([])
    }
  })
})

describe("DelegationActions — assigner visibility (AC3)", () => {
  it("test_assigner_open_shows_cancel — an assigner on any open state sees Cancel only", () => {
    for (const status of ["assigned", "accepted", "in_progress", "reopened"]) {
      cleanup()
      expect(events("assigner", status)).toEqual(["cancelled"])
    }
  })

  it("test_assigner_closed_shows_reopen — completed/cancelled → Reopen", () => {
    for (const status of ["completed", "cancelled"]) {
      cleanup()
      expect(events("assigner", status)).toEqual(["reopened"])
    }
  })

  it("test_assigner_declined_shows_reopen_and_cancel — declined → Reopen + Cancel", () => {
    expect(new Set(events("assigner", "declined"))).toEqual(new Set(["reopened", "cancelled"]))
  })
})

describe("DelegationActions — client map mirrors the server graph (AC3)", () => {
  it("test_legal_actions_map_matches_transition_edges — every client button is a legal, party-appropriate server edge", () => {
    let checked = 0
    for (const party of ["assignee", "assigner"] as ViewerParty[]) {
      for (const [status, actions] of Object.entries(LEGAL_ACTIONS[party])) {
        for (const action of actions) {
          // Legal edge from this status per the server graph…
          expect(TRANSITIONS[status]).toContain(action.event)
          // …and emittable by THIS party per the server's EVENT_PARTY map.
          expect(EVENT_PARTY[action.event]).toBe(party)
          checked += 1
        }
      }
    }
    // Guard against a silently-empty map passing vacuously.
    expect(checked).toBeGreaterThan(0)
  })

  it("never renders a wrong-party or illegal-edge button for any (party, status) pair", () => {
    for (const party of ["assignee", "assigner"] as ViewerParty[]) {
      for (const status of Object.keys(TRANSITIONS)) {
        cleanup()
        for (const event of events(party, status)) {
          expect(TRANSITIONS[status]).toContain(event)
          expect(EVENT_PARTY[event]).toBe(party)
        }
      }
    }
  })
})

describe("DelegationActions — emit wiring (AC4)", () => {
  it("test_action_click_calls_emit — a button calls onEmit(event) with the right event", () => {
    const onEmit = vi.fn()
    render(
      React.createElement(DelegationActions, {
        delegationId: 3,
        status: "assigned",
        viewerParty: "assignee",
        onEmit,
      }),
    )
    fireEvent.click(screen.getByTestId("delegation-action-accepted"))
    expect(onEmit).toHaveBeenCalledWith("accepted")
  })

  it("test_decline_passes_note — Decline reveals the input and onEmit('declined', note) carries the note", () => {
    const onEmit = vi.fn()
    render(
      React.createElement(DelegationActions, {
        delegationId: 3,
        status: "assigned",
        viewerParty: "assignee",
        onEmit,
      }),
    )
    // Clicking Decline reveals the note input rather than emitting immediately.
    fireEvent.click(screen.getByTestId("delegation-action-declined"))
    expect(onEmit).not.toHaveBeenCalled()
    const note = screen.getByTestId("delegation-decline-note")
    fireEvent.change(note, { target: { value: "wrong team" } })
    fireEvent.click(screen.getByTestId("delegation-decline-confirm"))
    expect(onEmit).toHaveBeenCalledWith("declined", "wrong team")
  })

  it("Decline with an empty note passes undefined (optional note)", () => {
    const onEmit = vi.fn()
    render(
      React.createElement(DelegationActions, {
        delegationId: 3,
        status: "assigned",
        viewerParty: "assignee",
        onEmit,
      }),
    )
    fireEvent.click(screen.getByTestId("delegation-action-declined"))
    fireEvent.click(screen.getByTestId("delegation-decline-confirm"))
    expect(onEmit).toHaveBeenCalledWith("declined", undefined)
  })
})
