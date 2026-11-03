import { useState } from "react";
import { Button } from "../ui.jsx";
import { post } from "../../lib/api.js";
import ContextDialog from "./ContextDialog.jsx";

export default function PasteNoteDialog({ onClose, onSaved }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError("");
    try {
      const name = (title.trim() || "notes.md").replace(/\s+/g, " ");
      await post("/api/notes", { text, name: name.endsWith(".md") || name.endsWith(".txt") ? name : `${name}.md` });
      onSaved();
      onClose();
    } catch (err) {
      setError(err.message || "Could not save note.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ContextDialog
      title="Paste note"
      description="Plain text or Markdown. Headings become sections Live can cite."
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={busy || !text.trim()} onClick={submit}>
            {busy ? "Saving…" : "Save note"}
          </Button>
        </>
      }
    >
      <form className="space-y-4" onSubmit={submit}>
        <label className="block">
          <span className="text-[13px] font-medium text-ink/90">Title</span>
          <input
            className="field mt-2 h-10 w-full px-3.5 text-sm"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Scope notes"
            aria-label="Note title"
          />
        </label>
        <label className="block">
          <span className="text-[13px] font-medium text-ink/90">Content</span>
          <textarea
            className="field scroll-thin mt-2 min-h-[160px] w-full resize-y p-3.5 text-sm leading-relaxed"
            value={text}
            onChange={(event) => setText(event.target.value)}
            aria-label="Note content"
            autoFocus
          />
        </label>
        {error ? (
          <p className="text-xs text-warn" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </ContextDialog>
  );
}
