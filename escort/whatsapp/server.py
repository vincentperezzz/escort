from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from escort.paths import repo_root
from escort.store import MeetingStore
from escort.whatsapp.app import create_app
from escort.whatsapp.config import load_whatsapp_config
from escort.whatsapp.demo import print_demo
from escort.whatsapp.graph import GraphClient
from escort.whatsapp.service import WhatsAppService


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="escort whatsapp")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8748)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    if args.demo:
        print_demo(repo_root() / "var" / "whatsapp")
        return
    config = load_whatsapp_config()
    store = MeetingStore(repo_root() / "var" / "whatsapp")
    service = WhatsAppService(config, GraphClient(config), store)
    uvicorn.run(create_app(service, poll=True), host=args.host, port=args.port)


def build_from_env(media_dir: Path | None = None) -> WhatsAppService:
    config = load_whatsapp_config()
    folder = media_dir or (repo_root() / "var" / "whatsapp")
    return WhatsAppService(config, GraphClient(config), MeetingStore(folder))
