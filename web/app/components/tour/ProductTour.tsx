"use client"

/**
 * The first-run product tour — a spotlight over the real UI.
 *
 * WHY A SPOTLIGHT AND NOT A CARD CAROUSEL. The thing worth teaching is where
 * the controls are, and a modal describing the rail teaches a picture of the
 * rail. So each step dims the page and cuts a hole over the actual element,
 * with the explanation anchored beside it. What they learn is the position
 * they will use tomorrow.
 *
 * THE HOLE IS NOT A CLIPPED DIV. It is one fixed overlay whose box-shadow
 * spreads far enough to cover the viewport, so the "dim" is the shadow and the
 * element shows through the un-shadowed middle. That way there is no
 * four-rectangle scrim to keep in sync, and the highlight animates by moving
 * one box rather than four.
 *
 * A MISSING ANCHOR IS A NORMAL CASE, NOT AN ERROR. Steps render centred when
 * their anchor is absent — which is what carries the tour below 900px, where
 * globals.css sets `.sidebar { display: none }` and every rail anchor really
 * is gone. Without that the tour would spotlight nothing on a narrow window
 * and look broken on the exact screens where a newcomer is least oriented.
 *
 * WHEN IT SHOWS: an authed user whose workspace has finished onboarding and
 * whose `profiles.product_tour_completed_at` is null. Finishing and skipping
 * both write that column — a skip is a decision, and re-offering something
 * dismissed is how a welcome mat becomes a nuisance.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { useAuth } from "../../lib/auth"
import { useWorkspace } from "../../context/WorkspaceContext"
import { markProductTourSeen } from "../../lib/onboarding/store"
import { parseFeatureFlags } from "../../lib/onboarding/types"
import { stepsFor, type TourAudience, type TourStep } from "./tourSteps"
import styles from "./ProductTour.module.css"

/** Breathing room between the spotlight and the element it reveals. */
const HALO = 8
/** Gap between the spotlight edge and the bubble. */
const BUBBLE_GAP = 14
/** Bubble width; the CSS caps it against small viewports too. */
const BUBBLE_W = 340
/** Keep this much clear of the viewport edge on every side. */
const EDGE = 16
/** Below this, the rail is display:none — every rail anchor is absent. */
const RAIL_BREAKPOINT = 900

type Rect = { top: number; left: number; width: number; height: number }

function rectOf(anchor: string | undefined): Rect | null {
  if (!anchor || typeof document === "undefined") return null
  const el = document.querySelector<HTMLElement>(`[data-tour="${anchor}"]`)
  if (!el) return null
  const r = el.getBoundingClientRect()
  // A zero-size box means present-but-hidden (a collapsed rail, a display:none
  // ancestor). Treat it as absent so the step centres rather than spotlighting
  // a 0x0 point in the corner.
  if (r.width < 1 || r.height < 1) return null
  return { top: r.top, left: r.left, width: r.width, height: r.height }
}

/**
 * Where the bubble goes for a given spotlight.
 *
 * Right of the element normally — the anchors are nearly all in the left rail,
 * so the text reads outward into the empty page rather than off-screen. Falls
 * back to below, then above, then to centred, so a bubble can never be placed
 * where it cannot be read.
 *
 * `h` IS THE BUBBLE'S MEASURED HEIGHT, and it has to be. This clamped against a
 * hardcoded guess at first, which held until a step with a longer body was
 * anchored near the bottom of the rail: the guess said the bubble ended 220px
 * below its top, the real one ran past that, and the buttons were cut off by
 * the viewport. A card whose Next button is off-screen is a stuck tour, so the
 * height is read from the DOM rather than assumed.
 */
function bubblePosition(rect: Rect | null, vw: number, vh: number, h: number) {
  if (!rect) return null
  // Clamp a preferred top into the viewport, given the real height.
  const clampTop = (preferred: number) =>
    Math.max(EDGE, Math.min(preferred, vh - h - EDGE))

  const right = rect.left + rect.width + HALO + BUBBLE_GAP
  if (right + BUBBLE_W + EDGE <= vw) {
    // Vertically centred on the element, then clamped so it cannot hang off
    // either edge.
    return { left: right, top: clampTop(rect.top + rect.height / 2 - h / 2) }
  }

  const left = Math.max(EDGE, Math.min(rect.left, vw - BUBBLE_W - EDGE))
  const below = rect.top + rect.height + HALO + BUBBLE_GAP
  if (below + h + EDGE <= vh) return { left, top: below }

  const above = rect.top - HALO - BUBBLE_GAP - h
  if (above >= EDGE) return { left, top: above }

  return null
}

export function ProductTour() {
  const auth = useAuth()
  const { profile, workspace, workspaces = [], orgRole, refresh } = useWorkspace()

  const [open, setOpen] = useState(false)
  const [index, setIndex] = useState(0)
  const [steps, setSteps] = useState<TourStep[]>([])
  const [rect, setRect] = useState<Rect | null>(null)
  const [viewport, setViewport] = useState({ w: 0, h: 0 })
  /** The bubble's REAL rendered height, fed back into the placement maths.
   *  Starts at a sane guess so the first paint is not wildly wrong; the layout
   *  effect below corrects it before the browser paints. */
  const [bubbleH, setBubbleH] = useState(220)
  const bubbleRef = useRef<HTMLDivElement | null>(null)
  /** Guards the one-shot open: without it a context refresh re-opens a tour
   *  the user just closed, because `product_tour_completed_at` is only null
   *  again for the instant before the write lands. */
  const decidedRef = useRef(false)

  const userId = auth.kind === "authed" ? auth.user.id : null

  // ── Should it run at all? ────────────────────────────────────────────────
  useEffect(() => {
    if (decidedRef.current) return
    if (!userId || !profile || !workspace) return
    // Never over the onboarding flow: those screens are their own guided
    // sequence, and two of them at once is not a welcome, it is a pile-up.
    if (!workspace.onboarding_completed_at) return
    if (profile.product_tour_completed_at) {
      decidedRef.current = true
      return
    }
    const audience: TourAudience = {
      flags: parseFeatureFlags(workspace.feature_flags),
      orgRole,
      firstName: profile.first_name,
      onTrial: workspace.subscription_status === "trialing",
      workspaceCount: workspaces.length,
    }
    const resolved = stepsFor(audience)
    // Defensive: a company with everything switched off could filter the list
    // down to the two anchorless bookends. A "tour" of welcome-then-goodbye is
    // worse than none, so it does not run.
    if (resolved.length < 3) {
      decidedRef.current = true
      return
    }
    decidedRef.current = true
    setSteps(resolved)
    setIndex(0)
    setOpen(true)
  }, [userId, profile, workspace, workspaces, orgRole])

  const step = open ? steps[index] : undefined

  // ── Track the anchor ─────────────────────────────────────────────────────
  // useLayoutEffect so the spotlight is positioned in the same frame the step
  // renders — with useEffect the hole visibly jumps from the previous step's
  // position on every Next.
  useLayoutEffect(() => {
    if (!open) return
    const measure = () => {
      setViewport({ w: window.innerWidth, h: window.innerHeight })
      setRect(window.innerWidth < RAIL_BREAKPOINT ? null : rectOf(step?.anchor))
    }
    measure()
    window.addEventListener("resize", measure)
    // The rail scrolls its recent-chats list, and the page behind can scroll
    // too; either moves an anchor out from under the hole.
    window.addEventListener("scroll", measure, true)
    return () => {
      window.removeEventListener("resize", measure)
      window.removeEventListener("scroll", measure, true)
    }
  }, [open, step?.anchor, index])

  // Measure AFTER the step's copy has rendered, and before paint — the height
  // changes with every step because the bodies differ in length.
  useLayoutEffect(() => {
    if (!open) return
    const h = bubbleRef.current?.offsetHeight
    if (h && Math.abs(h - bubbleH) > 1) setBubbleH(h)
  })

  const close = useCallback(async () => {
    setOpen(false)
    if (userId) {
      await markProductTourSeen(userId)
      // Re-read so `profile.product_tour_completed_at` is populated in this
      // session too; without it the flag is only correct after a reload.
      void refresh?.()
    }
  }, [userId, refresh])

  const next = useCallback(() => {
    setIndex((i) => {
      if (i + 1 >= steps.length) {
        void close()
        return i
      }
      return i + 1
    })
  }, [steps.length, close])

  const back = useCallback(() => setIndex((i) => Math.max(0, i - 1)), [])

  // ── Keyboard ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault()
        void close()
      } else if (e.key === "ArrowRight" || e.key === "Enter") {
        e.preventDefault()
        next()
      } else if (e.key === "ArrowLeft") {
        e.preventDefault()
        back()
      }
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [open, close, next, back])

  // Focus the bubble on each step so a screen reader announces the new copy
  // and the arrow keys land somewhere sensible.
  useEffect(() => {
    if (open) bubbleRef.current?.focus()
  }, [open, index])

  if (!open || !step) return null

  const placed = bubblePosition(rect, viewport.w, viewport.h, bubbleH)
  const isLast = index === steps.length - 1

  return (
    <div className={styles.root} role="dialog" aria-modal="true" aria-label="Product tour">
      {/* The scrim. With an anchor it is the box-shadow around the hole; with
          none it is a plain full-page dim behind a centred bubble. */}
      {rect ? (
        <div
          className={styles.spotlight}
          style={{
            top: rect.top - HALO,
            left: rect.left - HALO,
            width: rect.width + HALO * 2,
            height: rect.height + HALO * 2,
          }}
          aria-hidden
        />
      ) : (
        <div className={styles.scrim} aria-hidden />
      )}

      <div
        ref={bubbleRef}
        tabIndex={-1}
        className={`${styles.bubble}${placed ? "" : ` ${styles.bubbleCentred}`}`}
        style={placed ? { top: placed.top, left: placed.left } : undefined}
        data-testid="product-tour-bubble"
      >
        <div className={styles.count} aria-hidden>
          {index + 1} / {steps.length}
        </div>
        <h2 className={styles.title}>{step.title}</h2>
        <p className={styles.body}>{step.body}</p>

        <div className={styles.actions}>
          <button
            type="button"
            className={styles.skip}
            onClick={() => void close()}
            data-testid="product-tour-skip"
          >
            {isLast ? "Close" : "Skip"}
          </button>
          <div className={styles.spacer} />
          {index > 0 && (
            <button
              type="button"
              className={styles.back}
              onClick={back}
              data-testid="product-tour-back"
            >
              Back
            </button>
          )}
          <button
            type="button"
            className={styles.next}
            onClick={next}
            data-testid="product-tour-next"
          >
            {isLast ? "Get started" : "Next"}
          </button>
        </div>
        {/* Announced once rather than per step: the shortcut does not change,
            and repeating it in every step's live region is noise. */}
        <div className={styles.hint} aria-hidden>
          Esc to close · ← → to move
        </div>
      </div>
    </div>
  )
}
