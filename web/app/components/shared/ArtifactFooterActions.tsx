"use client"

import type { ReactNode } from "react"
import { useRouter } from "next/navigation"
import { IconTicket, IconMicroscope, IconFileText, IconDeviceDesktop } from "@tabler/icons-react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { prototypePath } from "../../lib/routes"

export type ArtifactId = "prd" | "evidence" | "tickets"

/**
 * A row of contextual actions rendered at the BOTTOM of each rail artifact
 * (PRD / Evidence / Tickets). It surfaces the sibling artifacts + the primary
 * next action so a PM can move PRD → evidence → prototype → tickets without
 * hunting for the tabs. The action for the artifact you're already on is
 * omitted.
 *
 * Every action reuses behavior that already exists — nothing new is invented:
 *  - View PRD / View evidence / Create ticket → openContentPanel(tab); the
 *    sibling rail tabs. Opening Tickets kicks off PRD→tickets generation
 *    (TicketsTab), so "Create ticket" lands on the ticket surface.
 *  - View prototype → router.push(prototypePath(prdId)) — the exact nav the
 *    PRD drawer / ApproveModal already use (`/prototype?prd=<id>`).
 *
 * "Create ticket" and "View prototype" both need a PRD, so they are hidden
 * when no PRD is loaded.
 */
export function ArtifactFooterActions({ current }: { current: ArtifactId }) {
  const { openContentPanel } = useNavigation()
  const { content } = useContent()
  const router = useRouter()
  const prdId = content.prd?.prd_id ?? null

  const actions: {
    key: ArtifactId | "prototype"
    label: string
    icon: ReactNode
    onClick: () => void
    show: boolean
  }[] = [
    {
      key: "tickets",
      label: "Create ticket",
      icon: <IconTicket size={14} />,
      onClick: () => openContentPanel("tickets"),
      show: prdId != null,
    },
    {
      key: "evidence",
      label: "View evidence",
      icon: <IconMicroscope size={14} />,
      onClick: () => openContentPanel("evidence"),
      show: true,
    },
    {
      key: "prd",
      label: "View PRD",
      icon: <IconFileText size={14} />,
      onClick: () => openContentPanel("prd"),
      show: true,
    },
    {
      key: "prototype",
      label: "View prototype",
      icon: <IconDeviceDesktop size={14} />,
      onClick: () => {
        if (prdId != null) router.push(prototypePath(prdId))
      },
      show: prdId != null,
    },
  ]

  const visible = actions.filter((a) => a.key !== current && a.show)
  if (visible.length === 0) return null

  return (
    <div
      className="artifact-foot-actions"
      role="group"
      aria-label="Artifact actions"
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 8,
        padding: "14px 0 4px",
        marginTop: 8,
        borderTop: "1px solid var(--line)",
      }}
    >
      {visible.map((a) => (
        <button
          key={a.key}
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={a.onClick}
          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          {a.icon}
          {a.label}
        </button>
      ))}
    </div>
  )
}
