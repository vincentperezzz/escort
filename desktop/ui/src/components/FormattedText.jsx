/**
 * Safe, lightweight Markdown renderer for Escort AI chat replies.
 * Renders paragraphs, bullet/numbered lists, headings, fenced code blocks, **bold**, *italic*, and `inline code`.
 */
export default function FormattedText({ text, cursor = null, className = "" }) {
  const blocks = parseBlocks(String(text || ""));
  return (
    <div className={className}>
      {blocks.map((block, idx) => {
        const isLast = idx === blocks.length - 1;
        const tail = isLast ? cursor : null;
        if (block.type === "ul") {
          return (
            <ul key={idx} className="my-2 space-y-1.5 pl-5 list-disc marker:text-accent">
              {block.items.map((item, j) => (
                <li key={j} className="pl-1 text-ink/90">
                  <Inline text={item} />
                  {isLast && j === block.items.length - 1 ? tail : null}
                </li>
              ))}
            </ul>
          );
        }
        if (block.type === "ol") {
          return (
            <ol key={idx} className="my-2 space-y-1.5 pl-5 list-decimal marker:text-muted">
              {block.items.map((item, j) => (
                <li key={j} className="pl-1 text-ink/90">
                  <Inline text={item} />
                  {isLast && j === block.items.length - 1 ? tail : null}
                </li>
              ))}
            </ol>
          );
        }
        if (block.type === "code") {
          return (
            <div key={idx} className="my-2 overflow-x-auto rounded-xl bg-white/[0.04] p-3 text-xs font-mono text-ink/90 hairline">
              <pre className="whitespace-pre">{block.lines.join("\n")}</pre>
              {tail}
            </div>
          );
        }
        if (block.type === "heading") {
          return (
            <p key={idx} className="mt-2 font-semibold text-ink">
              <Inline text={block.text} />
              {tail}
            </p>
          );
        }
        return (
          <p key={idx} className="whitespace-pre-wrap leading-relaxed text-ink/90">
            <Inline text={block.text} />
            {tail}
          </p>
        );
      })}
    </div>
  );
}

function parseBlocks(raw) {
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let para = [];
  let list = null;
  let codeBlock = null;

  const flushPara = () => {
    if (para.length) {
      blocks.push({ type: "p", text: para.join("\n") });
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push(list);
      list = null;
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      flushPara();
      flushList();
      if (codeBlock) {
        blocks.push(codeBlock);
        codeBlock = null;
      } else {
        const lang = trimmed.slice(3).trim();
        codeBlock = { type: "code", lang, lines: [] };
      }
      continue;
    }
    if (codeBlock) {
      codeBlock.lines.push(line);
      continue;
    }
    if (!trimmed) {
      flushPara();
      flushList();
      continue;
    }
    const headingMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      flushPara();
      flushList();
      blocks.push({ type: "heading", text: headingMatch[2] });
      continue;
    }
    const ulMatch = trimmed.match(/^(?:[-*•])\s+(.+)$/);
    if (ulMatch) {
      flushPara();
      if (!list || list.type !== "ul") {
        flushList();
        list = { type: "ul", items: [] };
      }
      list.items.push(ulMatch[1]);
      continue;
    }
    const olMatch = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (olMatch) {
      flushPara();
      if (!list || list.type !== "ol") {
        flushList();
        list = { type: "ol", items: [] };
      }
      list.items.push(olMatch[1]);
      continue;
    }
    if (list) {
      flushList();
    }
    para.push(line);
  }
  flushPara();
  flushList();
  if (codeBlock) {
    blocks.push(codeBlock);
  }
  return blocks.length ? blocks : [{ type: "p", text: raw }];
}

const INLINE_RE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\s\n][^*\n]*?\*)/g;

function Inline({ text }) {
  if (!text) return null;
  const parts = String(text).split(INLINE_RE);
  return parts.map((part, i) => {
    if (!part) return null;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={i} className="font-semibold text-ink">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} className="mono rounded-md bg-white/[0.08] px-1.5 py-0.5 text-[0.92em] text-ink">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return (
        <em key={i} className="italic text-ink/80">
          {part.slice(1, -1)}
        </em>
      );
    }
    return part;
  });
}
