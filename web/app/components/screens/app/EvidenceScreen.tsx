"use client"

import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"
import { PrdSections } from "./PrdScreen"

/** Full-page Evidence document — generated lazily by /v1/evidence/generate
 *  and rendered with the same markdown adapter + section primitives as PRD.
 */
export function EvidenceScreen() {
  const { goTo } = useNavigation()
  const { content } = useContent()
  const evidence = content.evidence

  return (
    <AppLayout mainStyle={{ maxWidth: 900 }}>
      <a className="detail-back" onClick={() => goTo("detail")}>
        ← Back to evidence summary
      </a>

      <div className="prd-frame">
        {evidence ? (
          <div className="prd-body">
            {evidence.metaLine ? (
              <div className="prd-meta">{evidence.metaLine}</div>
            ) : null}
            <h1 className="prd-title">{evidence.title}</h1>
            <PrdSections sections={evidence.sections} />
          </div>
        ) : (
          <div className="prd-body" style={{ minHeight: 280 }}>
            <EmptyPane
              title="No evidence page loaded"
              hint="Open a finding from the weekly brief and click 'View full evidence' to generate this document."
              placeholders={0}
            />
          </div>
        )}
      </div>
    </AppLayout>
  )
}
