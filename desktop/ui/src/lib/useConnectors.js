import { useCallback, useEffect, useRef, useState } from "react";
import { api, post } from "./api.js";

const POLL_MS = 2000;
const POLL_LIMIT_MS = 120000;

// Used when the backend has not shipped the connectors API yet.
const FALLBACK = [
  { id: "google", label: "Google (Calendar + Docs)", connected: false, account: "", mode: "mock", configured: false, setup_hint: "" },
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
    (id, done = (p) => p?.connected) => {
      stopPolling(id);
      const started = Date.now();
      timers.current[id] = setInterval(async () => {
        const list = await load();
        const hit = list?.find((p) => p.id === id);
        if (done(hit)) {
          stopPolling(id);
          setFlag(setBusy, id);
          setFlag(setAuthUrls, id);
          onChangeRef.current?.();
        } else if (Date.now() - started > POLL_LIMIT_MS) {
          stopPolling(id);
          setFlag(setBusy, id);
          const name = id === "notion" ? "Notion" : "Google";
          setFlag(setErrors, id, `Still waiting on ${name}. Try Connect again when you are ready.`);
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

  const canWrite = (p) => Boolean(p?.capabilities?.write_notes);

  /** "Allow Docs access": sign in again so Google grants documents.readonly to an older connection. */
  const allowDocsAccess = useCallback(async () => {
    setFlag(setErrors, "google");
    setFlag(setBusy, "google", "consenting");
    try {
      const data = await post("/api/connectors/google/connect");
      if (data?.auth_url) setFlag(setAuthUrls, "google", data.auth_url);
      poll("google", (p) => Boolean(p?.capabilities?.read_docs));
    } catch (err) {
      setFlag(setBusy, "google");
      setFlag(setErrors, "google", err.message || "Could not open Google's consent page.");
    }
  }, [poll]);

  /** "Choose a page to keep updated" / remove it. Remembers the pick only; no write to Notion. */
  const setNotionTarget = useCallback(async ({ add, remove }) => {
    setFlag(setErrors, "notion");
    try {
      const data = await post("/api/connectors/notion/targets", add ? { add } : { remove });
      if (Array.isArray(data?.providers)) setProviders(data.providers);
      onChangeRef.current?.();
      return true;
    } catch (err) {
      setFlag(setErrors, "notion", err.message || "Could not change the pages to keep updated.");
      return false;
    }
  }, []);

  /** "Save notes to Drive" on/off. On without the Docs scopes opens Google's consent page. */
  const setSaveNotes = useCallback(
    async (enabled) => {
      setFlag(setErrors, "google");
      setFlag(setBusy, "google", "notes");
      try {
        const data = await post("/api/connectors/google/notes", { enabled });
        if (Array.isArray(data?.providers)) setProviders(data.providers);
        if (data?.auth_url) {
          setFlag(setAuthUrls, "google", data.auth_url);
          setFlag(setBusy, "google", "consenting");
          poll("google", canWrite);
          return;
        }
        setFlag(setBusy, "google");
        onChangeRef.current?.();
      } catch (err) {
        setFlag(setBusy, "google");
        setFlag(setErrors, "google", err.message || "Could not change note saving.");
      }
    },
    [poll],
  );

  /** "Allow note saving": ask Google for drive.file + documents only. */
  const allowNoteSaving = useCallback(async () => {
    setFlag(setErrors, "google");
    setFlag(setBusy, "google", "consenting");
    try {
      const data = await post("/api/connectors/google/consent");
      if (data?.auth_url) setFlag(setAuthUrls, "google", data.auth_url);
      poll("google", canWrite);
    } catch (err) {
      setFlag(setBusy, "google");
      setFlag(setErrors, "google", err.message || "Could not open Google's consent page.");
    }
  }, [poll]);

  /** "Save notes to Notion" on/off, and the parent page Escort writes under. No network to Notion. */
  const setNotionNotes = useCallback(async ({ enabled, parentId, parentTitle }) => {
    setFlag(setErrors, "notion");
    setFlag(setBusy, "notion", "notes");
    try {
      const body = { enabled };
      if (parentId) Object.assign(body, { parent_id: parentId, parent_title: parentTitle });
      const data = await post("/api/connectors/notion/notes", body);
      if (Array.isArray(data?.providers)) setProviders(data.providers);
      onChangeRef.current?.();
      return true;
    } catch (err) {
      setFlag(setErrors, "notion", err.message || "Could not change note saving.");
      return false;
    } finally {
      setFlag(setBusy, "notion");
    }
  }, []);

  const saveGoogleCredentials = useCallback(
    async ({ clientId, clientSecret, apiKey = "" }) => {
      setFlag(setErrors, "google");
      setFlag(setBusy, "google", "saving");
      try {
        const body = {};
        if (clientId.trim()) body.google_client_id = clientId.trim();
        if (clientSecret.trim()) body.google_client_secret = clientSecret.trim();
        if (apiKey.trim()) body.google_api_key = apiKey.trim();
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

  /** One-click Notion: the backend opens Notion's consent page; poll until the loopback finishes. */
  const connectNotionOAuth = useCallback(async () => {
    setFlag(setErrors, "notion");
    setFlag(setBusy, "notion", "connecting");
    try {
      const data = await post("/api/connectors/notion/connect");
      if (data?.auth_url) setFlag(setAuthUrls, "notion", data.auth_url);
      setFlag(setBusy, "notion", "waiting");
      poll("notion", (p) => p?.connected && p?.auth === "oauth");
    } catch (err) {
      setFlag(setBusy, "notion");
      setFlag(setErrors, "notion", err.message || "Could not open Notion in your browser.");
    }
  }, [poll]);

  /** Advanced: connect with a pasted internal-integration token instead. */
  const connectNotion = useCallback(
    async (token) => {
      setFlag(setErrors, "notion");
      setFlag(setBusy, "notion", "checking");
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

  return {
    providers,
    unavailable,
    busy,
    errors,
    authUrls,
    connectGoogle,
    setSaveNotes,
    allowNoteSaving,
    allowDocsAccess,
    setNotionTarget,
    setNotionNotes,
    saveGoogleCredentials,
    connectNotion,
    connectNotionOAuth,
    disconnect,
    cancelWait,
    reload: load,
  };
}
