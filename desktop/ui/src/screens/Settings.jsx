import { Button, Panel, PageHeader } from "../components/ui.jsx";
import { post } from "../lib/api.js";

export default function Settings({ status, refresh }) {
  const latency = status?.meeting?.latency;

  async function wipe() {
    await post("/api/meeting/delete", {});
    refresh();
  }
  async function demo() {
    await post("/api/demo", {});
    window.location.hash = "/live";
    refresh();
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <PageHeader title="Settings" lede="Demo data, diagnostics, and a way to clear this meeting." />

      <Panel title="Services" hint="Local endpoints Escort exposes while it runs.">
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
          <dt className="text-muted">MCP</dt>
          <dd className="mono">{status?.mcp ?? "unknown"}</dd>
          <dt className="text-muted">WhatsApp</dt>
          <dd className="mono">{status?.whatsapp ?? "unknown"}</dd>
          {latency?.count ? (
            <>
              <dt className="text-muted">Card latency</dt>
              <dd className="mono">
                p50 {Math.round(latency.p50_ms)}ms, p95 {Math.round(latency.p95_ms)}ms
              </dd>
            </>
          ) : null}
        </dl>
      </Panel>

      <Panel title="Demo" hint="Replays a fictional BayaniPay client call so you can see every card type.">
        <Button onClick={demo}>Load staged demo</Button>
      </Panel>

      <Panel title="Danger zone" hint="Delete wipes turns, cards, and frames. Keychain secrets and connected accounts stay put.">
        <Button variant="danger" onClick={wipe}>
          Delete meeting data
        </Button>
      </Panel>
    </div>
  );
}
