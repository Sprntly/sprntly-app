"use client"

import Link from "next/link"
import { IconArrowRight, IconFileText } from "@tabler/icons-react"
import { publicPath } from "../lib/public-path"
import { docsByCategory } from "./content"
import { DocsTopbar } from "./DocsTopbar"
import { DocsSearch } from "./DocsSearch"

/** Docs landing: hero with search + document cards grouped by category. */
export function DocsHome() {
  const groups = docsByCategory()

  return (
    <>
      <DocsTopbar showSearch={false} />

      <main className="docs-home">
        <section className="docs-hero">
          <h1 className="docs-hero-title">Sprntly Documentation</h1>
          <p className="docs-hero-sub">
            Guides for going from signals to PRD to prototype to build. Search
            across everything, or pick a document below.
          </p>
          <div className="docs-hero-search">
            <DocsSearch placeholder="Search the docs…" autoFocus />
          </div>
        </section>

        {groups.map((group) => (
          <section key={group.category} className="docs-home-group">
            <h2 className="docs-home-group-title">{group.category}</h2>
            <div className="docs-card-grid">
              {group.docs.map((doc) => (
                <Link
                  key={doc.slug}
                  href={publicPath(`/docs/${doc.slug}`)}
                  className="docs-card"
                >
                  <span className="docs-card-icon">
                    <IconFileText size={20} stroke={1.7} />
                  </span>
                  <span className="docs-card-body">
                    <span className="docs-card-title">{doc.title}</span>
                    <span className="docs-card-desc">{doc.description}</span>
                    <span className="docs-card-meta">
                      {doc.version ? `Version ${doc.version}` : null}
                      {doc.version && doc.updated ? " · " : null}
                      {doc.updated ? `Updated ${doc.updated}` : null}
                    </span>
                  </span>
                  <IconArrowRight
                    size={18}
                    stroke={1.8}
                    className="docs-card-arrow"
                  />
                </Link>
              ))}
            </div>
          </section>
        ))}
      </main>
    </>
  )
}
