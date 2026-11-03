import { useEffect, useRef } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useFocusTrap(active) {
  const rootRef = useRef(null);

  useEffect(() => {
    if (!active || !rootRef.current) return undefined;
    const root = rootRef.current;
    const previouslyFocused = document.activeElement;

    const focusables = () => [...root.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
    const first = () => focusables()[0];

    const t = requestAnimationFrame(() => {
      (first() || root).focus?.();
    });

    function onKeyDown(e) {
      if (e.key === "Tab") {
        const nodes = focusables();
        if (!nodes.length) {
          e.preventDefault();
          return;
        }
        const i = nodes.indexOf(document.activeElement);
        if (e.shiftKey) {
          if (i <= 0) {
            e.preventDefault();
            nodes[nodes.length - 1].focus();
          }
        } else if (i === nodes.length - 1) {
          e.preventDefault();
          nodes[0].focus();
        }
      }
    }

    root.addEventListener("keydown", onKeyDown);
    return () => {
      cancelAnimationFrame(t);
      root.removeEventListener("keydown", onKeyDown);
      if (previouslyFocused?.focus) previouslyFocused.focus();
    };
  }, [active]);

  return rootRef;
}
