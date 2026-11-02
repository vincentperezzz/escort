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
      { id: "i2", source_label: "Drive", title: "Statement of work v3" },
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

const providers = [
  { id: "google", label: "Google", connected: false, account: "", mode: "mock", configured: params.get("google") !== "missing", credentials_source: params.get("google") === "missing" ? null : "setup", has_client_secret: params.get("google") !== "missing", setup_hint: "" },
  { id: "notion", label: "Notion", connected: true, account: "BayaniPay workspace", mode: "mock", configured: true, setup_hint: "" },
];

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

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
    return status();
  }
  if (path === "/api/connectors/status") return { providers };
  if (path === "/api/context/search") return { hits: [{ id: "h1", title: "BayaniPay phase scope", snippet: "Phase 2 ships after KYC go-live." }] };
  if (path === "/api/context/match") return { proposals: [] };
  return status();
}
