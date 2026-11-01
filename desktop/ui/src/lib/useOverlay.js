import { useCallback, useEffect, useRef, useState } from "react";
import { api, post } from "./api.js";

const DEFAULTS = { hide_from_share: true, visible: false, listening: false, supported: true };

/**
 * Overlay window state from GET/POST /api/overlay. Coded defensively: when the
 * endpoint is missing, `available` is false and writes are refused locally.
 */
export function useOverlay({ pollMs = 0 } = {}) {
  const [state, setState] = useState(DEFAULTS);
  const [available, setAvailable] = useState(null); // null = unknown yet
  const [error, setError] = useState("");
  const alive = useRef(true);
  const stateRef = useRef(state);
  stateRef.current = state;

  const load = useCallback(async () => {
    try {
      const data = await api("/api/overlay");
      if (!alive.current) return;
      // Keep the same object when nothing changed, so a poll never re-renders its users.
      if (data && typeof data === "object") setState((cur) => (Object.keys(data).every((k) => cur[k] === data[k]) ? cur : { ...cur, ...data }));
      setAvailable(true);
    } catch {
      if (alive.current) setAvailable(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    load();
    const id = pollMs ? setInterval(load, pollMs) : null;
    return () => {
      alive.current = false;
      if (id) clearInterval(id);
    };
  }, [load, pollMs]);

  const update = useCallback(async (patch) => {
    setError("");
    const previous = stateRef.current;
    setState({ ...previous, ...patch });
    try {
      const data = await post("/api/overlay", patch);
      if (alive.current && data && typeof data === "object") setState((cur) => ({ ...cur, ...data }));
      setAvailable(true);
      return true;
    } catch (err) {
      if (!alive.current) return false;
      setState(previous);
      if (err.status === 404 || err.status === 405) setAvailable(false);
      else setError(err.message || "Could not update the overlay.");
      return false;
    }
  }, []);

  return { ...state, available, error, update, reload: load };
}
