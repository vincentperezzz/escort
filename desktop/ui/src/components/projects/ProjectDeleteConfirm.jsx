import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "@phosphor-icons/react";
import { Button } from "../ui.jsx";
import { api } from "../../lib/api.js";
import { useFocusTrap } from "./useFocusTrap.js";

const REMOVED = [
  "Uploaded files in this project",
  "Imported Notion and Google items",
  "Local folder watch paths",
  "Prep chat history",
  "Recaps",
  "Linked calendar meetings",
];

export default function ProjectDeleteConfirm({ project, onClose, onDeleted, refresh }) {
  const trapRef = useFocusTrap(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape" && !busy) onClose();
    }
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose, busy]);

  async function confirm() {
    if (!project?.id || busy) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/projects/${project.id}`, { method: "DELETE" });
      await refresh();
      onDeleted?.();
      onClose();
    } catch (err) {
      setError(err.message || "Could not delete project.");
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[210] flex items-center justify-center p-4"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div className="absolute inset-0 bg-black/75" aria-hidden />
      <div
        ref={trapRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="project-delete-title"
        aria-describedby="project-delete-desc"
        tabIndex={-1}
        className="relative flex max-h-[calc(100vh-2rem)] w-full max-w-md flex-col overflow-hidden rounded-2xl border border-line-strong bg-[#14151c] shadow-[var(--glow)] fade-in"
      >
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-white/[0.08] p-5">
          <div className="min-w-0">
            <h2 id="project-delete-title" className="text-[17px] font-semibold tracking-[-0.02em] text-ink">
              Delete project?
            </h2>
            <p id="project-delete-desc" className="mt-1.5 text-[13px] leading-relaxed text-muted">
              <span className="font-medium text-ink">{project?.name}</span> and everything tied to it will be removed from Escort on this computer.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
            className="press grid h-9 w-9 shrink-0 place-items-center rounded-full text-muted hover:bg-white/[0.08] hover:text-ink disabled:opacity-45"
          >
            <X size={16} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto scroll-thin px-5 py-4">
          <p className="text-[12px] font-medium uppercase tracking-wider text-muted">This removes</p>
          <ul className="mt-2 space-y-1.5 text-[13px] text-ink/90">
            {REMOVED.map((line) => (
              <li key={line} className="flex gap-2">
                <span className="text-conflict" aria-hidden>
                  ·
                </span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
          {error ? (
            <p className="mt-4 text-[13px] text-conflict" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-white/[0.08] p-5">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" className="!bg-conflict hover:!bg-conflict/90" onClick={confirm} disabled={busy}>
            {busy ? "Deleting…" : "Delete project"}
          </Button>
        </div>
      </div>
    </div>,
    document.body
  );
}
