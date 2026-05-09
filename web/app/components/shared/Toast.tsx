"use client"

import { useNavigation } from "../../context/NavigationContext"

export function Toast() {
  const { toast, hideToast } = useNavigation()

  if (!toast) return null

  return (
    <div className={`toast ${toast ? "visible" : ""}`}>
      <div className="toast-icon">✓</div>
      <div className="toast-body">
        <div className="toast-title">{toast.title}</div>
        <div className="toast-sub">
          {toast.sub}
          {toast.link && (
            <>
              {" "}
              <a className="toast-link" href="#">
                {toast.link}
              </a>
            </>
          )}
        </div>
      </div>
      <button className="toast-close" onClick={hideToast}>
        ✕
      </button>
    </div>
  )
}
