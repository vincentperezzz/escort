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
    hide_from_share: true,
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

const providers = [
  { id: "google", label: "Google", connected: false, account: "", mode: "mock", configured: true, setup_hint: "" },
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
    },
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
  if (path === "/api/context/chat") {
    await wait(1100);
    state.context.chat = [...state.context.chat, { role: "user", content: body.message }];
    const reply = REPLIES[state.context.chat.length % REPLIES.length];
    state.context.chat = [...state.context.chat, { role: "assistant", content: reply }];
    return { reply, context: state.context };
  }
  if (path.startsWith("/api/cards/")) {
    if (body.action === "ignore") state.meeting.cards = state.meeting.cards.filter((c) => !path.includes(`/${c.id}/`));
    return status();
  }
  if (path === "/api/connectors/status") return { providers };
  if (path === "/api/context/search") return { hits: [{ id: "h1", title: "BayaniPay phase scope", snippet: "Phase 2 ships after KYC go-live." }] };
  if (path === "/api/context/match") return { proposals: [] };
  return status();
}
