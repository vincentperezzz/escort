import { useEffect, useRef, useState } from "react";
import { CaretDown, FileArrowUp, FolderSimple, NotePencil, Plus } from "@phosphor-icons/react";
import { GoogleDriveLogo, NotionLogo } from "../BrandIcons.jsx";
import { Button } from "../ui.jsx";

const OPTIONS = [
  { id: "notion", label: "Notion", Logo: NotionLogo },
  { id: "google", label: "Google Drive", Logo: GoogleDriveLogo },
  { id: "folder", label: "Local folder sync", Icon: FolderSimple },
  { id: "upload", label: "Upload files", Icon: FileArrowUp },
  { id: "note", label: "Paste note", Icon: NotePencil },
];

export default function AddContextMenu({ onPick, onUploadFiles }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const fileRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(event) {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    }
    function onKey(event) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function choose(id) {
    setOpen(false);
    if (id === "upload") fileRef.current?.click();
    else onPick(id);
  }

  return (
    <div className="relative" ref={rootRef}>
      <Button variant="primary" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen((v) => !v)}>
        <Plus size={16} aria-hidden />
        Add
        <CaretDown size={14} aria-hidden className={`transition-transform duration-300 ${open ? "rotate-180" : ""}`} />
      </Button>
      {open ? (
        <ul
          role="menu"
          className="absolute right-0 top-[calc(100%+8px)] z-20 min-w-[220px] rounded-2xl bg-[#14151c] p-1.5 shadow-xl hairline-strong"
        >
          {OPTIONS.map(({ id, label, Logo, Icon }) => (
            <li key={id} role="none">
              <button
                type="button"
                role="menuitem"
                className="press flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm hover:bg-white/[0.06]"
                onClick={() => choose(id)}
              >
                {Logo ? (
                  <span className="grid h-[18px] w-[18px] shrink-0 place-items-center" aria-hidden>
                    <Logo size={16} />
                  </span>
                ) : (
                  <Icon size={18} className="shrink-0 text-[#c6bfff]" aria-hidden />
                )}
                {label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <input
        ref={fileRef}
        type="file"
        className="sr-only"
        accept=".pdf,.docx,.md,.txt,application/pdf,text/plain,text/markdown"
        multiple
        onChange={(event) => {
          if (event.target.files?.length) onUploadFiles(event.target.files);
          event.target.value = "";
        }}
      />
    </div>
  );
}
