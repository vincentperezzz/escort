import { useCallback, useEffect, useState } from "react";
import { MagnifyingGlass } from "@phosphor-icons/react";
import ConnectAccounts from "../ConnectAccounts.jsx";
import { Button } from "../ui.jsx";
import { api, post } from "../../lib/api.js";
import ContextDialog from "./ContextDialog.jsx";

export default function NotionImportDialog({ onClose, onImported, onConnectAccounts }) {
  const [notion, setNotion] = useState(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState([]);
  const [searched, setSearched] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [searchBusy, setSearchBusy] = useState(false);
  const [error, setError] = useState("");

  const loadStatus = useCallback(async () => {
    try {
      const data = await api("/api/connectors/status");
      const row = (data?.providers || []).find((p) => p.id === "notion") || null;
      setNotion(row);
      return row;
    } catch {
      setNotion(null);
      return null;
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const connected = Boolean(notion?.connected);

  async function runSearch(event) {
    event?.preventDefault();
    if (!connected) return;
    setSearchBusy(true);
    setError("");
    try {
      const data = await post("/api/context/search", { source: "notion", query: query.trim() });
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
    if (!connected) return undefined;
    let cancelled = false;
    (async () => {
      setSearchBusy(true);
      setError("");
      try {
        const data = await post("/api/context/search", { source: "notion", query: "" });
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
  }, [connected]);

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
        await post("/api/context/import", { source: "notion", external_id: id });
      }
      onImported();
      onClose();
    } catch (err) {
      setError(err.message || "Import failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ContextDialog
      title="Import from Notion"
      description="Search pages Escort can access, then import the ones you need."
      onClose={onClose}
      widthClass="max-w-xl"
      footer={
        connected ? (
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
          <p className="mb-4 text-sm text-muted">Connect Notion to search and import pages into this project.</p>
          <ConnectAccounts
            compact
            onChange={() => {
              loadStatus().then((row) => {
                if (row?.connected) onConnectAccounts?.();
              });
            }}
          />
        </div>
      ) : (
        <>
          <form className="field flex items-center gap-2 py-1 pl-4 pr-1" onSubmit={runSearch}>
            <MagnifyingGlass size={16} className="shrink-0 text-faint" aria-hidden />
            <input
              className="h-9 min-w-0 flex-1 bg-transparent text-sm outline-none"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="Search Notion"
              placeholder="Search pages"
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
          <ul className="mt-4 max-h-64 space-y-1 overflow-y-auto scroll-thin" aria-label="Notion search results">
            {searched && hits.length === 0 ? (
              <li className="rounded-xl px-3 py-6 text-center text-sm text-muted">No pages matched. Try a different search.</li>
            ) : (
              hits.map((hit) => (
                <li key={hit.id}>
                  <label className="flex cursor-pointer gap-3 rounded-xl px-3 py-2.5 hover:bg-white/[0.04]">
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 accent-[var(--accent)]"
                      checked={selected.has(hit.id)}
                      onChange={() => toggle(hit.id)}
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium">{hit.title}</span>
                      {hit.snippet ? <span className="mt-0.5 block line-clamp-2 text-xs text-muted">{hit.snippet}</span> : null}
                    </span>
                  </label>
                </li>
              ))
            )}
          </ul>
        </>
      )}
    </ContextDialog>
  );
}
