/** Overlay transparency, 30% to 100%. `value` is 0.3..1. */
export default function OpacitySlider({ value, onChange, disabled = false, className = "", showValue = true }) {
  const v = Number.isFinite(value) ? Math.min(1, Math.max(0.3, value)) : 0.92;
  const pct = Math.round(v * 100);
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <input
        type="range"
        min={30}
        max={100}
        step={1}
        value={pct}
        disabled={disabled}
        aria-label="Overlay opacity"
        onChange={(e) => onChange(Number(e.target.value) / 100)}
        className="opacity-range min-w-0 flex-1"
        style={{ "--fill": `${((pct - 30) / 70) * 100}%` }}
      />
      {showValue ? <span className="mono w-9 shrink-0 text-right text-[12px] text-muted">{pct}%</span> : null}
    </div>
  );
}
