"use client";

import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";

type CardItem = {
  id: string;
  type: "PRD_CONFLICT" | "COMMITMENT" | "SUGGESTED_REPLY";
  title: string;
  body: string;
  prd_section: string | null;
  who: string | null;
  what: string | null;
  when: string | null;
  trigger_text: string | null;
  created_at: string;
  latency_ms: number;
  crop_url: string | null;
  source: string;
  deictic: string | null;
  segmenter: string | null;
  proactive: boolean;
};

type OpenItem = { id: string; title: string; prd_section: string | null; status: string };
type Commitment = { id: string; who: string | null; what: string | null; when: string };

type Meeting = {
  prd_sections: { anchor: string; heading: string; quote: string }[];
  commitments: Commitment[];
  open_items: OpenItem[];
  latency: { count: number; p50_ms: number | null; p95_ms: number | null; target_ms: number; over_target: number };
  retention: string;
};

type Utterance = {
  id: string;
  speaker: string;
  text: string;
  created_at: string;
};

type ListenerState = {
  title: string;
  mode: string;
  consent: string;
  transcriber: string;
  spark: string;
  segmenter: string;
  utterances: Utterance[];
  partials: Record<string, string>;
  cards: CardItem[];
  skipped_frames: number;
  warnings: string[];
  latest_frame_url: string | null;
  retention: string;
  meeting: Meeting;
  latency: Meeting["latency"];
};

const LABELS: Record<CardItem["type"], string> = {
  PRD_CONFLICT: "PRD conflict",
  COMMITMENT: "Commitment",
  SUGGESTED_REPLY: "Say this",
};

const STRIPE: Record<CardItem["type"], string> = {
  PRD_CONFLICT: "border-l-[#e36a57]",
  COMMITMENT: "border-l-[#3dbe8c]",
  SUGGESTED_REPLY: "border-l-[#f0b429]",
};

function mediaSrc(url: string | null) {
  if (!url) return null;
  return url.replace(/^\/panel\//, "/listener/");
}

function clock(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function LiveRail() {
  const [data, setData] = useState<ListenerState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const pull = useCallback(async () => {
    try {
      const response = await fetch("/listener/state", { cache: "no-store" });
      if (!response.ok) throw new Error(`Listener returned ${response.status}`);
      setData((await response.json()) as ListenerState);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Escort is quiet");
    } finally {
      setLoaded(true);
    }
  }, []);

  const wipe = useCallback(async () => {
    await fetch("/listener/delete", { method: "POST" });
    await pull();
  }, [pull]);

  useEffect(() => {
    void pull();
    const id = setInterval(() => void pull(), 1000);
    return () => clearInterval(id);
  }, [pull]);

  const cards = [...(data?.cards ?? [])].reverse();

  return (
    <main className="scope-rail min-h-dvh" style={{ fontFamily: "var(--font-outfit), sans-serif" }}>
      <div className="mx-auto flex min-h-dvh w-full max-w-md flex-col px-4 py-5 sm:px-5">
        <header className="mb-4 flex items-start justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold tracking-[0.22em] text-[#f0b429]">ESCORT</p>
            <h1
              className="text-3xl leading-none text-[#f4efe6]"
              style={{ fontFamily: "var(--font-fraunces), serif" }}
            >
              Escort
            </h1>
            <p className="mt-1 text-xs text-[#b7aa9b]">Live scope-creep guard for Taglish client calls</p>
            <p className="mt-2 text-sm text-[#b7aa9b]">{data?.title || "Waiting for a call"}</p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => void pull()}>
              Refresh
            </Button>
            <Button variant="ghost" size="sm" onClick={() => void wipe()}>
              Delete
            </Button>
          </div>
        </header>

        <div className="mb-4 flex flex-wrap gap-2">
          <Badge variant="secondary">{data?.mode || "offline"}</Badge>
          {data?.latency && data.latency.count > 0 ? (
            <Badge variant="outline">
              p50 {data.latency.p50_ms} ms · p95 {data.latency.p95_ms} ms
            </Badge>
          ) : null}
          {data?.spark ? <Badge variant="outline">{data.spark}</Badge> : null}
          {data ? <Badge variant="outline">last frame only</Badge> : null}
        </div>

        {data?.meeting?.open_items?.length ? (
          <section className="mb-4 space-y-2">
            <h2 className="text-[11px] font-semibold tracking-[0.18em] text-[#e36a57]">OPEN AGAINST THE PRD</h2>
            {data.meeting.open_items.map((item) => (
              <p key={item.id} className="text-sm text-[#f4efe6]">
                {item.title}
                {item.prd_section ? <span className="text-[#f0b429]"> · {item.prd_section}</span> : null}
              </p>
            ))}
          </section>
        ) : null}

        {data?.meeting?.commitments?.length ? (
          <section className="mb-4 space-y-2">
            <h2 className="text-[11px] font-semibold tracking-[0.18em] text-[#3dbe8c]">PROMISES</h2>
            {data.meeting.commitments.map((item) => (
              <p key={item.id} className="text-sm text-[#f4efe6]">
                {item.who} will {item.what}
                <span className="text-[#b7aa9b]"> · {item.when}</span>
              </p>
            ))}
          </section>
        ) : null}

        {!loaded ? <Skeleton /> : null}
        {loaded && error && !data ? <ErrorState message={error} /> : null}
        {loaded && data && cards.length === 0 ? <EmptyState /> : null}

        <div className="flex flex-col gap-3">
          {cards.map((card) => (
            <Card key={card.id} size="sm" className={`border-l-4 ${STRIPE[card.type]} bg-[#262019]`}>
              <CardHeader className="grid-cols-[1fr_auto] items-start">
                <div className="space-y-1">
                  <div className="flex flex-wrap gap-1">
                    <Badge variant={card.type === "PRD_CONFLICT" ? "destructive" : "secondary"}>
                      {LABELS[card.type]}
                    </Badge>
                    {card.proactive ? <Badge variant="outline">pushed live</Badge> : null}
                  </div>
                  <CardTitle
                    className="text-base text-[#f4efe6]"
                    style={{ fontFamily: "var(--font-fraunces), serif" }}
                  >
                    {card.title}
                  </CardTitle>
                  <p className="text-xs text-[#b7aa9b]">{clock(card.created_at)}</p>
                </div>
                <div className="text-right">
                  <p className="text-[10px] tracking-[0.16em] text-[#b7aa9b]">LATENCY</p>
                  <p className="font-mono text-3xl leading-none text-[#f0b429] tabular-nums">
                    {card.latency_ms}
                    <span className="ml-1 text-xs">ms</span>
                  </p>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm leading-relaxed text-[#f4efe6]">{card.body}</p>
                {card.prd_section ? (
                  <p className="rounded-md bg-[#1a1612] px-3 py-2 text-xs text-[#f0b429]">
                    PRD · {card.prd_section}
                  </p>
                ) : null}
                {card.who && card.what ? (
                  <p className="text-xs text-[#b7aa9b]">
                    {card.who} · {card.what}
                    {card.when ? ` · ${card.when}` : ""}
                  </p>
                ) : null}
                {card.trigger_text ? (
                  <p className="text-xs italic text-[#b7aa9b]">“{card.trigger_text}”</p>
                ) : null}
                {card.crop_url ? (
                  <figure className="space-y-1">
                    <div className="relative h-40 w-full overflow-hidden rounded-md border border-[#4a3f34]">
                      <Image
                        src={mediaSrc(card.crop_url) || ""}
                        alt={card.deictic ? `Crop for ${card.deictic}` : "Deictic crop"}
                        fill
                        unoptimized
                        className="object-cover object-left-top"
                      />
                    </div>
                    <figcaption className="text-[11px] text-[#b7aa9b]">
                      {card.segmenter === "sam-3.1" ? "SAM 3.1 crop" : "Share-area crop"}
                      {card.deictic ? ` · heard “${card.deictic}”` : ""}
                    </figcaption>
                  </figure>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>

        {data && data.utterances.length > 0 ? (
          <section className="mt-5">
            <Separator className="mb-3 bg-[#4a3f34]" />
            <h2 className="mb-2 text-[11px] font-semibold tracking-[0.18em] text-[#b7aa9b]">HEARD</h2>
            <ScrollArea className="h-48 rounded-lg border border-[#4a3f34]">
              <ul className="space-y-3 p-3">
                {data.utterances.map((line) => (
                  <li key={line.id} className="text-sm">
                    <span className={line.speaker === "Me" ? "text-[#f0b429]" : "text-[#7ec8c3]"}>
                      {line.speaker}
                    </span>
                    <span className="text-[#b7aa9b]"> · {clock(line.created_at)}</span>
                    <p className="text-[#f4efe6]">{line.text}</p>
                  </li>
                ))}
                {Object.entries(data.partials).map(([speaker, text]) => (
                  <li key={speaker} className="text-sm italic text-[#b7aa9b]">
                    {speaker} is still talking: {text}
                  </li>
                ))}
              </ul>
            </ScrollArea>
          </section>
        ) : null}

        {data?.latest_frame_url ? (
          <div className="relative mt-4 h-36 w-full overflow-hidden rounded-lg border border-[#4a3f34]">
            <Image
              src={mediaSrc(data.latest_frame_url) || ""}
              alt="Latest changed screen frame"
              fill
              unoptimized
              className="object-cover"
            />
          </div>
        ) : null}

        {data?.warnings?.length ? (
          <p className="mt-3 text-xs text-[#e36a57]">{data.warnings[data.warnings.length - 1]}</p>
        ) : null}

        <p className="mt-auto pt-6 text-xs leading-relaxed text-[#b7aa9b]">
          {data?.retention}
          {data?.consent ? ` ${data.consent}` : " Staged mock. Fictional BayaniPay. Nothing is kept after you delete the meeting."}
        </p>
      </div>
    </main>
  );
}

function Skeleton() {
  return (
    <div className="mb-3 space-y-3">
      <div className="h-28 animate-pulse rounded-xl bg-[#2c261f]" />
      <div className="h-28 animate-pulse rounded-xl bg-[#2c261f]" />
    </div>
  );
}

function EmptyState() {
  return (
    <Card size="sm" className="mb-3 bg-[#262019]">
      <CardHeader>
        <CardTitle style={{ fontFamily: "var(--font-fraunces), serif" }}>The call is quiet</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-[#b7aa9b]">
          A PRD conflict lands on its own while the line is still going. You do not have to ask.
          A commitment names who, what, and when. A reply is a Taglish line you can say.
        </p>
      </CardContent>
    </Card>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <Card size="sm" className="mb-3 border-l-4 border-l-[#e36a57] bg-[#262019]">
      <CardHeader>
        <CardTitle style={{ fontFamily: "var(--font-fraunces), serif" }}>Escort is quiet</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm text-[#e36a57]">{message}</p>
        <p className="font-mono text-xs text-[#b7aa9b]">
          python -m escort serve --replay --port 8747
        </p>
      </CardContent>
    </Card>
  );
}
