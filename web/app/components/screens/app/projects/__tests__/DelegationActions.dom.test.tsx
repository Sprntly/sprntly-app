// @vitest-environment jsdom
//
// DelegationActions — the shared party/state-aware action affordance for the
// SIMPLIFIED state model (no approve/reject; the agent owns follow-through
// once assigned; `cleared` is the assigner's one terminal kill switch).
// Proves AC1/AC2 (only party- and state-appropriate buttons ever render),
// AC3 (no decline-note form exists anymore), AC4 (every `LEGAL_ACTIONS`
// entry is a legal server edge), and AC5 (terminal statuses render null).
// `test_legal_actions_are_server_legal_edges` pins the client `LEGAL_ACTIONS`
// mirror against the server graph
// (`backend/app/db/delegation_events.py`'s `TRANSITIONS`/`EVENT_PARTY`),
// transcribed here as the reference edge list.
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
  assigned: ["in_progress", "completed", "cleared"],
  in_progress: ["completed", "cleared"],
  completed: [],
  cleared: [],
}
const EVENT_PARTY: Record<string, ViewerParty> = {
  in_progress: "assignee",
  completed: "assignee",
  cleared: "assigner",
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

describe("DelegationActions — assignee visibility (AC1)", () => {
  it("test_assignee_actions_progress_and_done_only — Mark in progress + Mark done for `assigned`; Mark done only for `in_progress`; no Accept/Decline ever", () => {
    expect(new Set(events("assignee", "assigned"))).toEqual(new Set(["in_progress", "completed"]))
    cleanup()
    expect(new Set(events("assignee", "in_progress"))).toEqual(new Set(["completed"]))

    for (const status of ["assigned", "in_progress", "completed", "cleared"]) {
      cleanup()
      const shown = events("assignee", status)
      expect(shown).not.toContain("accepted")
      expect(shown).not.toContain("declined")
    }
  })
})

describe("DelegationActions — assigner visibility (AC2)", () => {
  it("test_assigner_action_clear_only — Clear task for `assigned`/`in_progress`; no Cancel/Reopen ever", () => {
    for (const status of ["assigned", "in_progress"]) {
      cleanup()
      expect(events("assigner", status)).toEqual(["cleared"])
    }
    for (const status of ["assigned", "in_progress", "completed", "cleared"]) {
      cleanup()
      const shown = events("assigner", status)
      expect(shown).not.toContain("cancelled")
      expect(shown).not.toContain("reopened")
    }
  })
})

describe("DelegationActions — no decline-note form (AC3)", () => {
  it("test_no_decline_note_form — clicking any rendered action never mounts delegation-decline-form", () => {
    for (const [party, status] of [
      ["assignee", "assigned"],
      ["assignee", "in_progress"],
      ["assigner", "assigned"],
      ["assigner", "in_progress"],
    ] as [ViewerParty, string][]) {
      cleanup()
      const onEmit = vi.fn()
      render(
        React.createElement(DelegationActions, {
          delegationId: 2,
          status,
          viewerParty: party,
          onEmit,
        }),
      )
      for (const btn of screen.queryAllByTestId(/^delegation-action-/)) {
        fireEvent.click(btn)
      }
      expect(screen.queryByTestId("delegation-decline-form")).toBeNull()
    }
  })
})

describe("DelegationActions — client map mirrors the server graph (AC4)", () => {
  it("test_legal_actions_are_server_legal_edges — every client button is a legal, party-appropriate server edge", () => {
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

describe("DelegationActions — terminal statuses render nothing (AC5)", () => {
  it("test_terminal_status_renders_null — completed and cleared render nothing for both parties", () => {
    for (const party of ["assignee", "assigner"] as ViewerParty[]) {
      for (const status of ["completed", "cleared"]) {
        cleanup()
        const { container } = render(
          React.createElement(DelegationActions, {
            delegationId: 4,
            status,
            viewerParty: party,
            onEmit: vi.fn(),
          }),
        )
        expect(container.firstChild).toBeNull()
      }
    }
  })

  it("degrades a legacy (pre-simplification) status to no actions rather than crashing", () => {
    for (const party of ["assignee", "assigner"] as ViewerParty[]) {
      for (const status of ["accepted", "declined", "cancelled", "reopened"]) {
        cleanup()
        expect(() =>
          render(
            React.createElement(DelegationActions, {
              delegationId: 5,
              status,
              viewerParty: party,
              onEmit: vi.fn(),
            }),
          ),
        ).not.toThrow()
        expect(screen.queryAllByTestId(/^delegation-action-/)).toEqual([])
      }
    }
  })
})

describe("DelegationActions — emit wiring", () => {
  it("test_action_click_calls_emit — a button calls onEmit(event) with the right event, no note", () => {
    const onEmit = vi.fn()
    render(
      React.createElement(DelegationActions, {
        delegationId: 3,
        status: "assigned",
        viewerParty: "assignee",
        onEmit,
      }),
    )
    fireEvent.click(screen.getByTestId("delegation-action-in_progress"))
    expect(onEmit).toHaveBeenCalledWith("in_progress")
  })

  it("the assigner's Clear task button emits `cleared`", () => {
    const onEmit = vi.fn()
    render(
      React.createElement(DelegationActions, {
        delegationId: 3,
        status: "assigned",
        viewerParty: "assigner",
        onEmit,
      }),
    )
    fireEvent.click(screen.getByTestId("delegation-action-cleared"))
    expect(onEmit).toHaveBeenCalledWith("cleared")
  })
})
