import { PhoneSlash, Folders, NotePencil, Question, ChatCircleDots, Clock, Signpost, BellRinging } from "@phosphor-icons/react";

/** Proactive nudge kind -> label, icon and the hue classes of its tile. */
export const NUDGE = {
  NO_MEETING: { label: "Not in the call?", Icon: PhoneSlash, tone: "warn" },
  WRONG_PROJECT: { label: "Wrong project?", Icon: Folders, tone: "warn" },
  NOTED: { label: "Noted", Icon: NotePencil, tone: "quiet" },
  CLARIFY: { label: "Clarify", Icon: Question, tone: "ask" },
  QUESTION_FOR_YOU: { label: "Asked of you", Icon: ChatCircleDots, tone: "ask" },
  TIME_CHECK: { label: "Time check", Icon: Clock, tone: "warn" },
  OFF_TOPIC: { label: "Off topic", Icon: Signpost, tone: "ask" },
  HEADS_UP: { label: "Heads up", Icon: BellRinging, tone: "ask" },
};

export const nudgeMeta = (kind) => NUDGE[kind] || NUDGE.HEADS_UP;

/** Open nudges, newest first. Loud ones (normal/urgent) before quiet acknowledgements. */
export function openNudges(meeting) {
  const list = (meeting?.nudges || []).filter((n) => n.status === "open").slice().reverse();
  const loud = list.filter((n) => n.priority !== "low");
  const quiet = list.filter((n) => n.priority === "low").slice(0, 2);
  return { loud, quiet };
}

let audio = null;
/** A soft two-note chime so a nudge is heard even when the overlay is behind the call. */
export function chime(urgent = false) {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    const notes = urgent ? [880, 1175, 880] : [784, 1047];
    notes.forEach((freq, i) => {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      const at = audio.currentTime + i * 0.14;
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.12, at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.32);
      osc.connect(gain).connect(audio.destination);
      osc.start(at);
      osc.stop(at + 0.34);
    });
  } catch {
    /* no audio device: the visual pulse still shows */
  }
}
