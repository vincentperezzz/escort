import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowSquareOut, Check, Eye, EyeSlash, NotePencil, Play, Stop } from "@phosphor-icons/react";
import { Bezel, Button, Chip, Dot, Empty, WaveBars } from "../components/ui.jsx";
import { post, mmss } from "../lib/api.js";
import { KIND, kindOf, CONFLICT_ACTIONS } from "../lib/cards.js";
import ScreenLine from "../components/ScreenLine.jsx";
import NoteDestination, { UpdatePreview } from "../components/NoteDestination.jsx";
import { DEST_LABEL, cancelNotePage, confirmNotePage, isPageDest, noteHint, previewNotePage, saveNote, useNoteDestination } from "../lib/notes.js";
import { listeningSince, useListenClock } from "../lib/useListenClock.js";

export default function Live({ status, refresh, setListening, go, transcriptOpen, onToggleTranscript }) {
  const meeting = status?.meeting || {};
  const cards = meeting.cards || [];
  const turns = meeting.utterances || [];
  const listening = Boolean(status?.listening);
  const latency = meeting.latency;
  const sorted = useMemo(() => cards.slice().reverse(), [cards]);
  const elapsed = useListenClock(listening, listeningSince(status));
  const [busy, setBusy] = useState(false);
  const destination = useNoteDestination(status);
  const [notes, setNotes] = useState({}); // card id -> { state: "saving" | "doc" | "local" | "error", hint?, url?, label? }

  async function act(id, action) {
    await post(`/api/cards/${id}/action`, { action });
    refresh();
  }

  // User-clicked only: appends "who: what (due)" to this meeting's doc or page in the chosen destination.
  async function noteIt(card) {
    const { dest, label } = destination;
    setNotes((cur) => ({ ...cur, [card.id]: { state: "saving" } }));
    try {
      if (isPageDest(dest)) {
        // Existing page: read it, show what would be added, and write only after Confirm.
        const preview = await previewNotePage(dest, { kind: "commitment", card_id: card.id });
        setNotes((cur) => ({ ...cur, [card.id]: { state: "preview", preview, label: preview.page?.title } }));
        return;
      }
      const data = await saveNote(dest, { kind: "commitment", card_id: card.id });
      setNotes((cur) => ({ ...cur, [card.id]: { state: "doc", url: data?.doc?.url, label } }));
      refresh();
    } catch (err) {
      const hint = noteHint(err, dest);
      setNotes((cur) => ({ ...cur, [card.id]: { state: hint.quiet ? "local" : "error", hint } }));
    }
  }

  async function confirmUpdate(card) {
    const held = notes[card.id];
    setNotes((cur) => ({ ...cur, [card.id]: { ...held, state: "saving" } }));
    try {
      await confirmNotePage(held.preview.plan_id);
      setNotes((cur) => ({ ...cur, [card.id]: { state: "doc", label: held.preview.page?.title } }));
      refresh();
    } catch (err) {
      setNotes((cur) => ({ ...cur, [card.id]: { state: "error", hint: noteHint(err, "notion") } }));
    }
  }

  async function cancelUpdate(card) {
    const held = notes[card.id];
    if (held?.preview) cancelNotePage(held.preview.plan_id).catch(() => {});
    setNotes((cur) => {
      const next = { ...cur };
      delete next[card.id];
      return next;
    });
  }

  function noteFor(card) {
    const local = notes[card.id];
    if (local) return local;
    for (const dest of ["drive", "notion"]) {
      const saved = dest === "drive" ? status?.notes?.drive || status?.notes_drive : status?.notes?.notion;
      if ((saved?.noted_cards || []).includes(card.id)) return { state: "doc", url: saved.doc?.url, label: DEST_LABEL[dest] };
    }
    return null;
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
                    <InsightCard
                      card={card}
                      onAct={act}
                      note={noteFor(card)}
                      onNote={noteIt}
                      onConfirmUpdate={confirmUpdate}
                      onCancelUpdate={cancelUpdate}
                      destination={destination}
                      onFix={() => go?.("settings", "connections")}
                    />
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

export function InsightCard({ card, onAct, note = null, onNote, onFix, destination = null, onConfirmUpdate, onCancelUpdate }) {
  const kind = kindOf(card.type);
  const k = KIND[kind];
  const { Icon } = k;
  const isConflict = kind === "conflict";
  const isCommit = kind === "commit" && Boolean(onNote);
  const noted = note?.state === "doc" || note?.state === "local";
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
      {isCommit ? (
        <div className="relative mt-5">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <Chip tone="commit" onClick={() => onNote(card)} disabled={note?.state === "saving" || note?.state === "preview" || noted} aria-live="polite">
              {noted ? <Check size={14} aria-hidden /> : <NotePencil size={14} aria-hidden />}
              {note?.state === "saving" ? "Saving…" : note?.state === "doc" ? `Noted in ${note.label || "Docs"}` : noted ? "Noted" : "Note it"}
            </Chip>
            {note?.state === "doc" && note.url ? (
              <a className="inline-flex items-center gap-1 text-[12.5px] font-medium text-commit underline-offset-4 hover:underline" href={note.url} target="_blank" rel="noreferrer">
                Open in {note.label === "Notion" ? "Notion" : "Docs"} <ArrowSquareOut size={13} aria-hidden />
              </a>
            ) : null}
            {!noted && destination ? (
              <NoteDestination enabled={destination.enabled} labels={destination.labels} dest={destination.dest} onChange={destination.setDest} />
            ) : null}
          </div>
          {note?.state === "preview" ? (
            <UpdatePreview className="mt-3 max-w-[60ch]" preview={note.preview} onConfirm={() => onConfirmUpdate?.(card)} onCancel={() => onCancelUpdate?.(card)} />
          ) : null}
          {note?.hint ? (
            <p className={`mt-2.5 max-w-[60ch] text-[12.5px] leading-relaxed ${note.state === "error" ? "text-warn" : "text-muted"}`} role="status">
              {note.hint.text}{" "}
              {note.hint.fix && onFix ? (
                <button type="button" className="font-medium text-[#c6bfff] underline-offset-4 hover:underline" onClick={onFix}>
                  Open Connections
                </button>
              ) : null}
              {note.hint.href ? (
                <a className="font-medium text-[#c6bfff] underline-offset-4 hover:underline" href={note.hint.href} target="_blank" rel="noreferrer">
                  {note.hint.hrefLabel}
                </a>
              ) : null}
            </p>
          ) : null}
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
