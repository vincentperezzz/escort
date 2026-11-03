const TABS = new Set(["summary", "transcript", "cards"]);

export function parseLiveSub(sub) {
  const parts = (sub || "").split("/").filter(Boolean);
  if (parts[0] === "session" && parts[1]) {
    return { view: "recap", sessionId: parts[1], tab: TABS.has(parts[2]) ? parts[2] : "summary" };
  }
  if (parts[0] === "recap") {
    return { view: "recap", sessionId: null, tab: TABS.has(parts[1]) ? parts[1] : "summary" };
  }
  return { view: "idle", sessionId: null, tab: "summary" };
}

export function liveRecapHash(sessionId, tab) {
  if (sessionId) {
    const base = `session/${sessionId}`;
    return tab && tab !== "summary" ? `${base}/${tab}` : base;
  }
  return tab && tab !== "summary" ? `recap/${tab}` : "recap";
}

export function sessionDurationLabel(session) {
  const sec = Number.isFinite(session?.duration_s)
    ? session.duration_s
    : session?.duration_ms
      ? session.duration_ms / 1000
      : session?.started_at && session?.ended_at
        ? (new Date(session.ended_at) - new Date(session.started_at)) / 1000
        : 0;
  if (sec < 60) return sec > 0 ? "< 1 min" : "";
  const mins = Math.round(sec / 60);
  return `${Math.max(1, mins)} min`;
}
