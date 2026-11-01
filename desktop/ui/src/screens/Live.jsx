import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, EyeSlash, Play, Stop } from "@phosphor-icons/react";
import { Bezel, Button, Chip, Dot, Empty, WaveBars } from "../components/ui.jsx";
import { post, mmss } from "../lib/api.js";
import { KIND, kindOf, CONFLICT_ACTIONS } from "../lib/cards.js";
import ScreenLine from "../components/ScreenLine.jsx";
import { listeningSince, useListenClock } from "../lib/useListenClock.js";

export default function Live({ status, refresh, setListening, transcriptOpen, onToggleTranscript }) {
  const meeting = status?.meeting || {};
  const cards = meeting.cards || [];
  const turns = meeting.utterances || [];
  const listening = Boolean(status?.listening);
  const latency = meeting.latency;
  const sorted = useMemo(() => cards.slice().reverse(), [cards]);
  const elapsed = useListenClock(listening, listeningSince(status));
  const [busy, setBusy] = useState(false);

  async function act(id, action) {
    await post(`/api/cards/${id}/action`, { action });
    refresh();
  }
  async function toggleListen() {
    setBusy(true);
    try {
      await setListening(!listening);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 px-4 pt-8 md:px-10">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-4">
          <div className="flex min-w-0 flex-1 items-center gap-4">
            <WaveBars active={listening} />
            <div className="min-w-0">
              <h1 className="truncate text-[26px] font-semibold leading-tight tracking-[-0.03em]">{meeting.title || "Live session"}</h1>
              <p className="mt-1 flex items-center gap-2 text-[13px] text-muted">
                <Dot tone={listening ? "signal" : "faint"} live={listening} />
                {listening ? (
                  <>
                    Listening <span className="mono text-ink/80">{mmss(elapsed)}</span>
                  </>
                ) : (
                  "Paused"
                )}
              </p>
              <ScreenLine status={status} className="mt-1" />
            </div>
          </div>
          {latency?.count ? (
            <p className="mono hidden text-[12px] text-muted sm:block" title="Time from speech to card">
              p50 <span className="text-ink">{Math.round(latency.p50_ms)}</span>ms <span className="text-faint">/</span> p95{" "}
              <span className="text-ink">{Math.round(latency.p95_ms)}</span>ms
            </p>
          ) : null}
          {listening ? (
            <Button variant="dangerGhost" onClick={toggleListen} disabled={busy}>
              <Stop size={14} weight="fill" aria-hidden /> Stop listening
            </Button>
          ) : (
            <Button variant="primary" onClick={toggleListen} disabled={busy} trailing={<Play size={13} weight="fill" />}>
              Start listening
            </Button>
          )}
        </div>
      </div>

      <div className={`mx-auto grid min-h-0 w-full max-w-6xl flex-1 gap-5 px-4 pb-6 pt-7 md:px-10 ${transcriptOpen ? "lg:grid-cols-[minmax(0,1fr)_360px]" : ""}`}>
        <div className="scroll-thin -mx-2 min-h-0 overflow-auto px-2 pb-4">
          <div className="flex flex-col gap-4">
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
              <ol className="flex flex-col gap-4" aria-live="polite">
                {sorted.map((card) => (
                  <li key={card.id} className="rise">
                    <InsightCard card={card} onAct={act} />
                  </li>
                ))}
              </ol>
            )}
            {!transcriptOpen ? (
              <Button variant="ghost" size="sm" className="self-start" onClick={onToggleTranscript}>
                <Eye size={15} aria-hidden /> Show transcript ({turns.length})
              </Button>
            ) : null}
          </div>
        </div>
        {transcriptOpen ? <Transcript turns={turns} onHide={onToggleTranscript} /> : null}
      </div>
    </div>
  );
}

export function InsightCard({ card, onAct }) {
  const kind = kindOf(card.type);
  const k = KIND[kind];
  const { Icon } = k;
  const isConflict = kind === "conflict";
  return (
    <Bezel as="article" size="sm" coreClassName="relative overflow-hidden px-6 py-5">
      {isConflict ? (
        <span
          className="pointer-events-none absolute inset-0"
          style={{ background: "radial-gradient(22rem 12rem at 0% 0%, rgba(255,106,85,0.12), transparent 70%)" }}
          aria-hidden
        />
      ) : null}
      <div className="relative flex items-center justify-between gap-3">
        <p className="flex min-w-0 items-center gap-2 text-[12.5px]">
          <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium ${k.chip}`}>
            <Icon size={14} weight="regular" aria-hidden />
            {k.label}
          </span>
          <span className="truncate text-faint">{k.hint}</span>
        </p>
        {Number.isFinite(card.latency_ms) ? (
          <p className="mono shrink-0 text-[11.5px] text-faint" title="Time from speech to card">
            {Math.round(card.latency_ms)}ms
          </p>
        ) : null}
      </div>
      <h3 className="relative mt-3.5 text-[18px] font-medium leading-snug tracking-[-0.02em] text-balance">{card.title}</h3>
      <p className="relative mt-1.5 max-w-[65ch] text-[14.5px] leading-relaxed text-ink/80">{card.body}</p>
      {card.prd_section ? <p className={`relative mt-3.5 inline-flex rounded-full px-2.5 py-1 text-[12px] ${k.chip}`}>{card.prd_section}</p> : null}
      {isConflict ? (
        <div className="relative mt-5 flex flex-wrap gap-2">
          {CONFLICT_ACTIONS.map((a, i) => (
            <Chip key={a.id} tone={i === 0 ? "conflict" : "neutral"} onClick={() => onAct(card.id, a.id)}>
              {a.label}
            </Chip>
          ))}
        </div>
      ) : null}
    </Bezel>
  );
}

function Transcript({ turns, onHide }) {
  const end = useRef(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);
  return (
    <Bezel as="aside" size="sm" className="min-h-[18rem] min-w-0 lg:min-h-0" coreClassName="flex min-h-0 flex-col" aria-label="Transcript">
      <div className="flex items-center justify-between gap-3 px-5 pb-3 pt-4">
        <div>
          <h2 className="text-[15px] font-medium">Transcript</h2>
          <p className="text-[12px] text-faint">Them on the left, you on the right</p>
        </div>
        <Button variant="ghost" size="sm" onClick={onHide} aria-label="Hide transcript">
          <EyeSlash size={15} aria-hidden /> Hide
        </Button>
      </div>
      <ul className="scroll-thin min-h-0 flex-1 space-y-2.5 overflow-auto px-4 pb-4">
        {turns.length === 0 ? (
          <li className="px-1 text-sm text-muted">Lines show up here as people speak.</li>
        ) : (
          turns.map((turn) => <Bubble key={turn.id} turn={turn} />)
        )}
        <li ref={end} aria-hidden />
      </ul>
    </Bezel>
  );
}

export function Bubble({ turn }) {
  const me = turn.speaker === "Me";
  return (
    <li className={`flex ${me ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[88%] rounded-[18px] px-3.5 py-2 text-[13.5px] leading-relaxed ${
          me ? "rounded-br-md bg-accent-soft" : "rounded-bl-md bg-white/[0.05]"
        }`}
      >
        <span className={`text-[11px] font-medium ${me ? "text-[#c6bfff]" : "text-faint"}`}>{turn.speaker}</span>
        <p className="mt-0.5 text-ink/90">{turn.text}</p>
      </div>
    </li>
  );
}
