import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, CalendarBlank, FileText, Plus } from "@phosphor-icons/react";
import Logo from "../components/Logo.jsx";
import ChangedChips from "../components/ChangedChips.jsx";
import FormattedText from "../components/FormattedText.jsx";
import { post, formatEventWhen } from "../lib/api.js";

const SUGGESTIONS = [
  "What should I watch for?",
  "Where could scope creep in?",
  "What did we already commit to?",
  "Draft my opening line",
];

const prefersReducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export default function Chat({ status, refresh, go, seed = "" }) {
  const ctx = status?.context || {};
  const serverChat = ctx.chat || [];
  const [local, setLocal] = useState(null);
  const [input, setInput] = useState(() => {
    try {
      return seed ? decodeURIComponent(seed) : "";
    } catch {
      return "";
    }
  });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [fresh, setFresh] = useState(-1);
  const endRef = useRef(null);
  const scroller = useRef(null);

  const chat = local && local.length > serverChat.length ? local : serverChat;

  useEffect(() => {
    if (local && serverChat.length >= local.length) setLocal(null);
  }, [local, serverChat.length]);

  const scrollToEnd = (smooth = true) => {
    const el = scroller.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth && !prefersReducedMotion() ? "smooth" : "auto" });
  };

  useEffect(() => {
    scrollToEnd(chat.length > 0);
  }, [chat.length, pending]);

  async function send(text) {
    const message = text.trim();
    if (!message || pending) return;
    setError("");
    const optimistic = [...chat, { role: "user", content: message }];
    setLocal(optimistic);
    setInput("");
    setPending(true);
    try {
      const data = await post("/api/context/chat", { message });
      const next = Array.isArray(data?.context?.chat)
        ? data.context.chat
        : [...optimistic, { role: "assistant", content: data?.reply || "", changed: data?.changed }];
      setLocal(next);
      setFresh(next.length - 1);
      refresh();
    } catch (err) {
      setLocal(null);
      setInput(message);
      setError(`Escort AI didn't answer (${err.message || "no response"}). Your message is back in the box; try again.`);
    } finally {
      setPending(false);
    }
  }

  const prepEvent = (ctx.upcoming || []).find((e) => e.id === ctx.prep?.selected_event_id);
  const items = ctx.items || [];
  const empty = chat.length === 0 && !pending;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scroller} className="scroll-thin min-h-0 flex-1 overflow-auto px-4 md:px-8">
        <div className="mx-auto flex min-h-full w-full max-w-[46rem] flex-col">
          {empty ? (
            <EmptyHero prepEvent={prepEvent} onPick={send} />
          ) : (
            <ol className="flex flex-col gap-7 pb-8 pt-10" aria-live="polite">
              {chat.map((turn, i) => (
                <Message key={i} turn={turn} stream={i === fresh} onGrow={() => scrollToEnd(false)} />
              ))}
              {pending ? <Thinking /> : null}
              <li ref={endRef} aria-hidden />
            </ol>
          )}
        </div>
      </div>

      <div className="shrink-0 px-4 pb-6 pt-2 md:px-8">
        <div className="mx-auto w-full max-w-[46rem]">
          <SourceChips items={items} prepEvent={prepEvent} go={go} />
          <Composer value={input} onChange={setInput} onSend={() => send(input)} pending={pending} />
          {error ? (
            <p className="mt-3 px-2 text-[13px] text-conflict" role="alert">
              {error}
            </p>
          ) : (
            <p className="mt-3 px-2 text-center text-[12px] text-faint">
              Escort AI uses your loaded notes, live call transcript, and screen. Enter to send, Shift+Enter for a new line.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function EmptyHero({ prepEvent, onPick }) {
  return (
    <div className="rise flex flex-1 flex-col items-center justify-center py-12 text-center">
      <Logo size={56} />
      <h1 className="mt-7 max-w-[20ch] text-[36px] font-semibold leading-[1.08] tracking-[-0.035em] text-balance">
        What do you want to walk in knowing?
      </h1>
      <p className="mt-3 max-w-md text-[15px] leading-relaxed text-muted text-pretty">
        {prepEvent
          ? `Prepping for ${prepEvent.title}, ${formatEventWhen(prepEvent.start)}.`
          : "Ask about your notes, the scope, or what the other side is likely to push for."}
      </p>
      <div className="mt-9 grid w-full max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((s, i) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            style={{ animationDelay: `${120 + i * 70}ms` }}
            className="rise press group flex items-center justify-between gap-3 rounded-2xl bg-white/[0.03] px-4 py-3.5 text-left text-sm text-ink/90 hairline hover:bg-white/[0.06]"
          >
            {s}
            <ArrowUp size={15} className="btn-icon rotate-45 text-faint group-hover:text-ink" aria-hidden />
          </button>
        ))}
      </div>
    </div>
  );
}

function SourceChips({ items, prepEvent, go }) {
  const shown = items.slice(0, 3);
  return (
    <div className="mb-3 flex flex-wrap items-center gap-1.5 px-1" aria-label="Context sources">
      {prepEvent ? (
        <span className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full bg-accent-soft px-3 text-[12px] text-[#c6bfff]">
          <CalendarBlank size={13} aria-hidden />
          {prepEvent.title}
        </span>
      ) : null}
      {shown.map((item) => (
        <span key={item.id} className="inline-flex h-7 max-w-[15rem] shrink-0 items-center gap-1.5 rounded-full bg-white/[0.04] px-3 text-[12px] text-muted hairline">
          <FileText size={13} className="shrink-0" aria-hidden />
          <span className="text-ink/80">{item.source_label}</span>
          <span className="truncate">{item.title}</span>
        </span>
      ))}
      {items.length > shown.length ? (
        <span className="inline-flex h-7 shrink-0 items-center rounded-full bg-white/[0.04] px-2.5 text-[12px] text-muted hairline">+{items.length - shown.length}</span>
      ) : null}
      <button
        type="button"
        onClick={() => go("context")}
        className="press inline-flex h-7 shrink-0 items-center gap-1 rounded-full px-2.5 text-[12px] text-faint hover:bg-white/[0.05] hover:text-ink"
      >
        <Plus size={13} aria-hidden />
        {items.length ? "Context" : "Add context"}
      </button>
    </div>
  );
}

function Composer({ value, onChange, onSend, pending }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value]);

  return (
    <form
      className="bezel"
      style={{ "--r": "30px", "--p": "6px" }}
      onSubmit={(e) => {
        e.preventDefault();
        onSend();
      }}
    >
      <div className="bezel-core flex items-end gap-3 py-2.5 pl-5 pr-2.5 transition-shadow duration-300 ease-glass focus-within:shadow-[inset_0_1px_0_rgba(255,255,255,0.07),inset_0_0_0_1px_rgba(139,124,255,0.4),0_0_0_4px_rgba(139,124,255,0.08)]">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              onSend();
            }
          }}
          placeholder="Ask Escort AI about this meeting…"
          aria-label="Message Escort AI"
          className="scroll-thin max-h-[180px] min-h-[24px] flex-1 resize-none bg-transparent py-2 text-[15px] leading-6 outline-none placeholder:text-faint focus-visible:outline-none"
        />
        <button
          type="submit"
          disabled={pending || !value.trim()}
          aria-label="Send"
          className="press grid h-10 w-10 shrink-0 place-items-center rounded-full bg-ink text-bg hover:bg-white disabled:bg-white/[0.08] disabled:text-faint"
        >
          <ArrowUp size={18} weight="regular" aria-hidden />
        </button>
      </div>
    </form>
  );
}

function Message({ turn, stream, onGrow }) {
  if (turn.role === "user") {
    return (
      <li className="rise flex justify-end">
        <p className="max-w-[80%] whitespace-pre-wrap rounded-[22px] rounded-br-md bg-white/[0.07] px-4 py-2.5 text-[15px] leading-relaxed shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
          {turn.content}
        </p>
      </li>
    );
  }
  return (
    <li className="rise flex gap-3.5">
      <Logo size={26} className="mt-0.5" />
      <div className="min-w-0 flex-1 pt-0.5">
        <p className="mb-1.5 text-[12.5px] font-medium text-muted">Escort AI</p>
        <StreamText text={turn.content || ""} stream={stream} onGrow={onGrow} />
        <ChangedChips changed={turn.changed} className="mt-3" />
      </div>
    </li>
  );
}

/** Reveals a fresh reply word by word so it lands like it is being written. */
function StreamText({ text, stream, onGrow }) {
  const words = text.split(/(\s+)/);
  const [shown, setShown] = useState(() => (stream && !prefersReducedMotion() ? 0 : words.length));
  const done = shown >= words.length;

  useEffect(() => {
    if (done) return undefined;
    const id = setInterval(() => {
      setShown((n) => Math.min(words.length, n + 3));
    }, 28);
    return () => clearInterval(id);
  }, [done, words.length]);

  useEffect(() => {
    if (!done) onGrow?.();
  }, [shown, done, onGrow]);

  const visible = done ? text : words.slice(0, shown).join("");
  const cursor = !done ? <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-[3px] rounded-full bg-accent" aria-hidden /> : null;
  return (
    <FormattedText
      text={visible}
      cursor={cursor}
      className="space-y-3 text-[15px] leading-7 text-ink/90 text-pretty"
    />
  );
}

function Thinking() {
  return (
    <li className="fade-in flex items-center gap-3.5" role="status" aria-label="Escort AI is thinking">
      <Logo size={26} />
      <span className="thinking flex gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-accent" />
        <span className="h-1.5 w-1.5 rounded-full bg-accent" />
        <span className="h-1.5 w-1.5 rounded-full bg-accent" />
      </span>
    </li>
  );
}
