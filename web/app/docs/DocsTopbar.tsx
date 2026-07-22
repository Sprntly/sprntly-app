"use client"

import Link from "next/link"
import { IconArrowUpRight } from "@tabler/icons-react"
import { publicPath } from "../lib/public-path"
import { DocsSearch } from "./DocsSearch"

/** Sticky top bar shared across the docs site: brand, optional search, app link. */
export function DocsTopbar({ showSearch = true }: { showSearch?: boolean }) {
  return (
    <header className="docs-topbar">
      <div className="docs-topbar-inner">
        <Link href={publicPath("/docs")} className="docs-brand">
          <span className="docs-wordmark">
            spr<span>ntly</span>
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
