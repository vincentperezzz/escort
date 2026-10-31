from __future__ import annotations

import threading
from pathlib import Path

from escort.audio_util import pcm_to_mono_24k, write_wav
from escort.paths import repo_root

SCRIPTS = (
    "Hi, thanks for jumping on. Pwede ba nating i-pull yung payment integration into Phase 1? Kailangan namin yung card checkout bago mag-launch.",
    "Sharing my screen na. Yung nasa taas, i-adjust natin yung number. Gusto ng finance namin na 2.9 percent. Yung QR at GCash, sama na natin.",
    "Noted. I-confirm ko yung fee change kay finance. I-check ko muna yung PRD, yung KYC, at yung BayaniPay API.",
)


def default_out_dir() -> Path:
    return repo_root() / "fixtures" / "eval" / "real"


def record_scripts(out_dir: Path | None = None) -> list[Path]:
    folder = out_dir or default_out_dir()
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    print("Read each line into the mic. Audio is saved as 24 kHz mono WAV plus the reference text.")
    print("Press Enter to start, read the line, then press Enter to stop.")
    for index, script in enumerate(SCRIPTS, start=1):
        print()
        print(f"Script {index} of {len(SCRIPTS)}")
        print(script)
        input("Enter to start... ")
        wav_path = folder / f"{index:02d}.wav"
        _record_mic(wav_path)
        text_path = folder / f"{index:02d}.txt"
        text_path.write_text(script + "\n", encoding="utf-8")
        written.append(wav_path)
        print(f"Saved {wav_path.name} and {text_path.name}")
    print()
    print("Rerun the gate with:")
    print(f"python -m escort eval --backend muse --audio {folder / '01.wav'} --reference {folder / '01.txt'}")
    return written


def _record_mic(path: Path) -> None:
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise SystemExit(
            "Recording needs PyAudioWPatch on Windows. pip install 'escort[windows]'"
        ) from exc
    pa = pyaudio.PyAudio()
    stream = None
    try:
        host = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        device = pa.get_device_info_by_index(int(host["defaultInputDevice"]))
        rate = int(device["defaultSampleRate"])
        channels = max(1, int(device["maxInputChannels"] or 1))
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            frames_per_buffer=1024,
            input_device_index=int(device["index"]),
        )
        stop = threading.Event()

        def wait_for_enter() -> None:
            input()
            stop.set()

        threading.Thread(target=wait_for_enter, daemon=True).start()
        print("Recording. Enter to stop.")
        chunks: list[bytes] = []
        state = None
        while not stop.is_set():
            raw = stream.read(1024, exception_on_overflow=False)
            pcm, state = pcm_to_mono_24k(raw, 2, channels, rate, state)
            chunks.append(pcm)
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        pa.terminate()
    write_wav(path, b"".join(chunks), 24000)


def main() -> None:
    record_scripts()
