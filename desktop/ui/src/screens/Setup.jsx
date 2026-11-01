import { useEffect, useState } from "react";
import { Button, Field, PageHeader, Panel, Select } from "../components/ui.jsx";
import ConnectAccounts from "../components/ConnectAccounts.jsx";
import { post } from "../lib/api.js";

const SECRETS = { meta_api_key: "", whatsapp_access_token: "", whatsapp_verify_token: "" };

export default function Setup({ status, refresh }) {
  const prefs = status?.prefs || {};
  const [form, setForm] = useState({
    ...SECRETS,
    whatsapp_phone_number_id: prefs.whatsapp_phone_number_id || "",
    whatsapp_allowed_numbers: prefs.whatsapp_allowed_numbers || "",
    mic_id: prefs.mic_id || "",
    loopback_id: prefs.loopback_id || "",
  });
  const [saved, setSaved] = useState(false);

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
    await post("/api/setup", form);
    setForm((current) => ({ ...current, ...SECRETS }));
    setSaved(true);
    refresh();
  }

  const devices = status?.devices || [];

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <PageHeader title="Setup" lede="Connect your accounts, add keys, pick audio devices. Secrets go to the OS keychain, never to this repo." />

      <ConnectAccounts onChange={refresh} />

      <form onSubmit={submit} className="space-y-4">
        <Panel title="Muse" hint="The model that writes your cards.">
          <Field
            label="Meta API key"
            type="password"
            autoComplete="off"
            value={form.meta_api_key}
            onChange={set("meta_api_key")}
            placeholder={status?.has_meta_key ? "Saved in keychain" : "LLM_..."}
          />
        </Panel>

        <Panel title="Audio" hint="You on the mic, them on the loopback. This is how Escort tells speakers apart.">
          <div className="grid gap-3 sm:grid-cols-2">
            <Select label="Microphone (you)" value={form.mic_id} onChange={set("mic_id")} options={devices.filter((d) => d.kind === "mic")} />
            <Select label="Loopback (them)" value={form.loopback_id} onChange={set("loopback_id")} options={devices.filter((d) => d.kind !== "mic")} />
          </div>
        </Panel>

        <Panel title="WhatsApp demo" hint="Hackathon allowlist only, not a production rollout.">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Access token" type="password" autoComplete="off" value={form.whatsapp_access_token} onChange={set("whatsapp_access_token")} placeholder={status?.has_whatsapp_token ? "Saved" : "Test token"} />
            <Field label="Verify token" type="password" autoComplete="off" value={form.whatsapp_verify_token} onChange={set("whatsapp_verify_token")} placeholder="You choose this" />
            <Field label="Phone number ID" value={form.whatsapp_phone_number_id} onChange={set("whatsapp_phone_number_id")} />
            <Field label="Allowlist" value={form.whatsapp_allowed_numbers} onChange={set("whatsapp_allowed_numbers")} placeholder="1555…" />
          </div>
        </Panel>

        <div className="flex items-center gap-3">
          <Button type="submit" variant="primary">
            Save setup
          </Button>
          {saved ? (
            <span className="text-sm text-commit" role="status">
              Saved
            </span>
          ) : null}
        </div>
      </form>
    </div>
  );
}
