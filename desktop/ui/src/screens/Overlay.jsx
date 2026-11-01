import { useCallback, useEffect, useState } from "react";
import { CaretDown, CaretUp, Play, Stop, ArrowUp, X, Lightning, ChatCircleText, ListChecks, ArrowsClockwise } from "@phosphor-icons/react";
import Logo from "../components/Logo.jsx";
import { api, post, mmss } from "../lib/api.js";
import { KIND, kindOf, CONFLICT_ACTIONS } from "../lib/cards.js";
import { listeningSince, useListenClock } from "../lib/useListenClock.js";

const QUICK = [
  { id: "assist", label: "Assist", Icon: Lightning, prompt: "Assist me right now: from my notes and this call so far, what matters most in the next minute?" },
  { id: "say", label: "What should I say?", Icon: ChatCircleText, prompt: "What should I say next? Give me one or two lines I can use as-is." },
  { id: "followups", label: "Follow-ups", Icon: ArrowsClockwise, prompt: "What follow-ups should I send after this call?" },
  { id: "recap", label: "Recap", Icon: ListChecks, prompt: "Give me a quick recap of the call so far: open items and who promised what." },
];

/**
 * The frameless always-on-top overlay (#/overlay): a control pill plus a glass
 * panel with the latest cards, quick actions and a one-line ask box.
 */
export default function Overlay() {
  const [status, setStatus] = useState(null);
  const [offline, setOffline] = useState(false);
  const [open, setOpen] = useState(true);
  const [answer, setAnswer] = useState(null); // { label, text } | { label, pending: true } | { label, error }
  const [input, setInput] = useState("");
  const [acting, setActing] = useState("");

  const refresh = useCallback(async () => {
    try {
      setStatus(await api("/api/status"));
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 1200);
    return () => clearInterval(id);
  }, [refresh]);

  const listening = Boolean(status?.listening);
  const elapsed = useListenClock(listening, listeningSince(status));
  const meeting = status?.meeting || {};
  const cards = (meeting.cards || []).slice().reverse();
  const conflict = cards.find((c) => kindOf(c.type) === "conflict");
  const rest = cards.filter((c) => c !== conflict).slice(0, conflict ? 2 : 3);

  async function toggleListen() {
    try {
      await post("/api/listen", { on: !listening });
    } finally {
      refresh();
    }
  }

  async function act(id, action) {
    setActing(`${id}:${action}`);
    try {
      await post(`/api/cards/${id}/action`, { action });
    } finally {
      setActing("");
      refresh();
    }
  }

  async function ask(label, message) {
    const text = message.trim();
    if (!text) return;
    setAnswer({ label, pending: true });
    try {
      const data = await post("/api/context/chat", { message: text });
      setAnswer({ label, text: data?.reply || "No answer came back." });
    } catch (err) {
      setAnswer({ label, error: `Muse didn't answer (${err.message || "no response"}).` });
    }
  }

  return (
    <div className="flex h-full w-full flex-col items-center gap-2 p-2 text-ink">
      <div className="pywebview-drag-region glass-solid flex w-full max-w-[440px] items-center gap-2 rounded-full py-1.5 pl-1.5 pr-1.5">
        <Logo state={listening ? "live" : "idle"} size={30} />
        <div className="pywebview-drag-region flex min-w-0 flex-1 items-center gap-2 pl-1">
          {listening ? (
            <span className="mono text-[14px] font-medium tracking-tight text-ink" aria-label={`Listening for ${mmss(elapsed)}`}>
              {mmss(elapsed)}
            </span>
          ) : (
            <span className="text-[13px] font-medium text-muted">{offline ? "Escort offline" : "Ready"}</span>
          )}
          {cards.length ? <span className="mono rounded-full bg-white/[0.07] px-1.5 text-[10.5px] leading-4 text-muted">{cards.length}</span> : null}
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="press flex h-8 items-center gap-1 rounded-full bg-white/[0.06] pl-3 pr-2.5 text-[12.5px] font-medium text-ink/90 hover:bg-white/[0.1]"
        >
          {open ? "Hide" : "Show"}
          {open ? <CaretUp size={13} aria-hidden /> : <CaretDown size={13} aria-hidden />}
        </button>
        <button
          type="button"
          onClick={toggleListen}
          disabled={offline}
          aria-label={listening ? "Stop listening" : "Start listening"}
          title={listening ? "Stop listening" : "Start listening"}
          className={`press grid h-8 w-8 shrink-0 place-items-center rounded-full disabled:opacity-40 ${
            listening ? "bg-conflict text-[#1d0703] hover:bg-[#ff7d6b]" : "bg-ink text-bg hover:bg-white"
          }`}
        >
          {listening ? <Stop size={13} weight="fill" aria-hidden /> : <Play size={13} weight="fill" aria-hidden />}
        </button>
      </div>

      {open ? (
        <section className="rise glass-solid flex min-h-0 w-full max-w-[440px] flex-1 flex-col overflow-hidden rounded-[24px]" aria-label="Escort live panel">
          <header className="pywebview-drag-region flex items-center justify-between gap-3 px-4 pb-2 pt-3.5">
            <p className="pywebview-drag-region truncate text-[12.5px] font-medium text-muted">{meeting.title || "Live session"}</p>
            {meeting.latency?.count ? (
              <p className="mono shrink-0 text-[11px] text-faint" title="Median time from speech to card">
                p50 {Math.round(meeting.latency.p50_ms)}ms
              </p>
            ) : null}
          </header>

          <div className="scroll-thin min-h-0 flex-1 space-y-2 overflow-auto px-2.5 pb-2.5">
            {answer ? <Answer answer={answer} onClose={() => setAnswer(null)} /> : null}
            {conflict ? <ConflictCard card={conflict} onAct={act} acting={acting} /> : null}
            {rest.map((card) => (
              <MiniCard key={card.id} card={card} />
            ))}
            {!cards.length ? (
              <p className="px-2 py-5 text-center text-[13px] leading-relaxed text-muted">
                {offline
                  ? "Can't reach Escort. Retrying."
                  : listening
                    ? "Listening. Cards land here when a line crosses your notes."
                    : "Press play to start listening."}
              </p>
            ) : null}
          </div>

          <div className="space-y-2 bg-black/20 px-2.5 pb-2.5 pt-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
            <div className="flex flex-wrap gap-1">
              {QUICK.map(({ id, label, Icon, prompt }) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => ask(label, prompt)}
                  disabled={answer?.pending}
                  className="press inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full bg-white/[0.06] px-2.5 text-[12px] font-medium text-ink/90 hover:bg-white/[0.1] disabled:opacity-50"
                >
                  <Icon size={13} aria-hidden />
                  {label}
                </button>
              ))}
            </div>
            <form
              className="flex items-center gap-2 rounded-full bg-black/30 py-1 pl-4 pr-1 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.09)] focus-within:shadow-[inset_0_0_0_1px_rgba(139,124,255,0.5)]"
              onSubmit={(e) => {
                e.preventDefault();
                ask("You asked", input);
                setInput("");
              }}
            >
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask Muse…"
                aria-label="Ask Muse"
                className="h-8 min-w-0 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-faint focus-visible:outline-none"
              />
              <button
                type="submit"
                disabled={!input.trim() || answer?.pending}
                aria-label="Send"
                className="press grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ink text-bg disabled:bg-white/[0.08] disabled:text-faint"
              >
                <ArrowUp size={15} weight="regular" aria-hidden />
              </button>
            </form>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function ConflictCard({ card, onAct, acting }) {
  return (
    <article className="rise relative overflow-hidden rounded-[18px] bg-[rgba(255,106,85,0.1)] p-4 shadow-[inset_0_0_0_1px_rgba(255,106,85,0.28)]">
      <p className="flex items-center justify-between gap-2 text-[11.5px] font-medium">
        <span className="inline-flex items-center gap-1.5 text-conflict">
          <KIND.conflict.Icon size={14} aria-hidden />
          Conflict
          {card.prd_section ? <span className="truncate font-normal text-[#ffb3a7]/80">· {card.prd_section}</span> : null}
        </span>
        {Number.isFinite(card.latency_ms) ? <span className="mono font-normal text-[#ffb3a7]/70">{Math.round(card.latency_ms)}ms</span> : null}
      </p>
      <h2 className="mt-2 text-[16px] font-medium leading-snug tracking-[-0.015em] text-balance">{card.title}</h2>
      <p className="mt-1 text-[13px] leading-relaxed text-ink/80">{card.body}</p>
      <div className="mt-3.5 flex flex-wrap gap-1.5">
        {CONFLICT_ACTIONS.map((a, i) => (
          <button
            key={a.id}
            type="button"
            disabled={Boolean(acting)}
            onClick={() => onAct(card.id, a.id)}
            className={`press h-8 rounded-full px-3.5 text-[12px] font-medium disabled:opacity-50 ${
              i === 0 ? "bg-conflict text-[#1d0703] hover:bg-[#ff7d6b]" : "bg-white/[0.08] text-ink/90 hover:bg-white/[0.13]"
            }`}
          >
            {acting === `${card.id}:${a.id}` ? "…" : a.label}
          </button>
        ))}
      </div>
    </article>
  );
}

function MiniCard({ card }) {
  const k = KIND[kindOf(card.type)];
  const { Icon } = k;
  return (
    <article className="rise rounded-[16px] bg-white/[0.035] px-3.5 py-3 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.06)]">
      <p className="flex items-center justify-between gap-2 text-[11.5px]">
        <span className={`inline-flex items-center gap-1.5 font-medium ${k.text}`}>
          <Icon size={13} aria-hidden />
          {k.label}
        </span>
        {Number.isFinite(card.latency_ms) ? <span className="mono text-faint">{Math.round(card.latency_ms)}ms</span> : null}
      </p>
      <p className="mt-1.5 text-[13.5px] font-medium leading-snug">{card.title}</p>
      <p className="mt-0.5 line-clamp-2 text-[12.5px] leading-relaxed text-muted">{card.body}</p>
    </article>
  );
}

function Answer({ answer, onClose }) {
  return (
    <article className="rise rounded-[16px] bg-[rgba(139,124,255,0.1)] px-3.5 py-3 shadow-[inset_0_0_0_1px_rgba(139,124,255,0.25)]" aria-live="polite">
      <p className="flex items-center justify-between gap-2 text-[11.5px] font-medium text-[#c6bfff]">
        {answer.label}
        <button type="button" onClick={onClose} aria-label="Dismiss answer" className="press grid h-6 w-6 place-items-center rounded-full text-[#c6bfff]/80 hover:bg-white/10 hover:text-ink">
          <X size={12} aria-hidden />
        </button>
      </p>
      {answer.pending ? (
        <span className="thinking mt-2 flex gap-1.5" role="status" aria-label="Muse is thinking">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
        </span>
      ) : answer.error ? (
        <p className="mt-1 text-[13px] text-conflict">{answer.error}</p>
      ) : (
        <p className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed text-ink/90">{answer.text}</p>
      )}
    </article>
  );
}
