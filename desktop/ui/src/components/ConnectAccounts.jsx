import { useState } from "react";
import {
  ArrowSquareOut,
  CaretDown,
  CheckCircle,
  FileText,
  MagnifyingGlass,
  ShieldCheck,
} from "@phosphor-icons/react";
import { BrandTile, GoogleLogo, NotionLogo } from "./BrandIcons.jsx";
import { Button, Pill, Toggle } from "./ui.jsx";
import { post } from "../lib/api.js";
import { useConnectors } from "../lib/useConnectors.js";

const META = {
  google: {
    name: "Google",
    scope: "Calendar + Docs, Sheets, Slides, Drive picks",
    Mark: GoogleLogo,
    blurb: "Connect your own account. Reads your upcoming calls, the Google links you paste, and the Drive files you pick.",
    // Shown until Google app credentials are saved.
    setupSteps: [
      "In Google Cloud Console, create a project.",
      "Enable the Google Calendar, Docs, Sheets, Slides, Drive and Google Picker APIs.",
      "Configure the OAuth consent screen and add yourself (and anyone testing) as a test user. Scopes: calendar.readonly and documents.readonly; spreadsheets.readonly, presentations.readonly and drive.file are asked for only when first used; documents only for saving notes.",
      "Create an OAuth client of type Desktop app.",
      "For Pick from Drive: create an API key and restrict it to the Google Picker API.",
      "Paste the Client ID, Client secret and API key below, then save. They stay on this computer.",
    ],
    steps: [
      "Press Connect Google. Your browser opens Google's sign-in.",
      "Sign in with your own Google account and allow read-only Calendar and Docs access. Sheets, Slides, Drive file picking and note saving are each asked separately, the first time you use them.",
      "Come back here. Escort detects the sign-in on its own.",
    ],
  },
  notion: {
    name: "Notion",
    scope: "The pages you choose",
    Mark: NotionLogo,
    blurb: "Connect your own workspace. Escort reads, and when you ask, updates only the pages you pick.",
    steps: [
      "Press Connect Notion. Notion opens in your browser.",
      "Choose the pages Escort can see, then allow access.",
      "Come back here. Escort picks up the connection on its own.",
    ],
  },
};

/**
 * Connect accounts: Google and Notion cards with connect, connected and
 * walkthrough states. `compact` hides the intro for use inside other screens.
 */
export default function ConnectAccounts({ onChange, compact = false, providers: providerFilter = null }) {
  const c = useConnectors({ onChange });
  const allowed = providerFilter?.length ? new Set(providerFilter) : null;
  const list = (c.providers || []).filter((p) => META[p.id] && (!allowed || allowed.has(p.id)));
  const skeletonCount = allowed ? allowed.size : 2;
  const gridCols = list.length > 1 || skeletonCount > 1 ? "md:grid-cols-2" : "grid-cols-1";

  return (
    <section aria-labelledby={compact && allowed?.size === 1 ? undefined : "connect-heading"}>
      {compact && allowed?.size === 1 ? null : (
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h3 id="connect-heading" className="text-[17px] font-medium tracking-[-0.015em]">
              Connect accounts
            </h3>
            {compact ? null : (
              <p className="mt-1 text-sm text-muted">Optional. Without them, Escort runs on clearly labelled demo data.</p>
            )}
          </div>
          {c.unavailable ? <Pill tone="warn">Backend not reachable</Pill> : null}
        </div>
      )}
      {c.providers === null ? (
        <div className={`grid gap-3 ${gridCols}`} aria-busy="true">
          {Array.from({ length: skeletonCount }, (_, i) => (
            <div key={i} className="h-44 rounded-[22px] bg-white/[0.025] hairline" />
          ))}
        </div>
      ) : (
        <div className={`grid items-stretch gap-3 ${gridCols}`}>
          {list.map((p) => (
            <ProviderCard key={p.id} provider={p} ctl={c} />
          ))}
        </div>
      )}
    </section>
  );
}

function ProviderCard({ provider, ctl }) {
  const meta = META[provider.id];
  const { Mark } = meta;
  const busy = ctl.busy[provider.id];
  const error = ctl.errors[provider.id];
  const authUrl = ctl.authUrls[provider.id];
  const [token, setToken] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const demo = provider.mode === "mock";
  const isGoogle = provider.id === "google";
  const reconnect = !isGoogle && !provider.connected && Boolean(provider.needs_reconnect);
  const needsCredentials = isGoogle && !provider.configured;
  const steps = needsCredentials ? meta.setupSteps : meta.steps;

  async function submitToken(event) {
    event.preventDefault();
    if (!token.trim()) return;
    if (await ctl.connectNotion(token.trim())) setToken("");
  }

  const live = provider.connected && !demo;

  return (
    <article className="flex h-full flex-col rounded-[22px] bg-white/[0.025] p-5 hairline">
      <header className="flex min-h-10 items-start gap-3">
        <BrandTile>
          <Mark size={22} />
        </BrandTile>
        <div className="min-w-0 flex-1">
          <h4 className="text-[15px] font-medium">{meta.name}</h4>
          <p className="text-xs text-muted">{meta.scope}</p>
        </div>
        {provider.connected ? (
          <Pill tone={demo ? "warn" : "good"}>{demo ? "Demo data" : "Connected"}</Pill>
        ) : reconnect ? (
          <Pill tone="warn">Access ended</Pill>
        ) : (
          <Pill>Not connected</Pill>
        )}
      </header>

      {/* Status row: one line in every state so both cards line up below it. */}
      <p className="mt-4 flex min-h-6 items-center gap-2 text-sm">
        {provider.connected ? (
          <>
            <CheckCircle size={18} weight="regular" className={demo ? "text-warn" : "text-commit"} aria-hidden />
            <span className="min-w-0 truncate font-medium">{provider.account || (isGoogle ? "Account connected" : "Notion workspace")}</span>
          </>
        ) : reconnect ? (
          <span className="text-warn">
            Notion ended Escort's access to {provider.reconnect_account || "your workspace"}. Your page picks are kept.
          </span>
        ) : (
          <span className="text-muted">{meta.blurb}</span>
        )}
      </p>

      <div className={provider.connected ? "" : "flex flex-1 flex-col"}>
      {provider.connected ? (
        <div>
          {demo ? (
            <p className="mt-2 text-xs leading-relaxed text-muted">
              This is fictional demo content (BayaniPay fixtures), not your real {meta.name}.
            </p>
          ) : null}
          {isGoogle && live && provider.needs_docs_consent ? <DocsConsent provider={provider} ctl={ctl} /> : null}
          {!isGoogle && live ? <NotionAccess provider={provider} ctl={ctl} /> : null}
        </div>
      ) : (
        <div>
          <ol className="mt-4 space-y-2.5">
            {steps.map((text, i) => (
              <li key={text} className="flex gap-3 text-sm leading-snug">
                <span className="mono grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white/[0.06] text-[11px] text-muted">
                  {i + 1}
                </span>
                <span className="text-ink/85">{text}</span>
              </li>
            ))}
          </ol>

          {needsCredentials ? <GoogleCredentials provider={provider} ctl={ctl} /> : null}
        </div>
      )}
      </div>

      {/* Capability row: same slot in both cards. Connected, it fills the card so both boxes share top
          and bottom edges; otherwise it sits just above the footer. */}
      <div className={provider.connected ? "flex flex-1 flex-col pt-4 [&>*]:flex-1" : "mt-auto pt-4"}>
        {isGoogle ? <SaveNotes provider={provider} ctl={ctl} live={live} /> : <NotionSaveNotes provider={provider} ctl={ctl} live={live} />}
      </div>

      <footer className="mt-4 flex min-h-8 flex-wrap items-center justify-between gap-3">
        {provider.connected ? (
          <Button variant="dangerGhost" size="sm" disabled={busy === "disconnecting"} onClick={() => ctl.disconnect(provider.id)}>
            {busy === "disconnecting" ? "Disconnecting…" : `Disconnect ${meta.name}`}
          </Button>
        ) : isGoogle ? (
          <>
            <Button variant="primary" disabled={Boolean(busy) || needsCredentials} onClick={ctl.connectGoogle}>
              <GoogleLogo size={16} />
              {busy === "connecting" ? "Opening browser…" : busy === "waiting" ? "Waiting for Google…" : "Connect Google"}
            </Button>
            {busy === "waiting" ? (
              <div className="flex items-center gap-3 text-xs">
                {authUrl ? (
                  <button type="button" className={`inline-flex items-center gap-1 ${LINK}`} onClick={ctl.connectGoogle}>
                    Open sign-in again <ArrowSquareOut size={14} aria-hidden />
                  </button>
                ) : null}
                <button type="button" className="font-medium text-muted hover:text-ink" onClick={() => ctl.cancelWait(provider.id)}>
                  Cancel
                </button>
              </div>
            ) : null}
          </>
        ) : (
          <>
            <Button variant="primary" disabled={busy === "connecting"} onClick={ctl.connectNotionOAuth}>
              <NotionLogo size={16} />
              {busy === "connecting"
                ? "Opening Notion…"
                : busy === "waiting"
                  ? "Open Notion again"
                  : reconnect
                    ? "Reconnect Notion"
                    : "Connect Notion"}
            </Button>
            {busy === "waiting" ? (
              <div className="flex items-center gap-3 text-xs">
                <span className="text-muted">Waiting for Notion…</span>
                <button type="button" className="font-medium text-muted hover:text-ink" onClick={() => ctl.cancelWait(provider.id)}>
                  Cancel
                </button>
              </div>
            ) : null}
          </>
        )}
        {(isGoogle && provider.configured) || (!isGoogle && !provider.connected && busy !== "waiting") ? (
          <button
            type="button"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((v) => !v)}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-muted hover:text-ink"
          >
            <CaretDown size={13} className={`transition-transform ${advancedOpen ? "rotate-180" : ""}`} aria-hidden />
            Advanced
          </button>
        ) : null}
      </footer>
      {isGoogle && provider.configured && advancedOpen ? <GoogleCredentials provider={provider} ctl={ctl} advanced /> : null}
      {!isGoogle && !provider.connected && advancedOpen ? (
        <div className="mt-3 rounded-2xl bg-white/[0.03] p-4 hairline" aria-label="Advanced: Notion integration token">
          <p className="text-xs font-medium">Use an integration token instead</p>
          <p className="mt-1 text-xs leading-relaxed text-muted text-pretty">
            For workspaces that block app connections. Create an internal integration at{" "}
            <a className={LINK} href={INTEGRATIONS_URL} target="_blank" rel="noreferrer">
              notion.so/profile/integrations
            </a>
            , share your pages with it (page menu &gt; Connections), and tick Insert content and Update content to save notes.
          </p>
          <form onSubmit={submitToken} className="mt-3 flex gap-2">
            <input
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="ntn_… or secret_…"
              aria-label="Notion integration token"
              className="field h-9 min-w-0 flex-1 px-3.5 text-[13px]"
            />
            <Button type="submit" size="sm" disabled={!token.trim() || busy === "checking"}>
              {busy === "checking" ? "Checking…" : "Connect with token"}
            </Button>
          </form>
        </div>
      ) : null}

      {busy === "waiting" && !provider.connected ? (
        <div className="mt-3 space-y-2 rounded-2xl bg-white/[0.03] p-3.5 hairline" role="status">
          <p className="text-xs leading-relaxed text-muted">
            {isGoogle
              ? "Finish signing in with Google in your browser. This checks every 2 seconds for up to 2 minutes."
              : "Notion opened in a secure sign-in window. Select your workspace and click Continue. The window will close automatically once approved."}
          </p>
        </div>
      ) : null}
      {error ? (
        <p className="mt-3 text-xs font-medium text-conflict" role="alert">
          {error}
        </p>
      ) : null}
    </article>
  );
}

/** How Notion is connected, and (for the one-click connection) a way to change which pages Escort sees. */
function NotionAccess({ provider, ctl }) {
  const busy = ctl.busy.notion;
  const oauth = provider.auth === "oauth";
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-relaxed text-muted">
      <span>{oauth ? "Escort sees only the pages you chose in Notion." : "Connected with an integration token. Escort sees the pages shared with it."}</span>
      {oauth ? (
        <button type="button" className={`text-xs ${LINK}`} disabled={Boolean(busy)} onClick={ctl.connectNotionOAuth}>
          {busy === "waiting" ? "Waiting for Notion…" : "Change pages"}
        </button>
      ) : null}
    </p>
  );
}

/** Shared shell for the optional write capability, so both cards render it identically. */
function CapabilityRow({ title, body, checked, onChange, disabled, note, children }) {
  return (
    <div className="rounded-2xl bg-white/[0.03] p-4 hairline">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-medium">
            <FileText size={16} className="text-muted" aria-hidden />
            {title}
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-muted text-pretty">{body}</p>
        </div>
        <Toggle checked={checked} onChange={onChange} label={title} disabled={disabled} />
      </div>
      {note ? <p className="mt-3 text-xs text-faint">{note}</p> : null}
      {children}
    </div>
  );
}

const LINK = "font-medium text-[#c6bfff] underline-offset-4 hover:underline";

/** Connected before Docs reading existed (or the Docs box was unticked): reconnect to allow it. */
function DocsConsent({ provider, ctl }) {
  const busy = ctl.busy.google;
  const waiting = busy === "consenting" || provider.docs_consent_pending;
  const authUrl = ctl.authUrls.google;
  return (
    <div className="mt-3 rounded-xl bg-warn-soft px-3.5 py-3" role="status">
      <p className="text-xs leading-relaxed text-warn">
        {waiting
          ? "Waiting for Google. Finish allowing Docs access in your browser."
          : "Google has not allowed reading Docs yet. Reconnect to allow it, so you can import a Google Docs link."}
      </p>
      <div className="mt-2.5 flex flex-wrap items-center gap-3">
        <Button size="sm" variant="accent" onClick={ctl.allowDocsAccess} disabled={busy === "consenting"}>
          <ShieldCheck size={14} aria-hidden />
          {busy === "consenting" ? "Waiting for Google…" : "Allow Docs access"}
        </Button>
        {busy === "consenting" ? (
          <>
            {authUrl ? (
              <a className={`inline-flex items-center gap-1 text-xs ${LINK}`} href={authUrl} target="_blank" rel="noreferrer">
                Open Google again <ArrowSquareOut size={13} aria-hidden />
              </a>
            ) : null}
            <button type="button" className="text-xs font-medium text-muted hover:text-ink" onClick={() => ctl.cancelWait("google")}>
              Cancel
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}
const REVOKE_URL = "https://myaccount.google.com/permissions";

/**
 * Optional write access. Off by default; turning it on asks Google only for drive.file +
 * documents, so Escort can touch nothing but the Docs it creates.
 */
function SaveNotes({ provider, ctl, live }) {
  const on = live && Boolean(provider.save_notes);
  const canWrite = Boolean(provider.capabilities?.write_notes);
  const busy = ctl.busy.google;
  const consenting = busy === "consenting" || provider.consent_pending;
  const needsConsent = on && !canWrite;
  const authUrl = ctl.authUrls.google;

  return (
    <CapabilityRow
      title="Save notes to Drive"
      body="Escort can create and edit only Google Docs it creates or that you pick. It never touches your other files."
      checked={on}
      onChange={ctl.setSaveNotes}
      disabled={!live || busy === "notes"}
      note={live ? null : provider.connected ? "Not available with demo data." : "Available after you connect Google."}
    >
      {on && canWrite ? (
        <p className="mt-3 flex gap-2 text-xs leading-relaxed text-ink/80">
          <CheckCircle size={15} className="mt-px shrink-0 text-commit" aria-hidden />
          <span>
            On. Each meeting gets one Google Doc, “Escort · meeting · date”, in an <span className="font-medium text-ink">Escort</span> folder.
            Escort writes only when you press Note it or Save to Google Docs.
          </span>
        </p>
      ) : null}

      {needsConsent ? (
        <div className="mt-3 rounded-xl bg-warn-soft px-3.5 py-3" role="status">
          <p className="text-xs font-medium text-warn">
            {consenting ? "Waiting for Google. Finish allowing note saving in your browser." : "Google has not allowed note saving yet."}
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-3">
            <Button size="sm" variant="accent" onClick={ctl.allowNoteSaving} disabled={busy === "consenting"}>
              <ShieldCheck size={14} aria-hidden />
              {busy === "consenting" ? "Waiting for Google…" : "Allow note saving"}
            </Button>
            {busy === "consenting" ? (
              <>
                {authUrl ? (
                  <a className={`inline-flex items-center gap-1 text-xs ${LINK}`} href={authUrl} target="_blank" rel="noreferrer">
                    Open Google again <ArrowSquareOut size={13} aria-hidden />
                  </a>
                ) : null}
                <button type="button" className="text-xs font-medium text-muted hover:text-ink" onClick={() => ctl.cancelWait("google")}>
                  Cancel
                </button>
              </>
            ) : null}
          </div>
        </div>
      ) : null}

      {live && !on && canWrite ? (
        <p className="mt-3 text-xs leading-relaxed text-muted">
          Off. Escort no longer writes to Drive. To remove its access entirely,{" "}
          <a className={LINK} href={REVOKE_URL} target="_blank" rel="noreferrer">
            Revoke Google access
          </a>
          .
        </p>
      ) : null}
    </CapabilityRow>
  );
}

const INTEGRATIONS_URL = "https://www.notion.so/profile/integrations";

/**
 * Optional Notion writes with the same integration token. Escort creates one child page per
 * meeting under a parent the user picks here, and writes nowhere else.
 */
function NotionSaveNotes({ provider, ctl, live }) {
  const on = live && Boolean(provider.save_notes);
  const parent = provider.parent;
  const busy = ctl.busy.notion === "notes";
  const oauth = provider.auth === "oauth";
  const [picking, setPicking] = useState(false);
  const showPicker = on && (!parent || picking);

  async function choose(hit) {
    if (await ctl.setNotionNotes({ enabled: true, parentId: hit.id, parentTitle: hit.title })) setPicking(false);
  }

  return (
    <CapabilityRow
      title="Save notes to Notion"
      body="Escort creates one page per meeting under a parent page you pick, and can update existing pages you choose below. It never writes anywhere else."
      checked={on}
      onChange={(enabled) => ctl.setNotionNotes({ enabled })}
      disabled={!live || busy}
      note={live ? null : provider.connected ? "Not available with demo data." : "Available after you connect Notion."}
    >
      {on && parent && !picking ? (
        <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-relaxed text-ink/80">
          <CheckCircle size={15} className="shrink-0 text-commit" aria-hidden />
          <span>
            Saving under <span className="font-medium text-ink">{parent.title}</span>.
          </span>
          <button type="button" className={`text-xs ${LINK}`} onClick={() => setPicking(true)}>
            Change
          </button>
        </p>
      ) : null}
      {showPicker ? <ParentPicker oauth={oauth} onPick={choose} onCancel={parent ? () => setPicking(false) : null} busy={busy} /> : null}
      {live ? <KeepUpdated provider={provider} ctl={ctl} /> : null}
      {on && provider.needs_capabilities ? (
        <div className="mt-3 rounded-xl bg-warn-soft px-3.5 py-3" role="status">
          <p className="text-xs leading-relaxed text-warn">
            {oauth
              ? "Notion blocked the last save. Click Change pages and include this page in what Escort can see, then try again."
              : "Notion blocked the last save. Give your integration the Insert content and Update content capabilities, then try again."}
          </p>
          {oauth ? null : (
            <a className={`mt-2 inline-flex items-center gap-1 text-xs ${LINK}`} href={INTEGRATIONS_URL} target="_blank" rel="noreferrer">
              Open Notion integrations <ArrowSquareOut size={13} aria-hidden />
            </a>
          )}
        </div>
      ) : null}
    </CapabilityRow>
  );
}

/** Existing pages the user picked to keep up to date. Only these (and Escort's own pages) can be edited. */
function KeepUpdated({ provider, ctl }) {
  const targets = provider.targets || [];
  const [adding, setAdding] = useState(false);

  async function add(hit) {
    if (await ctl.setNotionTarget({ add: { id: hit.id, title: hit.title } })) setAdding(false);
  }

  return (
    <div className="mt-3 border-t border-white/[0.06] pt-3">
      <p className="text-xs font-medium">Pages to keep updated</p>
      <p className="mt-1 text-xs leading-relaxed text-muted">
        Escort can add to or edit these existing pages when you ask. You review each change before it is written.
      </p>
      {targets.length ? (
        <ul className="mt-2 space-y-1">
          {targets.map((t) => (
            <li key={t.id} className="flex items-center justify-between gap-3 text-[13px]">
              <span className="min-w-0 truncate">{t.title}</span>
              <button type="button" className="shrink-0 text-xs font-medium text-muted hover:text-conflict" onClick={() => ctl.setNotionTarget({ remove: t.id })}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {adding ? (
        <ParentPicker
          title="Choose a page to keep updated"
          actionLabel="Keep this page updated"
          oauth={provider.auth === "oauth"}
          onPick={add}
          onCancel={() => setAdding(false)}
          busy={false}
        />
      ) : (
        <button type="button" className={`mt-2 text-xs ${LINK}`} onClick={() => setAdding(true)}>
          Choose a page to keep updated
        </button>
      )}
    </div>
  );
}

/** Pick a page from the same Notion search the Context screen uses. */
function ParentPicker({ onPick, onCancel, busy, oauth = false, title = "Pick the page notes go under", actionLabel = "Use this page" }) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState(null);
  const [error, setError] = useState("");

  async function search(event) {
    event?.preventDefault();
    setError("");
    try {
      const data = await post("/api/context/search", { source: "notion", query });
      setHits((data?.hits || []).filter((hit) => !hit.mock));
    } catch (err) {
      setError(err.message || "Could not search Notion.");
    }
  }

  return (
    <div className="mt-3">
      <p className="text-xs font-medium">{title}</p>
      <form onSubmit={search} className="mt-1.5 flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={oauth ? "Search the pages Escort can see" : "Search pages shared with Escort"}
          aria-label="Search Notion pages"
          className="field h-9 min-w-0 flex-1 px-3.5 text-[13px]"
        />
        <Button type="submit" size="sm">
          <MagnifyingGlass size={14} aria-hidden /> Search
        </Button>
      </form>
      {hits !== null ? (
        hits.length ? (
          <ul className="mt-2 max-h-40 space-y-1 overflow-auto scroll-thin">
            {hits.map((hit) => (
              <li key={hit.id}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onPick(hit)}
                  className="press flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-[13px] hover:bg-white/[0.05] disabled:opacity-45"
                >
                  <span className="min-w-0 truncate">{hit.title}</span>
                  <span className="shrink-0 text-xs text-[#c6bfff]">{actionLabel}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-xs text-muted">
            {oauth
              ? "No pages found. Click Change pages to let Escort see more of your workspace, then search again."
              : "No pages found. Share the page with your integration (page menu > Connections), then search again."}
          </p>
        )
      ) : null}
      {error ? <p className="mt-2 text-xs text-conflict">{error}</p> : null}
      {onCancel ? (
        <button type="button" className="mt-2 text-xs font-medium text-muted hover:text-ink" onClick={onCancel}>
          Cancel
        </button>
      ) : null}
    </div>
  );
}

const SOURCE_NOTE = {
  env: "Using credentials from the ESCORT_GOOGLE_CLIENT_ID environment variable.",
  user_file: "Using the google_client.json in your Escort folder (%APPDATA%\\Escort).",
  bundled: "Using the credentials supplied with this installer.",
  setup: "Using the credentials you saved here.",
};

/**
 * Google app credentials: an OAuth client of type Desktop app that you create in your
 * own Google Cloud project. The secret is write-only: the backend never returns it.
 */
function GoogleCredentials({ provider, ctl, advanced = false }) {
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [justSaved, setJustSaved] = useState(false);
  const saving = ctl.busy.google === "saving";
  const secretSaved = Boolean(provider.has_client_secret);
  const keySaved = Boolean(provider.has_api_key);
  const source = provider.credentials_source;
  // A key saved here only applies to credentials saved here (or the env var); files carry their own api_key.
  const keyEditable = !source || source === "setup" || source === "env";
  const canSave = Boolean(clientId.trim() || clientSecret.trim() || apiKey.trim()) && !saving;

  async function submit(event) {
    event.preventDefault();
    if (!canSave) return;
    if (await ctl.saveGoogleCredentials({ clientId, clientSecret, apiKey })) {
      setClientId("");
      setClientSecret("");
      setApiKey("");
      setJustSaved(true);
    }
  }

  const form = (
    <form onSubmit={submit} className="space-y-3" aria-label="Google app credentials">
      <p className="text-xs leading-relaxed text-muted">
        Create these in your own project at{" "}
        <a className="font-medium text-[#c6bfff] underline-offset-4 hover:underline" href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">
          console.cloud.google.com <ArrowSquareOut size={12} className="inline align-[-1px]" aria-hidden />
        </a>
        . Redirect note: Desktop app clients need no redirect URI.
      </p>
      <label className="block">
        <span className="text-xs font-medium">Client ID</span>
        <input
          type="text"
          autoComplete="off"
          spellCheck={false}
          value={clientId}
          onChange={(e) => {
            setClientId(e.target.value);
            setJustSaved(false);
          }}
          placeholder={provider.configured ? "Saved. Paste a new ID to replace it" : "….apps.googleusercontent.com"}
          className="field mt-1.5 h-11 w-full px-4 text-sm"
        />
      </label>
      <label className="block">
        <span className="flex items-center justify-between text-xs font-medium">
          Client secret
          {secretSaved ? (
            <span className="inline-flex items-center gap-1 font-normal text-commit">
              <CheckCircle size={13} aria-hidden /> Secret saved
            </span>
          ) : null}
        </span>
        <input
          type="password"
          autoComplete="new-password"
          value={clientSecret}
          onChange={(e) => {
            setClientSecret(e.target.value);
            setJustSaved(false);
          }}
          placeholder={secretSaved ? "Paste a new secret to replace it" : "GOCSPX-…"}
          className="field mt-1.5 h-11 w-full px-4 text-sm"
        />
      </label>
      {keyEditable ? (
        <label className="block">
          <span className="flex items-center justify-between text-xs font-medium">
            API key for the Drive file chooser
            {keySaved ? (
              <span className="inline-flex items-center gap-1 font-normal text-commit">
                <CheckCircle size={13} aria-hidden /> Key saved
              </span>
            ) : (
              <span className="font-normal text-faint">Optional</span>
            )}
          </span>
          <input
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => {
              setApiKey(e.target.value);
              setJustSaved(false);
            }}
            placeholder={keySaved ? "Paste a new key to replace it" : "AIza…"}
            className="field mt-1.5 h-11 w-full px-4 text-sm"
          />
          <span className="mt-1.5 block text-xs leading-relaxed text-muted">
            Needed only for Pick from Drive. Create it in the same project and restrict it to the Google Picker API.
          </span>
        </label>
      ) : (
        <p className="text-xs leading-relaxed text-muted">
          {keySaved ? "Drive file chooser: API key found in the same credentials file." : "Drive file chooser: add an \"api_key\" entry to the same google_client.json to enable Pick from Drive."}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant={advanced ? "ghost" : "primary"} size={advanced ? "sm" : undefined} disabled={!canSave}>
          {saving ? "Saving…" : "Save credentials"}
        </Button>
        {justSaved ? (
          <span className="text-xs text-commit" role="status">
            Saved. You can connect now.
          </span>
        ) : null}
      </div>
    </form>
  );

  if (!advanced) return <div className="mt-4 rounded-2xl bg-white/[0.03] p-4 hairline">{form}</div>;
  return (
    <div className="mt-3 rounded-2xl bg-white/[0.03] p-4 hairline" aria-label="Advanced: Google app credentials">
      <p className="mb-2 text-xs font-medium">Google app credentials</p>
      {SOURCE_NOTE[source] ? <p className="mb-3 text-xs text-muted">{SOURCE_NOTE[source]}</p> : null}
      {form}
    </div>
  );
}
