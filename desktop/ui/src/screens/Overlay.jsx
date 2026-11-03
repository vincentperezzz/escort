import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CaretDown, CaretUp, Play, Stop, ArrowUp, X, Lightning, ChatCircleText, ListChecks, ArrowsClockwise, Drop } from "@phosphor-icons/react";
import Logo from "../components/Logo.jsx";
import { api, post, mmss } from "../lib/api.js";
import ScreenLine from "../components/ScreenLine.jsx";
import ChangedChips from "../components/ChangedChips.jsx";
import FormattedText from "../components/FormattedText.jsx";
import { useOverlay } from "../lib/useOverlay.js";
import { KIND, kindOf, CONFLICT_ACTIONS } from "../lib/cards.js";
import { listeningSince, useListenClock } from "../lib/useListenClock.js";

const QUICK = [
  { id: "assist", label: "Assist", Icon: Lightning, prompt: "Assist me right now: from my notes and this call so far, what matters most in the next minute?" },
  { id: "say", label: "What to say", title: "What should I say?", Icon: ChatCircleText, prompt: "What should I say next? Give me one or two lines I can use as-is." },
  { id: "followups", label: "Follow-ups", title: "Draft follow-ups", Icon: ArrowsClockwise, prompt: "What follow-ups should I send after this call?" },
  { id: "recap", label: "Recap", Icon: ListChecks, prompt: "Give me a quick recap of the call so far: open items and who promised what." },
];

/** The Electron overlay's bridge (overlay/preload.js); absent in a plain browser. */
const bridge = typeof window !== "undefined" ? window.escortOverlay : undefined;

/**
 * Keep the overlay window interactive at all times so buttons and the Ask Muse
 * input never become unclickable after focus or style transitions on Windows.
 */
function useClickThrough() {
  useEffect(() => {
    if (!bridge) return undefined;
    bridge.setIgnoreMouse(false);
    const keepInteractive = () => bridge.setIgnoreMouse(false);
    window.addEventListener("pointerenter", keepInteractive, true);
    window.addEventListener("pointerdown", keepInteractive, true);
    return () => {
      window.removeEventListener("pointerenter", keepInteractive, true);
      window.removeEventListener("pointerdown", keepInteractive, true);
    };
  }, []);
}

/**
 * Move the window by dragging the pill's empty area. Pointer capture keeps the
 * drag alive as the window slides under the cursor; screen coordinates stay valid.
 */
function useWindowDrag() {
  const from = useRef(null);
  const frame = useRef(0);
  return useMemo(() => {
    if (!bridge?.dragStart) return {};
    const end = (e) => {
      if (!from.current) return;
      cancelAnimationFrame(frame.current);
      bridge.dragMove(e.screenX - from.current.x, e.screenY - from.current.y);
      from.current = null;
      if (e.currentTarget.hasPointerCapture?.(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
      bridge.dragEnd();
    };
    return {
      onPointerDown: (e) => {
        if (e.button !== 0 || e.target.closest("button, input, a")) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        from.current = { x: e.screenX, y: e.screenY };
        bridge.dragStart();
      },
      onPointerMove: (e) => {
        if (!from.current) return;
        const dx = e.screenX - from.current.x;
        const dy = e.screenY - from.current.y;
        cancelAnimationFrame(frame.current);
        frame.current = requestAnimationFrame(() => bridge.dragMove(dx, dy));
      },
      onPointerUp: end,
      onPointerCancel: end,
    };
  }, []);
}

/** GET /api/status every 1.2s, but only hand React a new object when something changed. */
function useStatus() {
  const [status, setStatus] = useState(null);
  const [offline, setOffline] = useState(false);
  const last = useRef("");
  const refresh = useCallback(async () => {
    try {
      const data = await api("/api/status");
      const key = JSON.stringify(data);
      if (key !== last.current) {
        last.current = key;
        setStatus(data);
      }
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
  return { status, offline, refresh };
}

/** Stable across polls as long as the card list itself is unchanged. */
function useCards(meeting) {
  const raw = meeting?.cards;
  const key = JSON.stringify(raw || []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => (raw || []).slice().reverse(), [key]);
}

/**
 * The in-meeting overlay (#/overlay): three glass surfaces on a fully transparent
 * page, with real gaps between them: the control pill, the body panel (screen
 * line, notes, cards, answers) and the bottom container (quick actions + ask). Opacity is applied to the native window.
 */
export default function Overlay() {
  useClickThrough();
  const { status, offline, refresh } = useStatus();
  const ov = useOverlay({ pollMs: 2000 });
  const { update: updateOverlay, reload: reloadOverlay } = ov;
  const collapsed = Boolean(ov.collapsed);
  const opacity = Number.isFinite(ov.opacity) ? ov.opacity : 0.92;
  const [tuning, setTuning] = useState(false);
  const [answer, setAnswer] = useState(null); // { label, text } | { label, pending: true } | { label, error }
  const [acting, setActing] = useState("");

  const activeProj = status?.active_project;
  const listening = Boolean(status?.listening);
  const startedAt = listeningSince(status);
  const meeting = status?.meeting;
  const cards = useCards(meeting);
  const conflict = cards.find((c) => kindOf(c.type) === "conflict");
  const rest = cards.filter((c) => c !== conflict).slice(0, conflict ? 2 : 3);
  const latency = meeting?.latency?.count ? `Median speech-to-card ${Math.round(meeting.latency.p50_ms)}ms` : "";

  const toggleCollapsed = useCallback(() => {
    setTuning(false);
    bridge?.setCollapsed(!collapsed);
    updateOverlay({ collapsed: !collapsed });
  }, [collapsed, updateOverlay]);

  const toggleListen = useCallback(async () => {
    try {
      await post("/api/listen", { on: !listening });
    } finally {
      refresh();
    }
  }, [listening, refresh]);

  const act = useCallback(
    async (id, action) => {
      setActing(`${id}:${action}`);
      try {
        await post(`/api/cards/${id}/action`, { action });
      } finally {
        setActing("");
        refresh();
      }
    },
    [refresh],
  );

  const ask = useCallback(
    async (label, message) => {
      const text = message.trim();
      if (!text) return;
      setAnswer({ label, pending: true });
      try {
        const data = await post("/api/context/chat", { message: text });
        setAnswer({ label, text: data?.reply || "No answer came back.", changed: data?.changed });
        if (data?.changed?.length) {
          // A chat command changed a setting: pull the new display / opacity / share state now.
          refresh();
          reloadOverlay();
        }
      } catch (err) {
        setAnswer({ label, error: `Escort AI didn't answer (${err.message || "no response"}).` });
      }
    },
    [refresh, reloadOverlay],
  );

  const openMainApp = useCallback(async () => {
    bridge?.releaseFocus();
    try {
      await post("/api/main-window/show", {});
      await post("/api/overlay", { visible: false });
    } catch {
      /* ignore */
    } finally {
      reloadOverlay();
    }
  }, [reloadOverlay]);

  const closeAnswer = useCallback(() => setAnswer(null), []);
  const toggleTuning = useCallback(() => setTuning((v) => !v), []);
  const commitOpacity = useCallback((v) => updateOverlay({ opacity: v }), [updateOverlay]);
  const busy = Boolean(answer?.pending);

  return (
    <div className="flex h-full w-full flex-col gap-2.5 p-2.5 text-ink">
      <Pill
        listening={listening}
        startedAt={startedAt}
        offline={offline}
        title={meeting?.title || ""}
        hint={latency}
        count={cards.length}
        open={!collapsed}
        tuning={tuning}
        onTune={toggleTuning}
        onToggle={toggleCollapsed}
        onListen={toggleListen}
        onOpenApp={openMainApp}
        project={activeProj}
      />

      {!collapsed && tuning ? <TuneBar opacity={opacity} onCommit={commitOpacity} /> : null}

      {!collapsed ? (
        <>
          <section data-hit className="ov-glass ov-panel flex min-h-0 shrink flex-col rounded-[24px]" aria-label="Escort live cards">
            <div className="ov-stack flex min-h-0 flex-col gap-2 overflow-y-auto p-2.5 pb-6">
              <ScreenChip status={status} />
              {answer ? <Answer answer={answer} onClose={closeAnswer} /> : null}
              {conflict ? <ConflictCard card={conflict} onAct={act} acting={acting} /> : null}
              {rest.map((card) => (
                <MiniCard key={card.id} card={card} />
              ))}
              {!cards.length && !answer ? <EmptyNote offline={offline} listening={listening} /> : null}
            </div>
          </section>
          <div data-hit className="ov-glass flex shrink-0 flex-col gap-1 rounded-[24px] p-1.5">
            <QuickRow onAsk={ask} busy={busy} />
            <Composer onAsk={ask} busy={busy} project={activeProj} />
          </div>
        </>
      ) : null}
    </div>
  );
}

const Clock = memo(function Clock({ listening, startedAt }) {
  const elapsed = useListenClock(listening, startedAt);
  return (
    <span className="mono shrink-0 text-[14px] font-medium tracking-tight text-ink" aria-label={`Listening for ${mmss(elapsed)}`}>
      {mmss(elapsed)}
    </span>
  );
});

const Pill = memo(function Pill({ listening, startedAt, offline, title, hint, count, open, tuning, onTune, onToggle, onListen, onOpenApp, project }) {
  const drag = useWindowDrag();
  return (
    <div
      data-hit
      {...drag}
      className={`ov-glass flex h-12 shrink-0 items-center gap-1.5 rounded-full py-1.5 pl-2 pr-1.5 ${drag.onPointerDown ? "cursor-grab active:cursor-grabbing" : ""}`}
      title={hint || undefined}
    >
      <button
        type="button"
        onClick={onOpenApp}
        onPointerDown={(e) => e.stopPropagation()}
        aria-label="Open Escort window"
        title="Open Escort window"
        className="press shrink-0 rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <Logo state={listening ? "live" : "idle"} size={32} />
      </button>
      <div className="flex min-w-0 flex-1 items-baseline gap-2 pl-1.5">
        {listening ? <Clock listening={listening} startedAt={startedAt} /> : <span className="shrink-0 text-[13px] font-medium text-muted">{offline ? "Escort offline" : "Ready"}</span>}
        {project?.name && project.name !== "General" ? (
          <span className="mono shrink-0 self-center rounded-full bg-accent/[0.15] px-2 text-[10.5px] font-medium leading-4 text-accent truncate max-w-[100px]">
            {project.name}
          </span>
        ) : null}
        {title && listening ? <span className="truncate text-[12px] text-faint">{title}</span> : null}
        {count ? <span className="mono shrink-0 self-center rounded-full bg-white/[0.07] px-1.5 text-[10.5px] leading-4 text-muted">{count}</span> : null}
      </div>
      {open ? (
        <button
          type="button"
          onClick={onTune}
          aria-expanded={tuning}
          aria-label="Overlay transparency"
          title="Overlay transparency"
          className={`press grid h-9 w-9 shrink-0 place-items-center rounded-full hover:bg-white/[0.1] ${tuning ? "bg-white/[0.12] text-ink" : "bg-white/[0.05] text-muted"}`}
        >
          <Drop size={15} aria-hidden />
        </button>
      ) : null}
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="press flex h-9 shrink-0 items-center gap-1 rounded-full bg-white/[0.05] pl-3.5 pr-3 text-[12.5px] font-medium text-ink/90 hover:bg-white/[0.1]"
      >
        {open ? "Hide" : "Show"}
        {open ? <CaretUp size={13} aria-hidden /> : <CaretDown size={13} aria-hidden />}
      </button>
      <button
        type="button"
        onClick={onListen}
        disabled={offline}
        aria-label={listening ? "Stop listening" : "Start listening"}
        title={listening ? "Stop listening" : "Start listening"}
        className={`press grid h-9 w-9 shrink-0 place-items-center rounded-full disabled:opacity-40 ${
          listening ? "bg-conflict text-[#1d0703] hover:bg-[#ff7d6b]" : "bg-ink text-bg hover:bg-white"
        }`}
      >
        {listening ? <Stop size={13} weight="fill" aria-hidden /> : <Play size={13} weight="fill" aria-hidden />}
      </button>
    </div>
  );
});

/**
 * Opacity slider. While dragging, the thumb follows a local value and the
 * native window follows at once; the backend hears about it at most every
 * 100ms and once more on release, so polls never fight the drag.
 */
const TuneBar = memo(function TuneBar({ opacity, onCommit }) {
  const [local, setLocal] = useState(opacity);
  const dragging = useRef(false);
  const sentAt = useRef(0);
  const pending = useRef(null);
  const timer = useRef(null);

  useEffect(() => {
    if (!dragging.current) setLocal(opacity);
  }, [opacity]);
  useEffect(() => () => clearTimeout(timer.current), []);

  const send = useCallback(
    (v) => {
      clearTimeout(timer.current);
      timer.current = null;
      pending.current = null;
      sentAt.current = Date.now();
      onCommit(v);
    },
    [onCommit],
  );

  const onInput = (e) => {
    const v = Number(e.target.value) / 100;
    setLocal(v);
    bridge?.setOpacity(v);
    const wait = 100 - (Date.now() - sentAt.current);
    if (wait <= 0) send(v);
    else {
      pending.current = v;
      if (!timer.current) timer.current = setTimeout(() => send(pending.current), wait);
    }
  };

  const release = (e) => {
    if (!dragging.current) return;
    dragging.current = false;
    if (e.currentTarget.hasPointerCapture?.(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    send(Number(e.currentTarget.value) / 100);
  };

  const pct = Math.round(Math.min(1, Math.max(0.3, local)) * 100);
  return (
    <div data-hit className="ov-glass rise flex h-11 shrink-0 items-center gap-3 rounded-full pl-4 pr-4">
      <Drop size={14} className="shrink-0 text-faint" aria-hidden />
      <input
        type="range"
        min={30}
        max={100}
        step={1}
        value={pct}
        aria-label="Overlay opacity"
        onPointerDown={(e) => {
          dragging.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerUp={release}
        onPointerCancel={release}
        onChange={onInput}
        onKeyUp={(e) => send(Number(e.currentTarget.value) / 100)}
        className="opacity-range min-w-0 flex-1"
        style={{ "--fill": `${((pct - 30) / 70) * 100}%` }}
      />
      <span className="mono w-9 shrink-0 text-right text-[12px] text-muted">{pct}%</span>
    </div>
  );
});

const QuickRow = memo(function QuickRow({ onAsk, busy }) {
  return (
    <div className="flex h-9 shrink-0 items-center gap-0.5 px-0.5">
      {QUICK.map(({ id, label, title, Icon, prompt }) => (
        <button
          key={id}
          type="button"
          title={title || label}
          onClick={() => onAsk(label, prompt)}
          disabled={busy}
          className="press inline-flex h-8 min-w-0 flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-full px-2 text-[12px] font-medium tracking-tight text-ink/85 hover:bg-white/[0.08] hover:text-ink disabled:opacity-45"
        >
          <Icon size={13} aria-hidden />
          {label}
        </button>
      ))}
    </div>
  );
});

/**
 * The ask capsule. Its text lives here, so status polls never touch it. Focus
 * is reported to the backend once per change; the native window only becomes
 * focusable while the caret is in this field.
 */
const Composer = memo(function Composer({ onAsk, busy, project }) {
  const [input, setInput] = useState("");
  const inputRef = useRef(null);
  const reported = useRef(false);

  const report = useCallback((on) => {
    if (on) {
      bridge?.setIgnoreMouse(false);
      bridge?.focusInput();
    } else if (reported.current) {
      bridge?.releaseFocus();
    }
    if (reported.current === on) return;
    reported.current = on;
    post("/api/overlay", { input_focus: on }).catch(() => {});
  }, []);

  const focusBox = useCallback(
    (e) => {
      if (e?.target instanceof Element && e.target.closest("button")) return;
      report(true);
      inputRef.current?.focus();
      requestAnimationFrame(() => inputRef.current?.focus());
      setTimeout(() => inputRef.current?.focus(), 40);
    },
    [report],
  );

  useEffect(() => () => report(false), [report]);

  const submit = (e) => {
    e.preventDefault();
    if (!input.trim() || busy) return;
    onAsk("You asked", input);
    setInput("");
  };

  return (
    <form
      className="ov-capsule flex h-12 shrink-0 cursor-text items-center gap-2 rounded-full py-1.5 pl-4 pr-1.5"
      onPointerDown={focusBox}
      onClick={focusBox}
      onSubmit={submit}
    >
      <input
        ref={inputRef}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onPointerDown={(e) => {
          e.stopPropagation();
          focusBox(e);
        }}
        onClick={(e) => {
          e.stopPropagation();
          focusBox(e);
        }}
        onFocus={() => report(true)}
        onBlur={() => report(false)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            inputRef.current?.blur();
          } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            if (!busy) onAsk(QUICK[0].label, QUICK[0].prompt);
          }
        }}
        placeholder={project?.name && project.name !== "General" ? `Ask ${project.name}…` : "Ask Escort AI…"}
        aria-label="Ask Escort AI"
        autoComplete="off"
        className="h-9 min-w-0 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-faint focus-visible:outline-none"
      />
      <button
        type="submit"
        disabled={!input.trim() || busy}
        aria-label="Send"
        className="press grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink text-bg disabled:bg-white/[0.08] disabled:text-faint"
      >
        <ArrowUp size={15} weight="regular" aria-hidden />
      </button>
    </form>
  );
});

const EmptyNote = memo(function EmptyNote({ offline, listening }) {
  return (
    <p className="ov-tile rounded-[18px] px-4 py-3.5 text-[13px] leading-relaxed text-muted">
      {offline ? "Can't reach Escort. Retrying." : listening ? "Listening. Taking live notes and watching for commitments or misalignments." : "Press play to start listening."}
    </p>
  );
});

function ScreenChip({ status }) {
  if (!status?.screen && !status?.capture_error) return null;
  if (!status?.listening && !status?.capture_error && !status?.screen?.monitoring) return null;
  return (
    <div className="ov-tile self-start rounded-full px-3 py-1.5">
      <ScreenLine status={status} />
    </div>
  );
}

const ConflictCard = memo(function ConflictCard({ card, onAct, acting }) {
  return (
    <article className="ov-tile-conflict rise shrink-0 rounded-[18px] p-4">
      <p className="flex items-center justify-between gap-2 text-[11.5px] font-medium">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-conflict">
          <KIND.conflict.Icon size={14} aria-hidden />
          Conflict
          {card.prd_section ? <span className="truncate font-normal text-[#ffb3a7]/80">· {card.prd_section}</span> : null}
        </span>
        {Number.isFinite(card.latency_ms) ? <span className="mono font-normal text-[#ffb3a7]/70">{Math.round(card.latency_ms)}ms</span> : null}
      </p>
      <h2 className="mt-2 text-[16px] font-medium leading-snug tracking-[-0.015em] text-balance">{card.title}</h2>
      <p className="mt-1 text-[13px] leading-relaxed text-[#f3d9d4]">{card.body}</p>
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
});

const MiniCard = memo(function MiniCard({ card }) {
  const k = KIND[kindOf(card.type)];
  const { Icon } = k;
  return (
    <article className="ov-tile rise shrink-0 rounded-[18px] px-4 py-3">
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
});

const Answer = memo(function Answer({ answer, onClose }) {
  return (
    <article className="ov-tile-answer rise shrink-0 rounded-[18px] px-4 py-3" aria-live="polite">
      <p className="flex items-center justify-between gap-2 text-[11.5px] font-medium text-[#c6bfff]">
        {answer.label}
        <button type="button" onClick={onClose} aria-label="Dismiss answer" className="press -mr-1.5 grid h-6 w-6 place-items-center rounded-full text-[#c6bfff]/80 hover:bg-white/10 hover:text-ink">
          <X size={12} aria-hidden />
        </button>
      </p>
      {answer.pending ? (
        <span className="thinking mt-2 flex gap-1.5" role="status" aria-label="Escort AI is thinking">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
        </span>
      ) : answer.error ? (
        <p className="mt-1 text-[13px] text-conflict">{answer.error}</p>
      ) : (
        <>
          <FormattedText text={answer.text} className="mt-1 select-text space-y-1.5 text-[13px] leading-relaxed text-ink/90" />
          <ChangedChips changed={answer.changed} className="mt-2" />
        </>
      )}
    </article>
  );
});
