import { Button } from "./ui.jsx";
import { DEST_LABEL } from "../lib/notes.js";

/** "Save to Google Docs | Notion" switch. Renders nothing unless both destinations are on. */
export default function NoteDestination({ enabled, labels = DEST_LABEL, dest, onChange, className = "" }) {
  if (!enabled || enabled.length < 2) return null;
  return (
    <div role="radiogroup" aria-label="Save notes to" className={`inline-flex items-center gap-1 rounded-full bg-white/[0.04] p-0.5 hairline ${className}`}>
      <span className="pl-2.5 pr-1 text-[11.5px] text-faint">Save to</span>
      {enabled.map((id) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={dest === id}
          onClick={() => onChange(id)}
          className={`press h-7 rounded-full px-3 text-[12px] font-medium ${
            dest === id ? "bg-white/[0.1] text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]" : "text-muted hover:text-ink"
          }`}
        >
          {labels[id] || id}
        </button>
      ))}
    </div>
  );
}

/**
 * Confirm before Escort edits an existing page: shows exactly what will be added.
 * `preview` is the backend's { summary, lines } read from the page; nothing is written until Confirm.
 */
export function UpdatePreview({ preview, onConfirm, onCancel, busy = false, className = "" }) {
  return (
    <div className={`rounded-2xl bg-white/[0.03] p-4 hairline ${className}`} role="group" aria-label="Review the change">
      <p className="text-[12.5px] font-medium">{preview.summary}</p>
      <ul className="mono mt-2 space-y-1 text-[12px] leading-relaxed text-ink/80">
        {(preview.lines || []).slice(0, 8).map((line, i) => (
          <li key={i} className="whitespace-pre-wrap break-words">
            {line || " "}
          </li>
        ))}
      </ul>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="primary" onClick={onConfirm} disabled={busy}>
          {busy ? "Updating…" : "Confirm"}
        </Button>
        <Button size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
