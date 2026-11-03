import { Trash } from "@phosphor-icons/react";
import { Bezel, Chip, Empty, IconButton, Pill } from "../ui.jsx";
import { FILTER_CHIPS, itemIcon, itemImportedLabel, itemStatus, matchesFilter } from "./itemMeta.js";

export default function ContextFilesList({ items, filter, onFilter, onRemove, drag, onDrag, onUpload, addControl }) {
  const filtered = items.filter((item) => matchesFilter(item, filter));

  return (
    <Bezel coreClassName="p-0">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] px-5 py-4">
        <div>
          <h2 className="text-[15px] font-medium tracking-[-0.01em]">Files</h2>
          <p className="mt-1 text-[13px] text-muted">
            {items.length ? `${items.length} in this project` : "Add sources Escort can cite on calls"}
          </p>
        </div>
        {addControl}
      </header>
      <div
        className={`px-5 py-4 transition-colors duration-300 ${drag ? "bg-accent-soft/40" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          onDrag(true);
        }}
        onDragLeave={() => onDrag(false)}
        onDrop={(event) => {
          event.preventDefault();
          onDrag(false);
          if (event.dataTransfer.files?.length) onUpload(event.dataTransfer.files);
        }}
      >
        <div className="mb-4 flex flex-wrap gap-1.5" role="tablist" aria-label="Filter by source type">
          {FILTER_CHIPS.map((chip) => (
            <Chip
              key={chip.id}
              role="tab"
              aria-selected={filter === chip.id}
              tone={filter === chip.id ? "accent" : "neutral"}
              onClick={() => onFilter(chip.id)}
            >
              {chip.label}
            </Chip>
          ))}
        </div>
        {filtered.length === 0 ? (
          <Empty
            title={items.length === 0 ? "No files yet" : "Nothing in this filter"}
            body={
              items.length === 0
                ? "Upload documents, sync a folder, or import from Notion or Google. Drop files anywhere on this list."
                : "Try another filter or add a new source."
            }
          />
        ) : (
          <ul className="divide-y divide-white/[0.06] rounded-2xl bg-white/[0.02] hairline">
            {filtered.map((item) => {
              const Icon = itemIcon(item);
              const status = itemStatus(item);
              const imported = itemImportedLabel(item);
              return (
                <li key={item.id} className="flex items-start gap-3 px-4 py-3.5 hover:bg-white/[0.03]">
                  <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-white/[0.05] text-[#c6bfff] hairline">
                    <Icon size={18} weight="duotone" aria-hidden />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Pill tone="accent">{item.source_label}</Pill>
                      <Pill tone={status.tone === "good" ? "good" : status.tone === "warn" ? "warn" : "accent"}>{status.label}</Pill>
                    </div>
                    <p className="mt-1.5 truncate text-sm font-medium">{item.title}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                      {imported ? <span>Imported {imported}</span> : null}
                      {status.reason ? <span className="text-warn">{status.reason}</span> : null}
                    </div>
                  </div>
                  <IconButton label={`Remove ${item.title}`} variant="ghost" onClick={() => onRemove(item.id)} className="hover:bg-conflict-soft hover:text-conflict">
                    <Trash size={16} aria-hidden />
                  </IconButton>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Bezel>
  );
}
