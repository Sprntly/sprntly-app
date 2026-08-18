"use client"

import Link from "next/link"
import { IconArrowUpRight } from "@tabler/icons-react"
import { publicPath } from "../lib/public-path"
import { DocsSearch } from "./DocsSearch"
import { SprntlyLockup } from "../components/shared/SprntlyMark"

/** Sticky top bar shared across the docs site: brand, optional search, app link. */
export function DocsTopbar({ showSearch = true }: { showSearch?: boolean }) {
  return (
    <header className="docs-topbar">
      <div className="docs-topbar-inner">
        <Link href={publicPath("/docs")} className="docs-brand">
          {/* The drawn lockup. The docs site is public and is frequently the
              first Sprntly page anyone lands on, so it should carry the brand
              rather than the name set in the UI font with a tint on half of
              it. */}
          <span className="docs-wordmark">
            <SprntlyLockup height={19} />
          </span>
          <span className="docs-brand-tag">Docs</span>
        </Link>

        {showSearch ? (
          <div className="docs-topbar-search">
            <DocsSearch />
          </div>
        ) : (
          <span className="docs-topbar-spacer" />
        )}

        <a
          href={publicPath("/")}
          className="docs-topbar-app"
        >
          Open Sprntly
          <IconArrowUpRight size={15} stroke={1.9} />
        </a>
      </div>
    </header>
  )
}
