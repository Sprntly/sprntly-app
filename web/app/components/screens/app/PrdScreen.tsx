"use client"

import type { CSSProperties, ReactNode } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import type { PrdState } from "../../../types/content"
import { renderInline } from "../../../lib/inline-md"
import { InlineChart } from "../../shared/InlineChart"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"
import {
  IconCheck,
  IconCopy,
  IconGrid,
  IconLinkInsert,
  IconListBullet,
  IconMail,
  IconRedo,
  IconUndo,
} from "../../shared/app-icons"

export function PrdScreen() {
  const { goTo, openModal, shareMenuOpen, setShareMenuOpen, showToast } =
    useNavigation()
  const { content } = useContent()
  const prd = content.prd

  const handleShare = (type: "email" | "slack" | "link") => {
    setShareMenuOpen(false)
    const messages = {
      email: {
        title: "Opening email draft",
        sub: "Your email client will open with the PRD attached.",
      },
      slack: {
        title: "Posted to Slack",
        sub: "PRD shared in #product. Your team can react & comment inline.",
      },
      link: {
        title: "Link copied",
        sub: "Anyone at sprntly.ai with the link can view this PRD.",
      },
    }
    const msg = messages[type]
    showToast(msg.title, msg.sub)
  }

  return (
    <AppLayout mainStyle={{ maxWidth: 900 }}>
      <a className="detail-back" onClick={() => goTo("detail")}>
        ← Back to evidence
      </a>

      <div className="prd-frame">
        <PrdToolbar hasDoc={!!prd} />
        {prd ? (
          <div
            className="prd-body"
            contentEditable
            spellCheck={false}
            suppressContentEditableWarning
          >
            <div className="prd-meta">{prd.metaLine}</div>
            <h1 className="prd-title">{prd.title}</h1>
            <PrdSections sections={prd.sections} />
          </div>
        ) : (
          <div className="prd-body" style={{ minHeight: 280 }}>
            <EmptyPane
              title="No PRD draft loaded"
              hint="When your LLM generates a mini-PRD, assign `content.prd` with `metaLine`, `title`, and `sections` (h2 / p / ul blocks). Toolbar actions stay available for future wiring."
              placeholders={0}
            />
          </div>
        )}

        <div className="prd-foot">
          <div className="prd-foot-left">
            <button type="button" className="btn btn-ghost btn-sm" disabled={!prd}>
              Save as draft
            </button>
          </div>
          <div className="prd-foot-right">
            <div style={{ position: "relative" }}>
              <button
                type="button"
                className="btn"
                disabled={!prd}
                onClick={(e) => {
                  e.stopPropagation()
                  if (!prd) return
                  setShareMenuOpen(!shareMenuOpen)
                }}
              >
                Share
                <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor">
                  <path d="M5 7L1 3h8z" />
                </svg>
              </button>
              {shareMenuOpen && prd && (
                <div className="share-menu open">
                  <ShareMenuItem
                    icon={<IconMail size={14} />}
                    title="Email"
                    desc="Send to teammates or stakeholders"
                    onClick={() => handleShare("email")}
                  />
                  <ShareMenuItem
                    icon={<span style={{ fontWeight: 700, fontSize: 10 }}>Sl</span>}
                    iconStyle={{ background: "#4A154B", color: "#fff" }}
                    title="Slack"
                    desc="Post to a channel"
                    onClick={() => handleShare("slack")}
                  />
                  <div className="share-menu-divider" />
                  <ShareMenuItem
                    icon={<IconCopy size={14} />}
                    title="Copy link"
                    desc="Viewable by your team"
                    onClick={() => handleShare("link")}
                  />
                </div>
              )}
            </div>
            <button
              type="button"
              className="btn btn-accent"
              disabled={!prd}
              onClick={() => prd && openModal("approve")}
            >
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <IconCheck size={16} />
                Approve & next step
              </span>
            </button>
          </div>
        </div>
      </div>
    </AppLayout>
  )
}

function PrdSections({
  sections,
}: {
  sections: PrdState["sections"]
}) {
  return (
    <>
      {sections.map((block, i) => {
        if (block.type === "h2") {
          return (
            <h2 key={i} className="prd-h2">
              {renderInline(block.text)}
            </h2>
          )
        }
        if (block.type === "p") {
          return (
            <p key={i}>{renderInline(block.text)}</p>
          )
        }
        if (block.type === "ul" && block.items) {
          return (
            <ul key={i}>
              {block.items.map((li, j) => (
                <li key={j}>{renderInline(li)}</li>
              ))}
            </ul>
          )
        }
        if (block.type === "table") {
          return (
            <table key={i} className="prd-table">
              <thead>
                <tr>
                  {block.headers.map((h, j) => (
                    <th key={j}>{renderInline(h)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, j) => (
                  <tr key={j}>
                    {row.map((cell, k) => (
                      <td key={k}>{renderInline(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
        if (block.type === "chart") {
          return (
            <InlineChart
              key={i}
              kind={block.kind}
              title={block.title}
              subtitle={block.subtitle}
              data={block.data}
            />
          )
        }
        return null
      })}
    </>
  )
}

function PrdToolbar({ hasDoc }: { hasDoc: boolean }) {
  return (
    <div className="prd-toolbar">
      <div className="prd-tools-l">
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Undo" aria-label="Undo">
          <IconUndo size={16} />
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Redo" aria-label="Redo">
          <IconRedo size={16} />
        </button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc}>
          <strong>B</strong>
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc}>
          <em>I</em>
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc}>
          <u>U</u>
        </button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc}>
          H1
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc}>
          H2
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Bullet list" aria-label="Bullet list">
          <IconListBullet size={16} />
        </button>
        <div className="prd-tool-divider" />
        <button
          type="button"
          className="prd-tool"
          disabled={!hasDoc}
          title="Insert link"
          style={{ display: "inline-flex", alignItems: "center" }}
        >
          <IconLinkInsert size={15} />
          <span style={{ marginLeft: 5 }}>Link</span>
        </button>
        <button
          type="button"
          className="prd-tool"
          disabled={!hasDoc}
          title="Insert table"
          style={{ display: "inline-flex", alignItems: "center" }}
        >
          <IconGrid size={15} />
          <span style={{ marginLeft: 5 }}>Table</span>
        </button>
      </div>
      <div className="prd-status">
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: hasDoc ? "var(--accent)" : "var(--muted)",
          }}
        />
        {hasDoc ? "Saved · Draft" : "No draft"}
      </div>
    </div>
  )
}

function ShareMenuItem({
  icon,
  iconStyle,
  title,
  desc,
  onClick,
}: {
  icon: ReactNode
  iconStyle?: CSSProperties
  title: string
  desc: string
  onClick: () => void
}) {
  return (
    <div className="share-menu-item" onClick={onClick}>
      <div className="share-menu-item-icon" style={iconStyle}>
        {icon}
      </div>
      <div>
        <div style={{ fontWeight: 600 }}>{title}</div>
        <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
          {desc}
        </div>
      </div>
    </div>
  )
}
