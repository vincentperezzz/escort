import { Bezel, Empty, PageHeader } from "../components/ui.jsx";
import { KIND } from "../lib/cards.js";

export default function Recap({ meeting, title }) {
  const open = meeting.open_items || [];
  const promises = meeting.commitments || [];
  const none = open.length === 0 && promises.length === 0;

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Recap" lede={`${title ? `${title}. ` : ""}What is still open against your notes, and what people promised.`} />
      {none ? (
        <Empty title="Nothing to recap yet" body="Open items and promises collect here after cards land in Live." />
      ) : (
        <div className="grid gap-5 md:grid-cols-2">
          <Column kind="conflict" title="Open against your notes" count={open.length} emptyText="No open conflicts.">
            {open.map((item) => (
              <li key={item.id} className="rounded-2xl bg-white/[0.025] px-4 py-3.5 hairline">
                <p className="text-[14.5px] font-medium leading-snug">{item.title}</p>
                {item.prd_section ? <p className="mt-1.5 text-[12.5px] text-muted">{item.prd_section}</p> : null}
              </li>
            ))}
          </Column>
          <Column kind="commit" title="Commitments" count={promises.length} emptyText="No commitments captured.">
            {promises.map((item) => (
              <li key={item.id} className="rounded-2xl bg-white/[0.025] px-4 py-3.5 hairline">
                <p className="text-[14.5px] font-medium leading-snug">{item.what}</p>
                <p className="mt-1.5 text-[12.5px] text-muted">
                  <span className="text-ink">{item.who}</span>
                  {item.when ? `, ${item.when}` : ""}
                </p>
              </li>
            ))}
          </Column>
        </div>
      )}
    </div>
  );
}

function Column({ kind, title, count, emptyText, children }) {
  const k = KIND[kind];
  const { Icon } = k;
  return (
    <Bezel className="rise" coreClassName="p-6">
      <h2 className="flex items-center justify-between gap-3">
        <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-[13px] font-medium ${k.chip}`}>
          <Icon size={15} aria-hidden />
          {title}
        </span>
        <span className="mono text-[13px] text-faint">{count}</span>
      </h2>
      {count === 0 ? <p className="mt-5 text-sm text-muted">{emptyText}</p> : <ul className="mt-5 flex flex-col gap-2">{children}</ul>}
    </Bezel>
  );
}
