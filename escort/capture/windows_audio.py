from __future__ import annotations

from escort.audio_util import pcm_to_mono_24k

FRAMES = 1024


class CaptureUnavailable(RuntimeError):
    pass


def choose_loopback_device(devices: list[dict], default_output_index: int, preferred: int | None = None) -> dict:
    if preferred is not None:
        for item in devices:
            if item["index"] == preferred and item.get("isLoopbackDevice"):
                return item
    default = next(item for item in devices if item["index"] == default_output_index)
    if default.get("isLoopbackDevice"):
        return default
    name = default.get("name") or ""
    for item in devices:
        if item.get("isLoopbackDevice") and name and name in (item.get("name") or ""):
            return item
    raise CaptureUnavailable("No WASAPI loopback device matches the default speakers")


def choose_mic_device(devices: list[dict], default_input_index: int, preferred: int | None = None) -> dict:
    if preferred is not None:
        for item in devices:
            if item["index"] == preferred and int(item.get("maxInputChannels") or 0) > 0 and not item.get("isLoopbackDevice"):
                return item
    for item in devices:
        if item["index"] == default_input_index:
            return item
    raise CaptureUnavailable("No default microphone device")


def parse_device_id(value: object) -> int | None:
    """Settings store PyAudio indices as strings; 'default-*' or '' mean the system default."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


class WindowsAudioCapture:
    def __init__(self) -> None:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as exc:
            raise CaptureUnavailable(
                "PyAudioWPatch is required on Windows for separate Me (mic) and Them (WASAPI loopback) streams. "
                "pip install 'escort[windows]'"
            ) from exc
        self._pyaudio = pyaudio
        self._pa = pyaudio.PyAudio()
        self._streams: list[tuple[str, object, int, int]] = []
        self._states: dict[str, tuple | None] = {"Me": None, "Them": None}
        self.devices: dict[str, str] = {}

    def open(self, mic_index: int | None = None, loopback_index: int | None = None) -> None:
        host = self._pa.get_host_api_info_by_type(self._pyaudio.paWASAPI)
        devices = []
        for index in range(self._pa.get_device_count()):
            info = dict(self._pa.get_device_info_by_index(index))
            info["index"] = index
            devices.append(info)
        mic = choose_mic_device(devices, int(host["defaultInputDevice"]), mic_index)
        loopback = choose_loopback_device(devices, int(host["defaultOutputDevice"]), loopback_index)
        self.devices = {"Me": str(mic.get("name") or mic["index"]), "Them": str(loopback.get("name") or loopback["index"])}
        self._streams = [self._open_input("Me", mic)]
        try:
            self._streams.append(self._open_input("Them", loopback))
        except Exception:
            self.close()
            raise

    def _open_input(self, speaker: str, device: dict) -> tuple[str, object, int, int]:
        channels = int(device["maxInputChannels"] or 1)
        rate = int(device["defaultSampleRate"])
        stream = self._pa.open(
            format=self._pyaudio.paInt16,
            channels=channels,
            rate=rate,
            frames_per_buffer=FRAMES,
            input=True,
            input_device_index=int(device["index"]),
        )
        return speaker, stream, rate, channels

    def read(self, speaker: str, block: bool = True) -> bytes:
        """Read ~1024 frames as 24 kHz mono PCM. Non-blocking reads return b'' when not enough is buffered.

        WASAPI loopback delivers nothing while the speakers are silent, so a blocking
        read on 'Them' would also starve the mic stream in a single reader loop.
        """
        name, stream, rate, channels = next(item for item in self._streams if item[0] == speaker)
        if not block and stream.get_read_available() < FRAMES:
            return b""
        raw = stream.read(FRAMES, exception_on_overflow=False)
        pcm, state = pcm_to_mono_24k(raw, 2, channels, rate, self._states[name])
        self._states[name] = state
        return pcm

    def speakers(self) -> list[str]:
        return [name for name, _stream, _rate, _channels in self._streams]

    def close(self) -> None:
        for _name, stream, _rate, _channels in self._streams:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        self._streams = []
        try:
            self._pa.terminate()
        except Exception:
            pass
