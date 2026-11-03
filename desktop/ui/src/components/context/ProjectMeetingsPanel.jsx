import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowUpRight, CalendarBlank, PencilSimple, Sparkle, UsersThree } from "@phosphor-icons/react";
import { Bezel, Button, Panel, Pill } from "../ui.jsx";
import { api, formatEventWhen, post, relativeStart } from "../../lib/api.js";

function splitMeetings(meetings, now = Date.now()) {
  const upcoming = [];
  const past = [];
  for (const meeting of meetings) {
    const t = new Date(meeting.start).getTime();
    if (Number.isFinite(t) && t >= now - 5 * 60000) upcoming.push(meeting);
    else past.push(meeting);
  }
  upcoming.sort((a, b) => new Date(a.start) - new Date(b.start));
  past.sort((a, b) => new Date(b.start) - new Date(a.start));
  return { upcoming, past };
}

export default function ProjectMeetingsPanel({
  projectId,
  prepEventId,
  initialProposals,
  initialPickedIds,
  onPrepSaved,
  onGoChat,
  chatCount,
  sections,
  go,
}) {
  const [proposals, setProposals] = useState(initialProposals || []);
  const [picked, setPicked] = useState(() => new Set(initialPickedIds || []));
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [editing, setEditing] = useState(false);
  const [calendarEvents, setCalendarEvents] = useState([]);
  const [draftLinked, setDraftLinked] = useState(() => new Set());
  const [editBusy, setEditBusy] = useState(false);

  useEffect(() => {
    setProposals(initialProposals || []);
    setPicked(new Set(initialPickedIds || []));
  }, [initialProposals, initialPickedIds]);

  const loadMeetings = useCallback(async () => {
    if (!projectId) {
      setData(null);
      return;
    }
    setLoadError("");
    try {
      const res = await api(`/api/projects/${projectId}/meetings`);
      setData(res);
    } catch (err) {
      if (err.status === 404) setData({ meetings: [], next: null });
      else {
        setLoadError(err.message || "Could not load meetings.");
        setData(null);
      }
    }
  }, [projectId]);

  useEffect(() => {
    loadMeetings();
  }, [loadMeetings]);

  const meetings = data?.meetings || [];
  const next = data?.next || null;
  const { upcoming, past } = useMemo(() => splitMeetings(meetings), [meetings]);

  const prepTargetId = prepEventId || next?.event_id;

  async function rematch() {
    if (!prepTargetId) return;
    const data = await post("/api/context/match", { event_id: prepTargetId });
    setProposals(data.proposals || []);
    setPicked(new Set((data.proposals || []).slice(0, 3).map((p) => p.item_id)));
  }

  async function confirmPrep() {
    if (!prepTargetId) return;
    await post("/api/context/prep", { event_id: prepTargetId, item_ids: [...picked] });
    onPrepSaved?.();
  }

  function togglePick(id) {
    setPicked((current) => {
      const nextSet = new Set(current);
      if (nextSet.has(id)) nextSet.delete(id);
      else nextSet.add(id);
      return nextSet;
    });
  }

  async function startEdit() {
    setEditing(true);
    setEditBusy(true);
    try {
      const res = await api("/api/calendar/events");
      const events = res?.events || [];
      setCalendarEvents(events);
      const linked = new Set(meetings.map((m) => m.event_id));
      setDraftLinked(linked);
    } catch (err) {
      if (err.status === 404) {
        setCalendarEvents([]);
        setDraftLinked(new Set(meetings.map((m) => m.event_id)));
      } else {
        setLoadError(err.message || "Could not load calendar.");
        setEditing(false);
      }
    } finally {
      setEditBusy(false);
    }
  }

  function cancelEdit() {
    setEditing(false);
    setCalendarEvents([]);
  }

  async function saveEdit() {
    if (!projectId) return;
    const linked = new Set(meetings.map((m) => m.event_id));
    const add = [...draftLinked].filter((id) => !linked.has(id));
    const remove = [...linked].filter((id) => !draftLinked.has(id));
    setEditBusy(true);
    try {
      const res = await api(`/api/projects/${projectId}/meetings`, {
        method: "PUT",
        body: JSON.stringify({ add, remove }),
      });
      setData(res);
      setEditing(false);
    } catch (err) {
      setLoadError(err.message || "Could not save meetings.");
    } finally {
      setEditBusy(false);
    }
  }

  function toggleDraft(id) {
    setDraftLinked((current) => {
      const nextSet = new Set(current);
      if (nextSet.has(id)) nextSet.delete(id);
      else nextSet.add(id);
      return nextSet;
    });
  }

  return (
    <div className="space-y-5">
      {next ? (
        <Bezel coreClassName="p-6">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-faint">Next meeting</p>
              <h2 className="mt-2 text-[22px] font-semibold leading-tight tracking-[-0.02em] text-balance">{next.title}</h2>
              <p className="mt-2 text-[14px] text-muted">{formatEventWhen(next.start)}</p>
              <p className="mt-1 text-xs text-faint">{relativeStart(next.start) || "Scheduled"}</p>
            </div>
            <Pill tone="accent">
              <UsersThree size={12} aria-hidden />
              {(next.attendees || []).length} attendee{(next.attendees || []).length === 1 ? "" : "s"}
            </Pill>
          </div>
          <div className="mt-6 flex flex-wrap gap-2">
            <Button variant="primary" size="sm" onClick={() => go("chat")}>
              <Sparkle size={14} aria-hidden />
              Prep chat
            </Button>
            {prepTargetId ? (
              <>
                <Button size="sm" variant="secondary" onClick={rematch}>
                  Re-match context
                </Button>
                <Button size="sm" variant="accent" onClick={confirmPrep}>
                  Load context into meeting
                </Button>
              </>
            ) : null}
          </div>
          {proposals?.length ? (
            <ul className="mt-5 space-y-2 border-t border-white/[0.06] pt-4">
              {proposals.map((p) => (
                <li key={p.item_id}>
                  <label className="flex cursor-pointer gap-3 rounded-2xl bg-white/[0.03] px-4 py-3 text-sm hairline hover:bg-white/[0.05]">
                    <input type="checkbox" checked={picked.has(p.item_id)} onChange={() => togglePick(p.item_id)} className="mt-0.5 h-4 w-4 accent-[var(--accent)]" />
                    <span>
                      <span className="block font-medium">
                        {p.source_label}: {p.title}
                      </span>
                      <span className="mt-0.5 block text-xs text-muted">{p.reason}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          ) : null}
        </Bezel>
      ) : (
        <Bezel coreClassName="px-6 py-10 text-center">
          <CalendarBlank size={28} className="mx-auto text-faint" aria-hidden />
          <p className="mt-3 text-sm font-medium">No upcoming meetings linked</p>
          <p className="mt-1 text-[13px] text-muted">Edit linked meetings to attach calendar events to this project.</p>
        </Bezel>
      )}

      <Panel
        title="Project meetings"
        hint={meetings.length ? `${meetings.length} linked` : "Link calendar events Escort should prep for"}
        action={
          editing ? (
            <div className="flex gap-2">
              <Button size="sm" variant="ghost" onClick={cancelEdit} disabled={editBusy}>
                Cancel
              </Button>
              <Button size="sm" variant="primary" onClick={saveEdit} disabled={editBusy}>
                {editBusy ? "Saving…" : "Save"}
              </Button>
            </div>
          ) : (
            <Button size="sm" variant="secondary" onClick={startEdit} disabled={!projectId}>
              <PencilSimple size={14} aria-hidden />
              Edit
            </Button>
          )
        }
      >
        {loadError ? (
          <p className="text-sm text-warn" role="alert">
            {loadError}
          </p>
        ) : null}
        {editing ? (
          <ul className="max-h-72 space-y-1 overflow-y-auto scroll-thin" aria-label="All synced calendar events">
            {editBusy && !calendarEvents.length ? (
              <li className="py-6 text-center text-sm text-muted">Loading calendar…</li>
            ) : calendarEvents.length === 0 ? (
              <li className="py-6 text-center text-sm text-muted">No calendar events synced yet.</li>
            ) : (
              calendarEvents.map((event) => (
                <li key={event.event_id}>
                  <label className="flex cursor-pointer gap-3 rounded-xl px-3 py-2.5 hover:bg-white/[0.04]">
                    <input
                      type="checkbox"
                      className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
                      checked={draftLinked.has(event.event_id)}
                      onChange={() => toggleDraft(event.event_id)}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{event.title}</span>
                      <span className="block text-xs text-muted">{formatEventWhen(event.start)}</span>
                    </span>
                    {event.project_id && event.project_id !== projectId ? (
                      <Pill tone="neutral">Other project</Pill>
                    ) : null}
                  </label>
                </li>
              ))
            )}
          </ul>
        ) : meetings.length === 0 ? (
          <p className="text-sm text-muted">No meetings linked yet. Use Edit to pick events from your calendar.</p>
        ) : (
          <div className="space-y-4">
            {upcoming.length ? (
              <MeetingGroup label="Upcoming" items={upcoming} />
            ) : null}
            {past.length ? (
              <MeetingGroup label="Past" items={past} muted />
            ) : null}
          </div>
        )}
      </Panel>

      <button type="button" onClick={onGoChat} className="bezel bezel-sm lift group block w-full text-left">
        <span className="bezel-core flex items-center gap-4 p-5">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-accent-soft text-[#c6bfff]">
            <Sparkle size={19} aria-hidden />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[15px] font-medium">Prep chat</span>
            <span className="block text-[13px] text-muted">
              {chatCount ? `${chatCount} message${chatCount === 1 ? "" : "s"} so far` : "Ask Escort what to watch for"}
            </span>
          </span>
          <ArrowUpRight size={17} className="btn-icon text-faint group-hover:text-ink" aria-hidden />
        </span>
      </button>

      <Panel title="Active citations" hint="Sections Live cards can point to.">
        {sections.length === 0 ? (
          <p className="text-sm text-muted">Confirm prep to merge sections into Live cards.</p>
        ) : (
          <ul className="scroll-thin flex max-h-40 flex-wrap gap-1.5 overflow-auto">
            {sections.map((section) => (
              <li key={section.anchor + section.heading} className="rounded-full bg-accent-soft px-2.5 py-1 text-xs text-[#c6bfff]">
                {section.source_label || `Notes: ${section.heading}`}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function MeetingGroup({ label, items, muted = false }) {
  return (
    <div>
      <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.08em] text-faint">{label}</p>
      <ul className="space-y-1">
        {items.map((meeting) => (
          <li key={meeting.event_id} className={`flex items-center justify-between gap-2 rounded-xl px-3 py-2.5 ${muted ? "opacity-75" : "hover:bg-white/[0.03]"}`}>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{meeting.title}</p>
              <p className="text-xs text-muted">{formatEventWhen(meeting.start)}</p>
            </div>
            {meeting.linked_by === "ai" ? <Pill tone="accent">AI linked</Pill> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
