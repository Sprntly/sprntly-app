// Wave-2 #4: Anonymous commenting via collapsible right-side panel
// Tests the by-token path (list + create), resolve suppression, name capture,
// and the trap (comments created via by-token must be LISTED via by-token, not
// the authed path — if they were, the create would succeed but the panel would
// show an empty list, hiding the just-posted comment from the viewer).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi, beforeAll } from "vitest"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"

// Classic-runtime transform: expose React globally (convention from the
// public-token-states.test.tsx / DesignAgentDrawer suite).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const HERE = dirname(fileURLToPath(import.meta.url))
const PUBLIC_VIEWER_PATH = resolve(HERE, "../PublicTokenViewer.tsx")
const CSS_PATH = resolve(HERE, "../../components/design-agent/design-agent.css")

let publicViewerSrc = ""
let cssSrc = ""
beforeAll(() => {
  publicViewerSrc = readFileSync(PUBLIC_VIEWER_PATH, "utf8")
  cssSrc = readFileSync(CSS_PATH, "utf8")
})

import {
  runLoadComments,
  runCreateComment,
  CommentsPanelView,
} from "../../components/design-agent/CommentsPanel"
import type { CommentRecord } from "../../lib/api"

function makeComment(overrides: Partial<CommentRecord> = {}): CommentRecord {
  return {
    id: 1,
    anchor_id: "abc123",
    body: "Test comment",
    author: "Viewer Name",
    status: "open",
    created_at: "2026-06-15T10:00:00Z",
    resolved_at: null,
    ...overrides,
  }
}

// 1 + 3. Public view uses .da-right collapsible sidebar
describe("Locked scope #1+#3 — da-right collapsible sidebar layout", () => {
  it("PublicTokenViewer.tsx contains da-right, da-right-top, da-right-body, and da-ready class names", () => {
    expect(publicViewerSrc).toContain("da-right")
    expect(publicViewerSrc).toContain("da-right-top")
    expect(publicViewerSrc).toContain("da-right-body")
    expect(publicViewerSrc).toContain("da-ready")
  })

  it("design-agent.css defines .da-right and .da-right.open selectors", () => {
    expect(cssSrc).toContain(".da-right")
    expect(cssSrc).toContain(".da-right.open")
  })

  it("commentsOpen controls the da-right open class in source (source invariant)", () => {
    // The aside element uses commentsOpen to conditionally append 'open' to da-right
    expect(publicViewerSrc).toMatch(/da-right.*commentsOpen/)
  })

  it("aria-hidden on aside reflects commentsOpen (accessible collapse)", () => {
    expect(publicViewerSrc).toMatch(/aria-hidden=\{commentsOpen\s*\?\s*"false"\s*:\s*"true"\}/)
  })
})

// 2. Old top-chrome CommentsPanel mount removed
describe("Locked scope #2 — CommentsPanel no longer in PrototypeViewer chrome slot", () => {
  it("chrome= prop of PrototypeViewer contains only ManualEditOverlay, not CommentsPanel", () => {
    // The old layout had CommentsPanel nested inside chrome={<>...</>}; the new
    // layout puts it in the da-right sidebar which is a sibling of PrototypeViewer.
    // Extract the chrome= prop block and confirm it only contains ManualEditOverlay.
    const chromeStart = publicViewerSrc.indexOf("chrome={")
    expect(chromeStart).toBeGreaterThan(-1)
    // Find the chrome={...} block up to its closing /> or /> end of PrototypeViewer.
    // We know the chrome block is only ManualEditOverlay (one element, no fragment).
    // Grab a bounded slice from chrome={ to the next />
    const chromePropSlice = publicViewerSrc.slice(chromeStart, chromeStart + 500)
    expect(chromePropSlice).toContain("ManualEditOverlay")
    expect(chromePropSlice).not.toContain("CommentsPanel")
  })

  it("CommentsPanel appears in da-right-body context (sibling aside, not chrome slot)", () => {
    // CommentsPanel must appear AFTER the da-right-body marker in source
    const rightBodyIdx = publicViewerSrc.indexOf("da-right-body")
    const commentsPanelIdx = publicViewerSrc.indexOf("<CommentsPanel")
    expect(rightBodyIdx).toBeGreaterThan(-1)
    expect(commentsPanelIdx).toBeGreaterThan(-1)
    // CommentsPanel comes after da-right-body (it's inside the sidebar, not before it)
    expect(commentsPanelIdx).toBeGreaterThan(rightBodyIdx)
  })
})

// 5. Name capture form is inside the panel (not a standalone floating overlay)
describe("Locked scope #5 — name capture form inside the da-right panel", () => {
  it("needsName pattern is still commentsOpen && !viewerName (source invariant)", () => {
    expect(publicViewerSrc).toMatch(/needsName\s*=\s*commentsOpen\s*&&\s*!viewerName/)
  })

  it("name form is conditionally rendered with commentsOpen && needsName && pattern (source invariant)", () => {
    expect(publicViewerSrc).toMatch(/commentsOpen\s*&&\s*needsName\s*&&/)
  })

  it("name form data-testids present (source invariant)", () => {
    expect(publicViewerSrc).toContain('data-testid="viewer-name-form"')
    expect(publicViewerSrc).toContain('data-testid="viewer-first-name-input"')
    expect(publicViewerSrc).toContain('data-testid="viewer-last-name-input"')
    expect(publicViewerSrc).toContain('data-testid="viewer-name-notice"')
  })

  it("PII notice text present (source invariant)", () => {
    expect(publicViewerSrc).toMatch(/Your name and comment are shared with the prototype/)
  })

  it("localStorage helpers are present (source invariant)", () => {
    expect(publicViewerSrc).toContain('"da-viewer-name"')
    expect(publicViewerSrc).toMatch(/localStorage\.getItem\(VIEWER_NAME_KEY\)/)
    expect(publicViewerSrc).toMatch(/localStorage\.setItem\(VIEWER_NAME_KEY/)
    expect(publicViewerSrc).toMatch(/persistViewerName\(name\)/)
    expect(publicViewerSrc).toMatch(/setViewerName\(name\)/)
  })
})

// 6 + THE TRAP: by-token create AND list
describe("Locked scope #6 + THE TRAP — by-token create AND list (not authed path)", () => {
  it("runLoadComments calls listCommentsByToken (not listComments) when prototypeId is absent", async () => {
    const listCommentsByToken = vi.fn().mockResolvedValue([makeComment()])
    const listComments = vi.fn().mockResolvedValue([])
    const result = await runLoadComments({
      token: "test-token-123",
      prototypeId: undefined,
      api: { listCommentsByToken, listComments },
    })
    // By-token path used
    expect(listCommentsByToken).toHaveBeenCalledWith("test-token-123")
    // Authed path NOT used — this is the trap: if list used the authed path,
    // comments created by-token would never appear (wrong endpoint returns 404/[])
    expect(listComments).not.toHaveBeenCalled()
    expect(result).toHaveLength(1)
  })

  it("TRAP: authed list path would return empty — prove by-token list returns data", async () => {
    const COMMENT = makeComment({ body: "I see this comment" })
    const listCommentsByToken = vi.fn().mockResolvedValue([COMMENT])
    const listComments = vi.fn().mockResolvedValue([]) // authed path returns nothing

    const result = await runLoadComments({
      token: "share-token-abc",
      prototypeId: undefined,
      api: { listCommentsByToken, listComments },
    })
    // If this regressed to the authed path, result would be []
    expect(result).toContain(COMMENT)
    expect(result[0].body).toBe("I see this comment")
  })

  it("runCreateComment uses createCommentByToken with viewer_name in payload", async () => {
    const created = makeComment({ id: 5, body: "New comment" })
    const createCommentByToken = vi.fn().mockResolvedValue(created)
    const result = await runCreateComment({
      token: "share-token-abc",
      anchorId: "abc123",
      body: "New comment",
      viewerName: "Anonymous User",
      api: { createCommentByToken },
      comments: [],
    })
    expect(createCommentByToken).toHaveBeenCalledWith("share-token-abc", {
      anchor_id: "abc123",
      body: "New comment",
      viewer_name: "Anonymous User",
    })
    expect(result[0]).toBe(created)
  })

  it("create then list: comment appears in re-list (end-to-end helper chain)", async () => {
    const newComment = makeComment({ id: 10, body: "Freshly created", author: "Jane Doe" })
    const createCommentByToken = vi.fn().mockResolvedValue(newComment)
    const listCommentsByToken = vi.fn().mockResolvedValue([newComment])

    // Step 1: create by-token
    const afterCreate = await runCreateComment({
      token: "tok-xyz",
      anchorId: "btn001",
      body: "Freshly created",
      viewerName: "Jane Doe",
      api: { createCommentByToken },
      comments: [],
    })
    expect(afterCreate[0].body).toBe("Freshly created")

    // Step 2: list by-token (simulating panel remount / refresh)
    const afterList = await runLoadComments({
      token: "tok-xyz",
      prototypeId: undefined,
      api: { listCommentsByToken, listComments: vi.fn() },
    })
    expect(afterList[0].body).toBe("Freshly created")
    expect(afterList[0].author).toBe("Jane Doe")
  })
})

// Resolve suppression for anonymous viewers
describe("Resolve suppression — anonymous = view + create only, no resolve affordance", () => {
  it("CommentsPanelView with canResolve=false hides the clickable resolve button", () => {
    const html = renderToStaticMarkup(
      React.createElement(CommentsPanelView, {
        comments: [makeComment()],
        canResolve: false,
      }),
    )
    // No clickable resolve button
    expect(html).not.toContain('data-testid="comment-resolve-1"')
    // Static (non-interactive) resolve indicator is still present
    expect(html).toContain("comment-resolve-btn--static")
  })

  it("resolved comment renders in muted state even for anonymous viewers (canResolve=false)", () => {
    const resolvedComment = makeComment({
      id: 2,
      status: "resolved",
      resolved_at: "2026-06-15T11:00:00Z",
    })
    const html = renderToStaticMarkup(
      React.createElement(CommentsPanelView, {
        comments: [resolvedComment],
        canResolve: false,
      }),
    )
    // Resolved visual state is displayed
    expect(html).toContain("comment--resolved")
    expect(html).toContain('data-testid="comments-resolved"')
    // No interactive resolve button (anonymous)
    expect(html).not.toContain('data-testid="comment-resolve-2"')
    // Static indicator present
    expect(html).toContain("comment-resolve-btn--static")
  })

  it("public CommentsPanel mount has no prototypeId (canResolve=false by construction)", () => {
    // CommentsPanel container: canResolve = prototypeId != null
    // With no prototypeId on the public mount, canResolve stays false
    const mountMatch = publicViewerSrc.match(/<CommentsPanel[\s\S]*?\/>/)
    expect(mountMatch).not.toBeNull()
    const mount = mountMatch![0]
    expect(mount).toContain("canComment")
    expect(mount).not.toContain("prototypeId")
    expect(mount).toContain("token=")
    expect(mount).toMatch(/viewerName=\{viewerName\}/)
  })
})

// Head controls wiring (carried over from source-invariant tests)
describe("Head controls source invariants", () => {
  it("public-mark-toggle and public-comments-toggle data-testids present", () => {
    expect(publicViewerSrc).toContain('data-testid="public-mark-toggle"')
    expect(publicViewerSrc).toContain('data-testid="public-comments-toggle"')
  })

  it("aria-pressed on both toggles reflects correct state", () => {
    expect(publicViewerSrc).toMatch(/aria-pressed=\{pin\.markMode\}/)
    expect(publicViewerSrc).toMatch(/aria-pressed=\{commentsOpen\}/)
  })

  it("headControls prop is present", () => {
    expect(publicViewerSrc).toMatch(/headControls=\{/)
  })

  it("stageOverlay contains MarkOverlay and PinLayer", () => {
    expect(publicViewerSrc).toMatch(/stageOverlay=\{/)
    const start = publicViewerSrc.indexOf("stageOverlay={")
    const block = publicViewerSrc.slice(start, start + 400)
    expect(block).toContain("<MarkOverlay")
    expect(block).toContain("onStageClick={pin.handleStageClick}")
    expect(block).toContain("<PinLayer")
  })

  it("usePinMarking is wired with createCommentByToken (not authed createComment)", () => {
    expect(publicViewerSrc).toContain("usePinMarking({")
    const start = publicViewerSrc.indexOf("usePinMarking({")
    const call = publicViewerSrc.slice(start, start + 500)
    expect(call).toMatch(/onCreate:\s*\(payload\)\s*=>\s*designAgentApi\.createCommentByToken\(/)
    expect(publicViewerSrc).not.toMatch(/designAgentApi\.createComment\(/)
  })

  it("PrototypeMarkLayer has editorMode=false, canResolve=false, no onPinApply/onPinIgnore", () => {
    expect(publicViewerSrc).toContain("<PrototypeMarkLayer")
    const start = publicViewerSrc.indexOf("<PrototypeMarkLayer")
    const mount = publicViewerSrc.slice(start, start + 400)
    expect(mount).toContain("editorMode={false}")
    expect(mount).toContain("canResolve={false}")
    expect(mount).toContain("onSubmitComment={pin.handlePinSubmit}")
    expect(mount).not.toContain("onPinApply")
    expect(mount).not.toContain("onPinIgnore")
  })
})

// Remount survive: by-token list works on remount
describe("Refresh survive — by-token list on remount", () => {
  it("on remount with token and no prototypeId, comments load via listCommentsByToken", async () => {
    const comments = [makeComment({ id: 1 }), makeComment({ id: 2, body: "Second" })]
    const listCommentsByToken = vi.fn().mockResolvedValue(comments)
    const listComments = vi.fn().mockResolvedValue([])

    const result = await runLoadComments({
      token: "remount-token",
      prototypeId: undefined,
      api: { listCommentsByToken, listComments },
    })

    expect(listCommentsByToken).toHaveBeenCalledWith("remount-token")
    expect(listComments).not.toHaveBeenCalled()
    expect(result).toHaveLength(2)
    expect(result[1].body).toBe("Second")
  })
})
