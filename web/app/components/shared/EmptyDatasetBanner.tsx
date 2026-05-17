"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { ApiError, datasetsApi } from "../../lib/api"

/**
 * One-shot empty-state banner that appears at the top of the demo when no
 * datasets are registered. Onboarded users won't see it again. Network errors
 * stay silent — the banner is purely additive UX.
 */
export function EmptyDatasetBanner() {
  const [empty, setEmpty] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    datasetsApi
      .list()
      .then((r) => {
        if (!cancelled) setEmpty(r.datasets.length === 0)
      })
      .catch((e) => {
        // 401 → auth gate handles redirect; anything else → don't show banner.
        if (!cancelled) setEmpty(false)
        if (!(e instanceof ApiError)) console.warn("EmptyDatasetBanner list failed:", e)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (!empty) return null

  return (
    <div className="empty-banner" role="region" aria-label="Onboarding required">
      <div className="msg">
        <strong>No datasets yet.</strong> Upload your first company&apos;s sources to see a weekly brief.
      </div>
      <Link href="/onboard" className="cta">
        Onboard a company →
      </Link>
      <style jsx>{`
        .empty-banner {
          display: flex;
          align-items: center;
          gap: 16px;
          padding: 12px 20px;
          background: linear-gradient(180deg, rgba(74, 140, 91, 0.12), rgba(74, 140, 91, 0.04));
          border-bottom: 1px solid rgba(74, 140, 91, 0.25);
          color: #e6e6ea;
          font-size: 14px;
        }
        .msg { flex: 1; }
        .cta {
          background: #e6e6ea;
          color: #0a0a0c;
          font-weight: 600;
          font-size: 13px;
          padding: 8px 14px;
          border-radius: 8px;
          text-decoration: none;
        }
        .cta:hover { background: #ffffff; }
      `}</style>
    </div>
  )
}
