import { useCallback, useEffect, useState } from "react";
import { File, FilePdf, FileText, MagnifyingGlass, PresentationChart, ShieldCheck, Table } from "@phosphor-icons/react";
import { GoogleDriveLogo } from "../BrandIcons.jsx";
import ConnectAccounts from "../ConnectAccounts.jsx";
import { Button } from "../ui.jsx";
import { api, post } from "../../lib/api.js";
import ContextDialog from "./ContextDialog.jsx";

const GOOGLE_DOC = "application/vnd.google-apps.document";
const GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet";
const GOOGLE_SLIDES = "application/vnd.google-apps.presentation";
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function mimeIcon(mime) {
  if (mime === GOOGLE_DOC || mime === "text/plain" || mime === "text/markdown" || mime === "text/x-markdown") {
    return FileText;
  }
  if (mime === GOOGLE_SHEET) return Table;
  if (mime === GOOGLE_SLIDES) return PresentationChart;
  if (mime === "application/pdf") return FilePdf;
  if (mime === DOCX) return FileText;
  return File;
}

export default function GoogleImportDialog({ onClose, onImported, refresh, onConnectAccounts }) {
  const afterImport = onImported || refresh;
  const [google, setGoogle] = useState(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState([]);
  const [searched, setSearched] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [searchBusy, setSearchBusy] = useState(false);
  const [error, setError] = useState("");
  const [consentBusy, setConsentBusy] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const data = await api("/api/connectors/status");
      const row = (data?.providers || []).find((p) => p.id === "google") || null;
      setGoogle(row);
      return row;
    } catch {
      setGoogle(null);
      return null;
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const connected = Boolean(google?.connected);
  const driveReady = Boolean(google?.capabilities?.browse_drive);

  async function runSearch(event) {
    event?.preventDefault();
    if (!connected || !driveReady) return;
    setSearchBusy(true);
    setError("");
    try {
      const data = await post("/api/context/search", { source: "google", query: query.trim() });
      setHits(data.hits || []);
      setSearched(true);
    } catch (err) {
      setError(err.message || "Search failed.");
      setHits([]);
      setSearched(true);
    } finally {
      setSearchBusy(false);
    }
  }

  useEffect(() => {
    if (!connected || !driveReady) return undefined;
    let cancelled = false;
    (async () => {
      setSearchBusy(true);
      setError("");
      try {
        const data = await post("/api/context/search", { source: "google", query: "" });
        if (!cancelled) {
          setHits(data.hits || []);
          setSearched(true);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || "Search failed.");
          setHits([]);
          setSearched(true);
        }
      } finally {
        if (!cancelled) setSearchBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [connected, driveReady]);

  function toggle(id) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function importSelected() {
    if (!selected.size) return;
    setBusy(true);
    setError("");
    try {
      for (const id of selected) {
        await post("/api/context/import", { source: "google", external_id: id });
      }
      afterImport?.();
      onClose();
    } catch (err) {
      setError(err.message || "Import failed.");
    } finally {
      setBusy(false);
    }
  }

  async function allowDrive() {
    setConsentBusy(true);
    setError("");
    try {
      await post("/api/connectors/google/consent", { capability: "drive_browse" });
      const deadline = Date.now() + 120000;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const row = await loadStatus();
        if (row?.capabilities?.browse_drive) {
          onConnectAccounts?.();
          return;
        }
      }
      setError("Google did not finish granting Drive access. Try Reconnect again.");
    } catch (err) {
      setError(err.message || "Could not open Google sign-in.");
    } finally {
      setConsentBusy(false);
    }
  }

  const needsDrive = connected && !driveReady;

  return (
    <ContextDialog
      title="Import from Google Drive"
      description="Search files in your Drive, then import Docs, Sheets, Slides, PDFs, and text."
      onClose={onClose}
      wide
      footer={
        connected && driveReady ? (
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" disabled={busy || selected.size === 0} onClick={importSelected}>
              {busy ? "Importing…" : `Import${selected.size ? ` (${selected.size})` : ""}`}
            </Button>
          </>
        ) : (
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        )
      }
    >
      {!connected ? (
        <div className="rounded-2xl bg-white/[0.03] p-4 hairline">
          <p className="mb-4 text-sm text-muted">Connect Google to search and import files from your Drive.</p>
          <ConnectAccounts
            compact
            providers={["google"]}
            onChange={() => {
              loadStatus().then((row) => {
                if (row?.connected) onConnectAccounts?.();
              });
            }}
          />
        </div>
      ) : needsDrive ? (
        <div className="rounded-2xl bg-warn-soft px-4 py-4 hairline">
          <p className="text-sm text-warn">
            Your Google account is connected for Calendar, but Escort still needs permission to browse Drive. Reconnect once to grant
            read-only Drive access.
          </p>
          <Button size="sm" variant="accent" className="mt-3" onClick={allowDrive} disabled={consentBusy}>
            <ShieldCheck size={14} aria-hidden />
            {consentBusy ? "Waiting for Google…" : "Reconnect to grant Drive access"}
          </Button>
          {error ? (
            <p className="mt-3 text-xs text-warn" role="alert">
              {error}
            </p>
          ) : null}
        </div>
      ) : (
        <>
          <form className="field flex items-center gap-2 py-1 pl-4 pr-1" onSubmit={runSearch}>
            <MagnifyingGlass size={16} className="shrink-0 text-faint" aria-hidden />
            <input
              className="h-9 min-w-0 flex-1 bg-transparent text-sm outline-none"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="Search Google Drive"
              placeholder="Search Drive files"
              autoFocus
            />
            <Button type="submit" size="sm" disabled={searchBusy}>
              {searchBusy ? "Searching…" : "Search"}
            </Button>
          </form>
          {error ? (
            <p className="mt-3 text-xs text-warn" role="alert">
              {error}
            </p>
          ) : null}
          <ul className="mt-4 max-h-64 space-y-1 overflow-y-auto scroll-thin" aria-label="Google Drive search results">
            {searched && hits.length === 0 ? (
              <li className="rounded-xl px-3 py-6 text-center text-sm text-muted">No files matched. Try a different search.</li>
            ) : (
              hits.map((hit) => {
                const MimeIcon = mimeIcon(hit.mimeType);
                return (
                  <li key={hit.id}>
                    <label className="flex cursor-pointer gap-3 rounded-xl px-3 py-2.5 hover:bg-white/[0.04]">
                      <input
                        type="checkbox"
                        className="mt-1 h-4 w-4 accent-[var(--accent)]"
                        checked={selected.has(hit.id)}
                        onChange={() => toggle(hit.id)}
                      />
                      <span className="flex min-w-0 flex-1 gap-2.5">
                        <GoogleDriveLogo size={28} className="mt-0.5 shrink-0" aria-hidden />
                        <MimeIcon size={16} className="mt-0.5 shrink-0 text-[#c6bfff]" aria-hidden />
                        <span className="min-w-0">
                          <span className="block text-sm font-medium">{hit.title}</span>
                          {hit.snippet ? <span className="mt-0.5 block line-clamp-2 text-xs text-muted">{hit.snippet}</span> : null}
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })
            )}
          </ul>
        </>
      )}
    </ContextDialog>
  );
}
