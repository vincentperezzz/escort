import { useEffect, useState } from "react";
import { ArrowsClockwise } from "@phosphor-icons/react";
import { Button } from "../ui.jsx";
import { api, post } from "../../lib/api.js";
import ContextDialog from "./ContextDialog.jsx";

export default function FolderSyncDialog({ projectId, projectName, initialPath, onClose, onSynced }) {
  const [folderPath, setFolderPath] = useState(initialPath || "");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    setFolderPath(initialPath || "");
    setMessage("");
  }, [initialPath, projectId]);

  async function saveAndSync() {
    if (!projectId || !folderPath.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/projects/${projectId}`, {
        method: "PUT",
        body: JSON.stringify({ folder_path: folderPath.trim() }),
      });
      const res = await post(`/api/projects/${projectId}/sync-folder`, {});
      const count = (res?.synced || []).length;
      setMessage(`Synced ${count} file${count === 1 ? "" : "s"} into ${projectName || "this project"}.`);
      onSynced();
    } catch (err) {
      setMessage(err.message || "Sync failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ContextDialog
      title="Local folder sync"
      description={`Watch a folder and import .md, .txt, .pdf, and .docx into ${projectName || "this project"}.`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={busy || !folderPath.trim()} onClick={saveAndSync}>
            <ArrowsClockwise size={14} className={busy ? "animate-spin" : ""} aria-hidden />
            {busy ? "Syncing…" : "Save & Sync"}
          </Button>
        </>
      }
    >
      <label className="block">
        <span className="text-[13px] font-medium text-ink/90">Folder path</span>
        <input
          type="text"
          value={folderPath}
          onChange={(event) => setFolderPath(event.target.value)}
          placeholder="C:\Users\you\Documents\ProjectFiles"
          className="field mt-2 h-10 w-full px-3.5 font-mono text-xs"
          aria-label="Folder path"
          autoFocus
        />
      </label>
      {message ? (
        <p className="mt-4 text-sm text-commit" role="status">
          {message}
        </p>
      ) : null}
    </ContextDialog>
  );
}
