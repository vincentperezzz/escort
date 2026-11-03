import { useState, useRef, useEffect } from "react";
import { Folder, CaretDown, Plus, Check, PencilSimple, Trash } from "@phosphor-icons/react";
import { post } from "../lib/api.js";
import { projectList, activeProject } from "../lib/projectGate.js";
import ProjectDialog from "./projects/ProjectDialog.jsx";
import ProjectDeleteConfirm from "./projects/ProjectDeleteConfirm.jsx";

export default function ProjectSelector({ status, refresh }) {
  const activeProj = activeProject(status);
  const projects = projectList(status);
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [busy, setBusy] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    function onDocClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("pointerdown", onDocClick);
      return () => document.removeEventListener("pointerdown", onDocClick);
    }
  }, [open]);

  if (!projects.length) return null;

  async function activate(id) {
    if (id === activeProj?.id) {
      setOpen(false);
      return;
    }
    setBusy(true);
    try {
      await post(`/api/projects/${id}/activate`, {});
      refresh();
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  const label = activeProj?.name || "Choose project";

  return (
    <>
      <div className="relative inline-flex items-center" ref={menuRef}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          disabled={busy}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label="Active project"
          title="Switch project"
          className={`press relative flex h-9 items-center gap-2 rounded-full px-3 text-[13px] font-medium transition-colors ${
            open ? "bg-white/[0.12] text-ink" : "bg-white/[0.05] text-ink/90 hover:bg-white/[0.08]"
          }`}
        >
          <Folder size={16} className="text-accent" weight="fill" aria-hidden />
          <span className="max-w-[130px] truncate sm:max-w-[180px]">{label}</span>
          <CaretDown size={12} className={`text-muted transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
        </button>

        {open ? (
          <div
            role="listbox"
            aria-label="Projects"
            className="absolute left-0 top-11 z-50 flex w-72 flex-col rounded-2xl border border-line bg-raised p-1.5 shadow-2xl fade-in"
          >
            <div className="px-3 py-2">
              <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Projects</p>
            </div>

            <div className="my-1 max-h-60 space-y-0.5 overflow-y-auto scroll-thin">
              {projects.map((p) => {
                const isSelected = p.id === activeProj?.id;
                return (
                  <div
                    key={p.id}
                    className={`group flex items-center justify-between rounded-xl px-2.5 py-2 text-sm transition-colors ${
                      isSelected ? "bg-white/[0.09] text-ink" : "text-ink/80 hover:bg-white/[0.05]"
                    }`}
                  >
                    <button
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      onClick={() => activate(p.id)}
                      className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
                    >
                      <Folder size={16} className={isSelected ? "text-accent" : "text-muted"} weight={isSelected ? "fill" : "regular"} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[13px] font-medium">{p.name}</p>
                        {p.description ? <p className="truncate text-[11px] text-muted">{p.description}</p> : null}
                      </div>
                      {isSelected ? <Check size={14} className="shrink-0 text-accent" /> : null}
                    </button>

                    <div className="ml-1 flex shrink-0 opacity-0 group-hover:opacity-100">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpen(false);
                          setDialog({ mode: "edit", project: p });
                        }}
                        title="Edit project"
                        aria-label={`Edit ${p.name}`}
                        className="press grid h-7 w-7 place-items-center rounded-lg text-muted hover:bg-white/10 hover:text-ink"
                      >
                        <PencilSimple size={13} />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpen(false);
                          setDeleteTarget(p);
                        }}
                        title="Delete project"
                        aria-label={`Delete ${p.name}`}
                        className="press grid h-7 w-7 place-items-center rounded-lg text-muted hover:bg-conflict-soft hover:text-conflict"
                      >
                        <Trash size={13} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="mt-1 space-y-0.5 border-t border-white/[0.08] pt-1">
              {activeProj ? (
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    setDeleteTarget(activeProj);
                  }}
                  className="press flex h-9 w-full items-center gap-2 rounded-xl px-2.5 text-[12.5px] font-medium text-conflict hover:bg-conflict-soft/40"
                >
                  <Trash size={14} aria-hidden />
                  <span>Delete project…</span>
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  setDialog({ mode: "create" });
                }}
                className="press flex h-9 w-full items-center gap-2 rounded-xl px-2.5 text-[12.5px] font-medium text-accent hover:bg-white/[0.06]"
              >
                <Plus size={14} />
                <span>New project</span>
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {dialog ? (
        <ProjectDialog
          mode={dialog.mode}
          project={dialog.project}
          onClose={() => setDialog(null)}
          refresh={refresh}
          initialDelete={Boolean(dialog.initialDelete)}
        />
      ) : null}

      {deleteTarget ? (
        <ProjectDeleteConfirm project={deleteTarget} onClose={() => setDeleteTarget(null)} refresh={refresh} />
      ) : null}
    </>
  );
}
