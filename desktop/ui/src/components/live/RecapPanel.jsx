import { useEffect, useMemo, useState } from "react";
import { ArrowsClockwise, ArrowLeft, ArrowSquareOut, Copy, Check, FileArrowUp, Play, Sparkle } from "@phosphor-icons/react";
import { Bezel, Button, Empty, Pill } from "../ui.jsx";
import { Bubble } from "../../screens/Live.jsx";
import { api, post } from "../../lib/api.js";
import { KIND } from "../../lib/cards.js";
import { nudgeMeta } from "../../lib/nudges.js";
import NoteDestination, { UpdatePreview } from "../NoteDestination.jsx";
import { cancelNotePage, confirmNotePage, isPageDest, noteHint, previewNotePage, saveNote, useNoteDestination } from "../../lib/notes.js";
import { liveRecapHash } from "./parseLiveRoute.js";

const TABS = [
  { id: "summary", label: "Summary" },
  { id: "transcript", label: "Transcript" },
  { id: "cards", label: "Cards" },
];

function transcriptToTurns(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.map((row, i) => ({
    id: row.id || `t-${i}`,
    speaker: row.speaker || "Them",
    text: row.text || "",
  }));
}

export default function RecapPanel({ status, refresh, sessionId, tab, go, onBack, showStartAnother, onStartAnother }) {
  const [fetched, setFetched] = useState(null);
  const [fetchState, setFetchState] = useState(sessionId ? "loading" : "idle");

  useEffect(() => {
    if (!sessionId) {
      setFetched(null);
      setFetchState("idle");
      return undefined;
    }
    let alive = true;
    setFetchState("loading");
    api(`/api/sessions/${encodeURIComponent(sessionId)}/recap`)
      .then((d) => {
        if (alive) {
          setFetched(d);
          setFetchState("ready");
        }
      })
      .catch((err) => {
        if (alive) {
          setFetched(null);
          setFetchState(err.status === 404 ? "missing" : "error");
        }
      });
    return () => {
      alive = false;
    };
  }, [sessionId]);

  const meeting = useMemo(() => {
    if (sessionId && fetched) {
      const turns = transcriptToTurns(fetched.transcript);
      return {
        title: fetched.title,
        utterances: turns,
        open_items: fetched.open_items || [],
        commitments: fetched.commitments || fetched.meeting?.commitments || [],
        cards: fetched.cards || [],
        snapshots: fetched.snapshots || [],
        jots: fetched.jots || [],
        nudges: fetched.nudges || [],
        started_at: fetched.started_at,
        ended_at: fetched.ended_at,
      };
    }
    return status?.meeting || {};
  }, [sessionId, fetched, status?.meeting]);

  const summary = useMemo(() => {
    if (sessionId && fetched) {
      const nested = fetched.summary;
      if (nested && typeof nested === "object" && (nested.status || nested.bullets || nested.commitments)) return nested;
      if (fetched.status || fetched.bullets || fetched.commitments || fetched.conflicts) {
        return {
          status: fetched.status || "ready",
          title: fetched.title,
          bullets: fetched.bullets || [],
          commitments: fetched.commitments || [],
          conflicts: fetched.conflicts || [],
        };
      }
      if (typeof nested === "string" && nested.trim()) return { status: "ready", title: fetched.title, bullets: [nested.trim()] };
      return { status: "idle" };
    }
    return status?.recap?.summary || status?.summary || { status: "idle" };
  }, [sessionId, fetched, status?.summary, status?.recap]);

  const active = TABS.some((t) => t.id === tab) ? tab : summary.status && summary.status !== "idle" ? "summary" : "cards";

  function setTab(next) {
    go("live", liveRecapHash(sessionId, next));
  }

  const title = meeting.title || fetched?.title || "Recap";

  return (
    <div className="scroll-thin mx-auto min-h-0 w-full max-w-5xl flex-1 overflow-auto px-4 pb-16 pt-8 md:px-10 md:pt-10">
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft size={15} aria-hidden /> Back
        </Button>
        {showStartAnother ? (
          <Button variant="primary" size="sm" onClick={onStartAnother} trailing={<Play size={13} weight="fill" />}>
            Start another meeting
          </Button>
        ) : null}
      </div>

      <div className="mb-2">
        <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.03em]">{title}</h1>
        <p className="mt-1 max-w-[62ch] text-[14px] leading-relaxed text-muted">Your notes from the call, what people promised, and what is still open.</p>
      </div>

      {fetchState === "loading" ? <p className="mb-6 text-[13px] text-faint">Loading recap…</p> : null}
      {fetchState === "missing" ? (
        <Empty title="Recap not found" body="This session may have been removed or is not available yet." />
      ) : null}
      {fetchState === "error" ? <Empty title="Could not load recap" body="Try again in a moment." /> : null}

      {fetchState === "missing" || fetchState === "error" ? null : (
        <>
          <div role="tablist" aria-label="Recap views" className="glass mb-6 inline-flex rounded-full p-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={active === t.id}
                onClick={() => setTab(t.id)}
                className={`press h-9 rounded-full px-4 text-[13px] font-medium ${
                  active === t.id ? "bg-white/[0.1] text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]" : "text-muted hover:text-ink"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          {active === "summary" ? (
            <SummaryView summary={summary} meeting={meeting} refresh={refresh} status={status} go={go} sessionScoped={Boolean(sessionId)} />
          ) : null}
          {active === "transcript" ? <TranscriptView turns={meeting.utterances || []} /> : null}
          {active === "cards" ? <CardsView meeting={meeting} /> : null}
        </>
      )}
    </div>
  );
}

const EXTRA_SECTIONS = [
  ["takeaways", "Key takeaways"],
  ["decisions", "Decisions"],
  ["open_questions", "Open questions"],
];

function summaryText(s, visuals = []) {
  const lines = [s.title || "Meeting summary", ""];
  if (s.bullets?.length) lines.push("Highlights", ...s.bullets.map((b) => `- ${b}`), "");
  if (s.notes?.length) {
    lines.push("Notes");
    for (const topic of s.notes) {
      lines.push("", `## ${topic.heading}`);
      for (const p of topic.points || []) lines.push(`- ${p.text}`, ...(p.sub || []).map((x) => `  - ${x}`));
    }
    lines.push("");
  }
  for (const [key, title] of EXTRA_SECTIONS) {
    if (s[key]?.length) lines.push(title, ...s[key].map((b) => `- ${b}`), "");
  }
  if (s.commitments?.length) lines.push("Commitments", ...s.commitments.map((c) => `- ${c.who}: ${c.what}${c.due ? ` (${c.due})` : ""}`), "");
  if (s.conflicts?.length) lines.push("Conflicts", ...s.conflicts.map((c) => `- ${c.title}${c.section ? ` (${c.section})` : ""}`), "");
  const captioned = visuals.filter((v) => v.caption);
  if (captioned.length) lines.push("Visuals", ...captioned.map((v) => `- ${v.caption}`));
  return lines.join("\n").trim();
}

function BulletList({ items }) {
  return (
    <ul className="space-y-2.5">
      {items.map((b, i) => (
        <li key={i} className="flex gap-3 text-[14.5px] leading-relaxed text-ink/85">
          <span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-faint" aria-hidden />
          <span className="max-w-[68ch]">{b}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Organized notes: one heading per topic; the user's own jots in full ink, what Escort
 * added from the transcript in grey, detail as sub-points.
 */
function MeetingNotes({ notes }) {
  const mine = notes.some((t) => (t.points || []).some((p) => p.from_you));
  return (
    <div className="space-y-6">
      {mine ? (
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-faint">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-ink" aria-hidden /> Your notes
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-faint" aria-hidden /> Added by Escort from the call
          </span>
        </p>
      ) : null}
      {notes.map((topic, i) => (
        <section key={i}>
          <h3 className="text-[15.5px] font-semibold tracking-[-0.01em] text-ink">{topic.heading}</h3>
          <ul className="mt-2 space-y-1.5">
            {(topic.points || []).map((p, j) => (
              <li key={j} className="max-w-[70ch]">
                <p className={`flex gap-3 text-[14.5px] leading-relaxed ${p.from_you ? "font-medium text-ink" : "text-muted"}`}>
                  <span className={`mt-[9px] h-1 w-1 shrink-0 rounded-full ${p.from_you ? "bg-ink" : "bg-faint"}`} aria-hidden />
                  <span>{p.text}</span>
                </p>
                {p.sub?.length ? (
                  <ul className="mt-1 space-y-1 pl-7">
                    {p.sub.map((x, k) => (
                      <li key={k} className="flex gap-2.5 text-[13.5px] leading-relaxed text-muted">
                        <span className="mt-[8px] h-px w-2 shrink-0 bg-faint" aria-hidden />
                        <span>{x}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function SummaryView({ summary, meeting, refresh, status, go, sessionScoped }) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState(null);
  const [saved, setSaved] = useState({});
  const [preview, setPreview] = useState(null);
  const hasTranscript = (meeting.utterances || []).length > 0;
  const destination = useNoteDestination(status);
  const { dest, label, current } = destination;
  const doc = saved[dest] || (current.summary_saved_at ? current.doc : null);
  const canSave = !sessionScoped;

  async function saveSummary() {
    setSaving(true);
    setSaveErr(null);
    try {
      if (isPageDest(dest)) {
        setPreview({ dest, ...(await previewNotePage(dest, { kind: "summary" })) });
        return;
      }
      const data = await saveNote(dest, { kind: "summary" });
      setSaved((cur) => ({ ...cur, [dest]: data?.doc || null }));
      await refresh?.();
    } catch (e) {
      setSaveErr({ err: e, dest });
    } finally {
      setSaving(false);
    }
  }

  async function confirmUpdate() {
    const held = preview;
    setSaving(true);
    try {
      await confirmNotePage(held.plan_id);
      setSaved((cur) => ({ ...cur, [held.dest]: { title: held.page?.title } }));
      setPreview(null);
      await refresh?.();
    } catch (e) {
      setSaveErr({ err: e, dest: "notion" });
      setPreview(null);
    } finally {
      setSaving(false);
    }
  }

  function cancelUpdate() {
    if (preview) cancelNotePage(preview.plan_id).catch(() => {});
    setPreview(null);
  }

  async function regenerate() {
    setBusy(true);
    setErr("");
    try {
      await post("/api/summary/regenerate", {});
      await refresh?.();
    } catch (e) {
      setErr(e.status === 404 ? "Summaries are not available in this build yet." : e.message || "Could not start the summary.");
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    const text = summaryText(summary, meeting.snapshots || []);
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        /* clipboard unavailable */
      }
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }

  if (summary.status === "generating") {
    return (
      <Bezel className="rise" coreClassName="relative overflow-hidden p-8 md:p-10">
        <div className="flex items-center gap-3" role="status" aria-live="polite">
          <span className="grid h-10 w-10 place-items-center rounded-full bg-accent-soft text-[#c6bfff]">
            <Sparkle size={18} className="thinking-pulse" aria-hidden />
          </span>
          <div>
            <h2 className="text-[18px] font-medium tracking-[-0.02em]">Writing your summary…</h2>
            <p className="mt-0.5 text-[13px] text-muted">Pulling out highlights, decisions and promises. This takes a few seconds.</p>
          </div>
        </div>
        <div className="mt-8 space-y-3" aria-hidden>
          <div className="sum-line h-5 w-2/3" />
          <div className="sum-line h-3.5 w-full" />
          <div className="sum-line h-3.5 w-[92%]" />
          <div className="sum-line h-3.5 w-4/5" />
          <div className="sum-line mt-6 h-5 w-1/3" />
          <div className="sum-line h-3.5 w-[70%]" />
        </div>
      </Bezel>
    );
  }

  if (summary.status === "error") {
    return (
      <Empty title="The summary did not finish" body="Something went wrong while writing it. Your transcript and cards are safe.">
        {!sessionScoped ? (
          <Button variant="primary" size="sm" onClick={regenerate} disabled={busy}>
            <ArrowsClockwise size={14} aria-hidden /> Try again
          </Button>
        ) : null}
        {err ? <p className="mt-3 text-[12.5px] text-warn">{err}</p> : null}
      </Empty>
    );
  }

  if (summary.status !== "ready") {
    return (
      <Empty title="No summary yet" body="A summary is written when you stop listening. You can also write one from the current transcript.">
        {!sessionScoped && hasTranscript ? (
          <Button variant="primary" size="sm" onClick={regenerate} disabled={busy}>
            Write summary
          </Button>
        ) : null}
        {err ? <p className="mt-3 text-[12.5px] text-warn">{err}</p> : null}
      </Empty>
    );
  }

  const bullets = summary.bullets || [];
  const commitments = summary.commitments || [];
  const conflicts = summary.conflicts || [];
  const visuals = (meeting.snapshots || []).filter((v) => v && v.url);
  return (
    <Bezel className="rise" coreClassName="p-7 md:p-10">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1 basis-80">
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-faint">Meeting summary</p>
          <h2 className="mt-2 text-[24px] font-semibold leading-tight tracking-[-0.03em] text-balance">{summary.title || meeting.title || "Meeting summary"}</h2>
        </div>
        <div className="flex flex-wrap gap-2">
          {!sessionScoped ? (
            <Button size="sm" onClick={regenerate} disabled={busy}>
              <ArrowsClockwise size={14} aria-hidden /> Regenerate
            </Button>
          ) : null}
          {canSave ? (
            <Button size="sm" onClick={saveSummary} disabled={saving}>
              <FileArrowUp size={14} aria-hidden /> {saving ? "Saving…" : isPageDest(dest) ? "Review update" : doc ? "Save again" : `Save to ${label}`}
            </Button>
          ) : null}
          <Button size="sm" variant="primary" onClick={copy}>
            {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />} {copied ? "Copied" : "Copy summary"}
          </Button>
        </div>
      </div>
      {canSave ? <NoteDestination className="mt-4" enabled={destination.enabled} labels={destination.labels} dest={dest} onChange={destination.setDest} /> : null}
      {preview ? <UpdatePreview className="mt-4 max-w-[60ch]" preview={preview} busy={saving} onConfirm={confirmUpdate} onCancel={cancelUpdate} /> : null}
      {doc ? (
        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-[12.5px]" role="status">
          <Pill tone="good">
            <Check size={12} aria-hidden /> Saved
          </Pill>
          <span className="min-w-0 truncate text-muted">{doc.title}</span>
          {doc.url ? (
            <a className="inline-flex items-center gap-1 font-medium text-[#c6bfff] underline-offset-4 hover:underline" href={doc.url} target="_blank" rel="noreferrer">
              Open in {dest === "notion" ? "Notion" : "Docs"} <ArrowSquareOut size={13} aria-hidden />
            </a>
          ) : null}
        </div>
      ) : null}
      {saveErr ? <SaveError err={saveErr.err} dest={saveErr.dest} go={go} /> : null}
      {!doc && !saveErr && canSave ? (
        <p className="mt-3 text-[12px] text-faint">
          {isPageDest(dest)
            ? "Adds a dated recap to the end of the page you picked. You review the change before anything is written."
            : dest === "notion"
              ? "Notion: one page per meeting under the parent page you picked. Saving again adds a dated section."
              : "Google Docs: one doc per meeting in your Escort folder. Saving again adds a dated section."}
        </p>
      ) : null}
      {err ? <p className="mt-3 text-[12.5px] text-warn">{err}</p> : null}

      <Section title="Highlights" count={bullets.length > 1 ? bullets.length : 0}>
        {bullets.length ? <BulletList items={bullets} /> : <p className="text-sm text-muted">Nothing notable.</p>}
      </Section>

      {summary.notes?.length ? (
        <Section title="Notes">
          <MeetingNotes notes={summary.notes} />
        </Section>
      ) : null}

      {EXTRA_SECTIONS.map(([key, title]) =>
        summary[key]?.length ? (
          <Section key={key} title={title}>
            <BulletList items={summary[key]} />
          </Section>
        ) : null,
      )}

      {visuals.length ? (
        <Section title="Visuals" count={visuals.length}>
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
            {visuals.map((v, i) => (
              <li key={v.id || i}>
                <a
                  href={v.url}
                  target="_blank"
                  rel="noreferrer"
                  className="press group block overflow-hidden rounded-2xl bg-white/[0.025] hairline"
                  title="Open full size"
                >
                  <img
                    src={v.url}
                    alt={v.caption || v.kind || "Meeting visual"}
                    loading="lazy"
                    className="aspect-video w-full object-cover transition-opacity group-hover:opacity-90"
                  />
                  {v.caption ? <p className="px-3 py-2.5 text-[12.5px] leading-snug text-ink/80">{v.caption}</p> : null}
                </a>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="Commitments" kind="commit" count={commitments.length}>
        {commitments.length ? (
          <ul className="flex flex-col gap-2">
            {commitments.map((c, i) => (
              <li key={i} className="flex flex-wrap items-baseline justify-between gap-x-4 rounded-2xl bg-white/[0.025] px-4 py-3 hairline">
                <p className="text-[14.5px] font-medium leading-snug">
                  <span className="text-commit">{c.who}</span> <span className="text-ink/90">{c.what}</span>
                </p>
                {c.due ? <span className="mono text-[12px] text-muted">{c.due}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">No commitments captured.</p>
        )}
      </Section>

      <Section title="Conflicts" kind="conflict" count={conflicts.length}>
        {conflicts.length ? (
          <ul className="flex flex-col gap-2">
            {conflicts.map((c, i) => (
              <li key={i} className="rounded-2xl bg-conflict-soft px-4 py-3 shadow-[inset_0_0_0_1px_rgba(255,106,85,0.22)]">
                <p className="text-[14.5px] font-medium leading-snug">{c.title}</p>
                {c.section ? <p className="mt-1 text-[12.5px] text-[#ffb3a7]/80">{c.section}</p> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">No conflicts against your notes.</p>
        )}
      </Section>
    </Bezel>
  );
}

function SaveError({ err, dest, go }) {
  const hint = noteHint(err, dest);
  const text = hint.quiet ? hint.text.replace("Noted in this meeting's recap. ", "") : hint.text;
  const link = "font-medium text-[#c6bfff] underline-offset-4 hover:underline";
  return (
    <p className={`mt-3 text-[12.5px] leading-relaxed ${hint.quiet ? "text-muted" : "text-warn"}`} role="alert">
      {text}{" "}
      {hint.fix ? (
        <button type="button" className={link} onClick={() => go?.("settings", "connections")}>
          Open Connections
        </button>
      ) : null}
      {hint.href ? (
        <a className={link} href={hint.href} target="_blank" rel="noreferrer">
          {hint.hrefLabel}
        </a>
      ) : null}
    </p>
  );
}

function Section({ title, kind, count, children }) {
  const k = kind ? KIND[kind] : null;
  return (
    <section className="mt-9">
      <h3 className="mb-3.5 flex items-center gap-2 text-[13px] font-medium text-muted">
        {k ? <k.Icon size={15} className={k.text} aria-hidden /> : null}
        {title}
        {count ? <span className="mono text-faint">{count}</span> : null}
      </h3>
      {children}
    </section>
  );
}

function TranscriptView({ turns }) {
  if (!turns.length) return <Empty title="No transcript" body="Lines show up here as people speak." />;
  return (
    <Bezel className="rise" coreClassName="p-5 md:p-7">
      <p className="mb-4 text-[12px] text-faint">Them on the left, you on the right</p>
      <ul className="space-y-2.5">
        {turns.map((t) => (
          <Bubble key={t.id} turn={t} />
        ))}
      </ul>
    </Bezel>
  );
}

function CardsView({ meeting }) {
  const cards = meeting.cards || [];
  const typed = (word) => cards.filter((c) => String(c.type || "").toLowerCase().includes(word));
  const open = meeting.open_items?.length ? meeting.open_items : typed("conflict");
  const promises = meeting.commitments?.length ? meeting.commitments : typed("commit");
  const replies = typed("reply");
  const nudges = meeting.nudges || [];
  if (open.length === 0 && promises.length === 0 && replies.length === 0 && nudges.length === 0) {
    return <Empty title="Nothing to recap yet" body="Conflicts, promises, suggested replies and Escort's heads-ups collect here as they land in Live." />;
  }
  return (
    <div className="grid gap-5 md:grid-cols-2">
      <Column kind="conflict" title="Open against your notes" count={open.length} emptyText="No open conflicts.">
        {open.map((item, i) => (
          <li key={item.id || i} className="rounded-2xl bg-white/[0.025] px-4 py-3.5 hairline">
            <p className="text-[14.5px] font-medium leading-snug">{item.title || item.what}</p>
            {item.prd_section ? <p className="mt-1.5 text-[12.5px] text-muted">{item.prd_section}</p> : null}
          </li>
        ))}
      </Column>
      <Column kind="commit" title="Commitments" count={promises.length} emptyText="No commitments captured.">
        {promises.map((item, i) => (
          <li key={item.id || i} className="rounded-2xl bg-white/[0.025] px-4 py-3.5 hairline">
            <p className="text-[14.5px] font-medium leading-snug">{item.what || item.title}</p>
            <p className="mt-1.5 text-[12.5px] text-muted">
              <span className="text-ink">{item.who}</span>
              {item.when || item.due ? `, ${item.when || item.due}` : ""}
            </p>
          </li>
        ))}
      </Column>
      <Column kind="reply" title="Suggested replies" count={replies.length} emptyText="No replies suggested.">
        {replies.map((item, i) => (
          <li key={item.id || i} className="rounded-2xl bg-white/[0.025] px-4 py-3.5 hairline">
            <p className="text-[14.5px] leading-snug">{item.body || item.title}</p>
            {item.trigger_text ? <p className="mt-1.5 line-clamp-2 text-[12.5px] text-muted">After: “{item.trigger_text}”</p> : null}
          </li>
        ))}
      </Column>
      <Column kind="reply" title="Escort spoke up" count={nudges.length} emptyText="No heads-ups during this call.">
        {nudges.map((item, i) => {
          const meta = nudgeMeta(item.kind);
          return (
            <li key={item.id || i} className="rounded-2xl bg-white/[0.025] px-4 py-3.5 hairline">
              <p className="flex items-center justify-between gap-2 text-[12px] text-muted">
                <span className="inline-flex items-center gap-1.5">
                  <meta.Icon size={13} aria-hidden /> {meta.label}
                </span>
                <span>{item.status === "dismissed" ? "Dismissed" : item.status === "acted" ? "Acted on" : ""}</span>
              </p>
              <p className="mt-1.5 text-[14.5px] font-medium leading-snug">{item.title}</p>
              <p className="mt-1 text-[13px] leading-relaxed text-muted">{item.body}</p>
            </li>
          );
        })}
      </Column>
    </div>
  );
}

function Column({ kind, title, count, emptyText, children }) {
  const k = KIND[kind];
  const { Icon } = k;
  return (
    <Bezel className="rise" coreClassName="p-6">
      <h2 className="flex items-center justify-between gap-3">
        <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-[13px] font-medium ${k.chip}`}>
          <Icon size={15} aria-hidden />
          {title}
        </span>
        <span className="mono text-[13px] text-faint">{count}</span>
      </h2>
      {count === 0 ? <p className="mt-5 text-sm text-muted">{emptyText}</p> : <ul className="mt-5 flex flex-col gap-2">{children}</ul>}
    </Bezel>
  );
}
