import { useEffect, useMemo, useState } from "react";

const NAV = [
  ["live", "Live", "wave"],
  ["context", "Context", "doc"],
  ["recap", "Recap", "list"],
  ["setup", "Setup", "gear"],
  ["settings", "Settings", "sliders"],
];

function route() {
  let name = window.location.hash.replace("#/", "") || "live";
  if (name === "prd" || name === "notes") name = "context";
  return NAV.some(([id]) => id === name) ? name : "live";
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && typeof options.body === "string" && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

async function apiUpload(path, file) {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(path, { method: "POST", body });
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

export default function App() {
  const [screen, setScreen] = useState(route);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [transcriptOpen, setTranscriptOpen] = useState(true);

  async function refresh() {
    try {
      setStatus(await api("/api/status"));
      setError("");
    } catch (err) {
      setError(err.message || "Escort is quiet");
    }
  }

  useEffect(() => {
    const onHash = () => setScreen(route());
    window.addEventListener("hashchange", onHash);
    refresh();
    const id = setInterval(refresh, 1500);
    return () => {
      window.removeEventListener("hashchange", onHash);
      clearInterval(id);
    };
  }, []);

  function go(next) {
    window.location.hash = `/${next}`;
    setScreen(next);
  }

  const meeting = status?.meeting || {};
  const cards = meeting.cards || [];
  const ctx = status?.context || {};
  const noteSections = meeting.prd_sections || [];
  const latency = meeting.latency;

  return (
    <div className="flex min-h-screen flex-col bg-[var(--paper)] md:flex-row">
      <aside className="order-2 flex shrink-0 gap-1 border-t border-[var(--line)] bg-[var(--ink)] px-2 py-2 text-[var(--paper)] md:order-1 md:w-[220px] md:flex-col md:gap-0 md:border-r md:border-t-0 md:px-0 md:py-5">
        <div className="hidden px-5 md:block">
          <p className="text-[10px] font-semibold tracking-[0.28em] text-[var(--ink-muted)]">ESCORT</p>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">Meeting guard</h1>
          <p className="mt-2 text-xs leading-relaxed text-[var(--ink-muted)]">Live capture with proactive nudges. Not a recorder.</p>
        </div>
        <nav className="flex flex-1 justify-around md:mt-8 md:flex-col md:gap-0.5 md:px-3">
          {NAV.map(([id, label, icon]) => (
            <button
              key={id}
              type="button"
              onClick={() => go(id)}
              className={`flex flex-col items-center gap-0.5 rounded-xl px-3 py-2 text-[11px] font-medium md:flex-row md:gap-3 md:px-3 md:py-2.5 md:text-sm ${
                screen === id ? "bg-white/10 text-white md:bg-white/12" : "text-[var(--ink-muted)] hover:bg-white/5 hover:text-white"
              }`}
            >
              <NavIcon name={icon} active={screen === id} />
              <span>{label}</span>
              {id === "context" && (ctx.items?.length || noteSections.length) ? (
                <span className="mono hidden rounded-md bg-[var(--amber-soft)] px-1.5 py-0.5 text-[10px] text-[var(--amber)] md:ml-auto md:inline">
                  {ctx.items?.length || noteSections.length}
                </span>
              ) : null}
            </button>
          ))}
        </nav>
        <div className="hidden px-5 pb-2 md:block">
          <ListenBadge listening={status?.listening} />
          <p className="mt-3 text-[10px] text-[var(--ink-muted)]">MCP · 8747 · WhatsApp · 8748</p>
        </div>
      </aside>

      <div className="order-1 flex min-h-0 min-w-0 flex-1 flex-col md:order-2">
        {screen === "live" ? (
          <SessionBar
            listening={status?.listening}
            title={meeting.title || "Live session"}
            latency={latency}
            onListen={() => api("/api/listen", { method: "POST", body: JSON.stringify({ on: !status?.listening }) }).then(refresh)}
          />
        ) : (
          <header className="border-b border-[var(--line)] bg-[var(--paper-elevated)] px-4 py-4 md:px-8">
            <h2 className="text-lg font-semibold tracking-tight">{NAV.find(([id]) => id === screen)?.[1]}</h2>
          </header>
        )}

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {error ? (
            <p className="mx-4 mt-4 rounded-xl border border-[var(--rose-soft)] bg-[var(--rose-soft)] px-4 py-3 text-sm text-[var(--rose)] md:mx-8">
              {error}
            </p>
          ) : null}

          <div className="min-h-0 flex-1 overflow-auto px-4 py-5 md:px-8">
            {screen === "setup" ? <Setup status={status} refresh={refresh} /> : null}
            {screen === "context" ? <ContextScreen status={status} refresh={refresh} /> : null}
            {screen === "live" ? (
              <Live
                status={status}
                context={ctx}
                cards={cards}
                turns={meeting.utterances || []}
                refresh={refresh}
                transcriptOpen={transcriptOpen}
                onToggleTranscript={() => setTranscriptOpen((v) => !v)}
                onGoContext={() => go("context")}
              />
            ) : null}
            {screen === "recap" ? <Recap meeting={meeting.meeting || {}} /> : null}
            {screen === "settings" ? <Settings status={status} refresh={refresh} /> : null}
          </div>

          {screen === "live" ? (
            <TranscriptDock open={transcriptOpen} onToggle={() => setTranscriptOpen((v) => !v)} turns={meeting.utterances || []} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function SessionBar({ listening, title, latency, onListen }) {
  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-[var(--line)] bg-[var(--paper-elevated)] px-4 py-3 md:px-8">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <WaveBars active={listening} />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{title}</p>
          <p className="text-xs text-[var(--ink-muted)]">{listening ? "Capturing Me + system audio" : "Session paused"}</p>
        </div>
      </div>
      {latency?.count ? (
        <p className="mono hidden text-xs text-[var(--ink-muted)] sm:block">
          p50 {Math.round(latency.p50_ms)} ms · p95 {Math.round(latency.p95_ms)} ms
        </p>
      ) : null}
      <button
        type="button"
        onClick={onListen}
        className={`rounded-full px-4 py-2 text-sm font-semibold ${
          listening ? "bg-[var(--ink-soft)] text-white" : "bg-[var(--sky)] text-white listen-pulse"
        }`}
      >
        {listening ? "Stop" : "Start listening"}
      </button>
    </header>
  );
}

function WaveBars({ active }) {
  return (
    <div className={`flex h-9 w-9 items-end justify-center gap-0.5 rounded-xl border border-[var(--line)] bg-[var(--paper)] p-1.5 ${active ? "" : "opacity-40"}`}>
      {[0, 1, 2, 3].map((i) => (
        <span
          key={i}
          className={`w-1 rounded-full bg-[var(--mint)] ${active ? "animate-pulse" : ""}`}
          style={{ height: `${40 + (i % 3) * 18}%`, animationDelay: `${i * 120}ms` }}
        />
      ))}
    </div>
  );
}

function ListenBadge({ listening }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={`h-2 w-2 rounded-full ${listening ? "bg-[var(--mint)] listen-pulse" : "bg-[var(--ink-muted)]"}`} />
      {listening ? "Listening" : "Idle"}
    </div>
  );
}

function Live({ context, cards, turns, refresh, transcriptOpen, onToggleTranscript, onGoContext }) {
  async function act(id, action) {
    await api(`/api/cards/${id}/action`, { method: "POST", body: JSON.stringify({ action }) });
    refresh();
  }

  const sorted = useMemo(() => cards.slice().reverse(), [cards]);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 pb-24 md:pb-28">
      <UpcomingMeetings context={context} refresh={refresh} onPrep={onGoContext} />
      <p className="text-sm text-[var(--ink-muted)]">
        Insights land on their own when a line finishes, tied to your scope notes.
      </p>
      {sorted.length === 0 ? (
        <Empty
          title="Quiet for now"
          body="Start listening, or load the staged demo from Settings. Cards show up when speech crosses your notes."
        />
      ) : (
        <div className="grid gap-3">
          {sorted.map((card) => (
            <InsightCard key={card.id} card={card} onAct={act} />
          ))}
        </div>
      )}
      {!transcriptOpen && turns.length ? (
        <button type="button" onClick={onToggleTranscript} className="text-left text-xs font-medium text-[var(--sky)]">
          Show transcript ({turns.length} lines)
        </button>
      ) : null}
    </div>
  );
}

function InsightCard({ card, onAct }) {
  const tone = cardTone(card.type);
  return (
    <article className={`overflow-hidden rounded-2xl border border-[var(--line)] bg-[var(--paper-elevated)] shadow-sm ${tone.ring}`}>
      <div className="flex items-start justify-between gap-3 border-b border-[var(--line)] px-4 py-3">
        <div>
          <p className={`text-[10px] font-bold tracking-[0.18em] ${tone.label}`}>{cardLabel(card.type)}</p>
          <h3 className="mt-0.5 text-base font-semibold leading-snug">{card.title}</h3>
        </div>
        <p className="mono shrink-0 text-lg font-semibold text-[var(--amber)]">
          {Math.round(card.latency_ms)}
          <span className="ml-0.5 text-[10px] font-normal text-[var(--ink-muted)]">ms</span>
        </p>
      </div>
      <div className="px-4 py-3">
        <p className="text-sm leading-relaxed text-[var(--ink)]">{card.body}</p>
        {card.prd_section ? (
          <p className="mt-2 text-xs font-medium text-[var(--amber)]">{card.prd_section}</p>
        ) : null}
        {card.type === "PRD_CONFLICT" ? (
          <div className="mt-4 flex flex-wrap gap-2">
            <Chip onClick={() => onAct(card.id, "raise")}>Raise it</Chip>
            <Chip onClick={() => onAct(card.id, "suggest")}>Suggest reply</Chip>
            <Chip onClick={() => onAct(card.id, "ignore")}>Ignore</Chip>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function TranscriptDock({ open, onToggle, turns }) {
  return (
    <div
      className={`border-t border-[var(--line)] bg-[var(--paper-elevated)] transition-[max-height] duration-300 ${
        open ? "max-h-[min(42vh,360px)]" : "max-h-12"
      }`}
    >
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left md:px-8"
      >
        <span className="flex items-center gap-2 text-sm font-semibold">
          <WaveBars active={turns.length > 0} />
          Transcript
        </span>
        <span className="text-xs text-[var(--ink-muted)]">{open ? "Hide" : "Show"} · {turns.length} lines</span>
      </button>
      {open ? (
        <ul className="max-h-[calc(min(42vh,360px)-3rem)] space-y-2 overflow-auto px-4 pb-4 md:px-8">
          {turns.length === 0 ? (
            <li className="text-sm text-[var(--ink-muted)]">Them on the left (system audio), you on the right (mic).</li>
          ) : (
            turns.map((turn) => <TranscriptBubble key={turn.id} turn={turn} />)
          )}
        </ul>
      ) : null}
    </div>
  );
}

function TranscriptBubble({ turn }) {
  const me = turn.speaker === "Me";
  return (
    <li className={`flex ${me ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[min(100%,520px)] rounded-2xl px-3 py-2 text-sm leading-relaxed ${
          me ? "rounded-br-md bg-[var(--mint-soft)] text-[var(--ink)]" : "rounded-bl-md bg-[#e8eaef] text-[var(--ink)]"
        }`}
      >
        <span className={`text-[10px] font-bold uppercase tracking-wide ${me ? "text-[var(--mint)]" : "text-[var(--ink-muted)]"}`}>
          {turn.speaker}
        </span>
        <p className="mt-0.5">{turn.text}</p>
      </div>
    </li>
  );
}

function UpcomingMeetings({ context, refresh, onPrep }) {
  const events = context?.upcoming || [];
  const prepEventId = context?.prep?.selected_event_id;
  const [busy, setBusy] = useState("");

  async function prep(eventId) {
    setBusy(eventId);
    try {
      await api("/api/context/match", { method: "POST", body: JSON.stringify({ event_id: eventId }) });
      await refresh();
      onPrep?.();
    } finally {
      setBusy("");
    }
  }

  if (!events.length) return null;

  return (
    <section className="rounded-2xl border border-[var(--line)] bg-[var(--paper-elevated)] p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Upcoming meetings</h3>
        <span className="text-[10px] font-bold tracking-[0.16em] text-[var(--ink-muted)]">CALENDAR</span>
      </div>
      <ul className="mt-3 space-y-2">
        {events.map((event) => (
          <li
            key={event.id}
            className={`flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2.5 text-sm ${
              prepEventId === event.id ? "border-[var(--sky)] bg-[var(--paper)]" : "border-[var(--line)] bg-[var(--paper)]"
            }`}
          >
            <div className="min-w-0 flex-1">
              <p className="font-medium leading-snug">{event.title}</p>
              <p className="text-xs text-[var(--ink-muted)]">{formatEventWhen(event.start)}</p>
            </div>
            <button
              type="button"
              disabled={busy === event.id}
              onClick={() => prep(event.id)}
              className="rounded-full bg-[var(--ink)] px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
            >
              {busy === event.id ? "Matching…" : "Prep"}
            </button>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-[var(--ink-muted)]">Prep auto-matches Notion, Drive, and uploads to the event title and attendees.</p>
    </section>
  );
}

function formatEventWhen(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function ContextScreen({ status, refresh }) {
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
  const prepEventId = ctx.prep?.selected_event_id;
  const prepEvent = (ctx.upcoming || []).find((e) => e.id === prepEventId);

  useEffect(() => {
    setProposals(ctx.prep?.proposals || []);
    setPicked(new Set(ctx.prep?.confirmed_ids || []));
  }, [ctx.prep?.proposals, ctx.prep?.confirmed_ids]);

  async function uploadFiles(fileList) {
    const files = [...fileList];
    for (const file of files) {
      await apiUpload("/api/context/upload", file);
    }
    refresh();
  }

  async function savePaste(event) {
    event.preventDefault();
    if (!paste.trim()) return;
    await api("/api/notes", { method: "POST", body: JSON.stringify({ text: paste, name: "notes.md" }) });
    setPaste("");
    refresh();
  }

  async function search(source, query, setter) {
    const data = await api("/api/context/search", { method: "POST", body: JSON.stringify({ source, query }) });
    setter(data.hits || []);
  }

  async function importHit(source, externalId) {
    await api("/api/context/import", { method: "POST", body: JSON.stringify({ source, external_id: externalId }) });
    refresh();
  }

  async function removeItem(id) {
    await api(`/api/context/items/${id}`, { method: "DELETE" });
    refresh();
  }

  async function rematch() {
    if (!prepEventId) return;
    const data = await api("/api/context/match", { method: "POST", body: JSON.stringify({ event_id: prepEventId }) });
    setProposals(data.proposals || []);
    setPicked(new Set((data.proposals || []).slice(0, 3).map((p) => p.item_id)));
  }

  async function confirmPrep() {
    if (!prepEventId) return;
    await api("/api/context/prep", {
      method: "POST",
      body: JSON.stringify({ event_id: prepEventId, item_ids: [...picked] }),
    });
    refresh();
  }

  async function sendChat(event) {
    event.preventDefault();
    if (!chatIn.trim()) return;
    setChatBusy(true);
    try {
      await api("/api/context/chat", { method: "POST", body: JSON.stringify({ message: chatIn }) });
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
  const chat = ctx.chat || [];

  return (
    <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[1fr_320px]">
      <div className="space-y-5">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Meeting context</h2>
          <p className="mt-1 text-sm text-[var(--ink-muted)]">
            Upload docs, connect Notion and Drive, then prep the next call, local-first.
          </p>
        </div>

        <div
          className={`rounded-2xl border-2 border-dashed px-5 py-8 text-center transition-colors ${
            drag ? "border-[var(--sky)] bg-[var(--sky)]/5" : "border-[var(--line)] bg-[var(--paper-elevated)]"
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
          <p className="text-sm font-medium">Drop PDF, DOCX, MD, or TXT</p>
          <p className="mt-1 text-xs text-[var(--ink-muted)]">Chunked by headings · stored for this meeting only</p>
          <label className="mt-4 inline-block cursor-pointer rounded-full bg-[var(--ink)] px-4 py-2 text-sm font-semibold text-white">
            Choose files
            <input
              type="file"
              className="hidden"
              accept=".pdf,.docx,.md,.txt,application/pdf,text/plain,text/markdown"
              multiple
              onChange={(e) => e.target.files?.length && uploadFiles(e.target.files)}
            />
          </label>
        </div>

        <form onSubmit={savePaste} className="space-y-2">
          <textarea
            className="min-h-[120px] w-full resize-y rounded-2xl border border-[var(--line)] bg-[var(--paper-elevated)] p-3 text-sm leading-relaxed outline-none focus:ring-2 focus:ring-[var(--sky)]"
            value={paste}
            onChange={(e) => setPaste(e.target.value)}
            placeholder="Or paste scope bullets / markdown…"
          />
          <button type="submit" className="rounded-full border border-[var(--line)] px-4 py-2 text-sm font-medium">
            Add pasted notes
          </button>
        </form>

        <Section title="Connectors">
          <ul className="space-y-2 text-sm">
            {connectors.map((c) => (
              <li key={c.name} className="flex items-center justify-between rounded-xl bg-[var(--paper)] px-3 py-2">
                <span>{c.label}</span>
                <span className={`text-xs font-semibold ${c.mode === "mock" ? "text-[var(--amber)]" : "text-[var(--mint)]"}`}>
                  {c.mode === "mock" ? "Demo data" : "Live"}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-[var(--ink-muted)]">OAuth tokens go in Setup → keychain only. Demo runs offline with fictional BayaniPay fixtures.</p>
        </Section>

        <div className="grid gap-4 md:grid-cols-2">
          <ConnectorSearch
            title="Notion"
            query={notionQ}
            onQuery={setNotionQ}
            onSearch={() => search("notion", notionQ, setNotionHits)}
            hits={notionHits}
            onImport={(id) => importHit("notion", id)}
          />
          <ConnectorSearch
            title="Drive"
            query={driveQ}
            onQuery={setDriveQ}
            onSearch={() => search("drive", driveQ, setDriveHits)}
            hits={driveHits}
            onImport={(id) => importHit("drive", id)}
          />
        </div>

        {prepEvent ? (
          <Section title="Prep for this meeting">
            <p className="text-sm font-medium">{prepEvent.title}</p>
            <p className="text-xs text-[var(--ink-muted)]">{formatEventWhen(prepEvent.start)}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" onClick={rematch} className="rounded-full border border-[var(--line)] px-3 py-1.5 text-xs font-semibold">
                Re-match context
              </button>
              <button type="button" onClick={confirmPrep} className="rounded-full bg-[var(--sky)] px-3 py-1.5 text-xs font-semibold text-white">
                Load selected into meeting
              </button>
            </div>
            {proposals.length === 0 ? (
              <p className="mt-3 text-sm text-[var(--ink-muted)]">Hit Prep on Live or Re-match to propose docs from title, attendees, and hints.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {proposals.map((p) => (
                  <li key={p.item_id} className="flex gap-2 rounded-xl border border-[var(--line)] bg-[var(--paper)] px-3 py-2 text-sm">
                    <input type="checkbox" checked={picked.has(p.item_id)} onChange={() => togglePick(p.item_id)} className="mt-1" />
                    <div>
                      <p className="font-medium">
                        {p.source_label} · {p.title}
                      </p>
                      <p className="text-xs text-[var(--ink-muted)]">{p.reason}</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        ) : null}
      </div>

      <aside className="flex flex-col gap-4">
        <div className="rounded-2xl border border-[var(--line)] bg-[var(--paper-elevated)] p-4">
          <h3 className="text-xs font-bold tracking-[0.16em] text-[var(--ink-muted)]">LIBRARY</h3>
          {(ctx.items || []).length === 0 ? (
            <p className="mt-3 text-sm text-[var(--ink-muted)]">Nothing yet — upload or import from connectors.</p>
          ) : (
            <ul className="mt-3 max-h-56 space-y-2 overflow-auto text-sm">
              {(ctx.items || []).map((item) => (
                <li key={item.id} className="rounded-xl bg-[var(--paper)] px-3 py-2">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-xs font-bold text-[var(--amber)]">{item.source_label}</p>
                      <p className="font-medium">{item.title}</p>
                    </div>
                    <button type="button" onClick={() => removeItem(item.id)} className="text-xs text-[var(--rose)]">
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-2xl border border-[var(--line)] bg-[var(--paper-elevated)] p-4">
          <h3 className="text-xs font-bold tracking-[0.16em] text-[var(--ink-muted)]">ACTIVE CITATIONS</h3>
          {sections.length === 0 ? (
            <p className="mt-3 text-sm text-[var(--ink-muted)]">Confirm prep to merge sections into Live cards.</p>
          ) : (
            <ul className="mt-3 max-h-40 space-y-1 overflow-auto text-xs">
              {sections.map((section) => (
                <li key={section.anchor + section.heading} className="rounded-lg bg-[var(--paper)] px-2 py-1.5">
                  <span className="font-semibold text-[var(--amber)]">{section.source_label || `Notes · ${section.heading}`}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex min-h-[280px] flex-col rounded-2xl border border-[var(--line)] bg-[var(--paper-elevated)] p-4">
          <h3 className="text-xs font-bold tracking-[0.16em] text-[var(--ink-muted)]">PREP CHAT · MUSE</h3>
          <div className="mt-2 flex-1 space-y-2 overflow-auto text-sm">
            {chat.length === 0 ? (
              <p className="text-[var(--ink-muted)]">Ask: Ano ang dapat kong bantayan sa call na to?</p>
            ) : (
              chat.map((turn, i) => (
                <p key={i} className={turn.role === "user" ? "text-right text-[var(--ink-muted)]" : "leading-relaxed"}>
                  {turn.content}
                </p>
              ))
            )}
          </div>
          <form onSubmit={sendChat} className="mt-3 flex gap-2">
            <input
              className="min-w-0 flex-1 rounded-xl border border-[var(--line)] bg-[var(--paper)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--sky)]"
              value={chatIn}
              onChange={(e) => setChatIn(e.target.value)}
              placeholder="What should I watch for?"
            />
            <button
              type="submit"
              disabled={chatBusy}
              className="rounded-full bg-[var(--ink)] px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </div>
      </aside>
    </div>
  );
}

function ConnectorSearch({ title, query, onQuery, onSearch, hits, onImport }) {
  return (
    <div className="rounded-2xl border border-[var(--line)] bg-[var(--paper-elevated)] p-4">
      <h4 className="text-sm font-semibold">{title}</h4>
      <div className="mt-2 flex gap-2">
        <input
          className="min-w-0 flex-1 rounded-xl border border-[var(--line)] bg-[var(--paper)] px-3 py-2 text-sm"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
        />
        <button type="button" onClick={onSearch} className="rounded-full border border-[var(--line)] px-3 py-2 text-xs font-semibold">
          Search
        </button>
      </div>
      <ul className="mt-2 space-y-2 text-sm">
        {hits.map((hit) => (
          <li key={hit.id} className="rounded-xl bg-[var(--paper)] px-3 py-2">
            <p className="font-medium">{hit.title}</p>
            <p className="line-clamp-2 text-xs text-[var(--ink-muted)]">{hit.snippet}</p>
            <button type="button" onClick={() => onImport(hit.id)} className="mt-2 text-xs font-semibold text-[var(--sky)]">
              Import
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Notes({ status, refresh, sections }) {
  const [text, setText] = useState("");
  async function submit(event) {
    event.preventDefault();
    await api("/api/notes", { method: "POST", body: JSON.stringify({ text, name: "notes.md" }) });
    setText("");
    refresh();
  }
  return (
    <div className="mx-auto grid max-w-4xl gap-6 lg:grid-cols-[1fr_280px]">
      <form onSubmit={submit} className="space-y-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Scope notes</h2>
          <p className="mt-1 text-sm text-[var(--ink-muted)]">
            Paste a PRD, a brief, bullet scope, or pre-call jotters. Escort cites headings — not a formal doc required.
          </p>
        </div>
        <textarea
          className="min-h-[280px] w-full resize-y rounded-2xl border border-[var(--line)] bg-[var(--paper-elevated)] p-4 text-sm leading-relaxed outline-none ring-[var(--sky)] focus:ring-2"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={"## Phase 2 — Payment integration\nShips after KYC go-live.\n\nOr plain bullets:\n- No fee changes on this call\n- QR rollout stays Q2"}
        />
        <button type="submit" className="rounded-full bg-[var(--ink)] px-5 py-2.5 text-sm font-semibold text-white">
          Save notes for this meeting
        </button>
      </form>
      <aside className="rounded-2xl border border-[var(--line)] bg-[var(--paper-elevated)] p-4">
        <h3 className="text-xs font-bold tracking-[0.16em] text-[var(--ink-muted)]">PARSED HEADINGS</h3>
        {sections.length === 0 ? (
          <p className="mt-3 text-sm text-[var(--ink-muted)]">Nothing loaded yet. Demo data fills this after you run the staged call.</p>
        ) : (
          <ul className="mt-3 max-h-80 space-y-2 overflow-auto text-sm">
            {sections.map((section) => (
              <li key={section.anchor} className="rounded-xl bg-[var(--paper)] px-3 py-2">
                <p className="font-medium">{section.heading}</p>
                {section.quote ? <p className="mt-1 line-clamp-2 text-xs text-[var(--ink-muted)]">{section.quote}</p> : null}
              </li>
            ))}
          </ul>
        )}
        {status?.has_meta_key ? null : (
          <p className="mt-4 text-xs text-[var(--amber)]">Add a Meta key in Setup before live Muse cards.</p>
        )}
      </aside>
    </div>
  );
}

function Setup({ status, refresh }) {
  const prefs = status?.prefs || {};
  const [form, setForm] = useState({
    meta_api_key: "",
    whatsapp_access_token: "",
    whatsapp_verify_token: "",
    notion_oauth_token: "",
    google_oauth_token: "",
    whatsapp_phone_number_id: prefs.whatsapp_phone_number_id || "",
    whatsapp_allowed_numbers: prefs.whatsapp_allowed_numbers || "",
    mic_id: prefs.mic_id || "",
    loopback_id: prefs.loopback_id || "",
  });

  useEffect(() => {
    setForm((current) => ({
      ...current,
      whatsapp_phone_number_id: prefs.whatsapp_phone_number_id || "",
      whatsapp_allowed_numbers: prefs.whatsapp_allowed_numbers || "",
      mic_id: prefs.mic_id || "",
      loopback_id: prefs.loopback_id || "",
    }));
  }, [prefs.mic_id, prefs.loopback_id, prefs.whatsapp_allowed_numbers, prefs.whatsapp_phone_number_id]);

  async function submit(event) {
    event.preventDefault();
    await api("/api/setup", { method: "POST", body: JSON.stringify(form) });
    setForm((current) => ({
      ...current,
      meta_api_key: "",
      whatsapp_access_token: "",
      whatsapp_verify_token: "",
      notion_oauth_token: "",
      google_oauth_token: "",
    }));
    refresh();
  }

  const devices = status?.devices || [];
  return (
    <form onSubmit={submit} className="mx-auto max-w-2xl space-y-6">
      <p className="text-sm text-[var(--ink-muted)]">Secrets live in the OS keychain, never in this repo.</p>
      <Section title="Muse">
        <Field
          label="Meta API key"
          type="password"
          value={form.meta_api_key}
          onChange={(value) => setForm({ ...form, meta_api_key: value })}
          placeholder={status?.has_meta_key ? "Saved in keychain" : "LLM_..."}
        />
      </Section>
      <Section title="Context connectors (OAuth tokens → keychain)">
        <Field
          label="Notion integration token"
          type="password"
          value={form.notion_oauth_token}
          onChange={(value) => setForm({ ...form, notion_oauth_token: value })}
          placeholder="secret_… or OAuth access token"
        />
        <Field
          label="Google OAuth access token"
          type="password"
          value={form.google_oauth_token}
          onChange={(value) => setForm({ ...form, google_oauth_token: value })}
          placeholder="ya29.… (Calendar + Drive scopes)"
        />
        <p className="text-xs text-[var(--ink-muted)]">Without tokens, Escort uses mock BayaniPay fixtures for demos.</p>
      </Section>
      <Section title="WhatsApp demo">
        <Field label="Access token" type="password" value={form.whatsapp_access_token} onChange={(v) => setForm({ ...form, whatsapp_access_token: v })} placeholder={status?.has_whatsapp_token ? "Saved" : "Test token"} />
        <Field label="Verify token" type="password" value={form.whatsapp_verify_token} onChange={(v) => setForm({ ...form, whatsapp_verify_token: v })} placeholder="You choose this" />
        <Field label="Phone number id" value={form.whatsapp_phone_number_id} onChange={(v) => setForm({ ...form, whatsapp_phone_number_id: v })} />
        <Field label="Allowlist" value={form.whatsapp_allowed_numbers} onChange={(v) => setForm({ ...form, whatsapp_allowed_numbers: v })} placeholder="1555…" />
        <p className="text-xs text-[var(--ink-muted)]">Hackathon allowlist only — not production rollout.</p>
      </Section>
      <Section title="Audio (Me / Them)">
        <Select label="Microphone · you" value={form.mic_id} onChange={(v) => setForm({ ...form, mic_id: v })} options={devices.filter((d) => d.kind === "mic")} />
        <Select label="Loopback · them" value={form.loopback_id} onChange={(v) => setForm({ ...form, loopback_id: v })} options={devices.filter((d) => d.kind !== "mic")} />
      </Section>
      <button type="submit" className="rounded-full bg-[var(--ink)] px-5 py-2.5 text-sm font-semibold text-white">
        Save setup
      </button>
    </form>
  );
}

function Recap({ meeting }) {
  const open = meeting.open_items || [];
  const promises = meeting.commitments || [];
  return (
    <div className="mx-auto max-w-xl space-y-6">
      {open.length === 0 && promises.length === 0 ? (
        <Empty title="Nothing to recap" body="Open items and promises appear after cards land in Live." />
      ) : null}
      {open.length ? (
        <section className="rounded-2xl border border-[var(--rose-soft)] bg-[var(--rose-soft)]/40 p-4">
          <h3 className="text-sm font-semibold text-[var(--rose)]">Open against your notes</h3>
          <ul className="mt-2 space-y-2 text-sm">
            {open.map((item) => (
              <li key={item.id}>
                {item.title}
                {item.prd_section ? <span className="text-[var(--ink-muted)]"> · {item.prd_section}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {promises.length ? (
        <section className="rounded-2xl border border-[var(--mint-soft)] bg-[var(--mint-soft)]/40 p-4">
          <h3 className="text-sm font-semibold text-[var(--mint)]">Commitments</h3>
          <ul className="mt-2 space-y-2 text-sm">
            {promises.map((item) => (
              <li key={item.id}>
                {item.who} · {item.what} ({item.when})
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function Settings({ status, refresh }) {
  async function wipe() {
    await api("/api/meeting/delete", { method: "POST", body: "{}" });
    refresh();
  }
  async function demo() {
    await api("/api/demo", { method: "POST", body: "{}" });
    window.location.hash = "/live";
    refresh();
  }
  const latency = status?.meeting?.latency;
  return (
    <div className="mx-auto max-w-xl space-y-5">
      <p className="text-sm text-[var(--ink-muted)]">
        MCP {status?.mcp} · WhatsApp {status?.whatsapp}
      </p>
      {latency?.count ? (
        <p className="mono text-sm">
          Card latency p50 {Math.round(latency.p50_ms)} ms · p95 {Math.round(latency.p95_ms)} ms
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={demo} className="rounded-full border border-[var(--line)] px-4 py-2 text-sm font-medium">
          Load staged demo
        </button>
        <button type="button" onClick={wipe} className="rounded-full bg-[var(--rose)] px-4 py-2 text-sm font-semibold text-white">
          Delete meeting data
        </button>
      </div>
      <p className="text-xs text-[var(--ink-muted)]">Delete wipes turns, cards, and frames. Keychain stays put.</p>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <section className="rounded-2xl border border-[var(--line)] bg-[var(--paper-elevated)] p-4">
      <h3 className="text-xs font-bold tracking-[0.16em] text-[var(--ink-muted)]">{title.toUpperCase()}</h3>
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

function Field({ label, value, onChange, type = "text", placeholder = "" }) {
  return (
    <label className="block text-sm font-medium">
      {label}
      <input
        className="mt-1.5 w-full rounded-xl border border-[var(--line)] bg-[var(--paper)] px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--sky)]"
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function Select({ label, value, onChange, options }) {
  return (
    <label className="block text-sm font-medium">
      {label}
      <select
        className="mt-1.5 w-full rounded-xl border border-[var(--line)] bg-[var(--paper)] px-3 py-2 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Choose device</option>
        {options.map((device) => (
          <option key={device.id} value={device.id}>
            {device.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function Chip({ children, onClick }) {
  return (
    <button type="button" onClick={onClick} className="rounded-full border border-[var(--line)] bg-[var(--paper)] px-3 py-1.5 text-xs font-semibold hover:border-[var(--sky)]">
      {children}
    </button>
  );
}

function Empty({ title, body }) {
  return (
    <div className="rounded-2xl border border-dashed border-[var(--line)] bg-[var(--paper-elevated)] px-5 py-6">
      <h3 className="text-lg font-semibold">{title}</h3>
      <p className="mt-1 text-sm text-[var(--ink-muted)]">{body}</p>
    </div>
  );
}

function cardLabel(type) {
  if (type === "PRD_CONFLICT") return "Off your notes";
  if (type === "COMMITMENT") return "Commitment";
  return "Say this";
}

function cardTone(type) {
  if (type === "PRD_CONFLICT") return { ring: "ring-1 ring-[var(--rose-soft)]", label: "text-[var(--rose)]" };
  if (type === "COMMITMENT") return { ring: "ring-1 ring-[var(--mint-soft)]", label: "text-[var(--mint)]" };
  return { ring: "ring-1 ring-[var(--amber-soft)]", label: "text-[var(--amber)]" };
}

function NavIcon({ name, active }) {
  const stroke = active ? "currentColor" : "currentColor";
  const paths = {
    wave: "M4 14v-2M8 16V8M12 14V6M16 12v-4",
    doc: "M8 4h8v16H8z M10 8h4M10 12h4",
    list: "M6 8h12M6 12h12M6 16h8",
    gear: "M12 8a4 4 0 110 8 4 4 0 010-8z M12 2v2M12 20v2M4 12H2M22 12h-2",
    sliders: "M4 6h16M4 12h10M4 18h6",
  };
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="1.8" strokeLinecap="round" aria-hidden>
      <path d={paths[name] || paths.wave} />
    </svg>
  );
}
