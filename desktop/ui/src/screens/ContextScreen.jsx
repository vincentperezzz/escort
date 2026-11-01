import { useEffect, useState } from "react";
import { UploadSimple, Trash, CaretDown, ArrowUpRight, Sparkle, MagnifyingGlass } from "@phosphor-icons/react";
import { Bezel, Button, Panel, PageHeader, Pill } from "../components/ui.jsx";
import ConnectAccounts from "../components/ConnectAccounts.jsx";
import { api, apiUpload, post, formatEventWhen } from "../lib/api.js";

export default function ContextScreen({ status, refresh, go }) {
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
  const [showConnect, setShowConnect] = useState(false);
  const prepEventId = ctx.prep?.selected_event_id;
  const prepEvent = (ctx.upcoming || []).find((e) => e.id === prepEventId);
  const items = ctx.items || [];
  const chatCount = (ctx.chat || []).length;

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
      <PageHeader title="Context" lede="Everything Escort checks the call against. Upload docs, pull from Notion and Drive, then prep the next meeting.">
        <Button variant="secondary" aria-expanded={showConnect} onClick={() => setShowConnect((v) => !v)}>
          Connect accounts
          <CaretDown size={14} aria-hidden className={`transition-transform duration-500 ease-glass ${showConnect ? "rotate-180" : ""}`} />
        </Button>
      </PageHeader>

      {showConnect ? (
        <Bezel className="rise mb-8" coreClassName="p-6">
          <ConnectAccounts compact onChange={refresh} />
        </Bezel>
      ) : anyDemo ? (
        <p className="mb-8 flex flex-wrap items-center gap-2.5 text-sm text-muted">
          <Pill tone="warn">Demo data</Pill>
          Searches below return fictional BayaniPay fixtures until you connect real accounts.
          <button type="button" className="font-medium text-[#c6bfff] underline-offset-4 hover:underline" onClick={() => setShowConnect(true)}>
            Connect accounts
          </button>
        </p>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-5">
          <Bezel coreClassName="p-0">
            <div
              className={`rounded-[22px] px-6 py-10 text-center transition-colors duration-300 ease-glass ${drag ? "bg-accent-soft" : ""}`}
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
              <span className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-white/[0.05] hairline">
                <UploadSimple size={20} className="text-ink/80" aria-hidden />
              </span>
              <p className="mt-4 text-[15px] font-medium">Drop PDF, DOCX, MD, or TXT here</p>
              <p className="mt-1 text-[13px] text-muted">Split by headings, kept for this meeting only</p>
              <label className="press group mt-6 inline-flex h-10 cursor-pointer items-center gap-2 rounded-full bg-ink pl-5 pr-5 text-sm font-medium text-bg hover:bg-white focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent">
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
          </Bezel>

          <Panel title="Paste scope notes" hint="Markdown headings become sections Live cards can cite.">
            <form onSubmit={savePaste} className="space-y-3">
              <textarea
                className="field scroll-thin min-h-[120px] w-full resize-y p-4 text-sm leading-relaxed"
                value={paste}
                onChange={(e) => setPaste(e.target.value)}
                aria-label="Scope notes"
                placeholder={"## Phase 2: Payment integration\nShips after KYC go-live.\n- No fee changes on this call"}
              />
              <Button type="submit" disabled={!paste.trim()}>
                Add pasted notes
              </Button>
            </form>
          </Panel>

          <div className="grid gap-5 md:grid-cols-2">
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
                <p className="text-sm text-muted">Press Prep on Home, or Re-match, to propose docs from the title, attendees, and hints.</p>
              ) : (
                <ul className="space-y-2">
                  {proposals.map((p) => (
                    <li key={p.item_id}>
                      <label className="flex cursor-pointer gap-3 rounded-2xl bg-white/[0.03] px-4 py-3 text-sm hairline hover:bg-white/[0.05]">
                        <input type="checkbox" checked={picked.has(p.item_id)} onChange={() => togglePick(p.item_id)} className="mt-0.5 h-4 w-4 accent-[var(--accent)]" />
                        <span>
                          <span className="block font-medium">
                            {p.source_label}: {p.title}
                          </span>
                          <span className="mt-0.5 block text-xs text-muted">{p.reason}</span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          ) : null}
        </div>

        <aside className="space-y-5">
          <Panel title="Library" hint={items.length ? `${items.length} item${items.length === 1 ? "" : "s"}` : undefined}>
            {items.length === 0 ? (
              <p className="text-sm text-muted">Nothing yet. Upload a file or import from Notion or Drive.</p>
            ) : (
              <ul className="scroll-thin -mx-1 flex max-h-72 flex-col gap-1 overflow-auto px-1">
                {items.map((item) => (
                  <li key={item.id} className="flex items-center justify-between gap-2 rounded-xl px-3 py-2 hover:bg-white/[0.03]">
                    <div className="min-w-0">
                      <p className="text-[11.5px] font-medium text-[#c6bfff]">{item.source_label}</p>
                      <p className="truncate text-sm">{item.title}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeItem(item.id)}
                      className="press grid h-8 w-8 shrink-0 place-items-center rounded-full text-faint hover:bg-conflict-soft hover:text-conflict"
                      aria-label={`Remove ${item.title}`}
                    >
                      <Trash size={15} aria-hidden />
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
                  <li key={section.anchor + section.heading} className="rounded-full bg-accent-soft px-2.5 py-1 text-xs text-[#c6bfff]">
                    {section.source_label || `Notes: ${section.heading}`}
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <button type="button" onClick={() => go("chat")} className="bezel bezel-sm lift group block w-full text-left">
            <span className="bezel-core flex items-center gap-4 p-5">
              <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-accent-soft text-[#c6bfff]">
                <Sparkle size={19} aria-hidden />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[15px] font-medium">Prep chat</span>
                <span className="block text-[13px] text-muted">
                  {chatCount ? `${chatCount} message${chatCount === 1 ? "" : "s"} so far` : "Ask Muse what to watch for"}
                </span>
              </span>
              <ArrowUpRight size={17} className="btn-icon text-faint group-hover:text-ink" aria-hidden />
            </span>
          </button>
        </aside>
      </div>
    </div>
  );
}

function SearchBox({ title, query, onQuery, onSearch, hits, onImport }) {
  return (
    <Panel title={title}>
      <form
        className="field flex items-center gap-2 py-1 pl-4 pr-1"
        onSubmit={(e) => {
          e.preventDefault();
          onSearch();
        }}
      >
        <MagnifyingGlass size={16} className="shrink-0 text-faint" aria-hidden />
        <input
          className="h-9 min-w-0 flex-1 bg-transparent text-sm outline-none focus-visible:outline-none"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          aria-label={`Search ${title}`}
        />
        <Button type="submit" size="sm">
          Search
        </Button>
      </form>
      {hits.length ? (
        <ul className="flex flex-col gap-1.5">
          {hits.map((hit) => (
            <li key={hit.id} className="rounded-2xl bg-white/[0.025] px-4 py-3 hairline">
              <p className="text-sm font-medium">{hit.title}</p>
              <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-muted">{hit.snippet}</p>
              <button type="button" onClick={() => onImport(hit.id)} className="mt-2 text-xs font-medium text-[#c6bfff] underline-offset-4 hover:underline">
                Import
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
