// @vitest-environment jsdom
//
// Authed General section (parity with the public viewer's General/Pinned
// split). The signed-in mounts (PrototypeRoute, DesignAgentLauncher) pass
// `showGeneralSection` so a null-anchor general comment gets its own section
// with its own composer instead of leaking into the flat pinned/open list.
//
// jsdom + @testing-library/react with the api module + CommentClarifyDialog's
// clarify call mocked so the load/create/apply flows are deterministic (same
// convention as CommentsPanel.onCommentsLoaded.dom.test.tsx).
import * as React from "react"
import { render, cleanup, waitFor, fireEvent } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../lib/api", () => ({
  designAgentApi: {
    listComments: vi.fn(),
    listCommentsByToken: vi.fn(),
    createComment: vi.fn(),
    createCommentByToken: vi.fn(),
    resolveComment: vi.fn(),
    deleteComment: vi.fn(),
    clarifyComment: vi.fn(),
  },
}))

import { CommentsPanel } from "../CommentsPanel"
import { designAgentApi } from "../../../lib/api"

const listComments = designAgentApi.listComments as unknown as ReturnType<typeof vi.fn>
const createComment = designAgentApi.createComment as unknown as ReturnType<typeof vi.fn>
const resolveComment = designAgentApi.resolveComment as unknown as ReturnType<typeof vi.fn>
const clarifyComment = designAgentApi.clarifyComment as unknown as ReturnType<typeof vi.fn>

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function generalComment(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 101,
    anchor_id: null,
    pin_x_pct: null,
    pin_y_pct: null,
    body: "Overall this feels smooth, nice palette.",
    author: "Ada Lovelace",
    status: "open" as const,
    created_at: "2026-07-06T08:00:00Z",
    resolved_at: null,
    ...overrides,
  }
}

function pinnedComment(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 202,
    anchor_id: "fb3007b5",
    pin_x_pct: 10,
    pin_y_pct: 20,
    body: "This button needs more weight",
    author: "Jane Doe",
    status: "open" as const,
    created_at: "2026-07-06T09:00:00Z",
    resolved_at: null,
    ...overrides,
  }
}

describe("authed General section — rendering (test_authed_general_renders_in_general_section)", () => {
  it("an open null-anchor comment renders in the General section, not the flat pinned list; no pin badge", async () => {
    listComments.mockResolvedValue([generalComment(), pinnedComment()])
    render(
      React.createElement(CommentsPanel, {
        token: "tok",
        prototypeId: 1,
        showGeneralSection: true,
      }),
    )
    await waitFor(() => expect(listComments).toHaveBeenCalled())

    const generalSection = await waitFor(() => {
      const el = document.querySelector('[data-testid="general-comments-section"]')
      expect(el).toBeTruthy()
      return el as HTMLElement
    })
    // The general comment renders INSIDE the General section...
    expect(generalSection.querySelector('[data-testid="comment-thread-101"]')).toBeTruthy()
    expect(generalSection.textContent).toContain("Overall this feels smooth")
    // ...styled with the DA-21 general-card discriminator, no pin badge markup.
    const generalCard = generalSection.querySelector('[data-testid="comment-thread-101"]')!
    expect(generalCard.className).toContain("comment-thread--general")

    // ...and NOT inside the flat pinned/open list (comments-open).
    const openList = document.querySelector('[data-testid="comments-open"]')
    expect(openList?.querySelector('[data-testid="comment-thread-101"]')).toBeFalsy()
    // The pinned comment stays in the flat open list, unaffected.
    expect(openList?.querySelector('[data-testid="comment-thread-202"]')).toBeTruthy()
  })

  it("renders the empty-general placeholder when there are no general comments yet", async () => {
    listComments.mockResolvedValue([pinnedComment()])
    render(
      React.createElement(CommentsPanel, {
        token: "tok",
        prototypeId: 1,
        showGeneralSection: true,
      }),
    )
    await waitFor(() => expect(listComments).toHaveBeenCalled())
    await waitFor(() => {
      expect(document.querySelector('[data-testid="general-comments-empty"]')).toBeTruthy()
    })
    expect(document.querySelector('[data-testid="general-comments-list"]')).toBeFalsy()
  })
})

describe("authed General composer (test_freeform_composer_posts_null_anchor)", () => {
  it("submits a general comment with anchor_id: null (was the 'general' sentinel string)", async () => {
    listComments.mockResolvedValue([])
    createComment.mockResolvedValue(generalComment({ id: 999 }))
    render(
      React.createElement(CommentsPanel, {
        token: "tok",
        prototypeId: 1,
        showGeneralSection: true,
      }),
    )
    await waitFor(() => expect(listComments).toHaveBeenCalled())

    const textarea = await waitFor(() => {
      const el = document.querySelector<HTMLTextAreaElement>('[data-testid="general-comment-input"]')
      expect(el).toBeTruthy()
      return el!
    })
    fireEvent.change(textarea, { target: { value: "Loving the new layout overall." } })
    const sendBtn = document.querySelector<HTMLButtonElement>('[data-testid="general-comment-send"]')!
    fireEvent.click(sendBtn)

    await waitFor(() => expect(createComment).toHaveBeenCalled())
    expect(createComment).toHaveBeenCalledWith(1, {
      anchor_id: null,
      body: "Loving the new layout overall.",
    })
  })

  it("does NOT render the old always-on top compose box when showGeneralSection is set (composer moved into the General section)", async () => {
    listComments.mockResolvedValue([])
    render(
      React.createElement(CommentsPanel, {
        token: "tok",
        prototypeId: 1,
        showGeneralSection: true,
      }),
    )
    await waitFor(() => expect(listComments).toHaveBeenCalled())
    expect(document.querySelector('[data-testid="da-comment-compose"]')).toBeFalsy()
  })

  it("the pre-existing top compose box still renders unchanged when showGeneralSection is omitted (default false, backward compatible)", async () => {
    listComments.mockResolvedValue([])
    render(
      React.createElement(CommentsPanel, {
        token: "tok",
        prototypeId: 1,
      }),
    )
    await waitFor(() => expect(listComments).toHaveBeenCalled())
    expect(document.querySelector('[data-testid="da-comment-compose"]')).toBeTruthy()
  })
})

describe("pinned rendering + element-Apply stay unchanged with showGeneralSection (regression)", () => {
  it("a pinned comment's Apply routes through onIterateComment with its real anchor_id preserved, then resolves", async () => {
    listComments.mockResolvedValue([pinnedComment()])
    clarifyComment.mockResolvedValue({ question: "Which style of weight — bold or a filled background?" })
    resolveComment.mockResolvedValue(pinnedComment({ status: "resolved" }))
    const onIterateComment = vi.fn()

    render(
      React.createElement(CommentsPanel, {
        token: "tok",
        prototypeId: 1,
        showGeneralSection: true,
        onIterateComment,
      }),
    )
    await waitFor(() => expect(listComments).toHaveBeenCalled())

    const applyBtn = await waitFor(() => {
      const el = document.querySelector<HTMLButtonElement>('[data-testid="comment-apply-202"]')
      expect(el).toBeTruthy()
      return el!
    })
    fireEvent.click(applyBtn)

    // The ClarifyDialog fires clarifyComment on open, then "Apply change" confirms.
    await waitFor(() => expect(clarifyComment).toHaveBeenCalled())
    const confirmBtn = await waitFor(() => {
      const el = document.querySelector<HTMLButtonElement>(".modal-confirm")
      expect(el).toBeTruthy()
      return el!
    })
    fireEvent.click(confirmBtn)

    await waitFor(() => expect(onIterateComment).toHaveBeenCalled())
    const applied = onIterateComment.mock.calls[0][0]
    expect(applied.anchor_id).toBe("fb3007b5") // element anchor preserved, unchanged
    await waitFor(() => expect(resolveComment).toHaveBeenCalledWith(1, 202))
  })
})
