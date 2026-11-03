import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowSquareOut, CheckCircle, FileText, GoogleDriveLogo, LinkSimple, PresentationChart, ShieldCheck, Table } from "@phosphor-icons/react";
import ConnectAccounts from "./ConnectAccounts.jsx";
import { Button } from "./ui.jsx";
import ContextDialog from "./context/ContextDialog.jsx";
import { api, post } from "../lib/api.js";
import { pickDriveFile } from "../lib/picker.js";

const KINDS = {
  docs: { label: "Google Docs", short: "Docs", Icon: FileText, source: "google_docs" },
  sheets: { label: "Google Sheets", short: "Sheets", Icon: Table, source: "google_sheets" },
  slides: { label: "Google Slides", short: "Slides", Icon: PresentationChart, source: "google_slides" },
};
const LINK_KINDS = { document: "docs", spreadsheets: "sheets", presentation: "slides" };
const CONSENT = {
  docs: ["read_docs", "Allow Docs access"],
  sheets: ["read_sheets", "Allow Sheets access"],
  slides: ["read_slides", "Allow Slides access"],
  drive_files: ["pick_drive_files", "Allow Drive file picking"],
};
const POLL_MS = 2000;
const POLL_LIMIT_MS = 120000;
const LINK = "font-medium text-[#c6bfff] underline-offset-4 hover:underline";

export function detectLink(text) {
  const raw = (text || "").trim();
  if (!raw) return { state: "empty" };
  if (/\/(document|spreadsheets|presentation)\/d\/e\//.test(raw)) return { state: "published" };
  const match = raw.match(/docs\.google\.com\/(document|spreadsheets|presentation)\/(?:u\/\d+\/)?d\/([\w-]+)/);
  if (match) return { state: "link", kind: LINK_KINDS[match[1]] };
  if (/^[\w-]{10,}$/.test(raw)) return { state: "id" };
  if (/drive\.google\.com|docs\.google\.com\/(forms|drawings)\//.test(raw)) return { state: "other" };
  return { state: "unknown" };
}

export default function GoogleImportDialog({ onClose, refresh, onConnectAccounts }) {
  const [link, setLink] = useState("");
  const [idKind, setIdKind] = useState("docs");
  const [linkState, setLinkState] = useState(null);
  const [pick, setPick] = useState(null);
  const [google, setGoogle] = useState(null);
  const [waiting, setWaiting] = useState(null);
  const [queue, setQueue] = useState([]);
  const retryRef = useRef(null);

  const loadGoogle = useCallback(async () => {
    try {
      const data = await api("/api/connectors/status");
      const row = (data?.providers || []).find((p) => p.id === "google") || null;
      setGoogle(row);
      return row;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    loadGoogle();
  }, [loadGoogle]);

  useEffect(() => {
    if (!waiting) return undefined;
    const timer = setInterval(async () => {
      const row = await loadGoogle();
      const [flag] = CONSENT[waiting.capability];
      if (row?.capabilities?.[flag]) {
        setWaiting(null);
        refresh?.();
        retryRef.current?.();
      } else if (Date.now() - waiting.started > POLL_LIMIT_MS) {
        setWaiting(null);
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, loadGoogle, refresh]);

  async function allow(capability, retry) {
    retryRef.current = retry;
    try {
      await post("/api/connectors/google/consent", { capability });
      setWaiting({ capability, started: Date.now() });
    } catch (err) {
      const fail = { error: err.message || "Could not open Google's consent page.", code: err.code };
      if (capability === "drive_files") setPick(fail);
      else setLinkState(fail);
    }
  }

  const detected = detectLink(link);
  const kind = detected.state === "link" ? detected.kind : detected.state === "id" ? idKind : null;
  const connected = Boolean(google?.connected);

  async function importLink(event) {
    event?.preventDefault();
    const value = link.trim();
    if (!value) return;
    const source = KINDS[kind || "docs"].source;
    setLinkState({ busy: true });
    try {
      await post("/api/context/import", { source, external_id: value });
      setLink("");
      setLinkState({ ok: true, label: KINDS[kind || "docs"].label });
      setQueue((q) => [...q, value]);
      refresh();
    } catch (err) {
      setLinkState({ error: err.message || "Could not import that link.", code: err.code, helpUrl: err.helpUrl, capability: err.capability });
    }
  }

  async function pickFromDrive() {
    setPick({ busy: "opening" });
    try {
      const config = await api("/api/google/picker-config");
      if (!config?.ready) {
        await loadGoogle();
        setPick(config?.reason === "needs_consent" ? null : { error: PICKER_REASONS[config?.reason] || "The Drive file chooser is not available.", code: config?.reason });
        return;
      }
      const file = await pickDriveFile(config);
      if (!file?.id) {
        setPick(null);
        return;
      }
      setPick({ busy: "importing", name: file.name });
      await post("/api/context/import-drive-file", file);
      setPick({ ok: true, name: file.name });
      refresh();
    } catch (err) {
      setPick({ error: err.message || "Could not import that file.", code: err.code, helpUrl: err.helpUrl, capability: err.capability });
    }
  }

  const busy = Boolean(linkState?.busy);
  const linkWaiting = waiting && waiting.capability !== "drive_files" ? waiting.capability : null;

  return (
    <ContextDialog
      title="Import from Google"
      description="Paste a Docs, Sheets, or Slides link, or pick a file from Drive."
      onClose={onClose}
      widthClass="max-w-xl"
      footer={
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      }
    >
      {!connected ? (
        <div className="rounded-2xl bg-white/[0.03] p-4 hairline">
          <p className="mb-4 text-sm text-muted">Connect Google to import Docs, Sheets, Slides, and Drive files.</p>
          <ConnectAccounts
            compact
            onChange={() => {
              loadGoogle().then((row) => {
                if (row?.connected) {
                  refresh?.();
                  onConnectAccounts?.();
                }
              });
            }}
          />
        </div>
      ) : (
        <>
          <form className="field flex items-center gap-2 py-1 pl-4 pr-1" onSubmit={importLink}>
            {kind ? (
              (() => {
                const { Icon } = KINDS[kind];
                return <Icon size={16} className="shrink-0 text-[#c6bfff]" aria-hidden />;
              })()
            ) : (
              <LinkSimple size={16} className="shrink-0 text-faint" aria-hidden />
            )}
            <input
              className="h-9 min-w-0 flex-1 bg-transparent text-sm outline-none focus-visible:outline-none"
              value={link}
              onChange={(e) => {
                setLink(e.target.value);
                if (linkState && !linkState.busy) setLinkState(null);
              }}
              aria-label="Google Docs, Sheets or Slides link"
              aria-describedby="google-link-kind"
              placeholder="https://docs.google.com/…"
              spellCheck={false}
              autoComplete="off"
              autoFocus
            />
            <Button type="submit" size="sm" disabled={busy || !link.trim() || ["other", "published"].includes(detected.state)}>
              {busy ? "Importing…" : "Import"}
            </Button>
          </form>

          <LinkKind detected={detected} idKind={idKind} onIdKind={setIdKind} />

          {linkState?.ok ? (
            <p className="flex items-center gap-1.5 text-xs text-commit" role="status">
              <CheckCircle size={14} aria-hidden /> Added to your library as {linkState.label}.
            </p>
          ) : null}
          {linkState?.error ? (
            <ImportError state={linkState} waiting={linkWaiting === linkState.capability} onAllow={() => allow(linkState.capability, () => importLink())} onConnect={onConnectAccounts} />
          ) : null}

          <div className="mt-5 border-t border-white/[0.06] pt-4">
            <DrivePicker
              google={google}
              pick={pick}
              waiting={waiting?.capability === "drive_files"}
              onPick={pickFromDrive}
              onAllow={() => allow("drive_files", pickFromDrive)}
              onConnect={onConnectAccounts}
            />
          </div>
          {queue.length ? (
            <p className="mt-3 text-xs text-muted" role="status">
              {queue.length} link{queue.length === 1 ? "" : "s"} imported this session.
            </p>
          ) : null}
        </>
      )}
    </ContextDialog>
  );
}

const PICKER_REASONS = {
  demo: "Connect your Google account to pick files from your Drive.",
  not_connected: "Connect your Google account to pick files from your Drive.",
  no_api_key: "The Drive file chooser needs an API key. Add it under Connect accounts, Google, Advanced.",
  no_app_id: "Escort could not read the project number from your Google client ID. Check the Client ID under Connect accounts.",
};

function LinkKind({ detected, idKind, onIdKind }) {
  const base = "flex min-h-7 flex-wrap items-center gap-2 text-xs";
  if (detected.state === "link") {
    const { label } = KINDS[detected.kind];
    return (
      <p id="google-link-kind" className={`${base} text-muted`}>
        Reads this as <span className="font-medium text-ink">{label}</span>
      </p>
    );
  }
  if (detected.state === "id") {
    return (
      <div id="google-link-kind" className={base}>
        <span className="text-muted">No link to detect the type from. Read this id as</span>
        <div role="radiogroup" aria-label="File type" className="inline-flex rounded-full bg-white/[0.04] p-0.5 hairline">
          {Object.entries(KINDS).map(([key, { short }]) => (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={idKind === key}
              onClick={() => onIdKind(key)}
              className={`press h-6 rounded-full px-2.5 text-[11.5px] font-medium transition-colors duration-200 ${
                idKind === key ? "bg-accent-soft text-[#c6bfff]" : "text-muted hover:text-ink"
              }`}
            >
              {short}
            </button>
          ))}
        </div>
      </div>
    );
  }
  const notes = {
    empty: "Docs, Sheets and Slides links work. Sheets import up to 200 rows by 30 columns per tab.",
    other: "Forms, Drawings and drive.google.com links cannot be pasted. Use Pick from Drive below for files.",
    published: "Published-to-the-web links cannot be read. Copy the link from your browser's address bar instead.",
    unknown: "Paste a docs.google.com link.",
  };
  const tone = detected.state === "other" || detected.state === "published" ? "text-warn" : "text-muted";
  return (
    <p id="google-link-kind" className={`${base} ${tone}`}>
      {notes[detected.state]}
    </p>
  );
}

function ImportError({ state, waiting, onAllow, onConnect }) {
  const consent = state.capability && CONSENT[state.capability];
  return (
    <div className={consent ? "rounded-xl bg-warn-soft px-3.5 py-3" : ""} role="alert">
      <p className="text-xs leading-relaxed text-warn">
        {waiting ? "Waiting for Google. Finish allowing access in your browser; the import continues on its own." : state.error}{" "}
        {state.helpUrl ? (
          <a className={`inline-flex items-center gap-1 ${LINK}`} href={state.helpUrl} target="_blank" rel="noreferrer">
            Enable it in Google Cloud <ArrowSquareOut size={12} aria-hidden />
          </a>
        ) : null}
        {state.code === "not_connected" ? (
          <button type="button" className={LINK} onClick={onConnect}>
            Open Connect accounts
          </button>
        ) : null}
      </p>
      {consent ? (
        <Button size="sm" variant="accent" className="mt-2.5" onClick={onAllow} disabled={waiting}>
          <ShieldCheck size={14} aria-hidden />
          {waiting ? "Waiting for Google…" : consent[1]}
        </Button>
      ) : null}
    </div>
  );
}

function DrivePicker({ google, pick, waiting, onPick, onAllow, onConnect }) {
  const live = Boolean(google?.connected) && google?.mode !== "mock";
  const hasKey = Boolean(google?.has_api_key);
  const allowed = Boolean(google?.capabilities?.pick_drive_files);
  const busy = pick?.busy;

  let note;
  let action;
  if (!live) {
    note = "Connect your Google account to pick PDFs, Word, text and Markdown files or Google files from your Drive.";
    action = (
      <Button size="sm" onClick={onConnect}>
        <GoogleDriveLogo size={15} aria-hidden /> Connect Google
      </Button>
    );
  } else if (!hasKey) {
    note = "Needs an API key for the Drive file chooser. Add it under Connect accounts, Google, Advanced.";
    action = (
      <Button size="sm" onClick={onConnect}>
        Add API key
      </Button>
    );
  } else if (!allowed) {
    note = waiting
      ? "Waiting for Google. Finish allowing Drive file picking in your browser; the chooser opens on its own."
      : "Google asks once. Escort can then open only the files you pick, nothing else in your Drive.";
    action = (
      <Button size="sm" variant="accent" onClick={onAllow} disabled={waiting}>
        <ShieldCheck size={14} aria-hidden />
        {waiting ? "Waiting for Google…" : "Allow Drive file picking"}
      </Button>
    );
  } else {
    note = "PDF, Word, text, Markdown, and Google Docs, Sheets and Slides. Up to 15 MB.";
    action = (
      <Button size="sm" variant="primary" onClick={onPick} disabled={Boolean(busy)}>
        <GoogleDriveLogo size={15} aria-hidden />
        {busy === "opening" ? "Opening Drive…" : busy === "importing" ? "Importing…" : "Pick from Drive"}
      </Button>
    );
  }

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="min-w-0 flex-1 basis-56 text-xs leading-relaxed text-muted text-pretty">{note}</p>
        {action}
      </div>
      {busy === "importing" && pick.name ? (
        <p className="text-xs text-muted" role="status">
          Reading {pick.name}…
        </p>
      ) : null}
      {pick?.ok ? (
        <p className="flex items-center gap-1.5 text-xs text-commit" role="status">
          <CheckCircle size={14} aria-hidden /> Added {pick.name} to your library as Google Drive.
        </p>
      ) : null}
      {pick?.error ? <ImportError state={pick} waiting={waiting && pick.capability === "drive_files"} onAllow={onAllow} onConnect={onConnect} /> : null}
    </div>
  );
}
