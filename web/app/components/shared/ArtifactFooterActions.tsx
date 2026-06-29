"use client"

import type { ReactNode } from "react"
import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { IconTicket, IconMicroscope, IconFileText, IconDeviceDesktop } from "@tabler/icons-react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { designAgentApi } from "../../lib/api"
import { prototypePath } from "../../lib/routes"

export type ArtifactId = "prd" | "evidence" | "tickets"

/**
 * Contextual footer for a rail artifact (PRD / Evidence / Ticket / Prototype).
 * It renders a short status label + exactly THREE chips that point at the
 * OTHER artifacts so a PM moves PRD → Evidence → Prototype → Ticket without
 * hunting for tabs. The chip for the artifact you're already on is omitted, so
 * the remaining three are always the siblings.
 *
 * Per the June 21 #6 spec, the per-view chip order is:
 *   - PRD       → Evidence, Prototype, Ticket
 *   - Ticket    → Evidence, PRD, Prototype
 *   - Evidence  → PRD, Ticket, Prototype
 *   - Prototype → PRD, Evidence, Ticket
 *
 * A chip whose artifact already EXISTS reads "View …" and opens it; one that
 * does not yet exist reads "Generate …" and kicks the generate flow. The first
 * chip is styled as the primary (design `.chip.b`), matching the mockup's
 * "Evidence ready — … · ready to draft PRD" footer whose primary CTA was the
 * forward step.
 *
 * Every action reuses behavior that already exists — nothing new is invented:
 *  - Evidence / PRD / Ticket chips → openContentPanel(tab). Those rail tabs own
 *    the load-if-present / generate-if-missing logic (ContentPanel effects,
 *    TicketsTab PRD→tickets generation), so View and Generate share one handler
 *    and differ only in label.
 *  - Prototype chips → router.push(prototypePath(prdId, { generate })) — the
 *    exact nav the PRD drawer / ApproveModal use; `generate` is set when no
 *    prototype-bearing PRD exists yet so PrototypeRoute opens the generate panel.
 *
 * Prototype + Ticket both require a PRD as their source, so their chips are
 * hidden when no PRD is loaded. The Prototype chip is additionally hidden when
 * the active finding is not prototypeable (`prototypeable={false}`), matching
 * the brief card which only offers a prototype for UI-renderable fixes.
 */
type FooterArtifact = ArtifactId | "prototype"

const ICONS: Record<FooterArtifact, ReactNode> = {
  evidence: <IconMicroscope size={13} />,
  prd: <IconFileText size={13} />,
  prototype: <IconDeviceDesktop size={13} />,
  tickets: <IconTicket size={13} />,
}

const NOUN: Record<FooterArtifact, string> = {
  evidence: "evidence",
  prd: "PRD",
  prototype: "prototype",
  tickets: "ticket",
}

/** The three siblings, in the spec's order, for each artifact view. */
const SIBLING_ORDER: Record<FooterArtifact, FooterArtifact[]> = {
  prd: ["evidence", "prototype", "tickets"],
  tickets: ["evidence", "prd", "prototype"],
  evidence: ["prd", "tickets", "prototype"],
  prototype: ["prd", "evidence", "tickets"],
}

/** Per-view status label (left of the chips). Mirrors the mockup's
 *  "<strong>Evidence ready</strong> — …" pattern. */
const LABEL: Record<FooterArtifact, { lead: string; rest: string }> = {
  evidence: { lead: "Evidence ready", rest: "synthesized · ready to draft the PRD" },
  prd: { lead: "PRD ready", rest: "ready to prototype or break into tickets" },
  tickets: { lead: "Tickets ready", rest: "trace each back to the PRD and evidence" },
  prototype: { lead: "Prototype ready", rest: "review it against the PRD and evidence" },
}

export function ArtifactFooterActions({
  current,
  prototypeable = true,
}: {
  current: FooterArtifact
  /** Whether the active finding can be visualized as a UI prototype. When
   *  false the prototype chip is hidden (data/pricing/ops fixes have nothing to
   *  render), matching the brief card's `finding.prototypeable` gate. */
  prototypeable?: boolean
}) {
  const { openContentPanel } = useNavigation()
  const { content } = useContent()
  const router = useRouter()
  const prdId = content.prd?.prd_id ?? null

  // REAL per-PRD prototype existence — NOT PRD existence. The prototype chip's
  // action navigates to THIS specific prdId, so the View-vs-Generate decision
  // must be keyed on the SAME prdId via getByPrd (the per-PRD endpoint that
  // ApproveModal already gates on: ready + bundle_url). A per-insight signal is
  // the wrong key here — it reports "ready" whenever ANY duplicate sibling PRD
  // on the insight has a prototype, so a no-prototype PRD whose sibling has one
  // would mis-read "View" and dead-end on the empty Generate page.
  // null = still resolving (round-trip in flight).
  const [prototypeExists, setPrototypeExists] = useState<boolean | null>(null)
  useEffect(() => {
    if (prdId == null) {
      setPrototypeExists(false)
      return
    }
    let cancelled = false
    setPrototypeExists(null)
    designAgentApi
      .getByPrd(prdId)
      .then((proto) => {
        // Ignore a stale resolution if prdId changed (cleanup flips cancelled).
        if (cancelled) return
        setPrototypeExists(
          Boolean(proto && proto.status === "ready" && proto.bundle_url),
        )
      })
      .catch(() => {
        // getByPrd already swallows 404→null; this guards transient throws.
        // Treat any failure as "no prototype" (degrade to Generate), never crash.
        if (!cancelled) setPrototypeExists(false)
      })
    return () => {
      cancelled = true
    }
  }, [prdId])

  // While the prototype lookup is in flight (prototypeExists === null) we DISABLE
  // the prototype chip (chosen over a neutral-nav default as the lowest-footprint
  // guard) so it can never mis-navigate before existence is known.
  const prototypeLoading = prototypeExists === null

  // Existence of each sibling artifact, used to pick "View" vs "Generate".
  const exists: Record<FooterArtifact, boolean> = {
    prd: content.prd != null,
    evidence: content.evidence != null,
    // A "tickets" artifact only ever exists once there's a PRD to derive it from;
    // we don't track a separate ready flag in content, so PRD presence is the
    // existence signal. Prototype uses the REAL per-PRD lookup above.
    tickets: prdId != null,
    prototype: prototypeExists === true,
  }

  // Prototype + tickets need a PRD as their source. Prototype additionally needs
  // a prototypeable finding.
  const show: Record<FooterArtifact, boolean> = {
    prd: true,
    evidence: true,
    tickets: prdId != null,
    prototype: prdId != null && prototypeable,
  }

  const open = (artifact: FooterArtifact) => {
    if (artifact === "prototype") {
      // generate-intent when no prototype-bearing PRD is ready yet
      router.push(prototypePath(prdId, { generate: !exists.prototype }))
      return
    }
    // Evidence / PRD / Ticket tabs each load-or-generate on open.
    openContentPanel(artifact)
  }

  const siblings = SIBLING_ORDER[current]
    .filter((a) => a !== current && show[a])
    .map((a) => ({
      key: a,
      // The prototype chip's label is ALWAYS "View prototype" — only its ACTION
      // branches on existence (view if a prototype exists, else generate). The
      // other artifacts keep their View/Generate label.
      label:
        a === "prototype"
          ? `View ${NOUN.prototype}`
          : `${exists[a] ? "View" : "Generate"} ${NOUN[a]}`,
      icon: ICONS[a],
      // Only the prototype chip has an async existence round-trip; disable it
      // until that resolves so it never navigates in the wrong direction.
      disabled: a === "prototype" && prototypeLoading,
      onClick: () => open(a),
    }))

  if (siblings.length === 0) return null

  const label = LABEL[current]

  return (
    <div
      className="artifact-foot-actions"
      role="group"
      aria-label="Artifact actions"
    >
      <span className="artifact-foot-lbl">
        <strong>{label.lead}</strong>
        {" — "}
        {label.rest}
      </span>
      {siblings.map((a, i) => (
        <button
          key={a.key}
          type="button"
          // Design: `.chip` (ghost) + `.chip.b` (brand-primary). The
          // first/most-forward sibling is the primary action.
          className={i === 0 ? "artifact-foot-chip is-primary" : "artifact-foot-chip"}
          disabled={a.disabled}
          onClick={a.onClick}
        >
          {a.icon}
          {a.label}
        </button>
      ))}
    </div>
  )
}
