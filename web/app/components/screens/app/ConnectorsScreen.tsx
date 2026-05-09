"use client"

import { useContent } from "../../../context/ContentContext"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export function ConnectorsScreen() {
  const { content } = useContent()
  const categories = content.connectorCategories
  const connected = new Set(content.connectedConnectorIds)

  if (categories.length === 0) {
    return (
      <AppLayout>
        <div className="main-header">
          <div>
            <h1 className="main-title">Connectors</h1>
            <p className="main-sub">
              The more sources you connect, the sharper your weekly brief becomes.
            </p>
          </div>
        </div>
        <EmptyPane
          title="No connector catalog loaded"
          hint="Populate `content.connectorCategories` (groups + items) and `content.connectedConnectorIds` from Airbyte or your connections table."
          placeholders={6}
        />
      </AppLayout>
    )
  }

  const connectedCount = content.connectedConnectorIds.length
  const totalItems = categories.reduce((n, c) => n + c.items.length, 0)

  return (
    <AppLayout>
      <div className="conn-summary">
        <div className="conn-summary-inner">
          <div className="conn-summary-eyebrow">Signal coverage</div>
          <h1 className="conn-summary-headline">
            <span>{connectedCount} connected</span> of {totalItems} integrations in
            your catalog — wire live metrics from your sync service when ready.
          </h1>
          <div className="conn-summary-stats">
            <div className="conn-summary-stat">
              <strong className="pos">{connectedCount}</strong>Connected
            </div>
            <div className="conn-summary-stat">
              <strong>{totalItems}</strong>Available
            </div>
            <div className="conn-summary-stat">
              <strong>—</strong>Signals/week
            </div>
            <div className="conn-summary-stat">
              <strong>
                {categories.filter((c) => c.items.some((i) => connected.has(i.id))).length}{" "}
                of {categories.length}
              </strong>Categories covered
            </div>
          </div>
        </div>
        <button
          type="button"
          className="btn"
          style={{
            background: "var(--surface)",
            color: "var(--ink)",
            borderColor: "var(--surface)",
          }}
        >
          + Request connector
        </button>
      </div>

      {categories.map((cat) => {
        const connectedItems = cat.items.filter((i) => connected.has(i.id))
        const availableItems = cat.items.filter((i) => !connected.has(i.id))
        return (
          <div key={cat.key} className="conn-mgmt-group">
            <div className="conn-mgmt-head">
              <div className="conn-mgmt-title-row">
                <div className="conn-mgmt-icon">{cat.icon ?? "⊞"}</div>
                <div>
                  <h3 className="conn-mgmt-title">{cat.title}</h3>
                  <p className="conn-mgmt-sub">
                    {cat.subtitle ?? "Integrations in this category."}
                  </p>
                </div>
              </div>
              <div className="conn-mgmt-status">
                <span
                  className={`conn-mgmt-badge ${connectedItems.length ? "" : "none"}`}
                >
                  {connectedItems.length} connected
                </span>
              </div>
            </div>
            <div className="conn-mgmt-body">
              {connectedItems.length > 0 ? (
                <div className="conn-mgmt-connected-list">
                  {connectedItems.map((item) => (
                    <div key={item.id} className="conn-mgmt-connected-pill">
                      <div className="conn-logo">{item.logo}</div>
                      {item.name}
                    </div>
                  ))}
                </div>
              ) : null}
              {availableItems.length > 0 ? (
                <div className="conn-mgmt-available">
                  {availableItems.map((item) => (
                    <div key={item.id} className="conn-mgmt-available-chip">
                      <div className="conn-logo">{item.logo}</div>
                      {item.name}
                    </div>
                  ))}
                </div>
              ) : connectedItems.length === 0 ? (
                <div className="conn-mgmt-empty">No integrations listed for this group.</div>
              ) : null}
            </div>
          </div>
        )
      })}
    </AppLayout>
  )
}
