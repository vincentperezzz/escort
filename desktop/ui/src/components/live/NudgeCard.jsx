import { memo } from "react";
import { X } from "@phosphor-icons/react";
import { nudgeMeta } from "../../lib/nudges.js";

const TONE = {
  warn: { tile: "ov-tile-nudge-warn", label: "text-[#ffc46b]", primary: "bg-[#ffc46b] text-[#1f1302] hover:bg-[#ffd58f]" },
  ask: { tile: "ov-tile-nudge-ask", label: "text-[#9fd8ff]", primary: "bg-[#9fd8ff] text-[#04131f] hover:bg-[#bfe5ff]" },
  quiet: { tile: "ov-tile", label: "text-muted", primary: "bg-white/[0.1] text-ink hover:bg-white/[0.15]" },
};

/** One proactive nudge: what Escort noticed, and up to three one-click answers. */
const NudgeCard = memo(function NudgeCard({ nudge, onAct, acting }) {
  const meta = nudgeMeta(nudge.kind);
  const tone = TONE[meta.tone] || TONE.ask;
  const { Icon } = meta;
  const quiet = nudge.priority === "low";
  const busy = acting.startsWith(`${nudge.id}:`);
  return (
    <article
      className={`${tone.tile} rise shrink-0 rounded-[18px] ${quiet ? "px-4 py-2.5" : "p-4"} ${nudge.priority === "urgent" ? "nudge-urgent" : ""}`}
      role={quiet ? "status" : "alert"}
    >
      <p className={`flex items-center justify-between gap-2 text-[11.5px] font-medium ${tone.label}`}>
        <span className="inline-flex min-w-0 items-center gap-1.5">
          <Icon size={14} aria-hidden />
          {meta.label}
        </span>
        <button
          type="button"
          onClick={() => onAct(nudge, "dismiss")}
          disabled={busy}
          aria-label="Dismiss"
          className="press -mr-1.5 grid h-6 w-6 place-items-center rounded-full opacity-70 hover:bg-white/10 hover:opacity-100"
        >
          <X size={12} aria-hidden />
        </button>
      </p>
      {quiet ? (
        <p className="mt-1 text-[12.5px] leading-snug text-ink/85">
          <span className="font-medium">{nudge.title}</span> <span className="text-muted">{nudge.body}</span>
        </p>
      ) : (
        <>
          <h2 className="mt-1.5 text-[15px] font-medium leading-snug tracking-[-0.01em] text-balance">{nudge.title}</h2>
          <p className="mt-1 select-text text-[13px] leading-relaxed text-ink/80">{nudge.body}</p>
          {nudge.actions?.length ? (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {nudge.actions.map((a, i) => (
                <button
                  key={a.id + i}
                  type="button"
                  disabled={busy}
                  onClick={() => onAct(nudge, a.id, a)}
                  className={`press h-8 rounded-full px-3.5 text-[12px] font-medium disabled:opacity-50 ${i === 0 ? tone.primary : "bg-white/[0.08] text-ink/90 hover:bg-white/[0.13]"}`}
                >
                  {acting === `${nudge.id}:${a.id}` ? "…" : a.label}
                </button>
              ))}
            </div>
          ) : null}
        </>
      )}
    </article>
  );
});

export default NudgeCard;
