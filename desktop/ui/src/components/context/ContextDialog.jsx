import { useEffect, useId, useRef } from "react";
import { X } from "@phosphor-icons/react";
import { IconButton } from "../ui.jsx";

export default function ContextDialog({ title, description, onClose, children, footer, widthClass = "max-w-lg" }) {
  const titleId = useId();
  const panelRef = useRef(null);

  useEffect(() => {
    const prev = document.activeElement;
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
      if (prev instanceof HTMLElement) prev.focus();
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? `${titleId}-desc` : undefined}
        tabIndex={-1}
        className={`glass w-full ${widthClass} rounded-3xl p-6 shadow-2xl hairline fade-in outline-none`}
      >
        <div className="flex items-start justify-between gap-4 pb-4">
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
        <div className="max-h-[min(70vh,560px)] overflow-y-auto scroll-thin">{children}</div>
        {footer ? <div className="mt-5 flex flex-wrap items-center justify-end gap-2 border-t border-white/[0.06] pt-4">{footer}</div> : null}
      </div>
    </div>
  );
}
