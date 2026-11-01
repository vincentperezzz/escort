import { useEffect, useState } from "react";
import { Waveform, FileText, ListChecks, Plugs, SlidersHorizontal } from "@phosphor-icons/react";
import { api } from "./lib/api.js";
import Live from "./screens/Live.jsx";
import ContextScreen from "./screens/ContextScreen.jsx";
import Recap from "./screens/Recap.jsx";
import Setup from "./screens/Setup.jsx";
import Settings from "./screens/Settings.jsx";

const NAV = [
  { id: "live", label: "Live", Icon: Waveform },
  { id: "context", label: "Context", Icon: FileText },
  { id: "recap", label: "Recap", Icon: ListChecks },
  { id: "setup", label: "Setup", Icon: Plugs },
  { id: "settings", label: "Settings", Icon: SlidersHorizontal },
];

function route() {
  let name = window.location.hash.replace("#/", "") || "live";
  if (name === "prd" || name === "notes") name = "context";
  return NAV.some(({ id }) => id === name) ? name : "live";
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

  const ctx = status?.context || {};
  const badge = ctx.items?.length || (status?.meeting?.prd_sections || []).length;
  const listening = status?.listening;

  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="order-2 flex shrink-0 items-center gap-1 bg-[var(--rail)] px-2 py-1.5 text-[var(--rail-ink)] md:order-1 md:w-[88px] md:flex-col md:px-2 md:py-5">
        <div className="hidden flex-col items-center gap-1 md:flex">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent text-lg font-bold text-accent-ink" aria-hidden>
            E
          </span>
          <span className="text-[11px] font-semibold tracking-wide">Escort</span>
        </div>
        <nav aria-label="Primary" className="flex flex-1 justify-around gap-1 md:mt-7 md:flex-none md:flex-col md:justify-start">
          {NAV.map(({ id, label, Icon }) => {
            const active = screen === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => go(id)}
                aria-current={active ? "page" : undefined}
                className={`relative flex flex-1 flex-col items-center gap-1 rounded-xl px-2 py-2 text-[11px] font-medium transition-colors md:flex-none md:py-2.5 ${
                  active ? "bg-white/12 text-white" : "text-[var(--rail-muted)] hover:bg-white/8 hover:text-white"
                }`}
              >
                <Icon size={22} weight={active ? "fill" : "regular"} aria-hidden />
                {label}
                {id === "context" && badge ? (
                  <span className="mono absolute right-1.5 top-1 rounded-full bg-accent px-1.5 text-[10px] font-semibold leading-4 text-accent-ink">{badge}</span>
                ) : null}
              </button>
            );
          })}
        </nav>
        <div className="hidden flex-col items-center gap-1.5 md:flex" role="status">
          <span className={`h-2.5 w-2.5 rounded-full ${listening ? "live-dot bg-commit" : "bg-[var(--rail-muted)]"}`} aria-hidden />
          <span className="text-[11px] text-[var(--rail-muted)]">{listening ? "Listening" : "Idle"}</span>
        </div>
      </aside>

      <main className="order-1 flex min-h-0 min-w-0 flex-1 flex-col md:order-2">
        {error ? (
          <p className="border-b border-conflict/30 bg-conflict-soft px-5 py-2.5 text-sm font-medium text-conflict md:px-8" role="alert">
            Cannot reach Escort ({error}). Retrying.
          </p>
        ) : null}
        {screen === "live" ? (
          <Live
            status={status}
            refresh={refresh}
            transcriptOpen={transcriptOpen}
            onToggleTranscript={() => setTranscriptOpen((v) => !v)}
            onGoContext={() => go("context")}
          />
        ) : (
          <div className="scroll-thin min-h-0 flex-1 overflow-auto px-5 py-7 md:px-10">
            {screen === "setup" ? <Setup status={status} refresh={refresh} /> : null}
            {screen === "context" ? <ContextScreen status={status} refresh={refresh} /> : null}
            {screen === "recap" ? <Recap meeting={status?.meeting?.meeting || {}} /> : null}
            {screen === "settings" ? <Settings status={status} refresh={refresh} /> : null}
          </div>
        )}
      </main>
    </div>
  );
}
