# Proactive meeting agent

How Escort speaks up on its own during a call, decides which screen frames are worth a model call, keeps visuals for the notes, and writes summaries that grow with the meeting.

## 1. What triggers a nudge

Real-time meeting assistants converge on a short list of moments worth interrupting for: going off topic (Hedy, Tack), action items and owners (cue phrases like "I will"), clarifications and key points (LiveSuggest), and goal-alignment probes (arXiv 2504.01082). Research on these probes names timing and cognitive load as the main costs. The open problem in the field is telling a casual remark from a real commitment, which is why vagueness is judged by the model, not a regex, whenever a key is set.

Escort uses that list as a menu, not a script (`TRIGGER_MENU` in `escort/proactive.py`):

| Kind | When | Who decides |
| --- | --- | --- |
| `NO_MEETING` | Listening for 60 s (180 s if a meeting app is in front) and the other side hasn't spoken. The card offers the invite's join link, or asks whether the meeting moved and if the team chat has been checked. | Rules |
| `TIME_CHECK` | Under 5 min left in the slot, with open conflicts or undated promises | Rules |
| `WRONG_PROJECT` | The screen or talk belongs to another project or repo | Model (two confident looks), or rules when the window title names another project |
| `NOTED` | Quiet acknowledgement such as "Noted: Jessa's update on the RBAC scope" | Model; rules use commitment cards when there's no key |
| `CLARIFY` | Something agreed vaguely (no owner, date, number or definition), with the exact question to ask | Model; rules use undated or vague commitments |
| `QUESTION_FOR_YOU`, `OFF_TOPIC`, `HEADS_UP` | Asked directly, drift, risks or contradictions, and anything else the model judges useful | Model |

The model gets: the active project and every other project (id, name, description), the slot now under way and whether it has a join link, minutes left, the notes, the last 30 turns, cards and nudges already shown (including dismissed ones), the window in front (title and exe, read locally via Win32), and the newest *changed* frame. It returns a screen judgement (`matches` / `other_project` / `unrelated` / `unclear`), whether to keep the frame as a visual, and zero or more nudges. **Silence is the default.**

### Guard rails against nagging

- Confidence below 0.6 is dropped. `WRONG_PROJECT` needs two confident (≥ 0.75) looks in a row, or one very sure one (≥ 0.88).
- Each issue carries a dedupe key, so it is raised once per session.
- Cool-downs per priority: urgent 20 s, normal 30 s, low 12 s.
- Dismissing a kind snoozes that kind for 10 min.
- The model is called at most every 12 s. It runs only when something changed: a scene cut, a new window, a new card, or new talk (25 s gap), plus a 90 s heartbeat.
- Buttons are limited to what Escort can do, with values it trusts. `open_link` always uses the invite's own URL. `switch_project` only accepts a real project id. The API carries out a button from the stored nudge and never from request values.

### Being heard

A `normal` or `urgent` nudge sets the overlay to visible and expanded on the server. The Electron shell polls `/api/overlay` and resizes. The page also expands itself, plays a soft chime, and an urgent card rings twice. When the overlay is collapsed to the pill, the newest nudge title shows as a ticker in the pill. `NOTED` stays quiet: no expand, no chime.

### Switching project mid-call

`set_active_project` while listening files the recap under the new project and points the card brain at the new project's notes.

## 2. Which frames reach the model (the OBS idea)

A video codec sends a keyframe, then only what changed. Escort does the same at 1 fps (`FrameGate` in `escort/diff.py`):

- Grayscale, downscale, light blur, then a tile grid diffed against the **last kept** frame, not the previous grab.
- Cursor-sized changes (a couple of tiles) never count, so mouse movement and a blinking caret are ignored.
- A detected change is held until the screen **settles**. A scroll, fade or slide transition yields one frame, not a burst.
- Continuous motion (a video playing) is rate-limited to one frame every few seconds, like a keyframe interval.
- `last_change` and `scene_cut` tell the proactive engine whether this was a new slide or app (worth a look now) or a small region update.

The same slide left on screen therefore never triggers a model call, and neither does mouse movement.

## 3. Visuals in the notes

When the model judges a changed frame to be a chart, graph, diagram, wireframe, sketch, whiteboard or dense table, Escort keeps it with a one-line caption. It skips code editors, chat, video tiles and plain text slides. Near-duplicates are skipped by dHash distance, and a meeting keeps at most 40.

Visuals appear under **Visuals** in the recap and are copied into the saved session (`sessions/<id>_media/`), so they survive the next meeting's media wipe. Their captions feed the summary prompt. `delete_meeting_state` removes them along with everything else.

**Not done yet:** Docs and Notion exports carry the captions only, not the images. Embedding the images needs a publicly reachable image URL for Docs, or the Notion file-upload API.

## 4. Summaries that scale with the meeting

- There is no fixed bullet count. The model writes as many highlights as the content warrants: never padded for a quick call, never cut for a dense workshop.
- New sections: key takeaways, decisions and open questions. Each is shown only when it isn't empty.
- Long calls are now summarised in full. Previously only the last 20 k characters were sent, so a 2-hour meeting lost its first ~90 minutes. Now the transcript is split on turn boundaries into ~17 k-character parts, each part gets segment notes, and the final pass combines them with the last few minutes verbatim.

## Sources

- [Hedy: automatic suggestions](https://hedy.ai/pdf/posts/hedy-ai-automatic-suggestions.pdf)
- [Tack.im](https://www.producthunt.com/p/tack-6/tack-im)
- [LiveSuggest](https://alternativeto.net/software/livesuggest/about)
- [AI-assisted reflection probes in meetings (arXiv 2504.01082)](https://arxiv.org/html/2504.01082v2)
- [CTO Magazine on telling commitments from chatter](https://ctomagazine.com/ai-meeting-assistants-evolution-in-conversation-with-brian-farrell/)
- [Adaptive keyframe sampling for screen analysis](https://dev.to/romiteld/adaptive-keyframe-sampling-how-i-spend-a-frame-budget-like-its-cash-4c74)
- [Block-based slide change detection](https://dev.to/larryxue/how-i-built-a-browser-based-video-to-powerpoint-converter-ag0)
- [FFmpeg: compare against the last detected change](https://ffmpeg.org/pipermail/ffmpeg-trac/2016-July/035464.html)
