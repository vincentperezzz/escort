import json

from escort.context.sources.google_auth import GoogleAuth


def _write(path, data, bom):
    raw = json.dumps(data).encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + raw)


def test_client_file_with_utf8_bom_is_read(tmp_path):
    path = tmp_path / "google_client.json"
    _write(path, {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "s3cret", "api_key": "AIza-key"}, bom=True)
    assert GoogleAuth._read_client_file(path) == ("123-abc.apps.googleusercontent.com", "s3cret", "AIza-key")


def test_client_file_without_bom_is_read(tmp_path):
    path = tmp_path / "google_client.json"
    _write(path, {"client_id": "cid", "client_secret": "sec"}, bom=False)
    assert GoogleAuth._read_client_file(path) == ("cid", "sec", "")


def test_broken_client_file_yields_nothing(tmp_path):
    path = tmp_path / "google_client.json"
    path.write_text("{not json", encoding="utf-8")
    assert GoogleAuth._read_client_file(path) == ("", "", "")
