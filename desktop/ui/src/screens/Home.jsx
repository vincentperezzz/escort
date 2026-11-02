import { useEffect, useState } from "react";
import { ArrowUpRight, ArrowRight, CalendarBlank, EyeSlash, Eye, Stop, ArrowUp, Plugs, PhoneDisconnect } from "@phosphor-icons/react";
import { BrandTile, GoogleMark, NotionMark } from "../components/BrandMark.jsx";
import { Bezel, Button, Dot, Kbd, Pill } from "../components/ui.jsx";
import { api, post, formatEventWhen, formatTime, relativeStart, mmss } from "../lib/api.js";
import { kindOf } from "../lib/cards.js";
import { useOverlay } from "../lib/useOverlay.js";
import { listeningSince, useListenClock } from "../lib/useListenClock.js";

const SUGGESTIONS = ["What should I watch for on my next call?", "Summarize my notes in three lines"];

export default function Home({ status, refresh, go, setListening }) {
  const listening = Boolean(status?.listening);
  const elapsed = useListenClock(listening, listeningSince(status));
  const overlay = useOverlay();
  const ctx = status?.context || {};
  const events = ctx.upcoming || [];
  const [starting, setStarting] = useState(false);

  async function toggle() {
    setStarting(true);
    try {
      await setListening(!listening);
      if (!listening) go("live");
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl">
      <div className="grid grid-cols-1 gap-5 md:grid-cols-12">
        <Hero className="rise md:col-span-8" listening={listening} elapsed={elapsed} overlay={overlay} toggle={toggle} starting={starting} disabled={!status} go={go} />
        <NextEvent className="rise md:col-span-4" event={events[0]} ctx={ctx} refresh={refresh} go={go} />
        <PrepChat className="rise md:col-span-5" go={go} />
        <Connections className="rise md:col-span-4" go={go} />
        <LastCall className="rise md:col-span-3" status={status} />
        <Earlier className="rise md:col-span-12" status={status} go={go} />
      </div>
    </div>
  );
}

function Hero({ className, listening, elapsed, overlay, toggle, starting, disabled, go }) {
  return (
    <Bezel className={className} coreClassName="relative flex min-h-[340px] flex-col justify-between overflow-hidden p-7 md:p-9">
      <div
        className="pointer-events-none absolute inset-0 transition-opacity duration-700 ease-glass"
        style={{ background: `radial-gradient(30rem 22rem at 100% 0%, ${listening ? "rgba(255,106,85,0.14)" : "rgba(139,124,255,0.14)"}, transparent 70%)` }}
        aria-hidden
      />
      <div className="relative flex flex-wrap items-start justify-between gap-2">
        <StatusChip listening={listening} elapsed={elapsed} />
        <ShareChip overlay={overlay} />
      </div>

      <div className="relative mt-10">
        <p className="inline-flex rounded-full bg-white/[0.05] px-3 py-1 text-[10px] font-medium uppercase tracking-[0.2em] text-muted hairline">
          {listening ? "In the call with you" : "Ready when you are"}
        </p>
        {listening ? (
          <p className="mono mt-4 text-[64px] font-medium leading-none tracking-[-0.04em] md:text-[84px]" aria-label={`Listening for ${mmss(elapsed)}`}>
            {mmss(elapsed)}
          </p>
        ) : (
          <h1 className="mt-4 max-w-[14ch] text-[44px] font-semibold leading-[1.02] tracking-[-0.04em] text-balance md:text-[60px]">Your next call, covered.</h1>
        )}
      </div>

      <div className="relative mt-9 flex flex-wrap items-center gap-x-5 gap-y-3">
        {listening ? (
          <>
            <button
              type="button"
              onClick={toggle}
              disabled={starting}
              className="press group inline-flex h-14 items-center gap-3 rounded-full bg-conflict pl-7 pr-2 text-[15px] font-medium text-[#1d0703] hover:bg-[#ff7d6b] disabled:opacity-50"
            >
              Stop
              <span className="btn-icon grid h-10 w-10 place-items-center rounded-full bg-[#1d0703] text-conflict" aria-hidden>
                <Stop size={15} weight="fill" />
              </span>
            </button>
            <Button variant="secondary" size="lg" onClick={() => go("live")} trailing={<ArrowUpRight size={18} />}>
              Open live view
            </Button>
            <Button variant="ghost" size="lg" onClick={() => post("/api/overlay", { visible: true }).catch(() => {})} trailing={<ArrowUpRight size={18} />}>
              Back to overlay
            </Button>
          </>
        ) : (
          <button
            type="button"
            onClick={toggle}
            disabled={starting || disabled}
            className="press group inline-flex h-14 items-center gap-3 rounded-full bg-ink pl-7 pr-2 text-[15px] font-medium text-bg hover:bg-white disabled:opacity-50"
          >
            {starting ? "Starting…" : "Start Escort"}
            <span className="btn-icon grid h-10 w-10 place-items-center rounded-full bg-bg text-ink" aria-hidden>
              <ArrowRight size={18} />
            </span>
          </button>
        )}
        <span className="flex items-center gap-1.5 text-[12.5px] text-faint">
          <Kbd>Ctrl</Kbd>
          <Kbd>Shift</Kbd>
          <Kbd>E</Kbd>
          <span className="ml-1">shows the overlay</span>
        </span>
      </div>
    </Bezel>
  );
}

function NextEvent({ event, ctx, refresh, go, className }) {
  const items = ctx.items || [];
  const [busy, setBusy] = useState(false);
  const when = event ? relativeStart(event.start) : "";
  const attendees = Array.isArray(event?.attendees) ? event.attendees.filter(Boolean) : [];
  const meta = [when, attendees.length ? `${attendees.length} attendee${attendees.length === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ");

  async function review() {
    setBusy(true);
    try {
      await post("/api/context/match", { event_id: event.id });
      await refresh();
    } catch {
      /* still open the context screen */
    } finally {
      setBusy(false);
      go("context");
    }
  }

  return (
    <Bezel className={className} coreClassName="flex min-h-[340px] flex-col p-7" aria-label="Up next">
      <p className="flex items-center gap-2 text-[13px] text-muted">
        <CalendarBlank size={16} aria-hidden /> Up next
      </p>
      {event ? (
        <>
          <p className="mono mt-6 text-[30px] font-medium leading-none tracking-[-0.03em]">{formatTime(event.start)}</p>
          <h2 className="mt-3 text-[19px] font-medium leading-snug tracking-[-0.02em] text-balance">{event.title}</h2>
          {meta ? <p className="mt-1.5 text-[13px] text-muted">{meta}</p> : null}
        </>
      ) : (
        <>
          <h2 className="mt-6 text-[19px] font-medium leading-snug tracking-[-0.02em]">Nothing on the calendar</h2>
          <p className="mt-1.5 text-[13px] leading-relaxed text-muted">Connect Google to see upcoming calls here.</p>
        </>
      )}
      <div className="mt-auto pt-6">
        {items.length ? (
          <ul className="flex flex-wrap gap-1.5">
            {items.slice(0, 3).map((item) => (
              <li key={item.id} className="max-w-full truncate rounded-full bg-white/[0.05] px-2.5 py-1 text-[11.5px] text-muted hairline">
                <span className="text-[#c6bfff]">{item.source_label}</span> {item.title}
              </li>
            ))}
            {items.length > 3 ? <li className="rounded-full px-1.5 py-1 text-[11.5px] text-faint">+{items.length - 3}</li> : null}
          </ul>
        ) : (
          <p className="text-[12.5px] text-faint">No context loaded yet</p>
        )}
        {event ? (
          <button type="button" onClick={review} disabled={busy} className="press mt-4 inline-flex items-center gap-1.5 text-[13px] font-medium text-[#c6bfff] hover:text-white disabled:opacity-50">
            {busy ? "Matching…" : "Review prep"}
            <ArrowUpRight size={14} aria-hidden />
          </button>
        ) : (
          <Button variant="ghost" size="sm" className="mt-3 -ml-3.5" onClick={() => go("settings", "connections")}>
            Connect
          </Button>
        )}
      </div>
    </Bezel>
  );
}

function PrepChat({ className, go }) {
  const [text, setText] = useState("");
  const open = (value) => go("chat", encodeURIComponent(value));
  return (
    <Bezel className={className} coreClassName="flex h-full min-h-[200px] flex-col p-7">
      <h2 className="text-[17px] font-medium tracking-[-0.015em]">Prep chat</h2>
      <p className="mt-1 text-[13px] text-muted">Ask your notes before the call starts.</p>
      <form
        className="mt-5 flex items-center gap-2 rounded-full bg-black/30 py-1 pl-4 pr-1 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.09)] focus-within:shadow-[inset_0_0_0_1px_rgba(139,124,255,0.5)]"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) open(text.trim());
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask Escort AI anything…"
          aria-label="Ask prep chat"
          className="h-9 min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-faint"
        />
        <button
          type="submit"
          disabled={!text.trim()}
          aria-label="Open in prep chat"
          className="press grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink text-bg disabled:bg-white/[0.08] disabled:text-faint"
        >
          <ArrowUp size={16} aria-hidden />
        </button>
      </form>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {SUGGESTIONS.map((q) => (
          <button key={q} type="button" onClick={() => open(q)} className="press rounded-full bg-white/[0.05] px-3 py-1.5 text-left text-[12px] text-muted hairline hover:bg-white/[0.09] hover:text-ink">
            {q}
          </button>
        ))}
      </div>
    </Bezel>
  );
}

function Connections({ className, go }) {
  const [providers, setProviders] = useState(null);
  useEffect(() => {
    let alive = true;
    api("/api/connectors/status")
      .then((d) => alive && setProviders(Array.isArray(d?.providers) ? d.providers : []))
      .catch(() => alive && setProviders([]));
    return () => {
      alive = false;
    };
  }, []);
  const list = (providers || []).filter((p) => p.id === "google" || p.id === "notion");
  return (
    <Bezel className={className} coreClassName="flex h-full min-h-[200px] flex-col p-7">
      <div className="flex items-center justify-between">
        <h2 className="text-[17px] font-medium tracking-[-0.015em]">Connections</h2>
        <Plugs size={17} className="text-faint" aria-hidden />
      </div>
      <ul className="mt-4 flex flex-col gap-2">
        {providers === null ? (
          <li className="text-[13px] text-faint">Checking…</li>
        ) : list.length === 0 ? (
          <li className="text-[13px] text-muted">Connect Google and Notion to pull in your calendar and notes.</li>
        ) : (
          list.map((p) => (
            <li key={p.id} className="flex items-center gap-3 rounded-2xl bg-white/[0.025] px-3 py-2.5 hairline">
              <BrandTile size={30}>{p.id === "google" ? <GoogleMark size={17} /> : <NotionMark size={18} />}</BrandTile>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13.5px] font-medium">{(p.label || p.id).replace(/ \(.*\)$/, "")}</p>
                <p className="truncate text-[12px] text-muted">{p.connected ? p.account || "Connected" : "Not connected"}</p>
              </div>
              <Dot tone={p.connected ? "mint" : "faint"} />
            </li>
          ))
        )}
      </ul>
      <button type="button" onClick={() => go("settings", "connections")} className="press mt-auto inline-flex items-center gap-1.5 self-start pt-4 text-[13px] font-medium text-[#c6bfff] hover:text-white">
        Manage <ArrowUpRight size={14} aria-hidden />
      </button>
    </Bezel>
  );
}

function LastCall({ className, status }) {
  const summary = status?.summary;
  const cards = status?.meeting?.cards || [];
  const fromSummary = summary?.status === "ready";
  const conflicts = fromSummary ? (summary.conflicts || []).length : cards.filter((c) => kindOf(c.type) === "conflict").length;
  const commits = fromSummary ? (summary.commitments || []).length : cards.filter((c) => kindOf(c.type) === "commit").length;
  const any = fromSummary || cards.length > 0;
  return (
    <Bezel className={className} coreClassName="flex h-full min-h-[200px] flex-col p-7">
      <h2 className="text-[17px] font-medium tracking-[-0.015em]">Last call</h2>
      {any ? (
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-1">
          <div>
            <p className="mono text-[40px] font-medium leading-none tracking-[-0.04em] text-conflict">{conflicts}</p>
            <p className="mt-1.5 text-[12.5px] text-muted">conflict{conflicts === 1 ? "" : "s"} caught</p>
          </div>
          <div>
            <p className="mono text-[40px] font-medium leading-none tracking-[-0.04em] text-commit">{commits}</p>
            <p className="mt-1.5 text-[12.5px] text-muted">commitment{commits === 1 ? "" : "s"}</p>
          </div>
        </div>
      ) : (
        <p className="mt-4 text-[13px] leading-relaxed text-muted">Numbers from your latest call land here.</p>
      )}
    </Bezel>
  );
}

function Earlier({ className, status, go }) {
  const meeting = status?.meeting || {};
  const history = Array.isArray(status?.sessions) ? status.sessions : [];
  const hasCurrent = (meeting.cards || []).length > 0 || (meeting.utterances || []).length > 0;
  const latest =
    history[0] ||
    (hasCurrent && !status?.listening
      ? { title: meeting.title || "Latest session", started_at: meeting.started_at, ended_at: meeting.ended_at, duration_ms: meeting.duration_ms, current: true }
      : null);
  const summaryState = status?.summary?.status;
  const dur = latest?.duration_ms || (latest?.started_at && latest?.ended_at ? new Date(latest.ended_at) - new Date(latest.started_at) : 0);
  const mins = dur > 0 ? Math.max(1, Math.round(dur / 60000)) : 0;
  const sub = latest?.current && summaryState && summaryState !== "idle" ? "summary" : "";
  return (
    <Bezel className={className} coreClassName="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 px-7 py-5">
      {latest ? (
        <>
          <div className="flex min-w-0 items-center gap-4">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-white/[0.05] text-faint hairline">
              <PhoneDisconnect size={17} aria-hidden />
            </span>
            <div className="min-w-0">
              <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-faint">Earlier</p>
              <p className="mt-0.5 truncate text-[15px] font-medium">{latest.title}</p>
              <p className="mt-0.5 text-[12.5px] text-muted">
                {[mins ? `${mins} min` : "", latest.started_at ? formatEventWhen(latest.started_at) : ""].filter(Boolean).join(" · ")}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {latest.current && summaryState === "ready" ? <Pill tone="accent">Summary ready</Pill> : null}
            <Button variant="secondary" onClick={() => go("recap", sub)} trailing={<ArrowUpRight size={15} />}>
              Open summary
            </Button>
          </div>
        </>
      ) : (
        <p className="text-[13.5px] text-muted">Sessions you run show up here with their summary. Load the staged demo from Settings to see one.</p>
      )}
    </Bezel>
  );
}

function StatusChip({ listening, elapsed }) {
  return (
    <span
      className={`inline-flex h-8 items-center gap-2 rounded-full px-3.5 text-[12.5px] font-medium ${
        listening ? "bg-conflict-soft text-[#ffb3a7]" : "bg-white/[0.05] text-muted hairline"
      }`}
      role="status"
    >
      <Dot tone={listening ? "signal" : "faint"} live={listening} />
      {listening ? "Live" : "Idle"}
      {listening ? <span className="mono text-[12px] text-[#ffb3a7]/80">{mmss(elapsed)}</span> : null}
    </span>
  );
}

function ShareChip({ overlay }) {
  const unavailable = overlay.available === false;
  const on = Boolean(overlay.hide_from_share);
  const Icon = on ? EyeSlash : Eye;
  return (
    <span className="inline-flex flex-col gap-1">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        disabled={unavailable}
        onClick={() => overlay.update({ hide_from_share: !on })}
        title={unavailable ? "Screen-share hiding is not available in this build yet" : "Keep Escort's windows out of screen shares and recordings"}
        className={`press inline-flex h-8 items-center gap-2 rounded-full pl-3 pr-1.5 text-[12.5px] font-medium disabled:cursor-not-allowed disabled:opacity-50 ${
          on && !unavailable ? "bg-accent-soft text-[#c6bfff]" : "bg-white/[0.05] text-muted hairline"
        }`}
      >
        <Icon size={15} aria-hidden />
        {unavailable ? "Share hiding unavailable" : on ? "Hidden from share" : "Visible in share"}
        <span className={`relative h-[18px] w-[30px] rounded-full transition-colors duration-300 ease-glass ${on && !unavailable ? "bg-accent" : "bg-white/10"}`} aria-hidden>
          <span
            className={`absolute left-[3px] top-[3px] h-3 w-3 rounded-full transition-transform duration-500 ease-glass ${
              on && !unavailable ? "translate-x-3 bg-white" : "bg-[#c9ccd8]"
            }`}
          />
        </span>
      </button>
      {overlay.supported === false ? (
        <span className="pl-1 text-[11.5px] text-faint">Capture hiding needs Windows 10 version 2004 or later.</span>
      ) : null}
      {overlay.error ? <span className="pl-1 text-[11.5px] text-conflict">{overlay.error}</span> : null}
    </span>
  );
}
