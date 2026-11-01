import { useEffect, useState } from "react";
import { UploadSimple, Trash, PaperPlaneRight, CaretDown } from "@phosphor-icons/react";
import { Button, Panel, PageHeader, Pill } from "../components/ui.jsx";
import ConnectAccounts from "../components/ConnectAccounts.jsx";
import { api, apiUpload, post, formatEventWhen } from "../lib/api.js";

export default function ContextScreen({ status, refresh }) {
  const ctx = status?.context || {};
  const sections = status?.meeting?.prd_sections || [];
  const [paste, setPaste] = useState("");
  const [drag, setDrag] = useState(false);
  const [notionQ, setNotionQ] = useState("BayaniPay");
  const [driveQ, setDriveQ] = useState("SOW");
  const [notionHits, setNotionHits] = useState([]);
  const [driveHits, setDriveHits] = useState([]);
  const [proposals, setProposals] = useState(ctx.prep?.proposals || []);
  const [picked, setPicked] = useState(new Set(ctx.prep?.confirmed_ids || []));
  const [chatIn, setChatIn] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [showConnect, setShowConnect] = useState(false);
  const prepEventId = ctx.prep?.selected_event_id;
  const prepEvent = (ctx.upcoming || []).find((e) => e.id === prepEventId);
  const items = ctx.items || [];
  const chat = ctx.chat || [];

  useEffect(() => {
    setProposals(ctx.prep?.proposals || []);
    setPicked(new Set(ctx.prep?.confirmed_ids || []));
  }, [ctx.prep?.proposals, ctx.prep?.confirmed_ids]);

  async function uploadFiles(fileList) {
    for (const file of [...fileList]) await apiUpload("/api/context/upload", file);
    refresh();
  }
  async function savePaste(event) {
    event.preventDefault();
    if (!paste.trim()) return;
    await post("/api/notes", { text: paste, name: "notes.md" });
    setPaste("");
    refresh();
  }
  async function search(source, query, setter) {
    const data = await post("/api/context/search", { source, query });
    setter(data.hits || []);
  }
  async function importHit(source, externalId) {
    await post("/api/context/import", { source, external_id: externalId });
    refresh();
  }
  async function removeItem(id) {
    await api(`/api/context/items/${id}`, { method: "DELETE" });
    refresh();
  }
  async function rematch() {
    if (!prepEventId) return;
    const data = await post("/api/context/match", { event_id: prepEventId });
    setProposals(data.proposals || []);
    setPicked(new Set((data.proposals || []).slice(0, 3).map((p) => p.item_id)));
  }
  async function confirmPrep() {
    if (!prepEventId) return;
    await post("/api/context/prep", { event_id: prepEventId, item_ids: [...picked] });
    refresh();
  }
  async function sendChat(event) {
    event.preventDefault();
    if (!chatIn.trim()) return;
    setChatBusy(true);
    try {
      await post("/api/context/chat", { message: chatIn });
      setChatIn("");
      refresh();
    } finally {
      setChatBusy(false);
    }
  }
  function togglePick(id) {
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const connectors = ctx.connectors || [];
  const anyDemo = connectors.some((c) => c.mode === "mock");

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Context"
        lede="Everything Escort checks the call against. Upload docs, pull from Notion and Drive, then prep the next meeting."
      >
        <Button variant="secondary" size="sm" aria-expanded={showConnect} onClick={() => setShowConnect((v) => !v)}>
          Connect accounts
          <CaretDown size={14} aria-hidden className={showConnect ? "rotate-180" : ""} />
        </Button>
      </PageHeader>

      {showConnect ? (
        <div className="mb-6 rounded-xl border border-line bg-bg p-4">
          <ConnectAccounts compact onChange={refresh} />
        </div>
      ) : anyDemo ? (
        <p className="mb-6 flex flex-wrap items-center gap-2 text-sm text-muted">
          <Pill tone="warn">Demo data</Pill>
          Searches below return fictional BayaniPay fixtures until you connect real accounts.
          <button type="button" className="font-semibold text-accent hover:underline" onClick={() => setShowConnect(true)}>
            Connect accounts
          </button>
        </p>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-4">
          <div
            className={`rounded-xl border-2 border-dashed px-5 py-7 text-center transition-colors ${
              drag ? "border-accent bg-accent-soft" : "border-line-strong bg-surface"
            }`}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              if (e.dataTransfer.files?.length) uploadFiles(e.dataTransfer.files);
            }}
          >
            <UploadSimple size={26} className="mx-auto text-muted" aria-hidden />
            <p className="mt-2 text-sm font-medium">Drop PDF, DOCX, MD, or TXT here</p>
            <p className="mt-0.5 text-xs text-muted">Split by headings, kept for this meeting only</p>
            <label className="press mt-4 inline-flex h-10 cursor-pointer items-center rounded-lg bg-accent px-4 text-sm font-semibold text-accent-ink focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent">
              Choose files
              <input
                type="file"
                className="sr-only"
                accept=".pdf,.docx,.md,.txt,application/pdf,text/plain,text/markdown"
                multiple
                onChange={(e) => e.target.files?.length && uploadFiles(e.target.files)}
              />
            </label>
          </div>

          <form onSubmit={savePaste} className="space-y-2">
            <label className="block">
              <span className="text-sm font-medium">Paste scope notes</span>
              <textarea
                className="mt-1.5 min-h-[110px] w-full resize-y rounded-lg border border-line-strong bg-raised p-3 text-sm leading-relaxed placeholder:text-faint"
                value={paste}
                onChange={(e) => setPaste(e.target.value)}
                placeholder={"## Phase 2: Payment integration\nShips after KYC go-live.\n- No fee changes on this call"}
              />
            </label>
            <Button type="submit" disabled={!paste.trim()}>
              Add pasted notes
            </Button>
          </form>

          <div className="grid gap-4 md:grid-cols-2">
            <SearchBox title="Notion" query={notionQ} onQuery={setNotionQ} onSearch={() => search("notion", notionQ, setNotionHits)} hits={notionHits} onImport={(id) => importHit("notion", id)} />
            <SearchBox title="Drive" query={driveQ} onQuery={setDriveQ} onSearch={() => search("drive", driveQ, setDriveHits)} hits={driveHits} onImport={(id) => importHit("drive", id)} />
          </div>

          {prepEvent ? (
            <Panel title="Prep for this meeting" hint={`${prepEvent.title}, ${formatEventWhen(prepEvent.start)}`}>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" onClick={rematch}>
                  Re-match context
                </Button>
                <Button size="sm" variant="primary" onClick={confirmPrep}>
                  Load selected into meeting
                </Button>
              </div>
              {proposals.length === 0 ? (
                <p className="text-sm text-muted">Press Prep on Live, or Re-match, to propose docs from the title, attendees, and hints.</p>
              ) : (
                <ul className="space-y-2">
                  {proposals.map((p) => (
                    <li key={p.item_id}>
                      <label className="flex cursor-pointer gap-3 rounded-lg border border-line bg-raised px-3 py-2.5 text-sm">
                        <input type="checkbox" checked={picked.has(p.item_id)} onChange={() => togglePick(p.item_id)} className="mt-1 h-4 w-4 accent-[var(--accent)]" />
                        <span>
                          <span className="block font-medium">
                            {p.source_label}: {p.title}
                          </span>
                          <span className="block text-xs text-muted">{p.reason}</span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          ) : null}
        </div>

        <aside className="space-y-4">
          <Panel title="Library" hint={items.length ? `${items.length} item${items.length === 1 ? "" : "s"}` : undefined}>
            {items.length === 0 ? (
              <p className="text-sm text-muted">Nothing yet. Upload a file or import from Notion or Drive.</p>
            ) : (
              <ul className="scroll-thin -mx-1 max-h-64 divide-y divide-line overflow-auto px-1">
                {items.map((item) => (
                  <li key={item.id} className="flex items-start justify-between gap-2 py-2">
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-accent">{item.source_label}</p>
                      <p className="truncate text-sm font-medium">{item.title}</p>
                    </div>
                    <button type="button" onClick={() => removeItem(item.id)} className="rounded-md p-1.5 text-muted hover:bg-conflict-soft hover:text-conflict" aria-label={`Remove ${item.title}`}>
                      <Trash size={16} aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Active citations" hint="Sections Live cards can point to.">
            {sections.length === 0 ? (
              <p className="text-sm text-muted">Confirm prep to merge sections into Live cards.</p>
            ) : (
              <ul className="scroll-thin flex max-h-40 flex-wrap gap-1.5 overflow-auto">
                {sections.map((section) => (
                  <li key={section.anchor + section.heading} className="rounded-md bg-accent-soft px-2 py-1 text-xs font-medium text-accent">
                    {section.source_label || `Notes: ${section.heading}`}
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Prep chat" hint="Ask Muse what to watch for.">
            <div className="scroll-thin max-h-64 min-h-[7rem] space-y-2 overflow-auto text-sm">
              {chat.length === 0 ? (
                <p className="text-muted">Try: Ano ang dapat kong bantayan sa call na to?</p>
              ) : (
                chat.map((turn, i) => (
                  <p
                    key={i}
                    className={
                      turn.role === "user"
                        ? "ml-8 rounded-xl rounded-br-sm bg-accent-soft px-3 py-2"
                        : "mr-8 rounded-xl rounded-bl-sm bg-sunken px-3 py-2 leading-relaxed"
                    }
                  >
                    {turn.content}
                  </p>
                ))
              )}
            </div>
            <form onSubmit={sendChat} className="flex gap-2">
              <input
                className="h-10 min-w-0 flex-1 rounded-lg border border-line-strong bg-raised px-3 text-sm placeholder:text-faint"
                value={chatIn}
                onChange={(e) => setChatIn(e.target.value)}
                placeholder="What should I watch for?"
                aria-label="Message Muse"
              />
              <Button type="submit" variant="primary" disabled={chatBusy || !chatIn.trim()} aria-label="Send">
                <PaperPlaneRight size={16} aria-hidden />
              </Button>
            </form>
          </Panel>
        </aside>
      </div>
    </div>
  );
}

function SearchBox({ title, query, onQuery, onSearch, hits, onImport }) {
  return (
    <Panel title={title}>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          onSearch();
        }}
      >
        <input
          className="h-10 min-w-0 flex-1 rounded-lg border border-line-strong bg-raised px-3 text-sm"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          aria-label={`Search ${title}`}
        />
        <Button type="submit">Search</Button>
      </form>
      {hits.length ? (
        <ul className="divide-y divide-line">
          {hits.map((hit) => (
            <li key={hit.id} className="py-2.5">
              <p className="text-sm font-medium">{hit.title}</p>
              <p className="line-clamp-2 text-xs text-muted">{hit.snippet}</p>
              <button type="button" onClick={() => onImport(hit.id)} className="mt-1.5 text-xs font-semibold text-accent hover:underline">
                Import
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
