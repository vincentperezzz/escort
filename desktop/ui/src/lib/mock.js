// Dev-only fixture backend (loaded when the dev server URL has ?mock). Never bundled in builds.
const inMinutes = (m) => new Date(Date.now() + m * 60000).toISOString();
const params = new URLSearchParams(window.location.search);

function SUMMARY(kind) {
  const ready = {
    status: "ready",
    title: "BayaniPay scope review: Phase 2 hinges on KYC go-live",
    bullets: [
      "Maria wants the payment rails live by end of month; your plan ties Phase 2 to KYC sign-off next week.",
      "She floated dropping the transaction fee to 1%. The SOW fixes 1.5% until Q3, so it was parked for the commercial review.",
      "Agreed to restate scope in writing before any dates are committed.",
    ],
    commitments: [
      { who: "You", what: "Send the revised Phase 2 timeline", due: "Friday" },
      { who: "Maria", what: "Confirm KYC vendor sign-off date", due: "Next Tuesday" },
    ],
    conflicts: [{ title: "Fee change is out of scope", section: "SOW: Fees" }],
    generated_at: new Date().toISOString(),
  };
  if (kind === "ready") return ready;
  if (kind === "generating") return { status: "generating", bullets: [], commitments: [], conflicts: [] };
  if (kind === "error") return { status: "error", bullets: [], commitments: [], conflicts: [] };
  return { status: "idle", bullets: [], commitments: [], conflicts: [] };
}

const state = {
  listening: params.get("mock") === "live",
  listening_started_at: new Date(Date.now() - 754000).toISOString(),
  overlay: {
    hide_from_share: params.get("share") !== "off",
    visible: true,
    supported: params.get("mock") !== "unsupported",
    collapsed: params.get("collapsed") === "1",
    opacity: Number(params.get("opacity")) || 0.92,
    input_focus: false,
  },
  summary: SUMMARY(params.get("summary") || "idle"),
  meeting: {
    title: "BayaniPay scope review",
    mode: "live",
    retention: "Turns, cards and frames stay on this machine until you delete them.",
    started_at: new Date(Date.now() - 3600000).toISOString(),
    prd_sections: [
      { anchor: "phase-2", heading: "Phase 2: Payment integration", source_label: "Notes: Phase 2" },
      { anchor: "fees", heading: "Fees", source_label: "SOW: Fees" },
    ],
    utterances: [
      { id: "u1", speaker: "Them", text: "We were hoping to have the payment rails live by end of month." },
      { id: "u2", speaker: "Me", text: "Let me check that against the plan we agreed." },
      { id: "u3", speaker: "Them", text: "And we could drop the transaction fee to one percent, right?" },
      { id: "u4", speaker: "Me", text: "I will send over the revised timeline by Friday." },
    ],
    cards: [
      { id: "c1", type: "SUGGESTED_REPLY", title: "Anchor on the KYC dependency", body: "Phase 2 ships after KYC go-live. You could say: we can lock a date once KYC is signed off next week.", prd_section: "Notes: Phase 2", latency_ms: 812 },
      { id: "c2", type: "COMMITMENT", title: "You: revised timeline by Friday", body: "You promised Maria the revised Phase 2 timeline by Friday.", prd_section: "", latency_ms: 640 },
      { id: "c3", type: "CONFLICT", title: "Fee change is out of scope", body: "They asked to drop the transaction fee to 1%. Your notes say no fee changes on this call.", prd_section: "SOW: Fees", latency_ms: 734 },
    ],
    latency: { count: 3, p50_ms: 734, p95_ms: 806 },
    meeting: {
      open_items: [{ id: "o1", title: "Fee change is out of scope", prd_section: "SOW: Fees" }],
      commitments: [{ id: "k1", who: "You", what: "Send the revised Phase 2 timeline", when: "Friday" }],
    },
  },
  context: {
    items: [
      { id: "i1", source_label: "Notion", title: "BayaniPay phase scope" },
      { id: "i2", source_label: "Google Docs", title: "Statement of work v3" },
      { id: "i3", source_label: "Notes", title: "notes.md" },
    ],
    connectors: [{ id: "notion", mode: "mock" }],
    upcoming: [
      { id: "evt_bayani_scope", title: "BayaniPay scope review", start: inMinutes(24), attendees: ["Maria", "Jun", "Lea"] },
      { id: "evt_kyc", title: "KYC vendor sync with Lumen", start: inMinutes(190) },
      { id: "evt_weekly", title: "Weekly delivery check-in", start: inMinutes(1460) },
    ],
    prep: { selected_event_id: "evt_bayani_scope", confirmed_ids: ["i1", "i2"], proposals: [] },
    chat: params.get("mock") === "empty" ? [] : [
      { role: "user", content: "What should I watch for on this call?" },
      { role: "assistant", content: "Three things. First, they will likely push for the payment rails before KYC is live; your notes make Phase 2 depend on KYC go-live. Second, keep fees off the table: the SOW fixes the 1.5% rate until Q3. Third, Maria tends to bundle new asks into the timeline discussion, so restate scope before you agree to dates." },
    ],
  },
};

// ?monitors=1 for a single display; default is a laptop with an external screen to its left.
const DISPLAYS = params.get("monitors") === "1"
  ? [{ index: 1, left: 0, top: 0, width: 2560, height: 1440 }]
  : [
      { index: 1, left: 0, top: 0, width: 2560, height: 1440 },
      { index: 2, left: -1920, top: 180, width: 1920, height: 1080 },
    ];
const MONITORS = DISPLAYS.map((m) => ({
  ...m,
  name: `Display ${m.index}`,
  label: `Display ${m.index} · ${m.width}×${m.height}`,
  primary: m.index === 1,
}));
state.watch = params.get("watch") === "all" ? "all" : Number(params.get("watch")) || "primary";

function monitorsStatus() {
  const pick = state.watch;
  let effective;
  if (pick === "all" && MONITORS.length > 1) effective = { selected: "all", index: 0, label: "All displays", notice: null };
  else {
    const found = MONITORS.find((m) => m.index === pick) || MONITORS[0];
    effective = { selected: pick, index: found.index, label: found.name, notice: null };
  }
  return { monitors: MONITORS, selected: pick, effective, notice: null, overlay_on_watched: state.overlay.visible ? effective.index !== 2 : null };
}

/** Tiny stand-in for escort/context/settings_intent.py so the chips can be seen in ?mock. */
function mockSettingsReply(message) {
  const text = message.toLowerCase();
  const number = text.match(/(?:monitor|screen|display)\s*(\d)/)?.[1] || (/\bsecond\b/.test(text) ? "2" : null);
  let pick = null;
  if (/\ball (screens|displays|monitors)\b/.test(text)) pick = "all";
  else if (/\bmain\b|\bprimary\b/.test(text)) pick = "primary";
  else if (number) pick = Number(number);
  if (pick !== null) {
    if (typeof pick === "number" && !MONITORS.some((m) => m.index === pick)) {
      return { reply: `I only see ${MONITORS.length} display${MONITORS.length > 1 ? "s" : ""}, so there's no Display ${pick}.`, changed: [] };
    }
    state.watch = pick;
    const label = monitorsStatus().effective.label;
    return { reply: `Done, I'm watching ${pick === "all" ? "all displays" : label} now.`, changed: [{ setting: "watch_monitor", value: pick, label: `Watching ${label}` }] };
  }
  const pct = text.match(/(\d{1,3})\s*%/);
  if (pct && /opacity|overlay/.test(text)) {
    state.overlay.opacity = Math.max(0.3, Math.min(1, Number(pct[1]) / 100));
    const shown = Math.round(state.overlay.opacity * 100);
    return { reply: `Done, the overlay is at ${shown}% opacity now.`, changed: [{ setting: "overlay_opacity", value: state.overlay.opacity, label: `Overlay ${shown}% opacity` }] };
  }
  return null;
}

// ?google=connected (notes off) | notes (on, allowed) | consent (on, Google has not allowed it) | revoke (off, still allowed)
// | docs (connected before Docs reading existed: shows "Allow Docs access") | missing (no Google app credentials)
// ?picker=ready (drive.file granted) | nokey (no API key) | offline (gapi script fails to load); default: needs consent
// ?sheets=1 / ?slides=1: those read scopes were already granted
const g = params.get("google");
const picker = params.get("picker");
const googleLive = ["connected", "notes", "consent", "revoke", "docs"].includes(g);
const providers = [
  {
    id: "google",
    label: "Google",
    connected: googleLive,
    account: googleLive ? "you@example.com" : "",
    mode: googleLive ? "live" : "mock",
    configured: g !== "missing",
    credentials_source: g === "missing" ? null : "setup",
    has_client_secret: g !== "missing",
    has_api_key: g !== "missing" && picker !== "nokey",
    setup_hint: "",
    capabilities: {
      read: true,
      read_docs: g !== "docs",
      read_sheets: params.has("sheets"),
      read_slides: params.has("slides"),
      pick_drive_files: ["ready", "offline"].includes(picker) || g === "notes" || g === "revoke",
      write_notes: g === "notes" || g === "revoke",
    },
    save_notes: g === "notes" || g === "consent",
    needs_consent: g === "consent",
    consent_pending: false,
    needs_docs_consent: g === "docs",
    docs_consent_pending: false,
  },
  notionRow(),
];

// ?notion=off (not connected) | live (connected, notes off) | pick (on, no parent) | notes (on, parent set) | caps (blocked)
function notionRow() {
  const n = params.get("notion");
  const live = ["live", "pick", "notes", "caps"].includes(n);
  const on = ["pick", "notes", "caps"].includes(n);
  const parent = ["notes", "caps"].includes(n) ? { id: "p-meetings", title: "Client meetings" } : null;
  return {
    id: "notion",
    label: "Notion",
    connected: n !== "off",
    account: "BayaniPay workspace",
    mode: live ? "live" : "mock",
    configured: true,
    setup_hint: "",
    capabilities: { read: true, write_notes: Boolean(parent) && n !== "caps" },
    save_notes: on,
    parent,
    targets: parent ? [{ id: "p-crm", title: "CRM notes", picked_at: new Date().toISOString() }] : [],
    needs_parent: on && !parent,
    needs_capabilities: n === "caps",
  };
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const MOCK_DOC = {
  id: "doc_mock",
  url: "https://docs.google.com/document/d/doc_mock/edit",
  title: `Escort · BayaniPay scope review · ${new Date().toISOString().slice(0, 10)}`,
};
// ?saved=1 shows the Recap after a summary was already saved.
const notesDrive = {
  doc: params.get("saved") ? MOCK_DOC : null,
  noted_cards: [],
  summary_saved_at: params.get("saved") ? new Date().toISOString() : null,
};

function mockError(message, status, code, helpUrl = null) {
  const err = new Error(message);
  Object.assign(err, { status, code, helpUrl });
  return err;
}

function notesStatus() {
  const google = providers[0];
  return {
    enabled: google.save_notes,
    connected: google.connected,
    write_notes: google.capabilities.write_notes,
    needs_consent: google.save_notes && !google.capabilities.write_notes,
    meeting_id: "mtg_mock",
    ...notesDrive,
  };
}

const MOCK_PAGE = { id: "page_mock", url: "https://www.notion.so/page_mock", title: MOCK_DOC.title };
const notionDrive = { doc: null, noted_cards: [], summary_saved_at: null };
let lastDest = params.get("dest") || null;

function notionStatus() {
  const n = providers[1];
  return { enabled: n.save_notes, connected: n.connected, write_notes: n.capabilities.write_notes, parent: n.parent, targets: n.targets, meeting_id: "mtg_mock", ...notionDrive };
}

function combinedNotes() {
  const enabled = [providers[0].save_notes && "drive", providers[1].save_notes && "notion"].filter(Boolean);
  return { enabled, last: enabled.includes(lastDest) ? lastDest : enabled[0] || "drive", drive: notesStatus(), notion: notionStatus() };
}

function saveNotion(body) {
  const n = providers[1];
  if (!n.save_notes) throw mockError("Turn on Save notes to Notion to keep this in Notion.", 400, "notes_off");
  if (!n.parent) throw mockError("Pick the Notion page Escort should save meeting pages under.", 400, "no_parent");
  if (n.needs_capabilities) {
    throw mockError("Notion blocked this (restricted_resource). Give your integration the 'Insert content' and 'Update content' capabilities.", 403, "needs_capabilities", "https://www.notion.so/profile/integrations");
  }
  notionDrive.doc = MOCK_PAGE;
  if (body.kind === "commitment") notionDrive.noted_cards = [...notionDrive.noted_cards, body.card_id];
  else notionDrive.summary_saved_at = new Date().toISOString();
  lastDest = "notion";
  return { ok: true, destination: "notion", kind: body.kind, doc: MOCK_PAGE, ...notionDrive };
}

function saveNotes(body) {
  const google = providers[0];
  if (!google.save_notes) throw mockError("Turn on Save notes to Drive to keep this in Google Docs.", 400, "notes_off");
  if (!google.capabilities.write_notes) throw mockError("Escort needs your permission to save notes to Google Docs.", 403, "needs_consent");
  notesDrive.doc = MOCK_DOC;
  lastDest = "drive";
  if (body.kind === "commitment") notesDrive.noted_cards = [...notesDrive.noted_cards, body.card_id];
  else notesDrive.summary_saved_at = new Date().toISOString();
  return { ok: true, kind: body.kind, doc: MOCK_DOC, ...notesDrive };
}

const KIND_META = {
  docs: { label: "Google Docs", noun: "document", api: "docs.googleapis.com", consent: ["needs_docs_consent", "Allow Docs access", "read Google Docs"] },
  sheets: { label: "Google Sheets", noun: "spreadsheet", api: "sheets.googleapis.com", consent: ["needs_sheets_consent", "Allow Sheets access", "read Google Sheets"] },
  slides: { label: "Google Slides", noun: "presentation", api: "slides.googleapis.com", consent: ["needs_slides_consent", "Allow Slides access", "read Google Slides"] },
};

function consentError(capability) {
  const [code, button, verb] = capability === "drive_files"
    ? ["needs_drive_consent", "Allow Drive file picking", "open the Drive files you pick"]
    : KIND_META[capability].consent;
  const err = mockError(`Escort needs your permission to ${verb}. Click ${button}, then try again.`, 403, code);
  err.capability = capability;
  return err;
}

// Mirrors escort/context/sources/google_source.py parse_google_link: Docs, Sheets and Slides links.
function importGoogleLink(text, source) {
  const raw = text.trim();
  const bad = (message, code) => mockError(message, 400, code);
  if (!raw) throw bad("Paste a Google Docs, Sheets or Slides link first.", "bad_link");
  if (/drive\.google\.com|docs\.google\.com\/(forms|drawings)\/|\/d\/e\//.test(raw)) {
    throw bad("Escort reads Google Docs, Sheets and Slides links. For other Drive files (PDF, Word, text), use Pick from Drive.", "unsupported_link");
  }
  const link = raw.match(/docs\.google\.com\/(document|spreadsheets|presentation)\/(?:u\/\d+\/)?d\/([\w-]+)/);
  if (!link && !/^[\w-]{10,}$/.test(raw)) throw bad("That does not look like a Google Docs, Sheets or Slides link. Paste a docs.google.com/... link, or an id with its type selected.", "bad_link");
  const kind = link ? { document: "docs", spreadsheets: "sheets", presentation: "slides" }[link[1]] : (source || "google_docs").replace("google_", "");
  const meta = KIND_META[kind];
  const google = providers[0];
  if (google.connected && !google.capabilities[`read_${kind}`]) throw consentError(kind);
  if (/fail404/.test(raw)) throw mockError(`Google could not find that ${meta.noun}, or it is not shared with the Google account Escort is connected to.`, 404, "not_found");
  if (/fail403/.test(raw)) throw mockError(`That ${meta.noun} is not shared with the Google account Escort is connected to. Ask for access, or connect the account that has it.`, 403, "not_shared");
  if (/failapi/.test(raw)) throw mockError(`Enable the ${meta.label} API in your Google Cloud project, then try again.`, 409, "api_disabled", `https://console.cloud.google.com/apis/library/${meta.api}`);
  state.context.items = [...state.context.items, { id: `i${state.context.items.length + 1}`, source_label: meta.label, title: `Pasted ${meta.label.replace("Google ", "")} (demo)`, mock: true }];
  return status();
}

function pickerConfig() {
  const google = providers[0];
  const base = { mode: google.mode, app_id: "123456789012", api_key: null, access_token: null, scope_ok: google.capabilities.pick_drive_files, ready: false, reason: null };
  if (!google.connected) return { ...base, reason: "not_connected" };
  if (google.mode === "mock") return { ...base, reason: "demo" };
  if (!google.has_api_key) return { ...base, reason: "no_api_key" };
  if (!google.capabilities.pick_drive_files) return { ...base, reason: "needs_consent" };
  return { ...base, api_key: "mock-key", access_token: "mock-token", ready: true, mock_offline: picker === "offline" };
}

function importDriveFile(body) {
  const google = providers[0];
  if (!google.capabilities.pick_drive_files) throw consentError("drive_files");
  if (/\.(png|jpe?g|zip)$/i.test(body.name || "")) throw mockError("Escort reads PDF, Word (.docx), text and Markdown files, and Google Docs, Sheets and Slides. That file is a different type.", 400, "unsupported_file");
  state.context.items = [...state.context.items, { id: `i${state.context.items.length + 1}`, source_label: "Google Drive", title: body.name || "Picked file", mock: true }];
  return status();
}

function status() {
  return {
    listening: state.listening,
    summary: state.summary,
    screen: {
      monitoring: state.listening || params.get("mock") === "live",
      last_frame_at: new Date(Date.now() - 3000).toISOString(),
      frames_seen: 142,
      changed_frames: 11,
      monitor: monitorsStatus().effective.label,
    },
    monitors: monitorsStatus(),
    capture_error: params.get("capture") === "error" ? "Screen capture is blocked by another app. Escort will keep retrying." : null,
    listening_started_at: state.listening ? state.listening_started_at : null,
    has_meta_key: true,
    has_whatsapp_token: false,
    prefs: { mic_id: "mic-1", loopback_id: "", whatsapp_phone_number_id: "", whatsapp_allowed_numbers: "" },
    devices: [
      { id: "mic-1", kind: "mic", name: "Shure MV7" },
      { id: "mic-2", kind: "mic", name: "MacBook microphone" },
      { id: "lb-1", kind: "loopback", name: "Speakers (loopback)" },
    ],
    meeting: state.meeting,
    context: state.context,
    notes_drive: notesStatus(),
    notes: combinedNotes(),
    mcp: "http://127.0.0.1:8747/mcp",
    whatsapp: "http://127.0.0.1:8748/webhook",
  };
}

const REPLIES = [
  "Lead with the dependency: Phase 2 cannot start before KYC go-live. Offer a dated plan once sign-off lands, rather than a date today.",
  "Their likely ask is a fee concession. Your SOW holds 1.5% until Q3, so park it for the commercial review instead of answering live.",
];

export async function handle(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const body = options.body ? JSON.parse(options.body) : {};
  await wait(120);
  if (path === "/api/status") return status();
  if (path === "/api/overlay") {
    if (method === "POST") Object.assign(state.overlay, body);
    return { ...state.overlay, listening: state.listening };
  }
  if (path === "/api/summary/regenerate") {
    state.summary = SUMMARY("generating");
    setTimeout(() => { state.summary = SUMMARY("ready"); }, 2500);
    return status();
  }
  if (path === "/api/listen") {
    const was = state.listening;
    state.listening = Boolean(body.on);
    if (was && !state.listening) {
      state.summary = SUMMARY("generating");
      setTimeout(() => { state.summary = SUMMARY("ready"); }, 3500);
    }
    if (state.listening) state.listening_started_at = new Date().toISOString();
    return status();
  }
  if (path === "/api/monitors") {
    if (method === "POST") {
      const pick = body.selected === "all" || body.selected === "primary" ? body.selected : Number(body.selected);
      if (typeof pick === "number" && !MONITORS.some((m) => m.index === pick)) {
        const err = new Error(`There is no Display ${body.selected}.`);
        err.status = 422;
        throw err;
      }
      state.watch = pick;
    }
    return monitorsStatus();
  }
  if (path === "/api/context/chat") {
    const settings = mockSettingsReply(body.message || "");
    await wait(settings ? 250 : 1100);
    state.context.chat = [...state.context.chat, { role: "user", content: body.message }];
    const reply = settings?.reply || REPLIES[state.context.chat.length % REPLIES.length];
    const changed = settings?.changed || [];
    state.context.chat = [...state.context.chat, { role: "assistant", content: reply, ...(changed.length ? { changed } : {}) }];
    return { reply, changed, context: state.context };
  }
  if (path.startsWith("/api/cards/")) {
    if (body.action === "ignore") state.meeting.cards = state.meeting.cards.filter((c) => !path.includes(`/${c.id}/`));
    return status();
  }
  if (path === "/api/setup") {
    const g = providers[0];
    if (body.google_client_id) {
      g.configured = true;
      g.credentials_source = "setup";
    }
    if (body.google_client_secret) g.has_client_secret = true;
    if (body.google_api_key) g.has_api_key = true;
    return status();
  }
  if (path === "/api/connectors/status") return { providers };
  if (path === "/api/connectors/google/notes") {
    const google = providers[0];
    google.save_notes = Boolean(body.enabled);
    google.needs_consent = google.save_notes && !google.capabilities.write_notes;
    if (google.needs_consent) {
      // Pretend the user allows it in the browser a few seconds later.
      google.consent_pending = true;
      setTimeout(() => Object.assign(google, { consent_pending: false, needs_consent: false, capabilities: { read: true, write_notes: true } }), 4000);
      return { providers, auth_url: "https://accounts.google.com/o/oauth2/v2/auth?mock=1" };
    }
    return { providers, ...(google.save_notes ? {} : { revoke_url: "https://myaccount.google.com/permissions" }) };
  }
  if (path === "/api/connectors/google/consent") {
    const google = providers[0];
    const capability = body.capability || "notes";
    const grant = { notes: { write_notes: true, pick_drive_files: true }, sheets: { read_sheets: true }, slides: { read_slides: true }, drive_files: { pick_drive_files: true }, docs: { read_docs: true } }[capability] || {};
    if (capability === "notes") google.consent_pending = true;
    setTimeout(() => {
      Object.assign(google, { capabilities: { ...google.capabilities, ...grant } });
      if (capability === "notes") Object.assign(google, { consent_pending: false, needs_consent: false });
    }, 4000);
    return { auth_url: "https://accounts.google.com/o/oauth2/v2/auth?mock=1" };
  }
  if (path === "/api/google/picker-config") return pickerConfig();
  if (path === "/api/context/import-drive-file") return importDriveFile(body);
  if (path === "/api/notes/drive") return method === "POST" ? saveNotes(body) : notesStatus();
  if (path === "/api/notes/notion") return method === "POST" ? saveNotion(body) : notionStatus();
  if (path === "/api/connectors/notion/notes") {
    const n = providers[1];
    n.save_notes = Boolean(body.enabled);
    if (body.parent_id) Object.assign(n, { parent: { id: body.parent_id, title: body.parent_title }, needs_capabilities: false });
    n.needs_parent = n.save_notes && !n.parent;
    n.capabilities = { read: true, write_notes: Boolean(n.parent) && !n.needs_capabilities };
    return { providers };
  }
  if (path === "/api/context/import" && /^google_(docs|sheets|slides)$/.test(body.source)) return importGoogleLink(String(body.external_id || ""), body.source);
  if (path === "/api/connectors/google/connect") {
    const google = providers[0];
    setTimeout(() => Object.assign(google, { needs_docs_consent: false, capabilities: { ...google.capabilities, read_docs: true } }), 4000);
    return { auth_url: "https://accounts.google.com/o/oauth2/v2/auth?mock=1" };
  }
  if (path === "/api/connectors/notion/targets") {
    const n = providers[1];
    if (body.remove) n.targets = n.targets.filter((t) => t.id !== body.remove);
    else n.targets = [...n.targets.filter((t) => t.id !== body.add.id), { ...body.add, picked_at: new Date().toISOString() }];
    return { providers };
  }
  if (path === "/api/notes/notion/update/preview") {
    const target = providers[1].targets.find((t) => t.id === body.page_id);
    if (!target) throw mockError("Escort only writes to Notion pages it created or that you picked under Choose a page to keep updated.", 403, "unknown_doc");
    return {
      plan_id: "upd_mock",
      kind: body.kind,
      card_id: body.card_id || null,
      page: { id: target.id, title: target.title },
      summary: body.kind === "summary" ? `Add a dated recap to the end of ${target.title}:` : `Add to ${target.title} under Commitments:`,
      lines: body.kind === "summary" ? ["Recap · today", "Summary", "- Phase 2 waits for KYC.", "Commitments", "- Pia: send SOW by Friday"] : ["- Pia: send SOW by Friday (Friday)"],
    };
  }
  if (path === "/api/notes/notion/update/confirm") return { ok: true, destination: "notion", updated: true, noted_cards: [], page: { id: "p-crm", title: "CRM notes" } };
  if (path === "/api/notes/notion/update/cancel") return { ok: true };
  if (path === "/api/context/search") {
    return {
      hits: [
        { id: "h1", title: "BayaniPay phase scope", snippet: "Phase 2 ships after KYC go-live." },
        { id: "h2", title: "Client meetings", snippet: "Notion page" },
      ],
    };
  }
  if (path === "/api/context/match") return { proposals: [] };
  return status();
}
