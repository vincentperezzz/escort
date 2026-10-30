from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass

import httpx
from PIL import Image

from taglish_listener.models import Box, DeicticHint
from taglish_listener.sam.fallback import crop_share
from taglish_listener.sam.parse import crop_box, extract_text, parse_boxes, pick_box
from taglish_listener.settings import Settings


class SamError(RuntimeError):
    pass


@dataclass
class CropResult:
    image: Image.Image
    source: str
    region: str
    noun: str
    box: Box | None


def build_sam_request(*, model: str, noun: str, image: Image.Image) -> dict:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {
        "model": model,
        "stream": False,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": noun},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"},
                ],
            }
        ],
    }


def boxes_from_response(body: str, content_type: str) -> list[Box]:
    if "text/event-stream" in content_type or body.lstrip().startswith("data:"):
        blobs: list[str] = []
        for line in body.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                blobs.append(extract_text(json.loads(raw)))
            except json.JSONDecodeError:
                blobs.append(raw)
        return parse_boxes("\n".join(blobs))
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return parse_boxes(body)
    return parse_boxes(extract_text(payload) + "\n" + body)


class MuseSam:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.api_key:
            raise SamError("MODEL_API_KEY is not set")
        self.settings = settings
        self.name = settings.sam_model
        self._client = client or httpx.Client(timeout=60)

    def boxes(self, image: Image.Image, noun: str) -> list[Box]:
        request = build_sam_request(model=self.settings.sam_model, noun=noun, image=image)
        response = self._client.post(
            f"{self.settings.base_url}/responses",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            json=request,
        )
        response.raise_for_status()
        return boxes_from_response(response.text, response.headers.get("content-type", ""))


class DeicticSegmenter:
    def __init__(self, sam: MuseSam | None, on_error=None) -> None:
        self.sam = sam
        self.on_error = on_error
        self.name = sam.name if sam is not None else "share-crop-fallback"

    def crop(self, image: Image.Image, hint: DeicticHint) -> CropResult:
        if self.sam is not None:
            try:
                boxes = self.sam.boxes(image, hint.noun)
                chosen = pick_box(boxes, hint.region)
                if chosen is not None:
                    self.name = self.sam.name
                    return CropResult(
                        image=crop_box(image, chosen),
                        source=self.sam.name,
                        region=hint.region,
                        noun=hint.noun,
                        box=chosen,
                    )
            except Exception as exc:
                if self.on_error:
                    self.on_error(f"SAM 3.1 failed, cropping the share area: {exc}")
        self.name = "share-crop-fallback"
        return CropResult(
            image=crop_share(image, hint.region),
            source="share-crop-fallback",
            region=hint.region,
            noun=hint.noun,
            box=None,
        )
