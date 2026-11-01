from __future__ import annotations

import logging

TARGET_MS = 5000
log = logging.getLogger("escort.whatsapp")


def breakdown(
    *,
    card_id: str,
    kind: str,
    webhook_ms: float,
    muse_ms: float,
    graph_ms: float,
) -> dict:
    e2e_ms = round(webhook_ms + muse_ms + graph_ms, 1)
    record = {
        "card_id": card_id,
        "kind": kind,
        "webhook_ms": round(webhook_ms, 1),
        "muse_ms": round(muse_ms, 1),
        "graph_ms": round(graph_ms, 1),
        "e2e_ms": e2e_ms,
        "target_ms": TARGET_MS,
        "within_target": e2e_ms <= TARGET_MS,
    }
    line = (
        f"whatsapp push {card_id} {kind} "
        f"webhook_ms={record['webhook_ms']} muse_ms={record['muse_ms']} "
        f"graph_ms={record['graph_ms']} e2e_ms={record['e2e_ms']} "
        f"target_ms={TARGET_MS} within_target={str(record['within_target']).lower()}"
    )
    log.info(line)
    print(line, flush=True)
    return record
