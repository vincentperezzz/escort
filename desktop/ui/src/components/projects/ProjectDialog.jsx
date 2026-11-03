import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "@phosphor-icons/react";
import { Button } from "../ui.jsx";
import { post, api } from "../../lib/api.js";
import { useFocusTrap } from "./useFocusTrap.js";
import ProjectDeleteConfirm from "./ProjectDeleteConfirm.jsx";

export default function ProjectDialog({ mode, project, onClose, refresh, initialDelete = false }) {
  const isNew = mode === "create";
  const trapRef = useFocusTrap(true);
  const [name, setName] = useState(project?.name || "");
  const [description, setDescription] = useState(project?.description || "");
  const [busy, setBusy] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(initialDelete);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape" && !deleteOpen) onClose();
    }
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose, deleteOpen]);

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

  const title = isNew ? "New project" : "Edit project";

  const dialog = (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !deleteOpen) onClose();
      }}
    >
      <div className="absolute inset-0 bg-black/75" aria-hidden />
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-dialog-title"
        tabIndex={-1}
        className="relative flex max-h-[calc(100vh-2rem)] w-full max-w-md flex-col overflow-hidden rounded-2xl border border-line-strong bg-[#14151c] shadow-[var(--glow)] fade-in"
      >
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-white/[0.08] p-5 pb-4">
          <div className="min-w-0 pr-2">
            <h2 id="project-dialog-title" className="text-[17px] font-semibold tracking-[-0.02em] text-ink">
              {title}
            </h2>
            {isNew ? (
              <p className="mt-0.5 text-[13px] text-muted">Name and optional description — that&apos;s all you need.</p>
            ) : null}
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

        <div className="min-h-0 flex-1 overflow-y-auto scroll-thin px-5 pb-5">
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

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" size="sm" type="button" onClick={onClose} disabled={busy}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" type="submit" disabled={!name.trim() || busy}>
                {isNew ? "Create" : "Save"}
              </Button>
            </div>
          </form>

          {!isNew && project?.id ? (
            <div className="mt-6 rounded-xl border border-conflict/25 bg-conflict-soft/30 p-4">
              <h3 className="text-[13px] font-medium text-ink">Danger zone</h3>
              <p className="mt-1 text-[12px] leading-relaxed text-muted">
                Permanently delete this project and all of its files, imports, chats, and recaps.
              </p>
              <Button
                variant="ghost"
                size="sm"
                type="button"
                className="mt-3 !text-conflict hover:!bg-conflict-soft"
                disabled={busy}
                onClick={() => setDeleteOpen(true)}
              >
                Delete project…
              </Button>
            </div>
          ) : null}
        </div>
      </div>
      {deleteOpen && project ? (
        <ProjectDeleteConfirm
          project={project}
          onClose={() => setDeleteOpen(false)}
          onDeleted={onClose}
          refresh={refresh}
        />
      ) : null}
    </div>
  );

  return createPortal(dialog, document.body);
}
