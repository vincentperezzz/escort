import { useEffect, useMemo, useRef, useState } from "react";
import { Warning, CheckCircle, ChatTeardropText, CaretRight, Eye, EyeSlash } from "@phosphor-icons/react";
import { Button, Chip, Empty, WaveBars } from "../components/ui.jsx";
import { post, formatEventWhen } from "../lib/api.js";

function kindOf(type = "") {
  if (type.includes("CONFLICT")) return "conflict";
  if (type.includes("COMMIT")) return "commit";
  return "reply";
}

const KIND = {
  conflict: { label: "Conflict", hint: "Against your notes", Icon: Warning, text: "text-conflict", soft: "bg-conflict-soft", rail: "bg-conflict" },
  commit: { label: "Commitment", hint: "Someone promised something", Icon: CheckCircle, text: "text-commit", soft: "bg-commit-soft", rail: "bg-commit" },
  reply: { label: "Suggested reply", hint: "You could say", Icon: ChatTeardropText, text: "text-reply", soft: "bg-reply-soft", rail: "bg-reply" },
};

export default function Live({ status, refresh, onGoContext, transcriptOpen, onToggleTranscript }) {
  const meeting = status?.meeting || {};
  const cards = meeting.cards || [];
  const turns = meeting.utterances || [];
  const listening = status?.listening;
  const latency = meeting.latency;
  const sorted = useMemo(() => cards.slice().reverse(), [cards]);

  async function act(id, action) {
    await post(`/api/cards/${id}/action`, { action });
    refresh();
  }
  function toggleListen() {
    post("/api/listen", { on: !listening }).then(refresh);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-line bg-surface px-5 py-3.5 md:px-8">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <WaveBars active={listening} />
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold leading-tight">{meeting.title || "Live session"}</h2>
            <p className="text-xs text-muted">{listening ? "Capturing your mic and their audio" : "Paused"}</p>
          </div>
        </div>
        {latency?.count ? (
          <p className="mono hidden text-xs text-muted sm:block" title="Time from speech to card">
            p50 {Math.round(latency.p50_ms)}ms <span className="text-faint">/</span> p95 {Math.round(latency.p95_ms)}ms
          </p>
        ) : null}
        <Button
          variant={listening ? "secondary" : "primary"}
          className="min-w-[8.5rem]"
          onClick={toggleListen}
        >
          {listening ? "Stop listening" : "Start listening"}
        </Button>
      </header>

      <div className={`grid min-h-0 flex-1 ${transcriptOpen ? "lg:grid-cols-[minmax(0,1fr)_380px]" : ""}`}>
        <div className="scroll-thin min-h-0 overflow-auto px-5 py-6 md:px-8">
          <div className="mx-auto flex max-w-2xl flex-col gap-5">
            <Upcoming context={status?.context} refresh={refresh} onPrep={onGoContext} />
            {sorted.length === 0 ? (
              <Empty
                title={listening ? "Listening. Nothing to flag yet." : "No cards yet"}
                body={
                  listening
                    ? "A card appears the moment a line crosses your notes, a promise, or a question you could answer."
                    : "Start listening, or load the staged demo from Settings. Cards appear when speech crosses your notes."
                }
              >
                {listening ? null : (
                  <Button variant="primary" size="sm" onClick={toggleListen}>
                    Start listening
                  </Button>
                )}
              </Empty>
            ) : (
              <ol className="flex flex-col gap-3" aria-live="polite">
                {sorted.map((card) => (
                  <li key={card.id} className="rise">
                    <InsightCard card={card} onAct={act} />
                  </li>
                ))}
              </ol>
            )}
            {!transcriptOpen ? (
              <Button variant="ghost" size="sm" className="self-start" onClick={onToggleTranscript}>
                <Eye size={16} aria-hidden /> Show transcript ({turns.length})
              </Button>
            ) : null}
          </div>
        </div>
        {transcriptOpen ? <Transcript turns={turns} onHide={onToggleTranscript} /> : null}
      </div>
    </div>
  );
}

function InsightCard({ card, onAct }) {
  const k = KIND[kindOf(card.type)];
  const { Icon } = k;
  const isConflict = kindOf(card.type) === "conflict";
  return (
    <article className="relative overflow-hidden rounded-xl border border-line bg-raised pl-[5px] shadow-[var(--shadow)]">
      <span className={`absolute inset-y-0 left-0 w-[5px] ${k.rail}`} aria-hidden />
      <div className="px-4 py-3.5">
        <div className="flex items-center justify-between gap-3">
          <p className={`flex items-center gap-1.5 text-xs font-semibold ${k.text}`}>
            <Icon size={16} weight="fill" aria-hidden />
            {k.label}
            <span className="font-normal text-muted">· {k.hint}</span>
          </p>
          <p className="mono shrink-0 text-xs text-muted" title="Latency">
            {Math.round(card.latency_ms)}ms
          </p>
        </div>
        <h3 className="mt-2 text-[17px] font-semibold leading-snug tracking-tight">{card.title}</h3>
        <p className="mt-1.5 text-sm leading-relaxed text-ink/90">{card.body}</p>
        {card.prd_section ? (
          <p className={`mt-3 inline-flex rounded-md px-2 py-1 text-xs font-medium ${k.soft} ${k.text}`}>{card.prd_section}</p>
        ) : null}
        {isConflict ? (
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

function Transcript({ turns, onHide }) {
  const end = useRef(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);
  return (
    <aside className="flex min-h-[18rem] min-w-0 flex-col border-t border-line bg-surface lg:min-h-0 lg:border-l lg:border-t-0" aria-label="Transcript">
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
        <div>
          <h3 className="text-sm font-semibold">Transcript</h3>
          <p className="text-xs text-muted">Them on the left, you on the right</p>
        </div>
        <Button variant="ghost" size="sm" onClick={onHide} aria-label="Hide transcript">
          <EyeSlash size={16} aria-hidden /> Hide
        </Button>
      </div>
      <ul className="scroll-thin min-h-0 flex-1 space-y-2.5 overflow-auto px-5 py-4">
        {turns.length === 0 ? (
          <li className="text-sm text-muted">Lines show up here as people speak.</li>
        ) : (
          turns.map((turn) => <Bubble key={turn.id} turn={turn} />)
        )}
        <li ref={end} aria-hidden />
      </ul>
    </aside>
  );
}

function Bubble({ turn }) {
  const me = turn.speaker === "Me";
  return (
    <li className={`flex ${me ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[88%] rounded-xl px-3 py-2 text-sm leading-relaxed ${
          me ? "rounded-br-sm bg-accent-soft" : "rounded-bl-sm bg-sunken"
        }`}
      >
        <span className={`text-[11px] font-semibold ${me ? "text-accent" : "text-muted"}`}>{turn.speaker}</span>
        <p className="mt-0.5">{turn.text}</p>
      </div>
    </li>
  );
}

function Upcoming({ context, refresh, onPrep }) {
  const events = context?.upcoming || [];
  const prepEventId = context?.prep?.selected_event_id;
  const [busy, setBusy] = useState("");

  async function prep(eventId) {
    setBusy(eventId);
    try {
      await post("/api/context/match", { event_id: eventId });
      await refresh();
      onPrep?.();
    } finally {
      setBusy("");
    }
  }

  if (!events.length) return null;
  return (
    <section className="rounded-xl border border-line bg-surface p-4" aria-label="Upcoming meetings">
      <h3 className="text-sm font-semibold">Up next</h3>
      <ul className="mt-2 divide-y divide-line">
        {events.map((event) => (
          <li key={event.id} className="flex flex-wrap items-center gap-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{event.title}</p>
              <p className="text-xs text-muted">{formatEventWhen(event.start)}</p>
            </div>
            <Button
              variant={prepEventId === event.id ? "secondary" : "primary"}
              size="sm"
              disabled={busy === event.id}
              onClick={() => prep(event.id)}
            >
              {busy === event.id ? "Matching…" : prepEventId === event.id ? "Prepped" : "Prep"}
              <CaretRight size={14} aria-hidden />
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
