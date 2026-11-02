/**
 * Safe, lightweight Markdown renderer for Escort AI chat replies.
 * Renders paragraphs, bullet/numbered lists, headings, **bold**, *italic*, and `inline code`.
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
            <ul key={idx} className="my-1.5 space-y-1.5 pl-4">
              {block.items.map((item, j) => (
                <li key={j} className="relative pl-1.5 marker:text-muted list-disc">
                  <Inline text={item} />
                  {isLast && j === block.items.length - 1 ? tail : null}
                </li>
              ))}
            </ul>
          );
        }
        if (block.type === "ol") {
          return (
            <ol key={idx} className="my-1.5 space-y-1.5 pl-5 list-decimal">
              {block.items.map((item, j) => (
                <li key={j} className="pl-1 marker:text-muted">
                  <Inline text={item} />
                  {isLast && j === block.items.length - 1 ? tail : null}
                </li>
              ))}
            </ol>
          );
        }
        if (block.type === "heading") {
          return (
            <p key={idx} className="font-semibold text-ink">
              <Inline text={block.text} />
              {tail}
            </p>
          );
        }
        return (
          <p key={idx} className="whitespace-pre-wrap">
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
  return blocks.length ? blocks : [{ type: "p", text: raw }];
}

const INLINE_RE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\s][^*\n]*?\*)/g;

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
        <em key={i} className="italic">
          {part.slice(1, -1)}
        </em>
      );
    }
    return part;
  });
}

