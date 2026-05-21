"use client"

import { useCallback, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { runPrdGeneration } from "../../../lib/runPrdGeneration"
import type {
  BriefV2CompactFinding,
  BriefV2HeroFinding,
} from "../../../lib/brief-v2-adapter"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"
import { BriefV2Render } from "../../shared/BriefV2Sections"

export function BriefScreen() {
  const { goTo, setAIBarValue, expandAiPanel, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const { briefV2, briefDetails } = content

  const [prdBusyKey, setPrdBusyKey] = useState<string | null>(null)

  const openEvidenceFor = (detailKey: string | undefined) => {
    if (detailKey && briefDetails?.[detailKey]) {
      setContent({ detail: briefDetails[detailKey] })
    }
    goTo("detail")
  }

  const handleAskAI = (question: string) => {
    expandAiPanel()
    setAIBarValue(question)
  }

  const handleSecondary = useCallback(
    async (card: BriefV2HeroFinding | BriefV2CompactFinding) => {
      const key = card.detailKey
      if (card.secondaryCtaBehavior === "generate_prd") {
        if (!key || !briefDetails?.[key]) {
          showToast(
            "Can't generate PRD",
            "Open evidence from a finding with a linked brief first.",
          )
          return
        }
        const meta = briefDetails[key].meta
        setPrdBusyKey(key)
        try {
          const result = await runPrdGeneration(meta)
          if (!result.ok) {
            showToast("PRD generation failed", result.message.slice(0, 200))
            return
          }
          setContent({ prd: result.prd })
          goTo("prd")
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e)
          showToast("PRD generation failed", msg.slice(0, 200))
        } finally {
          setPrdBusyKey(null)
        }
        return
      }
      const prompts: Record<string, string> = {
        strategy:
          "Draft a short strategy memo: decision needed, options, recommendation, and risks for leadership review.",
        open_analysis:
          "Outline the next analysis steps to confirm root cause for this signal — data cuts, cohorts, and what would falsify our hypothesis.",
        set_alert:
          "Suggest monitoring triggers and review cadence for this signal — thresholds, owners, and when to escalate to Investigate or Fix.",
      }
      expandAiPanel()
      setAIBarValue(
        prompts[card.secondaryCtaBehavior] ??
          `Help me think through next steps for: ${card.title.slice(0, 120)}`,
      )
    },
    [briefDetails, expandAiPanel, goTo, setContent, showToast],
  )

  const empty = !briefV2 || (!briefV2.hero && briefV2.supporting.length === 0)

  return (
    <AppLayout mainClassName="main--reading main--brief">
      {empty ? (
        <EmptyPane
          title="No findings in this brief"
          hint="When `/v1/brief/current` returns insights, the adapter promotes one to a hero card with chart + quote and lays the rest out as supporting findings."
          placeholders={4}
        />
      ) : (
        <BriefV2Render
          state={briefV2}
          callbacks={{
            prdBusyKey,
            onViewEvidence: openEvidenceFor,
            onAskAI: handleAskAI,
            onSecondary: handleSecondary,
          }}
        />
      )}
    </AppLayout>
  )
}
