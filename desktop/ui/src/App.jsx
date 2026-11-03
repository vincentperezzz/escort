import { useCallback, useEffect, useRef, useState } from "react";
import { IconContext, House, Sparkle, Waveform, FileText, GearSix, ArrowSquareOut } from "@phosphor-icons/react";
import { api, post } from "./lib/api.js";
import Logo, { logoSvg } from "./components/Logo.jsx";
import Home from "./screens/Home.jsx";
import Chat from "./screens/Chat.jsx";
import Live from "./screens/Live.jsx";
import ContextScreen from "./screens/ContextScreen.jsx";
import Settings from "./screens/Settings.jsx";
import Overlay from "./screens/Overlay.jsx";
import ProjectSelector from "./components/ProjectSelector.jsx";
import SwitchConfirmationBanner from "./components/SwitchConfirmationBanner.jsx";
import FirstProjectOnboarding from "./components/projects/FirstProjectOnboarding.jsx";
import { requiresProjectSetup } from "./lib/projectGate.js";

const NAV = [
  { id: "home", label: "Home", Icon: House },
  { id: "chat", label: "Prep chat", Icon: Sparkle },
  { id: "live", label: "Live", Icon: Waveform },
  { id: "context", label: "Context", Icon: FileText },
  { id: "settings", label: "Settings", Icon: GearSix },
];

const LEGACY = { prd: ["context"], notes: ["context"], setup: ["settings", "connections"] };

function parseRoute() {
  const [rawName = "", sub = ""] = window.location.hash.replace(/^#\/?/, "").split("/");
  if (rawName === "recap") {
    const liveSub = sub ? `recap/${sub}` : "recap";
    return { name: "live", sub: liveSub };
  }
  const [name, legacySub] = LEGACY[rawName] || [rawName];
  if (name === "overlay") return { name, sub: "" };
  return NAV.some(({ id }) => id === name) ? { name, sub: sub || legacySub || "" } : { name: "home", sub: "" };
}

export default function App() {
  const [route, setRoute] = useState(parseRoute);

  useEffect(() => {
    const onHash = () => {
      const next = parseRoute();
      document.documentElement.classList.toggle("overlay-mode", next.name === "overlay");
      setRoute(next);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <IconContext.Provider value={{ weight: "light", size: 18 }}>
      {route.name === "overlay" ? <Overlay /> : <Shell route={route} />}
    </IconContext.Provider>
  );
}

function useFaviconState(live) {
  useEffect(() => {
    const link = document.querySelector('link[rel="icon"]');
    if (!link) return;
    link.href = live ? `data:image/svg+xml,${encodeURIComponent(logoSvg("live"))}` : "./favicon.svg";
  }, [live]);
}

function Shell({ route }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [transcriptOpen, setTranscriptOpen] = useState(true);
  const screen = route.name;

  const refresh = useCallback(async () => {
    try {
      setStatus(await api("/api/status"));
      setError("");
    } catch (err) {
      setError(err.message || "Escort is quiet");
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 1500);
    return () => clearInterval(id);
  }, [refresh]);

  const go = useCallback((next, sub = "") => {
    window.location.hash = sub ? `/${next}/${sub}` : `/${next}`;
  }, []);

  const listening = Boolean(status?.listening);

  const setListening = useCallback(
    async (on) => {
      await post("/api/listen", { on });
      if (on) post("/api/overlay", { visible: true }).catch(() => {});
      await refresh();
    },
    [refresh],
  );

  const openOverlay = useCallback(async () => {
    try {
      await post("/api/overlay", { visible: true });
    } catch {
      /* overlay endpoint not available in this build */
    } finally {
      refresh();
    }
  }, [refresh]);

  // Ctrl+Shift+E shows or hides the overlay. The backend registers it system-wide
  // (which swallows the keystroke); this only fires if that registration failed.
  useEffect(() => {
    async function onKey(event) {
      if (!(event.ctrlKey && event.shiftKey && !event.altKey && event.key.toLowerCase() === "e")) return;
      event.preventDefault();
      try {
        const current = await api("/api/overlay");
        await post("/api/overlay", { visible: !current?.visible });
      } catch {
        /* overlay endpoint not available in this build */
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const wasListening = useRef(null);
  const [justEndedRecap, setJustEndedRecap] = useState(false);
  useEffect(() => {
    if (!status) return;
    if (wasListening.current === true && !listening) {
      window.location.hash = "/live/recap";
      setJustEndedRecap(true);
    }
    wasListening.current = listening;
  }, [listening, status]);
  useEffect(() => {
    if (route.name !== "live") setJustEndedRecap(false);
    else if (route.sub && !route.sub.startsWith("recap")) setJustEndedRecap(false);
  }, [route.name, route.sub]);

  useFaviconState(listening);

  const onboarding = requiresProjectSetup(status);

  if (onboarding) {
    return <FirstProjectOnboarding refresh={refresh} go={go} />;
  }

  const ctx = status?.context || {};
  const badge = ctx.items?.length || (status?.meeting?.prd_sections || []).length;
  const cardCount = (status?.meeting?.cards || []).length;

  return (
    <div className="relative flex h-full flex-col">
      <div className="ambient" aria-hidden />

      <header className="relative z-20 flex shrink-0 justify-center px-4 pt-4">
        <nav aria-label="Primary" className="glass flex w-max max-w-full items-center gap-1 rounded-full p-1.5">
          <button
            type="button"
            onClick={() => go("home")}
            className="press mr-1 flex items-center gap-2.5 rounded-full py-0.5 pl-0.5 pr-3"
            aria-label="Escort home"
          >
            <Logo state={listening ? "live" : "idle"} size={30} />
            <span className="hidden text-[13.5px] font-semibold tracking-[-0.01em] sm:inline">Escort</span>
          </button>
          <span className="mr-1 hidden h-5 w-px bg-white/10 sm:block" aria-hidden />
          <ProjectSelector status={status} refresh={refresh} />
          <span className="mx-1 hidden h-5 w-px bg-white/10 sm:block" aria-hidden />
          {NAV.map(({ id, label, Icon }) => {
            const active = screen === id;
            const count = id === "context" ? badge : id === "live" ? cardCount : 0;
            return (
              <button
                key={id}
                type="button"
                onClick={() => go(id)}
                aria-current={active ? "page" : undefined}
                aria-label={label}
                title={label}
                className={`press relative flex h-9 items-center gap-2 rounded-full px-2.5 text-[13px] font-medium sm:px-3 ${
                  active ? "bg-white/[0.09] text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]" : "text-muted hover:bg-white/[0.05] hover:text-ink"
                }`}
              >
                <Icon size={17} weight={active ? "regular" : "light"} aria-hidden />
                <span className="hidden lg:inline">{label}</span>
                {count ? (
                  <span
                    className={`mono hidden rounded-full px-1.5 text-[10.5px] leading-4 sm:inline ${
                      id === "live" && listening ? "bg-conflict-soft text-conflict" : "bg-white/[0.08] text-muted"
                    }`}
                  >
                    {count}
                  </span>
                ) : null}
              </button>
            );
          })}
          {listening ? (
            <>
              <span className="mx-1 hidden h-5 w-px bg-white/10 sm:block" aria-hidden />
              <button
                type="button"
                onClick={openOverlay}
                aria-label="Back to overlay"
                title="Switch back to the live overlay (Ctrl+Shift+E)"
                className="press flex h-9 items-center gap-1.5 rounded-full bg-accent-soft px-3 text-[12.5px] font-medium text-[#c6bfff] hover:bg-accent/30 hover:text-white"
              >
                <ArrowSquareOut size={15} weight="regular" aria-hidden />
                <span className="hidden sm:inline">Back to overlay</span>
              </button>
            </>
          ) : null}
        </nav>
      </header>

      <SwitchConfirmationBanner status={status} refresh={refresh} />

      <main className="relative z-10 flex min-h-0 min-w-0 flex-1 flex-col">
        {screen === "live" ? (
          <Live
            status={status}
            refresh={refresh}
            setListening={setListening}
            go={go}
            liveSub={route.sub}
            justEndedRecap={justEndedRecap}
            onClearJustEndedRecap={() => setJustEndedRecap(false)}
            transcriptOpen={transcriptOpen}
            onToggleTranscript={() => setTranscriptOpen((v) => !v)}
          />
        ) : screen === "chat" ? (
          <Chat key={route.sub} status={status} refresh={refresh} go={go} seed={route.sub} />
        ) : (
          <div className="scroll-thin min-h-0 flex-1 overflow-auto px-4 pb-16 pt-10 md:px-10 md:pt-14">
            {screen === "home" ? <Home status={status} refresh={refresh} go={go} setListening={setListening} /> : null}
            {screen === "context" ? <ContextScreen status={status} refresh={refresh} go={go} /> : null}
            {screen === "settings" ? <Settings status={status} refresh={refresh} section={route.sub} go={go} /> : null}
          </div>
        )}
      </main>

      {error ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-5 z-30 flex justify-center px-4" role="alert">
          <p className="glass fade-in flex items-center gap-2.5 rounded-full px-4 py-2 text-[13px] text-muted">
            <span className="h-1.5 w-1.5 rounded-full bg-conflict" aria-hidden />
            Can't reach Escort's local service ({error}). Retrying every few seconds.
          </p>
        </div>
      ) : null}
    </div>
  );
}
