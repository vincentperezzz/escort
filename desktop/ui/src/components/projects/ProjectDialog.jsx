import { useEffect, useState } from "react";
import { Folder, X, Trash } from "@phosphor-icons/react";
import { Button } from "../ui.jsx";
import { post, api } from "../../lib/api.js";
import { useFocusTrap } from "./useFocusTrap.js";

export default function ProjectDialog({ mode, project, onClose, refresh, projectCount = 0 }) {
  const isNew = mode === "create";
  const trapRef = useFocusTrap(true);
  const [name, setName] = useState(project?.name || "");
  const [description, setDescription] = useState(project?.description || "");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  async function submit(e) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      if (isNew) {
        await post("/api/projects", {
          name: trimmed,
          description: description.trim(),
          activate: true,
        });
      } else if (project?.id) {
        await api(`/api/projects/${project.id}`, {
          method: "PUT",
          body: JSON.stringify({ name: trimmed, description: description.trim() }),
        });
      }
      await refresh();
      onClose();
    } finally {
      setBusy(false);
    }
  }

  async function deleteProj() {
    if (!project?.id || busy) return;
    if (!confirm(`Delete "${project.name}" and all of its context?`)) return;
    setBusy(true);
    try {
      await api(`/api/projects/${project.id}`, { method: "DELETE" });
      await refresh();
      onClose();
    } finally {
      setBusy(false);
    }
  }

  const title = isNew ? "New project" : "Rename project";
  const canDelete = !isNew && projectCount > 1;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4 sm:p-6"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="absolute inset-0 bg-black/70 backdrop-blur-md" aria-hidden />
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-dialog-title"
        tabIndex={-1}
        className="relative w-full max-w-md rounded-2xl border border-line-strong bg-raised p-6 shadow-[var(--glow)] fade-in"
      >
        <div className="flex items-start justify-between gap-3 pb-5">
          <div className="flex min-w-0 items-center gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">
              <Folder size={18} weight="fill" aria-hidden />
            </div>
            <div className="min-w-0">
              <h2 id="project-dialog-title" className="text-[17px] font-semibold tracking-[-0.02em] text-ink">
                {title}
              </h2>
              {isNew ? (
                <p className="mt-0.5 text-[13px] text-muted">Name and optional description — that&apos;s all you need.</p>
              ) : null}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="press grid h-9 w-9 shrink-0 place-items-center rounded-full text-muted hover:bg-white/[0.08] hover:text-ink"
          >
            <X size={16} />
          </button>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label htmlFor="project-name" className="block text-[12px] font-medium text-muted">
              Project name <span className="text-conflict">*</span>
            </label>
            <input
              id="project-name"
              type="text"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. BayaniPay rollout"
              className="field mt-2 h-11 w-full px-3.5 text-sm"
              required
            />
          </div>

          <div>
            <label htmlFor="project-description" className="block text-[12px] font-medium text-muted">
              Description <span className="font-normal text-faint">(optional)</span>
            </label>
            <textarea
              id="project-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this workspace is for"
              className="field scroll-thin mt-2 min-h-[88px] w-full resize-y px-3.5 py-2.5 text-sm leading-relaxed"
            />
          </div>

          <div className="flex items-center justify-between gap-3 pt-2">
            {canDelete ? (
              <button
                type="button"
                onClick={deleteProj}
                disabled={busy}
                className="press inline-flex items-center gap-1.5 text-[12px] font-medium text-conflict hover:underline disabled:opacity-45"
              >
                <Trash size={14} aria-hidden />
                Delete
              </button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" type="submit" disabled={!name.trim() || busy}>
                {isNew ? "Create" : "Save"}
              </Button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
