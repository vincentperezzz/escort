import { Warning, CheckCircle, ChatTeardropText } from "@phosphor-icons/react";

export function kindOf(type = "") {
  if (type.includes("CONFLICT")) return "conflict";
  if (type.includes("COMMIT")) return "commit";
  return "reply";
}

/** Card type -> label, hint, icon and the Tailwind classes for its hue. */
export const KIND = {
  conflict: {
    label: "Conflict",
    hint: "Against your notes",
    Icon: Warning,
    text: "text-conflict",
    soft: "bg-conflict-soft",
    chip: "bg-conflict-soft text-[#ffb3a7]",
  },
  commit: {
    label: "Commitment",
    hint: "Someone promised something",
    Icon: CheckCircle,
    text: "text-commit",
    soft: "bg-commit-soft",
    chip: "bg-commit-soft text-commit",
  },
  reply: {
    label: "Suggested reply",
    hint: "You could say",
    Icon: ChatTeardropText,
    text: "text-reply",
    soft: "bg-reply-soft",
    chip: "bg-reply-soft text-[#c6bfff]",
  },
};

export const CONFLICT_ACTIONS = [
  { id: "raise", label: "Raise it" },
  { id: "suggest", label: "Suggest reply" },
  { id: "ignore", label: "Ignore" },
];
