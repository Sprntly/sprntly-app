"use client"

import type { ReactNode } from "react"
import { useRouter } from "next/navigation"
import { IconTicket, IconMicroscope, IconFileText, IconDeviceDesktop } from "@tabler/icons-react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
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

  // Existence of each sibling artifact, used to pick "View" vs "Generate".
  const exists: Record<FooterArtifact, boolean> = {
    prd: content.prd != null,
    evidence: content.evidence != null,
    // A "tickets" / "prototype" artifact only ever exists once there's a PRD to
    // derive it from; we don't track a separate ready flag in content, so the
    // PRD presence is the existence signal (and the gate, below).
    tickets: prdId != null,
    prototype: prdId != null,
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
      label: `${exists[a] ? "View" : "Generate"} ${NOUN[a]}`,
      icon: ICONS[a],
      onClick: () => open(a),
    }))

  if (siblings.length === 0) return null

  const label = LABEL[current]

  return (
    <div
      className="artifact-foot-actions"
      role="group"
      aria-label="Artifact actions"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "14px 0 4px",
        marginTop: 8,
        borderTop: "1px solid var(--line)",
      }}
    >
      <span
        className="artifact-foot-lbl"
        style={{ flex: 1, fontSize: 12, color: "var(--ink-2)", lineHeight: 1.4 }}
      >
        <strong style={{ color: "var(--ink)", fontWeight: 500 }}>{label.lead}</strong>
        {" — "}
        {label.rest}
      </span>
      {siblings.map((a, i) => (
        <button
          key={a.key}
          type="button"
          // Design: `.chip` (ghost) + `.chip.b` (primary). The first/most-forward
          // sibling is the primary action.
          className={i === 0 ? "artifact-foot-chip is-primary" : "artifact-foot-chip"}
          onClick={a.onClick}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 12.5,
            padding: "7px 14px",
            borderRadius: 30,
            cursor: "pointer",
            whiteSpace: "nowrap",
            border:
              i === 0 ? "1px solid var(--accent)" : "1px solid var(--line-strong)",
            background: i === 0 ? "var(--accent)" : "var(--surface-2)",
            color: i === 0 ? "#fff" : "var(--ink-2)",
          }}
        >
          {a.icon}
          {a.label}
        </button>
      ))}
    </div>
  )
}
