import socket
import time

from escort.desktop.single_instance import SingleInstance, escort_protocol_urls


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_escort_protocol_urls_from_argv():
    assert escort_protocol_urls(["--web", "escort://oauth/notion?code=1"]) == ["escort://oauth/notion?code=1"]


def test_single_instance_forwards_to_primary(monkeypatch):
    port = _free_port()
    monkeypatch.setenv("ESCORT_IPC_PORT", str(port))
    received: list[str] = []
    primary = SingleInstance()
    assert primary.try_acquire()
    primary.start_listener(received.append)
    secondary = SingleInstance()
    assert not secondary.try_acquire()
    assert secondary.forward("escort://oauth/notion?code=abc&state=xyz")
    deadline = time.time() + 2.0
    while time.time() < deadline and not received:
        time.sleep(0.05)
    primary.release()
    assert received == ["escort://oauth/notion?code=abc&state=xyz"]
