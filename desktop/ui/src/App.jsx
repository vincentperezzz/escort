import { useEffect, useState } from "react";

const NAV = [
  ["setup", "Setup"],
  ["prd", "PRD"],
  ["live", "Live"],
  ["recap", "Recap"],
  ["settings", "Settings"],
];

function route() {
  const name = window.location.hash.replace("#/", "") || "live";
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

  return (
    <main className="mx-auto grid min-h-screen max-w-6xl grid-cols-1 md:grid-cols-[220px_1fr]">
      <aside className="flex flex-col gap-6 border-b border-[#1a140f]/10 px-5 py-6 md:border-b-0 md:border-r">
        <div>
          <p className="text-[11px] font-semibold tracking-[0.22em] text-[#c47b12]">ESCORT</p>
          <h1 className="mark text-4xl italic leading-none text-[#1a140f]">Escort</h1>
          <p className="mt-2 text-sm text-[#5c5146]">Live scope-creep guard for Taglish calls. Not a recorder.</p>
        </div>
        <nav className="flex gap-2 overflow-x-auto md:flex-col">
          {NAV.map(([id, label]) => (
            <button
              key={id}
              onClick={() => go(id)}
              className={`rounded-full px-3 py-1.5 text-left text-sm ${screen === id ? "bg-[#1a140f] text-[#efe6d6]" : "text-[#1a140f] hover:bg-[#1a140f]/5"}`}
            >
              {label}
            </button>
          ))}
        </nav>
        <p className="mt-auto hidden text-xs text-[#5c5146] md:block">
          {status?.listening ? "Listening" : "Idle"} · MCP on 8747
        </p>
      </aside>
      <section className="px-5 py-6 md:px-8">
        {error ? <p className="mb-4 rounded-2xl bg-[#b6402a]/10 px-4 py-3 text-sm text-[#b6402a]">{error}</p> : null}
        {screen === "setup" ? <Setup status={status} refresh={refresh} /> : null}
        {screen === "prd" ? <Prd status={status} refresh={refresh} /> : null}
        {screen === "live" ? <Live status={status} cards={cards} refresh={refresh} /> : null}
        {screen === "recap" ? <Recap meeting={meeting.meeting || {}} /> : null}
        {screen === "settings" ? <Settings status={status} refresh={refresh} /> : null}
      </section>
    </main>
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
    <form onSubmit={submit} className="max-w-xl space-y-5">
      <header>
        <h2 className="mark text-3xl italic">First run</h2>
        <p className="mt-1 text-sm text-[#5c5146]">Keys stay in the OS keychain. This screen never writes them to the repo.</p>
      </header>
      <Field label="Meta API key" type="password" value={form.meta_api_key} onChange={(value) => setForm({ ...form, meta_api_key: value })} placeholder={status?.has_meta_key ? "Saved in the keychain" : "LLM_..."} />
      <Field label="WhatsApp access token" type="password" value={form.whatsapp_access_token} onChange={(value) => setForm({ ...form, whatsapp_access_token: value })} placeholder={status?.has_whatsapp_token ? "Saved in the keychain" : "Hackathon test token"} />
      <Field label="WhatsApp verify token" type="password" value={form.whatsapp_verify_token} onChange={(value) => setForm({ ...form, whatsapp_verify_token: value })} placeholder="You choose this" />
      <Field label="Phone number id" value={form.whatsapp_phone_number_id} onChange={(value) => setForm({ ...form, whatsapp_phone_number_id: value })} />
      <Field label="Allowlist numbers" value={form.whatsapp_allowed_numbers} onChange={(value) => setForm({ ...form, whatsapp_allowed_numbers: value })} placeholder="15550001111, ..." />
      <p className="text-xs text-[#5c5146]">The test number and allowlist are a hackathon demo, not a production rollout.</p>
      <label className="block text-sm">
        Microphone
        <select className="mt-1 w-full rounded-2xl border border-[#1a140f]/15 bg-white/70 px-3 py-2" value={form.mic_id} onChange={(event) => setForm({ ...form, mic_id: event.target.value })}>
          <option value="">Choose a mic</option>
          {devices.filter((device) => device.kind === "mic").map((device) => <option key={device.id} value={device.id}>{device.name}</option>)}
        </select>
      </label>
      <label className="block text-sm">
        System audio
        <select className="mt-1 w-full rounded-2xl border border-[#1a140f]/15 bg-white/70 px-3 py-2" value={form.loopback_id} onChange={(event) => setForm({ ...form, loopback_id: event.target.value })}>
          <option value="">Choose loopback</option>
          {devices.filter((device) => device.kind !== "mic").map((device) => <option key={device.id} value={device.id}>{device.name}</option>)}
        </select>
      </label>
      <button className="rounded-full bg-[#1a140f] px-4 py-2 text-sm text-[#efe6d6]">Save setup</button>
    </form>
  );
}

function Prd({ refresh }) {
  const [text, setText] = useState("");
  async function submit(event) {
    event.preventDefault();
    await api("/api/prd", { method: "POST", body: JSON.stringify({ text, name: "uploaded.md" }) });
    refresh();
  }
  return (
    <form onSubmit={submit} className="max-w-2xl space-y-4">
      <h2 className="mark text-3xl italic">PRD</h2>
      <p className="text-sm text-[#5c5146]">Paste a fictional scope doc. Escort cites its headings when a line drifts.</p>
      <textarea className="h-64 w-full rounded-3xl border border-[#1a140f]/15 bg-white/70 p-4" value={text} onChange={(event) => setText(event.target.value)} placeholder="## 2.2 Phase 2 — Payment integration" />
      <button className="rounded-full bg-[#1a140f] px-4 py-2 text-sm text-[#efe6d6]">Use this scope</button>
    </form>
  );
}

function Live({ status, cards, refresh }) {
  const turns = status?.meeting?.utterances || [];
  async function listen(on) {
    await api("/api/listen", { method: "POST", body: JSON.stringify({ on }) });
    refresh();
  }
  async function act(id, action) {
    await api(`/api/cards/${id}/action`, { method: "POST", body: JSON.stringify({ action }) });
    refresh();
  }
  return (
    <div className="space-y-5">
      <header className="flex items-end justify-between gap-3">
        <div>
          <h2 className="mark text-3xl italic">Live meeting</h2>
          <p className="text-sm text-[#5c5146]">{status?.meeting?.title || "No meeting yet"}</p>
        </div>
        <button onClick={() => listen(!status?.listening)} className="rounded-full bg-[#c47b12] px-4 py-2 text-sm text-[#1a140f]">
          {status?.listening ? "Stop listening" : "Start listening"}
        </button>
      </header>
      {cards.length === 0 ? <Empty title="The call is quiet" body="Cards land when a line finishes. Load the staged demo from Settings if you are rehearsing." /> : null}
      <div className="grid gap-3">
        {cards.slice().reverse().map((card) => (
          <article key={card.id} className={`rounded-3xl bg-[#f7f1e6] p-4 shadow-[0_10px_30px_rgba(26,20,15,0.05)] border-l-4 ${stripe(card.type)}`}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[11px] font-semibold tracking-[0.16em] text-[#5c5146]">{label(card.type)}</p>
                <h3 className="text-lg font-semibold">{card.title}</h3>
              </div>
              <p className="num text-2xl text-[#c47b12]">{card.latency_ms}<span className="ml-1 text-xs">ms</span></p>
            </div>
            <p className="mt-2 text-sm leading-relaxed">{card.body}</p>
            {card.prd_section ? <p className="mt-2 text-xs text-[#c47b12]">PRD · {card.prd_section}</p> : null}
            {card.type === "PRD_CONFLICT" ? (
              <div className="mt-3 flex flex-wrap gap-2">
                <Mini onClick={() => act(card.id, "raise")}>Raise it</Mini>
                <Mini onClick={() => act(card.id, "suggest")}>Suggest reply</Mini>
                <Mini onClick={() => act(card.id, "ignore")}>Ignore</Mini>
              </div>
            ) : null}
          </article>
        ))}
      </div>
      <div>
        <h3 className="text-[11px] font-semibold tracking-[0.16em] text-[#5c5146]">HEARD</h3>
        <ul className="mt-2 max-h-56 space-y-2 overflow-auto">
          {turns.map((turn) => (
            <li key={turn.id} className="text-sm">
              <span className={turn.speaker === "Me" ? "text-[#c47b12]" : "text-[#1f7a5a]"}>{turn.speaker}</span>
              <span className="text-[#1a140f]"> {turn.text}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Recap({ meeting }) {
  const open = meeting.open_items || [];
  const promises = meeting.commitments || [];
  return (
    <div className="max-w-xl space-y-4">
      <h2 className="mark text-3xl italic">Recap</h2>
      {open.length === 0 && promises.length === 0 ? <Empty title="Nothing to recap" body="Open items and promises show up after the call produces cards." /> : null}
      {open.length ? (
        <section>
          <h3 className="text-sm font-semibold text-[#b6402a]">Open against the PRD</h3>
          <ul className="mt-2 space-y-1 text-sm">{open.map((item) => <li key={item.id}>{item.title} · {item.prd_section}</li>)}</ul>
        </section>
      ) : null}
      {promises.length ? (
        <section>
          <h3 className="text-sm font-semibold text-[#1f7a5a]">Commitments</h3>
          <ul className="mt-2 space-y-1 text-sm">{promises.map((item) => <li key={item.id}>{item.who} will {item.what} ({item.when})</li>)}</ul>
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
    <div className="max-w-xl space-y-4">
      <h2 className="mark text-3xl italic">Settings</h2>
      <p className="text-sm text-[#5c5146]">MCP stays at {status?.mcp}. WhatsApp webhook stays at {status?.whatsapp}.</p>
      {latency?.count ? <p className="num text-sm">p50 {latency.p50_ms} ms · p95 {latency.p95_ms} ms</p> : null}
      <div className="flex flex-wrap gap-2">
        <button onClick={demo} className="rounded-full border border-[#1a140f]/20 px-4 py-2 text-sm">Load staged demo</button>
        <button onClick={wipe} className="rounded-full bg-[#b6402a] px-4 py-2 text-sm text-white">Delete meeting data</button>
      </div>
      <p className="text-xs text-[#5c5146]">Delete wipes turns, cards, and the last frame. It does not touch the keychain.</p>
    </div>
  );
}

function Field({ label, value, onChange, type = "text", placeholder = "" }) {
  return (
    <label className="block text-sm">
      {label}
      <input className="mt-1 w-full rounded-2xl border border-[#1a140f]/15 bg-white/70 px-3 py-2" type={type} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function Mini({ children, onClick }) {
  return <button onClick={onClick} className="rounded-full border border-[#1a140f]/15 px-3 py-1 text-xs">{children}</button>;
}

function Empty({ title, body }) {
  return (
    <div className="rounded-3xl bg-white/50 px-4 py-5">
      <h3 className="mark text-2xl italic">{title}</h3>
      <p className="mt-1 text-sm text-[#5c5146]">{body}</p>
    </div>
  );
}

function label(type) {
  if (type === "PRD_CONFLICT") return "PRD conflict";
  if (type === "COMMITMENT") return "Commitment";
  return "Say this";
}

function stripe(type) {
  if (type === "PRD_CONFLICT") return "border-[#b6402a]";
  if (type === "COMMITMENT") return "border-[#1f7a5a]";
  return "border-[#c47b12]";
}
