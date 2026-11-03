import { useEffect, useState } from "react";
import { ArrowRight, CalendarBlank, Play } from "@phosphor-icons/react";
import { Bezel, Button, Empty, WaveBars } from "../ui.jsx";
import { api, formatEventWhen, post, relativeStart } from "../../lib/api.js";
import { sessionDurationLabel } from "./parseLiveRoute.js";

export default function LiveIdle({ status, setListening, go, onOpenSession, busy, setBusy }) {
  const ctx = status?.context || {};
  const upcoming = ctx.upcoming || [];
  const nextEvent = upcoming[0];
  const projectId = status?.active_project?.id;
  const [sessions, setSessions] = useState(null);
  const [loadErr, setLoadErr] = useState("");

  useEffect(() => {
    let alive = true;
    if (!projectId) {
      setSessions([]);
      return undefined;
    }
    setSessions(null);
    api(`/api/sessions?project_id=${encodeURIComponent(projectId)}`)
      .then((d) => {
        if (alive) setSessions(Array.isArray(d?.sessions) ? d.sessions : []);
      })
      .catch((err) => {
        if (!alive) return;
        if (err.status === 404) {
          const fallback = Array.isArray(status?.sessions) ? status.sessions : [];
          setSessions(fallback);
        } else {
          setSessions([]);
          setLoadErr("");
        }
      });
    return () => {
      alive = false;
    };
  }, [projectId, status?.sessions]);

  async function goLive() {
    setBusy(true);
    try {
      await setListening(true);
    } finally {
      setBusy(false);
    }
  }

  async function startForEvent(event) {
    setBusy(true);
    try {
      await post("/api/context/match", { event_id: event.id }).catch(() => {});
      await setListening(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="scroll-thin mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col overflow-auto px-4 pb-16 pt-10 md:px-10 md:pt-14">
      <Bezel className="rise" coreClassName="relative flex flex-col items-center overflow-hidden px-7 py-12 text-center md:px-12 md:py-14">
        <div
          className="pointer-events-none absolute inset-0"
          style={{ background: "radial-gradient(28rem 20rem at 50% 0%, rgba(139,124,255,0.16), transparent 70%)" }}
          aria-hidden
        />
        <div className="relative flex flex-col items-center">
          <WaveBars active={false} />
          <h1 className="mt-6 max-w-[16ch] text-[36px] font-semibold leading-[1.05] tracking-[-0.04em] text-balance md:text-[44px]">Go live on your next call</h1>
          <p className="mt-3 max-w-[42ch] text-[14.5px] leading-relaxed text-muted">Escort listens in, flags conflicts with your notes, and captures commitments as people speak.</p>
          <Button variant="primary" size="lg" className="mt-8" onClick={goLive} disabled={busy} trailing={<Play size={16} weight="fill" />}>
            {busy ? "Starting…" : "Go live"}
          </Button>
        </div>
      </Bezel>

      {nextEvent ? (
        <Bezel className="rise mt-5" coreClassName="flex flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div className="flex min-w-0 items-start gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-white/[0.05] text-[#c6bfff] hairline">
              <CalendarBlank size={18} aria-hidden />
            </span>
            <div className="min-w-0 text-left">
              <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-faint">Upcoming on calendar</p>
              <p className="mt-1 truncate text-[16px] font-medium tracking-[-0.02em]">{nextEvent.title}</p>
              <p className="mt-0.5 text-[13px] text-muted">
                {[relativeStart(nextEvent.start), formatEventWhen(nextEvent.start)].filter(Boolean).join(" · ")}
              </p>
            </div>
          </div>
          <Button variant="secondary" onClick={() => startForEvent(nextEvent)} disabled={busy} trailing={<ArrowRight size={15} />}>
            Quick start
          </Button>
        </Bezel>
      ) : null}

      <div className="mt-10">
        <h2 className="text-[13px] font-medium text-muted">Past sessions</h2>
        {sessions === null ? (
          <p className="mt-4 text-[13px] text-faint">Loading sessions…</p>
        ) : sessions.length === 0 ? (
          <Empty className="mt-4" title="No sessions yet" body="When you finish a live call, its recap shows up here for this project." />
        ) : (
          <ul className="mt-4 flex flex-col gap-2">
            {sessions.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => onOpenSession(s.id)}
                  className="press w-full rounded-2xl bg-white/[0.025] px-5 py-4 text-left hairline hover:bg-white/[0.05]"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[15px] font-medium tracking-[-0.02em]">{s.title || "Session"}</p>
                      <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted">
                        {s.started_at ? <span>{formatEventWhen(s.started_at)}</span> : null}
                        {sessionDurationLabel(s) ? <span> · {sessionDurationLabel(s)}</span> : null}
                      </p>
                      {s.summary || s.summary_line ? (
                        <p className="mt-2 line-clamp-2 text-[13px] leading-relaxed text-ink/75">{s.summary || s.summary_line}</p>
                      ) : null}
                    </div>
                    {(s.card_count > 0 || s.commitment_count > 0) && (
                      <p className="mono shrink-0 text-[11.5px] text-faint">
                        {s.card_count ? `${s.card_count} card${s.card_count === 1 ? "" : "s"}` : ""}
                        {s.card_count && s.commitment_count ? " · " : ""}
                        {s.commitment_count ? `${s.commitment_count} promise${s.commitment_count === 1 ? "" : "s"}` : ""}
                      </p>
                    )}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
        {loadErr ? <p className="mt-2 text-[12px] text-warn">{loadErr}</p> : null}
      </div>
    </div>
  );
}
