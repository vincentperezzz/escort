from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from taglish_listener.pipeline import Pipeline
from taglish_listener.store import MeetingStore


class AppState:
    def __init__(self, store: MeetingStore) -> None:
        self.store = store
        self.pipeline: Pipeline | None = None


def create_server(state: AppState) -> MCPServer:
    mcp = MCPServer(
        "taglish-listener",
        instructions=(
            "Taglish scope guard for a client call. Me is the user mic. Them is system audio. "
            "get_live_context returns the transcript window, recent cards, and screen deltas. "
            "get_latest_frame returns the newest changed screen image. "
            "get_cards returns PRD_CONFLICT, COMMITMENT, and SUGGESTED_REPLY cards with latency_ms. "
            "push_suggestion puts a line on the side panel. "
            "The bundled demo is a staged fictional BayaniPay meeting. Do not invent a PRD section."
        ),
    )

    @mcp.tool(structured_output=False)
    def get_live_context(window: int = 12) -> dict:
        """Transcript window, recent scope cards, and screen deltas that actually changed."""
        snap = state.store.snapshot()
        size = max(1, min(window, 50))
        return {
            "title": snap["title"],
            "mode": snap["mode"],
            "consent": snap["consent"],
            "transcript": snap["utterances"][-size:],
            "partials": snap["partials"],
            "cards": snap["cards"][-size:],
            "screen_deltas": snap["frames"][-8:],
            "skipped_frames": snap["skipped_frames"],
            "warnings": snap["warnings"],
            "backends": {
                "transcriber": snap["transcriber"],
                "spark": snap["spark"],
                "segmenter": snap["segmenter"],
            },
        }

    @mcp.tool(structured_output=False)
    def get_latest_frame() -> Image:
        """Newest screen frame that passed the change detector, as an image."""
        frame = state.store.latest_frame()
        if frame is None:
            raise ToolError("No screen frame yet")
        path = Path(frame.path)
        if not path.is_file():
            raise ToolError("Latest frame file is missing")
        return Image(path=path)

    @mcp.tool(structured_output=False)
    def get_cards(limit: int = 20) -> dict:
        """Scope cards. Each one has type, timestamp, and latency_ms from the triggering utterance."""
        cards = state.store.snapshot()["cards"]
        size = max(1, min(limit, 100))
        return {"cards": cards[-size:]}

    @mcp.tool(structured_output=False)
    def push_suggestion(
        text: str,
        title: str = "Say this",
        card_type: str = "SUGGESTED_REPLY",
        prd_section: str | None = None,
        who: str | None = None,
        what: str | None = None,
        utterance_id: str | None = None,
    ) -> dict:
        """Push a card onto the live side panel. card_type is PRD_CONFLICT, COMMITMENT, or SUGGESTED_REPLY."""
        if state.pipeline is None:
            raise ToolError("Listener pipeline is not running")
        try:
            card = state.pipeline.push_suggestion(
                text,
                title=title,
                card_type=card_type,
                prd_section=prd_section,
                who=who,
                what=what,
                utterance_id=utterance_id,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return card.to_dict()

    @mcp.custom_route("/panel/health", methods=["GET"])
    async def panel_health(_request: Request) -> Response:
        return JSONResponse({"ok": True, "mode": state.store.mode})

    @mcp.custom_route("/panel/state", methods=["GET"])
    async def panel_state(_request: Request) -> Response:
        response = JSONResponse(state.store.snapshot())
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    @mcp.custom_route("/panel/media/{name}", methods=["GET"])
    async def panel_media(request: Request) -> Response:
        path = state.store.resolve_media(request.path_params["name"])
        if path is None:
            return Response(status_code=404)
        return FileResponse(path)

    return mcp
