from __future__ import annotations

from taglish_listener.audio_util import pcm_to_mono_24k


class CaptureUnavailable(RuntimeError):
    pass


def choose_loopback_device(devices: list[dict], default_output_index: int) -> dict:
    default = next(item for item in devices if item["index"] == default_output_index)
    if default.get("isLoopbackDevice"):
        return default
    name = default.get("name") or ""
    for item in devices:
        if item.get("isLoopbackDevice") and name and name in (item.get("name") or ""):
            return item
    raise CaptureUnavailable("No WASAPI loopback device matches the default speakers")


def choose_mic_device(devices: list[dict], default_input_index: int) -> dict:
    for item in devices:
        if item["index"] == default_input_index:
            return item
    raise CaptureUnavailable("No default microphone device")


class WindowsAudioCapture:
    def __init__(self) -> None:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as exc:
            raise CaptureUnavailable(
                "PyAudioWPatch is required on Windows for separate Me (mic) and Them (WASAPI loopback) streams. "
                "pip install 'taglish-listener[windows]'"
            ) from exc
        self._pyaudio = pyaudio
        self._pa = pyaudio.PyAudio()
        self._streams: list[tuple[str, object, int, int]] = []
        self._states: dict[str, tuple | None] = {"Me": None, "Them": None}

    def open(self) -> None:
        host = self._pa.get_host_api_info_by_type(self._pyaudio.paWASAPI)
        devices = []
        for index in range(self._pa.get_device_count()):
            info = dict(self._pa.get_device_info_by_index(index))
            info["index"] = index
            devices.append(info)
        mic = choose_mic_device(devices, int(host["defaultInputDevice"]))
        loopback = choose_loopback_device(devices, int(host["defaultOutputDevice"]))
        self._streams = [
            self._open_input("Me", mic),
            self._open_input("Them", loopback),
        ]

    def _open_input(self, speaker: str, device: dict) -> tuple[str, object, int, int]:
        channels = int(device["maxInputChannels"] or 1)
        rate = int(device["defaultSampleRate"])
        stream = self._pa.open(
            format=self._pyaudio.paInt16,
            channels=channels,
            rate=rate,
            frames_per_buffer=1024,
            input=True,
            input_device_index=int(device["index"]),
        )
        return speaker, stream, rate, channels

    def read(self, speaker: str) -> bytes:
        name, stream, rate, channels = next(item for item in self._streams if item[0] == speaker)
        raw = stream.read(1024, exception_on_overflow=False)
        pcm, state = pcm_to_mono_24k(raw, 2, channels, rate, self._states[name])
        self._states[name] = state
        return pcm

    def speakers(self) -> list[str]:
        return [name for name, _stream, _rate, _channels in self._streams]

    def close(self) -> None:
        for _name, stream, _rate, _channels in self._streams:
            stream.stop_stream()
            stream.close()
        self._pa.terminate()
