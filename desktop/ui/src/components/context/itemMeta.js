import { File, FolderSimple, Note, Sparkle, UploadSimple } from "@phosphor-icons/react";
import { GoogleDriveLogo, NotionLogo } from "../BrandIcons.jsx";
import { formatEventWhen } from "../../lib/api.js";

export const FILTER_CHIPS = [
  { id: "all", label: "All" },
  { id: "uploads", label: "Uploads" },
  { id: "notes", label: "Notes" },
  { id: "notion", label: "Notion" },
  { id: "google", label: "Google Drive" },
  { id: "folder", label: "Folder" },
  { id: "artifacts", label: "Artifacts" },
];

const LABEL_KIND = {
  Upload: "upload",
  Notes: "paste",
  Notion: "notion",
  "Google Docs": "google_docs",
  "Google Sheets": "google_sheets",
  "Google Slides": "google_slides",
  "Google Drive": "google_drive",
  "Local File": "local_file",
};

export function resolveSourceKind(item) {
  if (item.source_kind) return item.source_kind;
  return LABEL_KIND[item.source_label] || "upload";
}

export function itemFilterBucket(item) {
  const kind = resolveSourceKind(item);
  if (kind === "upload") return "uploads";
  if (kind === "paste" || kind === "notes") return "notes";
  if (kind === "notion") return "notion";
  if (kind.startsWith("google_")) return "google";
  if (kind === "local_file") return "folder";
  if (kind === "artifact" || kind.endsWith("_artifact") || kind.includes("brief") || kind.includes("summary")) return "artifacts";
  return "uploads";
}

export function itemIcon(item) {
  const kind = resolveSourceKind(item);
  if (kind === "notion") return { type: "brand", Logo: NotionLogo };
  if (kind.startsWith("google_")) return { type: "brand", Logo: GoogleDriveLogo };
  if (kind === "local_file") return FolderSimple;
  if (kind === "paste" || kind === "notes") return Note;
  if (itemFilterBucket(item) === "artifacts") return Sparkle;
  if (kind === "upload") return UploadSimple;
  return File;
}

export function itemStatus(item) {
  const raw = (item.status || item.sync_status || "").toLowerCase();
  if (raw === "failed" || raw === "error") {
    return { tone: "warn", label: "Failed", reason: item.error || item.failure_reason || item.status_reason || "" };
  }
  if (raw === "syncing" || raw === "pending") {
    return { tone: "accent", label: "Syncing", reason: "" };
  }
  return { tone: "good", label: "Imported", reason: "" };
}

export function itemImportedLabel(item) {
  const iso = item.imported_at || item.created_at;
  if (!iso) return null;
  try {
    return formatEventWhen(iso);
  } catch {
    return null;
  }
}

export function matchesFilter(item, filterId) {
  if (filterId === "all") return true;
  return itemFilterBucket(item) === filterId;
}
