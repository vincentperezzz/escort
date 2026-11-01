import { useEffect, useRef, useState } from "react";

/** Server-provided listening start time, under whichever key the backend ships. */
export function listeningSince(status) {
  return status?.listening_started_at || status?.listening_since || status?.overlay?.listening_started_at || null;
}

/**
 * Milliseconds since listening began. Prefers the server's start time; falls
 * back to the moment this window first saw `listening` flip on.
 */
export function useListenClock(listening, startedAt) {
  const localStart = useRef(null);
  const [now, setNow] = useState(() => Date.now());

  if (listening && !localStart.current) localStart.current = Date.now();
  if (!listening) localStart.current = null;

  useEffect(() => {
    if (!listening) return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [listening]);

  if (!listening) return 0;
  const server = startedAt ? new Date(startedAt).getTime() : NaN;
  const start = Number.isFinite(server) ? server : localStart.current;
  return Math.max(0, now - start);
}
