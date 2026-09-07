// @vitest-environment jsdom
//
// Container mount test for onboarding step 03 — "Invite your team", REINSTATED
// 2026-09-07 after being cut from the flow on 2026-09-03 (folded into
// Settings → Team & roles for that gap — see teamApi.test.ts for coverage of
// the pure parsePastedEmails / parseInvitesCsv helpers, which live in
// lib/teamApi.ts and are NOT re-tested here to avoid duplicate coverage).
//
// Rows of email + JOB role (JOB_ROLE_OPTIONS) + permission (member/admin/
// viewer), an "Add teammate" appender, and a CSV import + bulk paste behind
// the "Add multiple people at once" disclosure. Invites send best-effort on
// Continue via teamApi.invite(email, permission, [], jobRole), then the step
// advances to `review` and routes there; Skip advances without inviting.
//
// Matchers: native DOM only.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceStepMock = vi.fn()
const inviteMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
}))
// Only teamApi.invite is stubbed — parsePastedEmails/parseInvitesCsv are the
// real implementations, since the component itself relies on them for the
// bulk paste / CSV interaction test below.
vi.mock("../../../../lib/teamApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../lib/teamApi")>()
  return {
    ...actual,
    teamApi: { ...actual.teamApi, invite: (...a: unknown[]) => inviteMock(...a) },
  }
})
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))
// The mount effect kicks the review step's business-context draft prefetch in
// the background — stub it so no real API call fires from these tests.
const prefetchDraftMock = vi.fn((..._a: unknown[]) => Promise.resolve("drafted"))
vi.mock("../../../../lib/onboarding/draftPrefetch", () => ({
  prefetchBusinessContextDraft: (...a: unknown[]) => prefetchDraftMock(...a),
}))

import { InviteStep } from "../InviteStep"
import { JOB_ROLE_OPTIONS, stepForSlug } from "../../../../lib/onboarding/types"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

function mount(workspace = makeWorkspace({ onboarding_step: 3 })) {
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
  return render(React.createElement(InviteStep))
}

function emailInput(row = 1): HTMLInputElement {
  return document.querySelector(
    `input[aria-label="Teammate ${row} email"]`,
  ) as HTMLInputElement
}

function roleSelect(row = 1): HTMLSelectElement {
  return document.querySelector(
    `select[aria-label="Teammate ${row} role"]`,
  ) as HTMLSelectElement
}

function permissionSelect(row = 1): HTMLSelectElement {
  return document.querySelector(
    `select[aria-label="Teammate ${row} permission"]`,
  ) as HTMLSelectElement
}

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /^next$/i.test((b.textContent ?? "").trim()),
  ) as HTMLButtonElement
}

function skipBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find(
    (b) => (b.textContent ?? "").trim() === "Skip",
  ) as HTMLButtonElement
}

function addTeammateBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find(
    (b) => (b.textContent ?? "").trim() === "Add teammate",
  ) as HTMLButtonElement
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("InviteStep (onboarding step 3 — email + job role + permission rows)", () => {
  it("renders one starter row: email input, JOB_ROLE_OPTIONS role select, permission select", () => {
    const { container } = mount()
    expect(screen.getByText(/Invite your/)).not.toBeNull()
    expect(emailInput()).not.toBeNull()
    const role = roleSelect()
    expect(role).not.toBeNull()
    expect(Array.from(role.options).map((o) => o.value)).toEqual([...JOB_ROLE_OPTIONS])
    expect(role.value).toBe(JOB_ROLE_OPTIONS[0])
    const perm = permissionSelect()
    expect(perm).not.toBeNull()
    expect(Array.from(perm.options).map((o) => o.value)).toEqual([
      "member",
      "admin",
      "viewer",
    ])
    expect(perm.value).toBe("member")
    // CSV import + bulk paste moved behind the "Add multiple people at once"
    // disclosure — the default card is one clean row plus "Add teammate", so
    // the hidden file input isn't mounted until the disclosure opens.
    expect(screen.queryByText(/Import CSV/)).toBeNull()
    expect(
      container.querySelector('input[aria-label="Import teammates CSV"]'),
    ).toBeNull()
    expect(screen.getByText(/Add multiple people at once/)).not.toBeNull()

    fireEvent.click(screen.getByText(/Add multiple people at once/))
    expect(screen.getByText(/Import CSV/)).not.toBeNull()
    expect(
      container.querySelector('input[aria-label="Import teammates CSV"]'),
    ).not.toBeNull()
  })

  it("'Add teammate' appends another row", () => {
    mount()
    expect(emailInput(2)).toBeNull()
    fireEvent.click(addTeammateBtn())
    expect(emailInput(2)).not.toBeNull()
  })

  it("marks the chrome as step 3 of the numbered flow (stepForSlug, not a literal)", () => {
    const { container } = mount()
    expect(
      (container.querySelector(".onb-dots") as HTMLElement).getAttribute("data-step"),
    ).toBe(String(stepForSlug("invite")))
  })

  it("Continue sends each valid row via teamApi.invite (with jobRole), advances to review and routes there", async () => {
    inviteMock.mockResolvedValue({ id: "inv-1" })
    advanceStepMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: stepForSlug("review") ?? 4 }),
    )
    mount()

    fireEvent.change(emailInput(), { target: { value: "Teammate@Acme.com" } })
    fireEvent.change(roleSelect(), { target: { value: "Engineer" } })
    fireEvent.change(permissionSelect(), { target: { value: "admin" } })
    // A second, empty row must NOT produce an invite.
    fireEvent.click(addTeammateBtn())

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/review")
    })
    expect(inviteMock).toHaveBeenCalledTimes(1)
    expect(inviteMock).toHaveBeenCalledWith(
      "teammate@acme.com",
      "admin",
      [],
      "Engineer",
    )
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", stepForSlug("review"))
  })

  it("a failed invite is best-effort: a notice shows but the step still advances", async () => {
    inviteMock.mockRejectedValue(new Error("boom"))
    advanceStepMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: stepForSlug("review") ?? 4 }),
    )
    mount()

    fireEvent.change(emailInput(), { target: { value: "teammate@acme.com" } })
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/review")
    })
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", stepForSlug("review"))
    expect(screen.getByText(/Couldn't invite teammate@acme\.com/)).not.toBeNull()
  })

  it("Skip advances to review and routes there WITHOUT sending invites", async () => {
    advanceStepMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: stepForSlug("review") ?? 4 }),
    )
    mount()

    fireEvent.change(emailInput(), { target: { value: "teammate@acme.com" } })
    await act(async () => {
      skipBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/review")
    })
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", stepForSlug("review"))
    expect(inviteMock).not.toHaveBeenCalled()
  })

  it("Back routes to the connectors step", () => {
    mount()
    fireEvent.click(screen.getByText("Back").closest("button") as HTMLElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(InviteStep))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(InviteStep))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/company")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })

  it("adds pasted rows to the list, replacing a blank starter row", () => {
    mount()
    fireEvent.click(screen.getByText(/Add multiple people at once/))
    fireEvent.change(
      document.querySelector('[data-field="bulkEmails"] input') as HTMLInputElement,
      { target: { value: "a@acme.com, b@acme.com" } },
    )
    fireEvent.click(screen.getByText("Add").closest("button") as HTMLElement)
    const emails = Array.from(
      document.querySelectorAll('input[type="email"], .onb-card input[placeholder*="@"]'),
    )
      .map((el) => (el as HTMLInputElement).value)
      .filter(Boolean)
    expect(emails).toContain("a@acme.com")
    expect(emails).toContain("b@acme.com")
    expect(screen.getByText(/2 teammates added/)).not.toBeNull()
  })
})
