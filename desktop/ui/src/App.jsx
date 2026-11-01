import { useEffect, useMemo, useState } from "react";

const NAV = [
  ["live", "Live", "wave"],
  ["notes", "Notes", "doc"],
  ["recap", "Recap", "list"],
  ["setup", "Setup", "gear"],
  ["settings", "Settings", "sliders"],
];

function route() {
  let name = window.location.hash.replace("#/", "") || "live";
  if (name === "prd") name = "notes";
  return NAV.some(([id]) => id === name) ? name : "live";
}

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
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
              {id === "notes" && noteSections.length ? (
                <span className="mono hidden rounded-md bg-[var(--amber-soft)] px-1.5 py-0.5 text-[10px] text-[var(--amber)] md:ml-auto md:inline">
                  {noteSections.length}
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
            {screen === "notes" ? <Notes status={status} refresh={refresh} sections={noteSections} /> : null}
            {screen === "live" ? (
              <Live
                cards={cards}
                turns={meeting.utterances || []}
                refresh={refresh}
                transcriptOpen={transcriptOpen}
                onToggleTranscript={() => setTranscriptOpen((v) => !v)}
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

function Live({ cards, turns, refresh, transcriptOpen, onToggleTranscript }) {
  async function act(id, action) {
    await api(`/api/cards/${id}/action`, { method: "POST", body: JSON.stringify({ action }) });
    refresh();
  }

  const sorted = useMemo(() => cards.slice().reverse(), [cards]);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 pb-24 md:pb-28">
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
          <p className="mt-2 text-xs font-medium text-[var(--amber)]">Notes · {card.prd_section}</p>
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
    setForm((current) => ({ ...current, meta_api_key: "", whatsapp_access_token: "", whatsapp_verify_token: "" }));
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
