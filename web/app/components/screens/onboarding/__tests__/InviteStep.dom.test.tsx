// @vitest-environment jsdom
//
// Container mount test for onboarding step 08 — "Invite your team" (v6
// screenshot spec 2026-07-17, NEW step). Skippable. Rows of email + JOB role
// (JOB_ROLE_OPTIONS) + permission (member/admin/viewer), an "Add teammate"
// appender, and a CSV import. Invites send best-effort on Continue via
// teamApi.invite(email, permission, [], jobRole), then the step advances to 9
// and routes to /onboarding/review; Skip advances without inviting.
//
// Plus unit coverage for the exported parseInvitesCsv helper (header row
// skipped, dedupe, invalid emails dropped, job-role/permission defaults).
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
vi.mock("../../../../lib/teamApi", () => ({
  teamApi: { invite: (...a: unknown[]) => inviteMock(...a) },
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))
// The mount effect kicks the step-9 business-context draft prefetch in the
// background — stub it so no real API call fires from these tests.
const prefetchDraftMock = vi.fn((..._a: unknown[]) => Promise.resolve("drafted"))
vi.mock("../../../../lib/onboarding/draftPrefetch", () => ({
  prefetchBusinessContextDraft: (...a: unknown[]) => prefetchDraftMock(...a),
}))

import { InviteStep, parseInvitesCsv, parsePastedEmails } from "../InviteStep"
import { JOB_ROLE_OPTIONS, ONBOARDING_STEP_COUNT } from "../../../../lib/onboarding/types"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

function mount(workspace = makeWorkspace({ onboarding_step: 8 })) {
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

function skipLink(): HTMLButtonElement {
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

describe("parseInvitesCsv — teammate CSV import", () => {
  it("parses email, job role and permission per line", () => {
    expect(
      parseInvitesCsv("a@acme.com,Engineer,admin\nb@acme.com,Designer,viewer"),
    ).toEqual([
      { email: "a@acme.com", jobRole: "Engineer", permission: "admin" },
      { email: "b@acme.com", jobRole: "Designer", permission: "viewer" },
    ])
  })

  it("skips a header row whose first cell is 'email'", () => {
    expect(parseInvitesCsv("email,role,permission\na@acme.com,Engineer,admin")).toEqual([
      { email: "a@acme.com", jobRole: "Engineer", permission: "admin" },
    ])
  })

  it("dedupes repeated emails (case-insensitively) and lowercases them", () => {
    expect(
      parseInvitesCsv("a@acme.com,Engineer,admin\nA@Acme.com,Designer,viewer"),
    ).toEqual([{ email: "a@acme.com", jobRole: "Engineer", permission: "admin" }])
  })

  it("drops malformed emails and blank lines", () => {
    expect(parseInvitesCsv("not-an-email,Engineer\n\n ,x\nok@acme.com")).toEqual([
      { email: "ok@acme.com", jobRole: JOB_ROLE_OPTIONS[0], permission: "member" },
    ])
  })

  it("defaults a missing job role to the first option and an unknown permission to member", () => {
    expect(parseInvitesCsv("a@acme.com")).toEqual([
      { email: "a@acme.com", jobRole: JOB_ROLE_OPTIONS[0], permission: "member" },
    ])
    expect(parseInvitesCsv("b@acme.com,Marketing,owner")).toEqual([
      { email: "b@acme.com", jobRole: "Marketing", permission: "member" },
    ])
  })
})

describe("InviteStep (onboarding step 07 — email + job role + permission rows)", () => {
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

  it("Continue sends each valid row via teamApi.invite (with jobRole), advances to 9 and routes to review", async () => {
    inviteMock.mockResolvedValue({ id: "inv-1" })
    advanceStepMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: ONBOARDING_STEP_COUNT }),
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
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", ONBOARDING_STEP_COUNT)
  })

  it("a failed invite is best-effort: a notice shows but the step still advances", async () => {
    inviteMock.mockRejectedValue(new Error("boom"))
    advanceStepMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: ONBOARDING_STEP_COUNT }),
    )
    mount()

    fireEvent.change(emailInput(), { target: { value: "teammate@acme.com" } })
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/review")
    })
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", ONBOARDING_STEP_COUNT)
    expect(screen.getByText(/Couldn't invite teammate@acme\.com/)).not.toBeNull()
  })

  it("Skip advances to 9 and routes to review WITHOUT sending invites", async () => {
    advanceStepMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: ONBOARDING_STEP_COUNT }),
    )
    mount()

    fireEvent.change(emailInput(), { target: { value: "teammate@acme.com" } })
    await act(async () => {
      skipLink().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/review")
    })
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", ONBOARDING_STEP_COUNT)
    expect(inviteMock).not.toHaveBeenCalled()
  })

  it("Back routes to the metrics step", () => {
    mount()
    fireEvent.click(screen.getByText("Back").closest("button") as HTMLElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/metrics")
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
})

describe("parsePastedEmails — the bulk paste field", () => {
  it("splits on commas, semicolons, newlines and stray whitespace", () => {
    const rows = parsePastedEmails(
      "a@acme.com, b@acme.com;c@acme.com\nd@acme.com  e@acme.com",
    )
    expect(rows.map((r) => r.email)).toEqual([
      "a@acme.com",
      "b@acme.com",
      "c@acme.com",
      "d@acme.com",
      "e@acme.com",
    ])
    // Pasted rows default to the first job role + member permission.
    expect(rows[0].permission).toBe("member")
  })

  it("drops malformed addresses, self-duplicates, and ones already listed", () => {
    const rows = parsePastedEmails(
      "GOOD@acme.com, not-an-email, good@acme.com, dupe@acme.com",
      [{ email: "Dupe@acme.com", jobRole: "Engineer", permission: "admin" }],
    )
    expect(rows.map((r) => r.email)).toEqual(["good@acme.com"])
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
