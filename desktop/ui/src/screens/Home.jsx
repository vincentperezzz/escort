import { useState } from "react";
import { ArrowUpRight, ArrowRight, CalendarBlank, EyeSlash, Eye, Stop, Sparkle, ClockCounterClockwise } from "@phosphor-icons/react";
import { Bezel, Button, Dot, Kbd, Pill } from "../components/ui.jsx";
import { post, formatEventWhen, formatTime, relativeStart, mmss } from "../lib/api.js";
import { KIND, kindOf } from "../lib/cards.js";
import { useOverlay } from "../lib/useOverlay.js";
import { listeningSince, useListenClock } from "../lib/useListenClock.js";

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
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
        <Bezel className="rise lg:col-span-8" coreClassName="relative flex min-h-[360px] flex-col justify-between overflow-hidden p-8 md:p-10">
          <div
            className="pointer-events-none absolute inset-0 transition-opacity duration-700 ease-glass"
            style={{
              background: `radial-gradient(30rem 22rem at 100% 0%, ${listening ? "rgba(255,106,85,0.14)" : "rgba(139,124,255,0.14)"}, transparent 70%)`,
            }}
            aria-hidden
          />
          <div className="relative flex flex-wrap items-center gap-2">
            <StatusChip listening={listening} elapsed={elapsed} />
            <ShareChip overlay={overlay} />
          </div>

          <div className="relative mt-12">
            <h1 className="max-w-[16ch] text-[44px] font-semibold leading-[1.02] tracking-[-0.04em] text-balance md:text-[56px]">
              {listening ? "Escort is in the call with you." : "Walk into every call prepared."}
            </h1>
            <p className="mt-4 max-w-md text-[15px] leading-relaxed text-muted text-pretty">
              {listening
                ? "Cards land the moment a line crosses your notes, a promise, or a question you could answer."
                : "Escort listens alongside you, checks what is said against your notes, and flags conflicts before you agree to them."}
            </p>
          </div>

          <div className="relative mt-10 flex flex-wrap items-center gap-x-5 gap-y-3">
            {listening ? (
              <>
                <Button variant="secondary" size="lg" onClick={() => go("live")} trailing={<ArrowUpRight size={18} />}>
                  Open live view
                </Button>
                <Button variant="dangerGhost" size="lg" onClick={toggle} disabled={starting}>
                  <Stop size={16} weight="fill" aria-hidden /> Stop Escort
                </Button>
              </>
            ) : (
              <Button variant="primary" size="lg" onClick={toggle} disabled={starting || !status} trailing={<ArrowRight size={18} />}>
                {starting ? "Starting…" : "Start Escort"}
              </Button>
            )}
            <span className="flex items-center gap-1.5 text-[12.5px] text-faint">
              <Kbd>Ctrl</Kbd>
              <Kbd>Shift</Kbd>
              <Kbd>E</Kbd>
              <span className="ml-1">shows the overlay</span>
            </span>
          </div>
        </Bezel>

        <NextCall className="rise lg:col-span-4" event={events[0]} ctx={ctx} go={go} />

        <UpNext className="rise lg:col-span-7" events={events} prepEventId={ctx.prep?.selected_event_id} refresh={refresh} go={go} />
        <EarlierSessions className="rise lg:col-span-5" status={status} go={go} />
      </div>
    </div>
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

function NextCall({ event, ctx, go, className }) {
  const items = ctx.items || [];
  const prepped = event && ctx.prep?.selected_event_id === event.id;
  return (
    <Bezel className={className} coreClassName="flex min-h-[360px] flex-col p-7">
      <p className="flex items-center gap-2 text-[13px] text-muted">
        <CalendarBlank size={16} aria-hidden /> Next call
      </p>
      {event ? (
        <>
          <h2 className="mt-5 text-[22px] font-medium leading-snug tracking-[-0.02em] text-balance">{event.title}</h2>
          <p className="mt-2 text-sm text-muted">
            {formatEventWhen(event.start)}
            {relativeStart(event.start) ? <span className="text-ink/80"> · {relativeStart(event.start)}</span> : null}
          </p>
        </>
      ) : (
        <>
          <h2 className="mt-5 text-[22px] font-medium leading-snug tracking-[-0.02em]">Nothing on the calendar</h2>
          <p className="mt-2 text-sm leading-relaxed text-muted">Connect Google to see your upcoming calls here.</p>
        </>
      )}

      <div className="mt-auto pt-8">
        <p className="text-[12.5px] text-faint">{items.length ? `${items.length} source${items.length === 1 ? "" : "s"} loaded` : "No context loaded yet"}</p>
        {items.length ? (
          <ul className="mt-2.5 flex flex-wrap gap-1.5">
            {items.slice(0, 4).map((item) => (
              <li key={item.id} className="max-w-full truncate rounded-full bg-white/[0.05] px-2.5 py-1 text-[12px] text-muted hairline">
                <span className="text-[#c6bfff]">{item.source_label}</span> {item.title}
              </li>
            ))}
          </ul>
        ) : null}
        <div className="mt-5 flex flex-wrap gap-2">
          <Button variant="accent" onClick={() => go("chat")} trailing={<Sparkle size={15} />}>
            {prepped ? "Ask prep chat" : "Prep with chat"}
          </Button>
          {event ? null : (
            <Button variant="ghost" onClick={() => go("settings", "connections")}>
              Connect
            </Button>
          )}
        </div>
      </div>
    </Bezel>
  );
}

function UpNext({ events, prepEventId, refresh, go, className }) {
  const [busy, setBusy] = useState("");

  async function prep(eventId) {
    setBusy(eventId);
    try {
      await post("/api/context/match", { event_id: eventId });
      await refresh();
      go("context");
    } finally {
      setBusy("");
    }
  }

  return (
    <Bezel className={className} coreClassName="p-7" aria-label="Upcoming meetings">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[17px] font-medium tracking-[-0.015em]">Up next</h2>
        <span className="mono text-xs text-faint">{events.length || ""}</span>
      </div>
      {events.length === 0 ? (
        <p className="mt-4 max-w-sm text-sm leading-relaxed text-muted">
          Your calendar is clear. When Google is connected, upcoming calls show here with one-press prep.
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-1.5">
          {events.map((event) => {
            const prepped = prepEventId === event.id;
            const when = relativeStart(event.start);
            return (
              <li key={event.id} className="flex items-center gap-4 rounded-2xl bg-white/[0.025] px-4 py-3 hairline">
                <div className="w-14 shrink-0 text-center">
                  <p className="mono text-[13px] text-ink">{formatTime(event.start)}</p>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{event.title}</p>
                  <p className="mt-0.5 truncate text-xs text-muted">
                    {formatEventWhen(event.start)}
                    {when ? ` · ${when}` : ""}
                  </p>
                </div>
                {prepped ? (
                  <Pill tone="accent">Prepped</Pill>
                ) : (
                  <Button size="sm" variant="secondary" disabled={busy === event.id} onClick={() => prep(event.id)}>
                    {busy === event.id ? "Matching…" : "Prep"}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Bezel>
  );
}

function EarlierSessions({ status, go, className }) {
  const meeting = status?.meeting || {};
  const history = Array.isArray(status?.sessions) ? status.sessions : [];
  const cards = meeting.cards || [];
  const hasCurrent = cards.length > 0 || (meeting.utterances || []).length > 0;
  const counts = cards.reduce((acc, card) => ({ ...acc, [kindOf(card.type)]: (acc[kindOf(card.type)] || 0) + 1 }), {});
  const sessions = history.length
    ? history
    : hasCurrent
      ? [{ id: "current", title: meeting.title || "Latest session", started_at: meeting.started_at, counts, turns: (meeting.utterances || []).length }]
      : [];

  return (
    <Bezel className={className} coreClassName="flex h-full flex-col p-7">
      <div className="flex items-center justify-between">
        <h2 className="text-[17px] font-medium tracking-[-0.015em]">Earlier sessions</h2>
        <ClockCounterClockwise size={17} className="text-faint" aria-hidden />
      </div>
      {sessions.length === 0 ? (
        <p className="mt-4 max-w-sm text-sm leading-relaxed text-muted">
          Sessions you run show up here with their conflicts and promises. Load the staged demo from Settings to see one.
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-1.5">
          {sessions.map((s) => (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => go("recap")}
                className="press group flex w-full items-center gap-4 rounded-2xl bg-white/[0.025] px-4 py-3.5 text-left hairline hover:bg-white/[0.05]"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{s.title}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {["conflict", "commit", "reply"].map((k) =>
                      s.counts?.[k] ? (
                        <span key={k} className={`rounded-full px-2 py-0.5 text-[11px] ${KIND[k].chip}`}>
                          {s.counts[k]} {KIND[k].label.toLowerCase()}
                          {s.counts[k] > 1 ? "s" : ""}
                        </span>
                      ) : null,
                    )}
                    {s.turns ? <span className="rounded-full bg-white/[0.05] px-2 py-0.5 text-[11px] text-muted">{s.turns} lines</span> : null}
                  </div>
                </div>
                <ArrowUpRight size={16} className="btn-icon text-faint group-hover:text-ink" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Bezel>
  );
}
