import { useState, useRef, useEffect } from "react";
import { Folder, CaretDown, Plus, Check, Gear, Trash, ArrowsClockwise, X } from "@phosphor-icons/react";
import { Button } from "./ui.jsx";
import { post, api } from "../lib/api.js";

export default function ProjectSelector({ status, refresh }) {
  const activeProj = status?.active_project || { id: "proj_default", name: "General" };
  const projects = status?.projects || [activeProj];
  const [open, setOpen] = useState(false);
  const [modal, setModal] = useState(null); // { mode: "new" | "edit", proj?: object }
  const [busy, setBusy] = useState(false);
  const menuRef = useRef(null);

  // Close dropdown on outside click
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

  async function activate(id) {
    if (id === activeProj.id) {
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

  return (
    <div className="relative inline-flex items-center" ref={menuRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={busy}
        aria-expanded={open}
        aria-label="Select Project / Topic"
        title="Active Project: topics and notes are isolated per project"
        className={`press relative flex h-9 items-center gap-2 rounded-full px-3 text-[13px] font-medium transition-colors ${
          open ? "bg-white/[0.12] text-ink" : "bg-white/[0.05] text-ink/90 hover:bg-white/[0.08]"
        }`}
      >
        <Folder size={16} className="text-accent" weight="fill" aria-hidden />
        <span className="max-w-[130px] truncate sm:max-w-[180px]">{activeProj.name}</span>
        <CaretDown size={12} className={`text-muted transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>

      {open ? (
        <div className="glass absolute left-0 top-11 z-50 flex w-72 flex-col rounded-2xl p-1.5 shadow-2xl hairline fade-in">
          <div className="px-3 py-2">
            <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Projects & Topics</p>
            <p className="text-[12px] text-faint">Each project has isolated context, notes, and prep chat.</p>
          </div>

          <div className="my-1 max-h-60 overflow-y-auto space-y-0.5">
            {projects.map((p) => {
              const isSelected = p.id === activeProj.id;
              return (
                <div
                  key={p.id}
                  className={`group flex items-center justify-between rounded-xl px-2.5 py-2 text-sm transition-colors ${
                    isSelected ? "bg-white/[0.09] text-ink" : "text-ink/80 hover:bg-white/[0.05]"
                  }`}
                >
                  <button
                    type="button"
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

                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpen(false);
                      setModal({ mode: "edit", proj: p });
                    }}
                    title="Project Settings"
                    aria-label={`Configure ${p.name}`}
                    className="press ml-1 hidden h-7 w-7 place-items-center rounded-lg text-muted hover:bg-white/10 hover:text-ink group-hover:grid"
                  >
                    <Gear size={13} />
                  </button>
                </div>
              );
            })}
          </div>

          <div className="mt-1 border-t border-white/[0.08] pt-1">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setModal({ mode: "new" });
              }}
              className="press flex h-9 w-full items-center gap-2 rounded-xl px-2.5 text-[12.5px] font-medium text-accent hover:bg-white/[0.06]"
            >
              <Plus size={14} />
              <span>Create new project…</span>
            </button>
          </div>
        </div>
      ) : null}

      {modal ? <ProjectModal modal={modal} onClose={() => setModal(null)} refresh={refresh} projectsCount={projects.length} /> : null}
    </div>
  );
}

function ProjectModal({ modal, onClose, refresh, projectsCount }) {
  const isNew = modal.mode === "new";
  const p = modal.proj || {};
  const [name, setName] = useState(p.name || "");
  const [description, setDescription] = useState(p.description || "");
  const [keywords, setKeywords] = useState((p.calendar_keywords || []).join(", "));
  const [domains, setDomains] = useState((p.calendar_domains || []).join(", "));
  const [folderPath, setFolderPath] = useState(p.folder_path || "");
  const [busy, setBusy] = useState(false);
  const [syncStatus, setSyncStatus] = useState("");

  async function submit(e) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    const payload = {
      name: name.trim(),
      description: description.trim(),
      calendar_keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean),
      calendar_domains: domains.split(",").map((d) => d.trim().toLowerCase()).filter(Boolean),
      folder_path: folderPath.trim(),
    };
    try {
      if (isNew) {
        await post("/api/projects", { ...payload, activate: true });
      } else {
        await api(`/api/projects/${p.id}`, { method: "PUT", body: JSON.stringify(payload) });
      }
      refresh();
      onClose();
    } finally {
      setBusy(false);
    }
  }

  async function syncFolder() {
    if (!p.id || !folderPath.trim()) return;
    setBusy(true);
    setSyncStatus("");
    try {
      const res = await post(`/api/projects/${p.id}/sync-folder`, {});
      const count = (res?.synced || []).length;
      setSyncStatus(`Synced ${count} file${count === 1 ? "" : "s"} from folder.`);
      refresh();
    } catch (err) {
      setSyncStatus(`Sync error: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function deleteProj() {
    if (!p.id) return;
    if (!confirm(`Delete project "${p.name}" and all its isolated context?`)) return;
    setBusy(true);
    try {
      await api(`/api/projects/${p.id}`, { method: "DELETE" });
      refresh();
      onClose();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="glass w-full max-w-lg rounded-3xl p-6 shadow-2xl hairline fade-in">
        <div className="flex items-center justify-between pb-4">
          <div className="flex items-center gap-2.5">
            <div className="grid h-8 w-8 place-items-center rounded-full bg-accent/20 text-accent">
              <Folder size={16} weight="fill" />
            </div>
            <h2 className="text-[17px] font-semibold text-ink">
              {isNew ? "New Project / Topic" : `Edit "${p.name}"`}
            </h2>
          </div>
          <button type="button" onClick={onClose} className="press grid h-8 w-8 place-items-center rounded-full text-muted hover:bg-white/10 hover:text-ink">
            <X size={16} />
          </button>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-muted">Project or Client Name</label>
            <input
              type="text"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. BayaniPay, Investor Pitch, Internal Sprint"
              className="field mt-1.5 h-10 w-full px-3.5 text-sm"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-muted">Description (optional)</label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="e.g. Scope, escrow agreements and pricing calls"
              className="field mt-1.5 h-10 w-full px-3.5 text-sm"
            />
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="block text-xs font-medium text-muted">Calendar Keywords</label>
              <input
                type="text"
                value={keywords}
                onChange={(e) => setKeywords(e.target.value)}
                placeholder="BayaniPay, SOW, Escrow"
                className="field mt-1.5 h-10 w-full px-3.5 text-xs"
              />
              <p className="mt-1 text-[11px] text-faint">Matches meeting title or notes</p>
            </div>

            <div>
              <label className="block text-xs font-medium text-muted">Attendee Domains</label>
              <input
                type="text"
                value={domains}
                onChange={(e) => setDomains(e.target.value)}
                placeholder="bayanipay.com"
                className="field mt-1.5 h-10 w-full px-3.5 text-xs"
              />
              <p className="mt-1 text-[11px] text-faint">Matches client email domain</p>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-muted">Local Watch Folder (optional)</label>
            <div className="mt-1.5 flex gap-2">
              <input
                type="text"
                value={folderPath}
                onChange={(e) => setFolderPath(e.target.value)}
                placeholder="C:\Users\username\Documents\ClientNotes"
                className="field h-10 min-w-0 flex-1 px-3.5 text-xs font-mono"
              />
              {!isNew && folderPath.trim() ? (
                <Button variant="secondary" size="sm" onClick={syncFolder} disabled={busy}>
                  <ArrowsClockwise size={13} className={busy ? "animate-spin" : ""} />
                  Sync
                </Button>
              ) : null}
            </div>
            <p className="mt-1 text-[11px] text-faint">Auto-loads .md, .txt, .pdf, .docx files in this folder.</p>
            {syncStatus ? <p className="mt-1 text-xs text-commit">{syncStatus}</p> : null}
          </div>

          <div className="flex items-center justify-between pt-4 border-t border-white/[0.08]">
            {!isNew && projectsCount > 1 ? (
              <button
                type="button"
                onClick={deleteProj}
                disabled={busy}
                className="press flex items-center gap-1.5 text-xs font-medium text-conflict hover:underline"
              >
                <Trash size={14} />
                <span>Delete Project</span>
              </button>
            ) : <div />}

            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={onClose}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" type="submit" disabled={!name.trim() || busy}>
                {isNew ? "Create Project" : "Save Changes"}
              </Button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

