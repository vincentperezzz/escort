import { useEffect, useState } from "react";
import { post } from "./api.js";

export const DEST_LABEL = { drive: "Google Docs", notion: "Notion" };

// "Update an existing page": destinations of the form page:<notion page id>, one per page the user picked.
export const isPageDest = (dest) => typeof dest === "string" && dest.startsWith("page:");
export const pageIdOf = (dest) => dest.slice(5);

/**
 * Where "Note it" and "Save summary" write: Google Docs or Notion, whichever is turned on.
 * The backend remembers the last destination used; a local pick overrides it until the next save.
 */
export function useNoteDestination(status) {
  const notes = status?.notes || {};
  const saveTo = notes.enabled?.length ? notes.enabled : status?.notes_drive?.enabled ? ["drive"] : [];
  const targets = notes.notion?.targets || [];
  const labels = { ...DEST_LABEL };
  const pages = notionConnected(notes) ? targets.map((t) => `page:${t.id}`) : [];
  for (const t of targets) labels[`page:${t.id}`] = `Update: ${t.title}`;
  const enabled = [...saveTo, ...pages];
  const [picked, setPicked] = useState(null);
  const fallback = saveTo.includes(notes.last) ? notes.last : saveTo[0] || "drive";
  const dest = enabled.includes(picked) ? picked : fallback;
  useEffect(() => {
    if (picked && !enabled.includes(picked)) setPicked(null);
  }, [picked, enabled.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps
  const current = (isPageDest(dest) ? {} : dest === "notion" ? notes.notion : notes.drive || status?.notes_drive) || {};
  return { enabled, labels, dest, setDest: setPicked, current, label: labels[dest] };
}

function notionConnected(notes) {
  return Boolean(notes.notion?.connected);
}

/** Existing page: read it and describe the change. Nothing is written until confirmNotePage. */
export function previewNotePage(dest, payload) {
  return post("/api/notes/notion/update/preview", { page_id: pageIdOf(dest), ...payload });
}
export const confirmNotePage = (planId) => post("/api/notes/notion/update/confirm", { plan_id: planId });
export const cancelNotePage = (planId) => post("/api/notes/notion/update/cancel", { plan_id: planId });

export function saveNote(dest, payload) {
  return post(dest === "notion" ? "/api/notes/notion" : "/api/notes/drive", payload);
}

/** Plain-language follow-up for a failed save: what happened and where to fix it. */
export function noteHint(err, dest) {
  const label = DEST_LABEL[dest] || "Google Docs";
  if (err.code === "notes_off") {
    const toggle = dest === "notion" ? "Save notes to Notion" : "Save notes to Drive";
    return { text: `Noted in this meeting's recap. Turn on ${toggle} to keep this in ${label}.`, fix: true, quiet: true };
  }
  if (err.code === "needs_consent") return { text: "Google has not allowed note saving yet. Allow it in Connections.", fix: true };
  if (err.code === "not_connected") return { text: `Connect ${dest === "notion" ? "Notion" : "Google"} first to keep notes in ${label}.`, fix: true };
  if (err.code === "no_parent") return { text: "Pick the Notion page Escort should save meeting pages under.", fix: true };
  if (err.code === "needs_capabilities") return { text: err.message, href: err.helpUrl, hrefLabel: "Open Notion integrations" };
  if (err.code === "unknown_doc") return { text: "Escort only updates pages you picked under Choose a page to keep updated." };
  if (err.code === "plan_expired") return { text: "That update timed out. Press it again to review the change." };
  if (err.code === "block_not_found") return { text: err.message };
  if (err.code === "api_disabled") return { text: err.message, href: err.helpUrl, hrefLabel: "Open Google Cloud" };
  return { text: err.message || `Could not save to ${label}. Try again.` };
}
