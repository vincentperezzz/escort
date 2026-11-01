import { useEffect, useState } from "react";
import { Star, WarningCircle } from "@phosphor-icons/react";
import { post } from "../lib/api.js";

const DIAGRAM_W = 260;
const DIAGRAM_H = 112;

/** Which option a saved choice lights up: the primary display's pill stands for "primary". */
function activeKey(selected, monitors) {
  if (selected === "all") return "all";
  const primary = monitors.find((m) => m.primary);
  if (selected === "primary" || (primary && selected === primary.index)) return "primary";
  return selected;
}

/**
 * "Screen to watch": a to-scale sketch of the display layout plus one pill per
 * display. Writes go to POST /api/monitors and apply to a running meeting at once.
 */
export default function MonitorPicker({ status, refresh, hideFromShare }) {
  const data = status?.monitors || {};
  const monitors = data.monitors || [];
  const [selected, setSelected] = useState(data.selected ?? "primary");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!busy && data.selected !== undefined) setSelected(data.selected);
  }, [data.selected, busy]);

  async function choose(value) {
    if (busy) return;
    const previous = selected;
    setSelected(value);
    setError("");
    setBusy(true);
    try {
      const next = await post("/api/monitors", { selected: value });
      if (next?.selected !== undefined) setSelected(next.selected);
      refresh?.();
    } catch (err) {
      setSelected(previous);
      setError(`Couldn't switch displays (${err.message || "no response"}). Still watching the previous one.`);
    } finally {
      setBusy(false);
    }
  }

  if (!monitors.length) {
    return <p className="px-5 pb-4 pt-1 text-[13px] text-muted">Escort can't list your displays right now. It watches your main display.</p>;
  }

  const active = activeKey(selected, monitors);
  const label = data.effective?.label || "your main display";
  const options = [
    ...monitors.map((m) => ({ key: m.primary ? "primary" : m.index, value: m.primary ? "primary" : m.index, monitor: m })),
    ...(monitors.length > 1 ? [{ key: "all", value: "all", monitor: null }] : []),
  ];

  return (
    <div className="flex flex-col gap-4 px-5 pb-5 pt-2">
      {monitors.length > 1 ? <Layout monitors={monitors} active={active} onPick={choose} disabled={busy} /> : null}

      <div role="radiogroup" aria-label="Screen to watch" className="flex flex-wrap gap-1.5">
        {options.map(({ key, value, monitor }) => {
          const on = key === active;
          return (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={busy && !on}
              onClick={() => choose(value)}
              className={`press inline-flex h-9 items-center gap-2 rounded-full px-3.5 text-[13px] font-medium transition-colors duration-300 ease-glass disabled:opacity-60 ${
                on
                  ? "bg-accent-soft text-[#d9d3ff] shadow-[inset_0_0_0_1px_rgba(139,124,255,0.45)]"
                  : "bg-white/[0.04] text-ink/85 hairline hover:bg-white/[0.08] hover:text-ink"
              }`}
            >
              {monitor ? (
                <>
                  <span>{monitor.name}</span>
                  <span className={`mono text-[11.5px] font-normal ${on ? "text-[#c6bfff]/80" : "text-faint"}`}>
                    {monitor.width}×{monitor.height}
                  </span>
                  {monitor.primary ? <Star size={12} weight="fill" className={on ? "text-[#c6bfff]" : "text-faint"} aria-label="primary" /> : null}
                </>
              ) : (
                <span>All displays</span>
              )}
            </button>
          );
        })}
      </div>

      <p className="text-[12.5px] leading-relaxed text-muted" role="status">
        Watching <span className="text-ink">{label}</span>
        {monitors.length === 1 ? ". It's the only display connected." : ". Switching takes effect on the next frame, even mid-meeting."}
      </p>
      {data.notice ? (
        <p className="flex items-start gap-1.5 text-[12.5px] leading-snug text-warn">
          <WarningCircle size={14} className="mt-px shrink-0" aria-hidden />
          {data.notice}
        </p>
      ) : null}
      {hideFromShare === false && data.overlay_on_watched !== false ? (
        <p className="text-[12.5px] leading-snug text-faint">
          Hide from screen share is off, so if the overlay sits on {label === "All displays" ? "any display" : label}, Escort may read its own overlay.
        </p>
      ) : null}
      {error ? (
        <p className="text-[12.5px] text-conflict" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Displays drawn to scale from their desktop coordinates; click one to watch it. */
function Layout({ monitors, active, onPick, disabled }) {
  const minX = Math.min(...monitors.map((m) => m.left));
  const minY = Math.min(...monitors.map((m) => m.top));
  const maxX = Math.max(...monitors.map((m) => m.left + m.width));
  const maxY = Math.max(...monitors.map((m) => m.top + m.height));
  const scale = Math.min(DIAGRAM_W / (maxX - minX || 1), DIAGRAM_H / (maxY - minY || 1));
  const width = (maxX - minX) * scale;
  const height = (maxY - minY) * scale;
  const gap = 3;

  return (
    <div className="grid place-items-center rounded-[18px] bg-black/25 py-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.04),inset_0_0_0_1px_rgba(255,255,255,0.05)]">
      <div className="relative" style={{ width, height }} aria-hidden>
        {monitors.map((m) => {
          const on = active === "all" || active === m.index || (active === "primary" && m.primary);
          return (
            <button
              key={m.index}
              type="button"
              tabIndex={-1}
              disabled={disabled}
              onClick={() => onPick(m.primary ? "primary" : m.index)}
              title={m.label}
              className={`absolute grid place-items-center rounded-[7px] transition-[background-color,box-shadow,color] duration-300 ease-glass ${
                on
                  ? "bg-[rgba(139,124,255,0.22)] text-[#e4dfff] shadow-[inset_0_0_0_1.5px_rgba(139,124,255,0.75),0_6px_18px_-6px_rgba(139,124,255,0.45)]"
                  : "bg-white/[0.05] text-faint shadow-[inset_0_0_0_1px_rgba(255,255,255,0.12)] hover:bg-white/[0.09] hover:text-muted"
              }`}
              style={{
                left: (m.left - minX) * scale + gap / 2,
                top: (m.top - minY) * scale + gap / 2,
                width: m.width * scale - gap,
                height: m.height * scale - gap,
              }}
            >
              <span className="text-[15px] font-semibold tracking-[-0.02em]">{m.index}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
