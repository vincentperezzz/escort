from __future__ import annotations


def list_audio_devices() -> list[dict[str, str]]:
    try:
        import pyaudiowpatch as pyaudio
    except Exception:
        return [
            {"id": "default-mic", "name": "Default microphone", "kind": "mic"},
            {"id": "default-loop", "name": "Default speakers (loopback)", "kind": "loopback"},
        ]
    pa = pyaudio.PyAudio()
    devices: list[dict[str, str]] = []
    try:
        host = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        for index in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(index)
            kind = "mic" if int(info.get("maxInputChannels") or 0) > 0 else "output"
            if info.get("isLoopbackDevice"):
                kind = "loopback"
            if int(info.get("hostApi") or -1) != int(host.get("index") or -1) and kind != "loopback":
                continue
            devices.append({"id": str(index), "name": str(info.get("name") or f"Device {index}"), "kind": kind})
    finally:
        pa.terminate()
    if not devices:
        return [
            {"id": "default-mic", "name": "Default microphone", "kind": "mic"},
            {"id": "default-loop", "name": "Default speakers (loopback)", "kind": "loopback"},
        ]
    return devices
