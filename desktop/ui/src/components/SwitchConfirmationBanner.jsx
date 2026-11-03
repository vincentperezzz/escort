import { useState } from "react";
import { Folder, ArrowRight, X } from "@phosphor-icons/react";
import { Button } from "./ui.jsx";
import { post } from "../lib/api.js";

export default function SwitchConfirmationBanner({ status, refresh }) {
  const suggestion = status?.suggested_project;
  const [busy, setBusy] = useState(false);

  if (!suggestion) return null;

  async function acceptSwitch() {
    setBusy(true);
    try {
      await post(`/api/projects/${suggestion.project_id}/activate`, {});
      await post("/api/projects/dismiss-suggestion", { suggestion_id: suggestion.suggestion_id });
      refresh();
    } finally {
      setBusy(false);
    }
  }

  async function dismiss() {
    setBusy(true);
    try {
      await post("/api/projects/dismiss-suggestion", { suggestion_id: suggestion.suggestion_id });
      refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative z-30 mx-auto mt-2 w-full max-w-2xl px-4 fade-in">
      <div className="glass flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-accent/30 bg-accent/[0.08] px-4 py-2.5 shadow-lg backdrop-blur-md">
        <div className="flex min-w-0 items-center gap-2.5 text-xs text-ink/90 sm:text-sm">
          <Folder size={17} weight="fill" className="shrink-0 text-accent" />
          <span className="truncate">
            Upcoming meeting matches <strong className="font-semibold text-accent">{suggestion.project_name}</strong>. Switch context for this call?
          </span>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="accent" size="sm" onClick={acceptSwitch} disabled={busy}>
            Switch to {suggestion.project_name}
            <ArrowRight size={13} aria-hidden />
          </Button>
          <button
            type="button"
            onClick={dismiss}
            disabled={busy}
            title="Dismiss"
            aria-label="Dismiss suggestion"
            className="press grid h-7 w-7 place-items-center rounded-full text-muted hover:bg-white/10 hover:text-ink"
          >
            <X size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

