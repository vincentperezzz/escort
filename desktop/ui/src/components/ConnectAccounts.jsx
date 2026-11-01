import { useState } from "react";
import { CheckCircle, GoogleLogo, NotionLogo, Plugs, ArrowSquareOut } from "@phosphor-icons/react";
import { Button, Pill } from "./ui.jsx";
import { useConnectors } from "../lib/useConnectors.js";

const META = {
  google: {
    name: "Google",
    scope: "Calendar + Drive",
    Icon: GoogleLogo,
    blurb: "Pulls your upcoming calls and the docs shared around them.",
    steps: [
      "Press Connect Google. Your browser opens Google's sign-in.",
      "Pick your work account and allow read-only Calendar and Drive access.",
      "Come back here. Escort detects the sign-in on its own.",
    ],
  },
  notion: {
    name: "Notion",
    scope: "Pages + databases",
    Icon: NotionLogo,
    blurb: "Searches the pages you share with Escort for scope notes.",
    steps: [
      "Open notion.so/profile/integrations and create an internal integration.",
      "Copy its token, then share the pages you want with that integration.",
      "Paste the token below and press Connect Notion.",
    ],
  },
};

/**
 * Connect accounts: Google and Notion cards with connect, connected and
 * walkthrough states. `compact` hides the intro for use inside other screens.
 */
export default function ConnectAccounts({ onChange, compact = false }) {
  const c = useConnectors({ onChange });
  const list = (c.providers || []).filter((p) => META[p.id]);

  return (
    <section aria-labelledby="connect-heading">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 id="connect-heading" className="text-base font-semibold">
            Connect accounts
          </h3>
          {compact ? null : (
            <p className="mt-0.5 text-sm text-muted">Optional. Without them, Escort runs on clearly labelled demo data.</p>
          )}
        </div>
        {c.unavailable ? <Pill tone="warn">Backend not reachable</Pill> : null}
      </div>
      {c.providers === null ? (
        <div className="grid gap-3 md:grid-cols-2" aria-busy="true">
          {[0, 1].map((i) => (
            <div key={i} className="h-44 rounded-xl border border-line bg-surface" />
          ))}
        </div>
      ) : (
        <div className="grid items-start gap-3 md:grid-cols-2">
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
  const { Icon } = meta;
  const busy = ctl.busy[provider.id];
  const error = ctl.errors[provider.id];
  const authUrl = ctl.authUrls[provider.id];
  const [token, setToken] = useState("");
  const demo = provider.mode === "mock";

  async function submitToken(event) {
    event.preventDefault();
    if (!token.trim()) return;
    if (await ctl.connectNotion(token.trim())) setToken("");
  }

  return (
    <article className="rounded-xl border border-line bg-surface p-4">
      <header className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-line bg-raised">
          <Icon size={22} weight="regular" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-semibold">{meta.name}</h4>
          <p className="text-xs text-muted">{meta.scope}</p>
        </div>
        {provider.connected ? (
          <Pill tone={demo ? "warn" : "good"}>{demo ? "Demo data" : "Connected"}</Pill>
        ) : (
          <Pill>Not connected</Pill>
        )}
      </header>

      {provider.connected ? (
        <div className="mt-4">
          <p className="flex items-center gap-2 text-sm">
            <CheckCircle size={18} weight="fill" className={demo ? "text-warn" : "text-commit"} aria-hidden />
            <span className="min-w-0 truncate font-medium">{provider.account || "Account connected"}</span>
          </p>
          {demo ? (
            <p className="mt-2 text-xs leading-relaxed text-muted">
              This is fictional demo content (BayaniPay fixtures), not your real {meta.name}.
            </p>
          ) : null}
          <Button
            variant="dangerGhost"
            size="sm"
            className="mt-4"
            disabled={busy === "disconnecting"}
            onClick={() => ctl.disconnect(provider.id)}
          >
            {busy === "disconnecting" ? "Disconnecting…" : `Disconnect ${meta.name}`}
          </Button>
        </div>
      ) : (
        <div className="mt-4">
          <p className="text-sm text-muted">{meta.blurb}</p>

          <ol className="mt-4 space-y-2.5">
            {meta.steps.map((text, i) => (
              <li key={text} className="flex gap-3 text-sm leading-snug">
                <span className="mono grid h-5 w-5 shrink-0 place-items-center rounded-full bg-sunken text-[11px] font-semibold text-muted">
                  {i + 1}
                </span>
                <span>{text}</span>
              </li>
            ))}
          </ol>

          {!provider.configured && provider.setup_hint ? (
            <p className="mt-3 rounded-lg bg-warn-soft px-3 py-2 text-xs leading-relaxed text-warn">{provider.setup_hint}</p>
          ) : null}

          {provider.id === "notion" ? (
            <form onSubmit={submitToken} className="mt-4 space-y-2">
              <label className="block">
                <span className="text-xs font-medium">Integration token</span>
                <input
                  type="password"
                  autoComplete="off"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="ntn_… or secret_…"
                  className="mt-1 h-10 w-full rounded-lg border border-line-strong bg-raised px-3 text-sm placeholder:text-faint"
                />
              </label>
              <Button type="submit" variant="primary" disabled={!token.trim() || busy === "connecting"}>
                <Plugs size={16} aria-hidden />
                {busy === "connecting" ? "Checking token…" : "Connect Notion"}
              </Button>
            </form>
          ) : (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Button variant="primary" disabled={Boolean(busy)} onClick={ctl.connectGoogle}>
                <Plugs size={16} aria-hidden />
                {busy === "connecting" ? "Opening browser…" : busy === "waiting" ? "Waiting for Google…" : "Connect Google"}
              </Button>
              {busy === "waiting" ? (
                <div className="flex items-center gap-3 text-xs">
                  {authUrl ? (
                    <a className="inline-flex items-center gap-1 font-semibold text-accent underline-offset-2 hover:underline" href={authUrl} target="_blank" rel="noreferrer">
                      Open sign-in again <ArrowSquareOut size={14} aria-hidden />
                    </a>
                  ) : null}
                  <button type="button" className="font-semibold text-muted hover:text-ink" onClick={() => ctl.cancelWait(provider.id)}>
                    Cancel
                  </button>
                </div>
              ) : null}
            </div>
          )}
        </div>
      )}

      {busy === "waiting" ? (
        <p className="mt-3 text-xs text-muted" role="status">
          Finish signing in with Google in your browser. This checks every 2 seconds for up to 2 minutes.
        </p>
      ) : null}
      {error ? (
        <p className="mt-3 text-xs font-medium text-conflict" role="alert">
          {error}
        </p>
      ) : null}
    </article>
  );
}
