import { useEffect, useState } from "react";
import { WarningCircle } from "@phosphor-icons/react";
import { Dot } from "./ui.jsx";

function ago(iso, now) {
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 2) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  return `${Math.round(m / 60)} h ago`;
}

/** "Display 2" when there is a choice to make, "your screen" on a one-display machine. */
function displayName(label, count) {
  if (!label || count === 1) return "your screen";
  return label === "All displays" ? "all displays" : label;
}

/**
 * One quiet line that says whether Escort is looking at the screen. It only
 * claims monitoring when `status.screen.monitoring` is true; a capture error
 * is shown in a calm warning style instead.
 */
export default function ScreenLine({ status, className = "" }) {
  const screen = status?.screen;
  const error = status?.capture_error;
  const monitoring = Boolean(screen?.monitoring) && !error;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!monitoring) return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [monitoring]);

  if (error) {
    const text = typeof error === "string" ? error : error.message || "Screen capture hit a problem.";
    return (
      <p className={`flex items-start gap-1.5 text-[12px] leading-snug text-warn ${className}`} role="status">
        <WarningCircle size={14} className="mt-px shrink-0" aria-hidden />
        <span className="min-w-0 text-pretty">{text}</span>
      </p>
    );
  }
  if (!screen) return null;
  if (!monitoring) {
    return status?.listening ? <p className={`text-[12px] text-faint ${className}`}>Screen not being watched</p> : null;
  }
  const last = screen.last_frame_at ? ago(screen.last_frame_at, now) : "";
  const title = Number.isFinite(screen.frames_seen) ? `${screen.frames_seen} frames seen, ${screen.changed_frames ?? 0} changed` : undefined;
  const watched = displayName(screen.monitor, (status?.monitors?.monitors || []).length);
  return (
    <p className={`flex min-w-0 items-center gap-2 text-[12px] text-muted ${className}`} role="status" title={title}>
      <Dot tone="mint" live />
      <span className="truncate">
        Watching {watched}
        {last ? ` · last change ${last}` : " · no changes yet"}
      </span>
    </p>
  );
}
