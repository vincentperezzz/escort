import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "@phosphor-icons/react";
import { IconButton } from "../ui.jsx";

export default function ContextDialog({ title, description, onClose, children, footer, wide = false }) {
  const titleId = useId();
  const panelRef = useRef(null);

  useEffect(() => {
    const prev = document.activeElement;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.focus();
    function onKey(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      if (prev instanceof HTMLElement) prev.focus();
    };
  }, [onClose]);

  return createPortal(
    <div
      className="escort-dialog-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="presentation"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? `${titleId}-desc` : undefined}
        tabIndex={-1}
        className={`escort-dialog-panel glass flex flex-col overflow-hidden rounded-3xl shadow-2xl hairline fade-in outline-none ${wide ? "escort-dialog-panel--wide" : ""}`}
      >
        <div className="flex shrink-0 items-start justify-between gap-4 px-6 pb-4 pt-6">
          <div className="min-w-0">
            <h2 id={titleId} className="text-[17px] font-semibold tracking-[-0.015em] text-ink">
              {title}
            </h2>
            {description ? (
              <p id={`${titleId}-desc`} className="mt-1.5 text-[13px] leading-relaxed text-muted text-pretty">
                {description}
              </p>
            ) : null}
          </div>
          <IconButton label="Close dialog" onClick={onClose} size={32}>
            <X size={16} aria-hidden />
          </IconButton>
        </div>
        <div className="escort-dialog-body min-h-0 flex-1 overflow-y-auto scroll-thin px-6">{children}</div>
        {footer ? (
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-white/[0.06] px-6 pb-6 pt-4">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body
  );
}
