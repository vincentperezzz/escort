import { useEffect, useState } from "react";
import { SlidersHorizontal, Plugs, Keyboard, Microphone, ShieldCheck, CheckCircle } from "@phosphor-icons/react";
import { Bezel, Button, Field, Kbd, Pill, Select, Toggle } from "../components/ui.jsx";
import ConnectAccounts from "../components/ConnectAccounts.jsx";
import { post } from "../lib/api.js";
import OpacitySlider from "../components/OpacitySlider.jsx";
import MonitorPicker from "../components/MonitorPicker.jsx";
import { useOverlay } from "../lib/useOverlay.js";

const SECTIONS = [
  { id: "general", label: "General", Icon: SlidersHorizontal },
  { id: "connections", label: "Connections", Icon: Plugs },
  { id: "keybinds", label: "Keybinds", Icon: Keyboard },
  { id: "audio", label: "Audio", Icon: Microphone },
  { id: "privacy", label: "Privacy", Icon: ShieldCheck },
];

const SECRETS = { meta_api_key: "", whatsapp_access_token: "", whatsapp_verify_token: "" };

/** The /api/setup form, shared by Connections (keys) and Audio (devices). */
function useSetupForm(status, refresh) {
  const prefs = status?.prefs || {};
  const [form, setForm] = useState({
    ...SECRETS,
    whatsapp_phone_number_id: prefs.whatsapp_phone_number_id || "",
    whatsapp_allowed_numbers: prefs.whatsapp_allowed_numbers || "",
    mic_id: prefs.mic_id || "",
    loopback_id: prefs.loopback_id || "",
  });
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setForm((current) => ({
      ...current,
      whatsapp_phone_number_id: prefs.whatsapp_phone_number_id || "",
      whatsapp_allowed_numbers: prefs.whatsapp_allowed_numbers || "",
      mic_id: prefs.mic_id || "",
      loopback_id: prefs.loopback_id || "",
    }));
  }, [prefs.mic_id, prefs.loopback_id, prefs.whatsapp_allowed_numbers, prefs.whatsapp_phone_number_id]);

  const set = (key) => (value) => {
    setSaved(false);
    setForm((cur) => ({ ...cur, [key]: value }));
  };

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      await post("/api/setup", form);
      setForm((current) => ({ ...current, ...SECRETS }));
      setSaved(true);
      refresh();
    } catch (err) {
      setError(`Could not save (${err.message || "no response"}). Nothing was changed.`);
    }
  }

  return { form, set, submit, saved, error };
}

export default function Settings({ status, refresh, section, go }) {
  const active = SECTIONS.some((s) => s.id === section) ? section : "general";
  const setup = useSetupForm(status, refresh);

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="mb-10 text-[34px] font-semibold leading-none tracking-[-0.035em]">Settings</h1>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-8 md:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="md:sticky md:top-0 md:self-start">
          <ul className="flex gap-1 overflow-x-auto pb-1 scroll-thin md:flex-col md:overflow-visible">
            {SECTIONS.map(({ id, label, Icon }) => {
              const on = id === active;
              return (
                <li key={id}>
                  <button
                    type="button"
                    onClick={() => go("settings", id)}
                    aria-current={on ? "page" : undefined}
                    className={`press flex h-10 w-full items-center gap-3 whitespace-nowrap rounded-full px-4 text-left text-sm ${
                      on ? "bg-white/[0.08] text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.07)]" : "text-muted hover:bg-white/[0.04] hover:text-ink"
                    }`}
                  >
                    <Icon size={17} weight={on ? "regular" : "light"} aria-hidden />
                    {label}
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        <div key={active} className="rise min-w-0 space-y-5">
          {active === "general" ? <General status={status} refresh={refresh} /> : null}
          {active === "connections" ? <Connections status={status} refresh={refresh} setup={setup} /> : null}
          {active === "keybinds" ? <Keybinds /> : null}
          {active === "audio" ? <Audio status={status} setup={setup} /> : null}
          {active === "privacy" ? <Privacy status={status} refresh={refresh} /> : null}
        </div>
      </div>
    </div>
  );
}

function Group({ title, hint, children }) {
  return (
    <Bezel size="sm" coreClassName="p-2">
      {title ? (
        <header className="px-5 pb-1 pt-4">
          <h2 className="text-[15px] font-medium tracking-[-0.01em]">{title}</h2>
          {hint ? <p className="mt-1 text-[13px] leading-relaxed text-muted">{hint}</p> : null}
        </header>
      ) : null}
      <div className="flex flex-col">{children}</div>
    </Bezel>
  );
}

function Row({ title, body, children, note }) {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-[16px] px-5 py-4">
      <div className="min-w-0 flex-1 basis-64">
        <p className="text-sm font-medium">{title}</p>
        {body ? <p className="mt-1 text-[13px] leading-relaxed text-muted text-pretty">{body}</p> : null}
        {note ? <div className="mt-2">{note}</div> : null}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function General({ status, refresh }) {
  const overlay = useOverlay();
  const latency = status?.meeting?.latency;
  const unavailable = overlay.available === false;
  const [demoBusy, setDemoBusy] = useState(false);

  async function demo() {
    setDemoBusy(true);
    try {
      await post("/api/demo", {});
      window.location.hash = "/live";
      refresh();
    } finally {
      setDemoBusy(false);
    }
  }

  return (
    <>
      <Group title="Screen to watch" hint="Escort reads one display about once a second. You can also ask in chat, like “watch monitor 2”.">
        <MonitorPicker status={status} refresh={refresh} hideFromShare={overlay.available === false ? undefined : overlay.hide_from_share} />
      </Group>

      <Group title="Window">
        <Row
          title="Hide from screen share"
          body="Escort's windows stay visible to you but are left out of screen shares, recordings and screenshots."
          note={
            <>
              {overlay.supported === false ? <p className="text-[12.5px] text-warn">Capture hiding needs Windows 10 version 2004 or later.</p> : null}
              {unavailable ? <p className="text-[12.5px] text-faint">Not available in this build yet. It turns on by default once supported.</p> : null}
              {overlay.error ? <p className="text-[12.5px] text-conflict">{overlay.error}</p> : null}
            </>
          }
        >
          <Toggle
            label="Hide from screen share"
            checked={Boolean(overlay.hide_from_share)}
            disabled={unavailable || overlay.supported === false}
            onChange={(v) => overlay.update({ hide_from_share: v })}
          />
        </Row>
        <Row
          title="Overlay transparency"
          body="Make the in-meeting overlay more see-through. Text stays readable down to 30%."
          note={unavailable ? <p className="text-[12.5px] text-faint">Not available in this build yet.</p> : null}
        >
          <OpacitySlider
            className="w-56"
            value={Number.isFinite(overlay.opacity) ? overlay.opacity : 0.92}
            disabled={unavailable}
            onChange={(v) => overlay.update({ opacity: v })}
          />
        </Row>
        <Row title="Open at login" body="Start Escort quietly in the tray when you sign in to Windows." note={<Pill>Coming soon</Pill>}>
          <Toggle label="Open at login" checked={false} disabled onChange={() => {}} />
        </Row>
      </Group>

      <Group title="Demo" hint="Replays a fictional BayaniPay client call so you can see every card type.">
        <div className="px-5 pb-4 pt-3">
          <Button onClick={demo} disabled={demoBusy}>
            {demoBusy ? "Loading demo…" : "Load staged demo"}
          </Button>
        </div>
      </Group>

      <Group title="Services" hint="Local endpoints Escort exposes while it runs.">
        <dl className="grid grid-cols-[auto_1fr] gap-x-8 gap-y-2.5 px-5 pb-5 pt-3 text-sm">
          <dt className="text-muted">MCP</dt>
          <dd className="mono truncate text-[13px]">{status?.mcp ?? "unknown"}</dd>
          <dt className="text-muted">WhatsApp</dt>
          <dd className="mono truncate text-[13px]">{status?.whatsapp ?? "unknown"}</dd>
          {latency?.count ? (
            <>
              <dt className="text-muted">Card latency</dt>
              <dd className="mono text-[13px]">
                p50 {Math.round(latency.p50_ms)}ms, p95 {Math.round(latency.p95_ms)}ms
              </dd>
            </>
          ) : null}
        </dl>
      </Group>
    </>
  );
}

function SaveBar({ setup, label = "Save" }) {
  return (
    <div className="flex items-center gap-3 px-5 pb-4 pt-2">
      <Button type="submit" variant="primary">
        {label}
      </Button>
      {setup.saved ? (
        <span className="text-sm text-commit" role="status">
          Saved
        </span>
      ) : null}
      {setup.error ? (
        <span className="text-sm text-conflict" role="alert">
          {setup.error}
        </span>
      ) : null}
    </div>
  );
}

function Connections({ status, refresh, setup }) {
  const { form, set } = setup;
  const keySaved = Boolean(status?.has_meta_key);
  return (
    <>
      <Bezel size="sm" coreClassName="p-6">
        <ConnectAccounts onChange={refresh} />
      </Bezel>
      <form onSubmit={setup.submit} className="space-y-5">
        <Group title="Escort AI" hint="The model that writes your cards and powers chat. Your key stays on this machine, stored securely in your OS keychain and never hardcoded.">
          {keySaved ? (
            <p className="flex items-center gap-2 px-5 pt-3 text-sm text-commit" role="status">
              <CheckCircle size={17} weight="regular" aria-hidden />
              <span>API key saved on this machine</span>
            </p>
          ) : null}
          <div className="px-5 pb-2 pt-3">
            <Field
              label="Meta API key"
              type="password"
              autoComplete="off"
              value={form.meta_api_key}
              onChange={set("meta_api_key")}
              placeholder={keySaved ? "Saved in keychain · paste a new key to replace it" : "LLM_..."}
            />
          </div>
          <SaveBar setup={setup} label={keySaved ? "Update key" : "Save key"} />
        </Group>
        <Group title="WhatsApp demo" hint="Hackathon allowlist only, not a production rollout.">
          <div className="grid gap-4 px-5 pb-2 pt-3 sm:grid-cols-2">
            <Field label="Access token" type="password" autoComplete="off" value={form.whatsapp_access_token} onChange={set("whatsapp_access_token")} placeholder={status?.has_whatsapp_token ? "Saved" : "Test token"} />
            <Field label="Verify token" type="password" autoComplete="off" value={form.whatsapp_verify_token} onChange={set("whatsapp_verify_token")} placeholder="You choose this" />
            <Field label="Phone number ID" value={form.whatsapp_phone_number_id} onChange={set("whatsapp_phone_number_id")} />
            <Field label="Allowlist" value={form.whatsapp_allowed_numbers} onChange={set("whatsapp_allowed_numbers")} placeholder="1555…" />
          </div>
          <SaveBar setup={setup} label="Save WhatsApp" />
        </Group>
      </form>
    </>
  );
}

function Keybinds() {
  const binds = [
    { keys: ["Ctrl", "Shift", "E"], what: "Show or hide the overlay", where: "Anywhere in Windows" },
    { keys: ["Enter"], what: "Send a prep chat message", where: "Prep chat and overlay" },
    { keys: ["Shift", "Enter"], what: "New line in a message", where: "Prep chat" },
  ];
  return (
    <Group title="Keybinds" hint="Custom bindings are on the way. These are the defaults.">
      <ul className="flex flex-col px-2 pb-2 pt-2">
        {binds.map((b) => (
          <li key={b.what} className="flex flex-wrap items-center justify-between gap-3 rounded-[16px] px-3 py-3.5">
            <div>
              <p className="text-sm font-medium">{b.what}</p>
              <p className="mt-0.5 text-[12.5px] text-faint">{b.where}</p>
            </div>
            <span className="flex gap-1">
              {b.keys.map((k) => (
                <Kbd key={k}>{k}</Kbd>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </Group>
  );
}

function Audio({ status, setup }) {
  const devices = status?.devices || [];
  return (
    <form onSubmit={setup.submit}>
      <Group title="Audio" hint="You on the mic, them on the loopback. This is how Escort tells speakers apart.">
        <div className="grid gap-4 px-5 pb-2 pt-3 sm:grid-cols-2">
          <Select label="Microphone (you)" value={setup.form.mic_id} onChange={setup.set("mic_id")} options={devices.filter((d) => d.kind === "mic")} />
          <Select label="Loopback (them)" value={setup.form.loopback_id} onChange={setup.set("loopback_id")} options={devices.filter((d) => d.kind !== "mic")} />
        </div>
        <SaveBar setup={setup} label="Save devices" />
      </Group>
    </form>
  );
}

function Privacy({ status, refresh }) {
  const [confirming, setConfirming] = useState(false);
  const retention = status?.meeting?.retention;

  async function wipe() {
    await post("/api/meeting/delete", {});
    setConfirming(false);
    refresh();
  }

  return (
    <>
      <Group title="Your data">
        <Row
          title="Stays on this machine"
          body={retention || "Turns, cards and frames are stored locally. Keys live in the OS keychain."}
        />
      </Group>
      <Group title="Delete meeting data" hint="Wipes turns, cards, frames and loaded context. Keychain secrets and connected accounts stay put.">
        <div className="flex flex-wrap items-center gap-3 px-5 pb-4 pt-3">
          {confirming ? (
            <>
              <Button variant="danger" onClick={wipe}>
                Yes, delete everything
              </Button>
              <Button variant="ghost" onClick={() => setConfirming(false)}>
                Keep it
              </Button>
            </>
          ) : (
            <Button variant="dangerGhost" onClick={() => setConfirming(true)}>
              Delete meeting data
            </Button>
          )}
        </div>
      </Group>
    </>
  );
}
