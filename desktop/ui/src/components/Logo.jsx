import { useId } from "react";

export const LOGO_IRIS = "#8b7cff";
export const LOGO_SIGNAL = "#ff6a55";

/** Static SVG markup of the mark, used for the favicon swap while listening. */
export function logoSvg(state = "idle") {
  const dot = state === "live" ? LOGO_SIGNAL : LOGO_IRIS;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="h" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".2"/><stop offset=".5" stop-color="#fff" stop-opacity=".04"/><stop offset="1" stop-color="#fff" stop-opacity=".08"/></linearGradient></defs><rect x="1" y="1" width="62" height="62" rx="18" fill="#0b0c12"/><rect x="1.5" y="1.5" width="61" height="61" rx="17.5" fill="none" stroke="url(#h)"/><path d="M19 21H45M19 32H36M19 43H45" stroke="#f4f5fa" stroke-width="5.5" stroke-linecap="round"/><circle cx="47" cy="32" r="4.6" fill="${dot}"/></svg>`;
}

/**
 * The Escort mark: a three-bar E on a squircle tile, with a status dot that is
 * iris at rest and signal red with a pulse ring while listening.
 */
export default function Logo({ state = "idle", size = 32, title = "Escort", className = "" }) {
  const id = useId().replace(/:/g, "");
  const live = state === "live";
  const dot = live ? LOGO_SIGNAL : LOGO_IRIS;
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={`shrink-0 overflow-visible ${className}`}
      role="img"
      aria-label={live ? `${title}, listening` : title}
    >
      <defs>
        <linearGradient id={`hl-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity="0.2" />
          <stop offset="0.5" stopColor="#fff" stopOpacity="0.04" />
          <stop offset="1" stopColor="#fff" stopOpacity="0.08" />
        </linearGradient>
        <radialGradient id={`gl-${id}`} cx="0.5" cy="0" r="0.9">
          <stop offset="0" stopColor="#fff" stopOpacity="0.07" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect x="1" y="1" width="62" height="62" rx="18" fill="#0b0c12" />
      <rect x="1" y="1" width="62" height="62" rx="18" fill={`url(#gl-${id})`} />
      <rect x="1.5" y="1.5" width="61" height="61" rx="17.5" fill="none" stroke={`url(#hl-${id})`} />
      <path d="M19 21H45M19 32H36M19 43H45" stroke="#f4f5fa" strokeWidth="5.5" strokeLinecap="round" fill="none" />
      {live ? <circle className="logo-pulse on" cx="47" cy="32" r="4.6" fill={dot} /> : null}
      <circle cx="47" cy="32" r="4.6" fill={dot} style={{ transition: "fill 400ms cubic-bezier(0.32,0.72,0,1)" }} />
    </svg>
  );
}
