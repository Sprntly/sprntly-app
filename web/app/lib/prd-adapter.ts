/**
 * Convert the Markdown PRD returned by /v1/prd/generate into the
 * structured PrdState shape that PrdScreen renders.
 */
import type { PrdState } from "../types/content"

type Section = PrdState["sections"][number]

const HEADING_RULE = /^─+$/

export function markdownToPrdState(markdown: string): PrdState {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n")
  let title = ""
  const sections: Section[] = []
  let currentBullets: string[] | null = null
  const flushBullets = () => {
    if (currentBullets && currentBullets.length > 0) {
      sections.push({ type: "ul", items: currentBullets })
    }
    currentBullets = null
  }

  for (const raw of lines) {
    const line = raw.trim()
    if (!line) {
      flushBullets()
      continue
    }
    if (HEADING_RULE.test(line)) {
      // Decorative dividers ─────── that David's template uses between sections
      flushBullets()
      continue
    }
    if (line.startsWith("# ")) {
      flushBullets()
      const t = line.slice(2).trim()
      if (!title) title = t
      else sections.push({ type: "h2", text: t })
      continue
    }
    if (line.startsWith("## ")) {
      flushBullets()
      sections.push({ type: "h2", text: line.slice(3).trim() })
      continue
    }
    if (line.startsWith("### ") || line.startsWith("#### ")) {
      flushBullets()
      sections.push({ type: "h2", text: line.replace(/^#+\s*/, "").trim() })
      continue
    }
    if (line.startsWith("- ") || line.startsWith("* ")) {
      currentBullets ??= []
      currentBullets.push(line.slice(2).trim())
      continue
    }
    // Numbered list lines also become bullets
    if (/^\d+\.\s/.test(line)) {
      currentBullets ??= []
      currentBullets.push(line.replace(/^\d+\.\s/, "").trim())
      continue
    }
    flushBullets()
    sections.push({ type: "p", text: line })
  }
  flushBullets()

  return {
    metaLine: `Generated ${new Date().toLocaleDateString()}`,
    title: title || "PRD",
    sections,
  }
}
