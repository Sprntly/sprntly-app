"use client"

import { useNavigation } from "../../context/NavigationContext"
import { IconCheck, IconClose } from "./app-icons"

export function Toast() {
  const { toast, hideToast } = useNavigation()

  if (!toast) return null

  return (
    <div className={`toast ${toast ? "visible" : ""}`}>
      <div className="toast-icon">
        <IconCheck size={16} />
      </div>
      <div className="toast-body">
        <div className="toast-title">{toast.title}</div>
        <div className="toast-sub">
          {toast.sub}
          {toast.link && (
            <>
              {" "}
              {toast.onAction ? (
                <button
                  type="button"
                  className="btn btn-accent btn-sm"
                  onClick={() => { toast.onAction!(); hideToast() }}
                >
                  {toast.link}
                </button>
              ) : (
                <a className="toast-link" href="#">
                  {toast.link}
                </a>
              )}
            </>
          )}
        </div>
      </div>
      <button type="button" className="toast-close" onClick={hideToast} aria-label="Dismiss">
        <IconClose size={16} />
      </button>
    </div>
  )
}
