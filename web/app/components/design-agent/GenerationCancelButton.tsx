"use client"

// Shared "Cancel" control for the generation loading experience. Extracted
// from GenerationLoadingScreen's footer so the same button (and its
// data-testid contract) can also be rendered by GenerateModal during its
// "locating" phase, without a second, drifting re-implementation.

export function GenerationCancelButton({ onCancel }: { onCancel: () => void }) {
  return (
    <button
      type="button"
      className="btn btn-ghost btn-sm proto-gen-cancel-btn"
      data-testid="proto-gen-cancel-btn"
      onClick={onCancel}
    >
      Cancel
    </button>
  )
}
