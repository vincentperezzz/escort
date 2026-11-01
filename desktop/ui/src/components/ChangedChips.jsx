import { Check } from "@phosphor-icons/react";

/** "Changed: Watching Display 2" under a chat reply that changed an Escort setting. */
export default function ChangedChips({ changed, className = "" }) {
  if (!Array.isArray(changed) || !changed.length) return null;
  return (
    <ul className={`flex flex-wrap gap-1.5 ${className}`} aria-label="Settings changed">
      {changed.map((item) => (
        <li
          key={`${item.setting}:${String(item.value)}`}
          className="fade-in inline-flex h-7 items-center gap-1.5 rounded-full bg-commit-soft px-3 text-[12px] text-commit shadow-[inset_0_0_0_1px_rgba(93,228,181,0.22)]"
        >
          <Check size={12} weight="bold" aria-hidden />
          <span className="text-commit/75">Changed:</span>
          <span className="font-medium">{item.label || item.setting}</span>
        </li>
      ))}
    </ul>
  );
}
