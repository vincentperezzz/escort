import { Empty } from "../components/ui.jsx";

export default function Recap({ meeting }) {
  const open = meeting.open_items || [];
  const promises = meeting.commitments || [];
  const none = open.length === 0 && promises.length === 0;

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-6">
        <h2 className="text-2xl font-semibold tracking-tight">Recap</h2>
        <p className="mt-1.5 text-sm text-muted">What is still open against your notes, and what people promised.</p>
      </div>
      {none ? (
        <Empty title="Nothing to recap yet" body="Open items and promises collect here after cards land in Live." />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <Column title="Open against your notes" count={open.length} tone="conflict" emptyText="No open conflicts.">
            {open.map((item) => (
              <li key={item.id} className="py-3">
                <p className="text-sm font-medium leading-snug">{item.title}</p>
                {item.prd_section ? <p className="mt-1 text-xs text-muted">{item.prd_section}</p> : null}
              </li>
            ))}
          </Column>
          <Column title="Commitments" count={promises.length} tone="commit" emptyText="No commitments captured.">
            {promises.map((item) => (
              <li key={item.id} className="py-3">
                <p className="text-sm font-medium leading-snug">{item.what}</p>
                <p className="mt-1 text-xs text-muted">
                  <span className="font-semibold text-ink">{item.who}</span>
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

function Column({ title, count, tone, emptyText, children }) {
  const tones = {
    conflict: { bar: "bg-conflict", text: "text-conflict" },
    commit: { bar: "bg-commit", text: "text-commit" },
  }[tone];
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-surface">
      <div className={`h-1 ${tones.bar}`} aria-hidden />
      <div className="p-4">
        <h3 className="flex items-baseline justify-between text-sm font-semibold">
          <span className={tones.text}>{title}</span>
          <span className="mono text-xs text-muted">{count}</span>
        </h3>
        {count === 0 ? <p className="mt-3 text-sm text-muted">{emptyText}</p> : <ul className="mt-1 divide-y divide-line">{children}</ul>}
      </div>
    </section>
  );
}
