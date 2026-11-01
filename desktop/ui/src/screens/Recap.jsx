import { useState } from "react";
import { ArrowsClockwise, Copy, Check, Sparkle } from "@phosphor-icons/react";
import { Bezel, Button, Empty, PageHeader } from "../components/ui.jsx";
import { Bubble } from "./Live.jsx";
import { post } from "../lib/api.js";
import { KIND } from "../lib/cards.js";

const TABS = [
  { id: "summary", label: "Summary" },
  { id: "transcript", label: "Transcript" },
  { id: "cards", label: "Cards" },
];

export default function Recap({ status, refresh, tab, go }) {
  const meeting = status?.meeting || {};
  const summary = status?.summary || { status: "idle" };
  const active = TABS.some((t) => t.id === tab) ? tab : summary.status && summary.status !== "idle" ? "summary" : "cards";

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Recap" lede={`${meeting.title ? `${meeting.title}. ` : ""}Your notes from the call, what people promised, and what is still open.`} />
      <div role="tablist" aria-label="Recap views" className="glass mb-6 inline-flex rounded-full p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={active === t.id}
            onClick={() => go("recap", t.id)}
            className={`press h-9 rounded-full px-4 text-[13px] font-medium ${
              active === t.id ? "bg-white/[0.1] text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]" : "text-muted hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {active === "summary" ? <SummaryView summary={summary} meeting={meeting} refresh={refresh} /> : null}
      {active === "transcript" ? <TranscriptView turns={meeting.utterances || []} /> : null}
      {active === "cards" ? <CardsView meeting={meeting.meeting || {}} /> : null}
    </div>
  );
}

function summaryText(s) {
  const lines = [s.title || "Meeting summary", ""];
  if (s.bullets?.length) lines.push("Context", ...s.bullets.map((b) => `- ${b}`), "");
  if (s.commitments?.length) lines.push("Commitments", ...s.commitments.map((c) => `- ${c.who}: ${c.what}${c.due ? ` (${c.due})` : ""}`), "");
  if (s.conflicts?.length) lines.push("Conflicts", ...s.conflicts.map((c) => `- ${c.title}${c.section ? ` (${c.section})` : ""}`));
  return lines.join("\n").trim();
}

function SummaryView({ summary, meeting, refresh }) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState("");
  const hasTranscript = (meeting.utterances || []).length > 0;

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
    const text = summaryText(summary);
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
            <p className="mt-0.5 text-[13px] text-muted">Pulling out context, promises and conflicts. This takes a few seconds.</p>
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
        <Button variant="primary" size="sm" onClick={regenerate} disabled={busy}>
          <ArrowsClockwise size={14} aria-hidden /> Try again
        </Button>
        {err ? <p className="mt-3 text-[12.5px] text-warn">{err}</p> : null}
      </Empty>
    );
  }

  if (summary.status !== "ready") {
    return (
      <Empty title="No summary yet" body="A summary is written when you stop listening. You can also write one from the current transcript.">
        {hasTranscript ? (
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
  return (
    <Bezel className="rise" coreClassName="p-7 md:p-10">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1 basis-80">
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-faint">Meeting summary</p>
          <h2 className="mt-2 text-[24px] font-semibold leading-tight tracking-[-0.03em] text-balance">{summary.title || meeting.title || "Meeting summary"}</h2>
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={regenerate} disabled={busy}>
            <ArrowsClockwise size={14} aria-hidden /> Regenerate
          </Button>
          <Button size="sm" variant="primary" onClick={copy}>
            {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />} {copied ? "Copied" : "Copy summary"}
          </Button>
        </div>
      </div>
      {err ? <p className="mt-3 text-[12.5px] text-warn">{err}</p> : null}

      <Section title="Context">
        {bullets.length ? (
          <ul className="space-y-2.5">
            {bullets.map((b, i) => (
              <li key={i} className="flex gap-3 text-[14.5px] leading-relaxed text-ink/85">
                <span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-faint" aria-hidden />
                <span className="max-w-[68ch]">{b}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">Nothing notable.</p>
        )}
      </Section>

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
  const open = meeting.open_items || [];
  const promises = meeting.commitments || [];
  if (open.length === 0 && promises.length === 0) {
    return <Empty title="Nothing to recap yet" body="Open items and promises collect here after cards land in Live." />;
  }
  return (
    <div className="grid gap-5 md:grid-cols-2">
      <Column kind="conflict" title="Open against your notes" count={open.length} emptyText="No open conflicts.">
        {open.map((item) => (
          <li key={item.id} className="rounded-2xl bg-white/[0.025] px-4 py-3.5 hairline">
            <p className="text-[14.5px] font-medium leading-snug">{item.title}</p>
            {item.prd_section ? <p className="mt-1.5 text-[12.5px] text-muted">{item.prd_section}</p> : null}
          </li>
        ))}
      </Column>
      <Column kind="commit" title="Commitments" count={promises.length} emptyText="No commitments captured.">
        {promises.map((item) => (
          <li key={item.id} className="rounded-2xl bg-white/[0.025] px-4 py-3.5 hairline">
            <p className="text-[14.5px] font-medium leading-snug">{item.what}</p>
            <p className="mt-1.5 text-[12.5px] text-muted">
              <span className="text-ink">{item.who}</span>
              {item.when ? `, ${item.when}` : ""}
            </p>
          </li>
        ))}
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
