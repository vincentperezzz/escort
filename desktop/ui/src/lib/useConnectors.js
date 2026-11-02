import { useCallback, useEffect, useRef, useState } from "react";
import { api, post } from "./api.js";

const POLL_MS = 2000;
const POLL_LIMIT_MS = 120000;

// Used when the backend has not shipped the connectors API yet.
const FALLBACK = [
  { id: "google", label: "Google (Calendar + Drive)", connected: false, account: "", mode: "mock", configured: false, setup_hint: "" },
  { id: "notion", label: "Notion", connected: false, account: "", mode: "mock", configured: false, setup_hint: "" },
];

/** Connector status, connect/disconnect actions, and OAuth polling. */
export function useConnectors({ onChange } = {}) {
  const [providers, setProviders] = useState(null);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState({}); // id -> "connecting" | "waiting" | "disconnecting"
  const [errors, setErrors] = useState({}); // id -> message
  const [authUrls, setAuthUrls] = useState({});
  const timers = useRef({});
  const alive = useRef(true);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const setFlag = (setter, id, value) =>
    setter((cur) => {
      const next = { ...cur };
      if (value === undefined || value === "") delete next[id];
      else next[id] = value;
      return next;
    });

  const load = useCallback(async () => {
    try {
      const data = await api("/api/connectors/status");
      if (!alive.current) return null;
      const list = Array.isArray(data?.providers) ? data.providers : [];
      setProviders(list);
      setUnavailable(false);
      return list;
    } catch {
      if (!alive.current) return null;
      setUnavailable(true);
      setProviders((cur) => cur ?? FALLBACK);
      return null;
    }
  }, []);

  const stopPolling = useCallback((id) => {
    if (timers.current[id]) {
      clearInterval(timers.current[id]);
      delete timers.current[id];
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    load();
    const running = timers.current;
    return () => {
      alive.current = false;
      Object.values(running).forEach(clearInterval);
    };
  }, [load]);

  const poll = useCallback(
    (id) => {
      stopPolling(id);
      const started = Date.now();
      timers.current[id] = setInterval(async () => {
        const list = await load();
        const hit = list?.find((p) => p.id === id);
        if (hit?.connected) {
          stopPolling(id);
          setFlag(setBusy, id);
          setFlag(setAuthUrls, id);
          onChangeRef.current?.();
        } else if (Date.now() - started > POLL_LIMIT_MS) {
          stopPolling(id);
          setFlag(setBusy, id);
          setFlag(setErrors, id, "Still waiting on Google. Try Connect again when you are ready.");
        }
      }, POLL_MS);
    },
    [load, stopPolling],
  );

  const connectGoogle = useCallback(async () => {
    setFlag(setErrors, "google");
    setFlag(setBusy, "google", "connecting");
    try {
      const data = await post("/api/connectors/google/connect");
      if (data?.auth_url) setFlag(setAuthUrls, "google", data.auth_url);
      setFlag(setBusy, "google", "waiting");
      poll("google");
    } catch (err) {
      setFlag(setBusy, "google");
      setFlag(setErrors, "google", err.status === 404 ? "Connecting is not available in this build yet." : err.message || "Could not start Google sign-in.");
    }
  }, [poll]);

  const saveGoogleCredentials = useCallback(
    async ({ clientId, clientSecret }) => {
      setFlag(setErrors, "google");
      setFlag(setBusy, "google", "saving");
      try {
        const body = {};
        if (clientId.trim()) body.google_client_id = clientId.trim();
        if (clientSecret.trim()) body.google_client_secret = clientSecret.trim();
        await post("/api/setup", body);
        await load();
        return true;
      } catch (err) {
        setFlag(setErrors, "google", err.message || "Could not save the Google app credentials.");
        return false;
      } finally {
        setFlag(setBusy, "google");
      }
    },
    [load],
  );

  const connectNotion = useCallback(
    async (token) => {
      setFlag(setErrors, "notion");
      setFlag(setBusy, "notion", "connecting");
      try {
        await post("/api/connectors/notion/connect", { token });
        await load();
        onChangeRef.current?.();
        return true;
      } catch (err) {
        setFlag(setErrors, "notion", err.status === 404 ? "Connecting is not available in this build yet." : err.message || "Notion rejected that token.");
        return false;
      } finally {
        setFlag(setBusy, "notion");
      }
    },
    [load],
  );

  const disconnect = useCallback(
    async (id) => {
      stopPolling(id);
      setFlag(setErrors, id);
      setFlag(setBusy, id, "disconnecting");
      try {
        await post(`/api/connectors/${id}/disconnect`);
        await load();
        onChangeRef.current?.();
      } catch (err) {
        setFlag(setErrors, id, err.message || "Could not disconnect.");
      } finally {
        setFlag(setBusy, id);
        setFlag(setAuthUrls, id);
      }
    },
    [load, stopPolling],
  );

  const cancelWait = useCallback(
    (id) => {
      stopPolling(id);
      setFlag(setBusy, id);
      setFlag(setAuthUrls, id);
    },
    [stopPolling],
  );

  return { providers, unavailable, busy, errors, authUrls, connectGoogle, saveGoogleCredentials, connectNotion, disconnect, cancelWait, reload: load };
}
