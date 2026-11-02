// Dev-only fixture backend: open the Vite dev server with ?mock to see every state
// without the Python app. Production builds strip this branch entirely.
export const MOCK = import.meta.env.DEV && new URLSearchParams(window.location.search).has("mock");
let mock = null;
async function mockHandle(path, options) {
  mock ??= await import("./mock.js");
  return mock.handle(path, options);
}

export async function api(path, options = {}) {
  if (MOCK) return mockHandle(path, options);
  const headers = { ...(options.headers || {}) };
  if (options.body && typeof options.body === "string" && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let data = null;
    try {
      data = await response.json();
    } catch {
      /* body was not JSON */
    }
    const err = new Error(data?.detail || data?.error || `${response.status}`);
    err.status = response.status;
    err.code = data?.code || null; // e.g. needs_consent, api_disabled, notes_off
    err.helpUrl = data?.help_url || null;
    err.capability = data?.capability || null; // which "Allow … access" a needs_*_consent error asks for
    throw err;
  }
  return response.json();
}

export async function apiUpload(path, file) {
  if (MOCK) return mockHandle(path, { method: "POST", file });
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(path, { method: "POST", body });
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

export function post(path, payload = {}) {
  return api(path, { method: "POST", body: JSON.stringify(payload) });
}

export function formatEventWhen(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  } catch {
    return "";
  }
}

/** "in 25 min", "in 3 h", "tomorrow", or "" when the time has passed or is unparseable. */
export function relativeStart(iso, now = Date.now()) {
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";
  const mins = Math.round((t - now) / 60000);
  if (mins < -5) return "";
  if (mins <= 0) return "now";
  if (mins < 60) return `in ${mins} min`;
  const hours = Math.round(mins / 60);
  if (hours < 20) return `in ${hours} h`;
  const days = Math.round(hours / 24);
  return days <= 1 ? "tomorrow" : `in ${days} days`;
}

export function mmss(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}
