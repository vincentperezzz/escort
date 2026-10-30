import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def speak(text: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = dest.with_suffix(".raw.wav")
    subprocess.run(
        ["espeak-ng", "-v", "en-us", "-s", "148", "-w", str(raw), text],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(raw),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    raw.unlink(missing_ok=True)


def font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def waiting() -> Image.Image:
    image = Image.new("RGB", (960, 540), (18, 22, 28))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 960, 56), fill=(12, 16, 22))
    draw.text((24, 16), "Teams  ·  BayaniPay kickoff", fill=(220, 224, 230), font=font(22))
    draw.text((180, 240), "Waiting for a screen share", fill=(150, 158, 170), font=font(28))
    return image


def dashboard(highlight: bool) -> Image.Image:
    image = Image.new("RGB", (960, 540), (20, 24, 32))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 960, 56), fill=(12, 16, 22))
    draw.rectangle((0, 56, 72, 540), fill=(16, 20, 28))
    draw.text((88, 16), "Teams  ·  BayaniPay is sharing", fill=(230, 226, 214), font=font(20))
    draw.rectangle((96, 80, 900, 500), fill=(32, 28, 24))
    draw.text((120, 96), "PROCESSING FEE", fill=(180, 160, 120), font=font(16))
    draw.rectangle((140, 128, 420, 210), fill=(255, 196, 60))
    draw.text((156, 146), "2.4%", fill=(28, 18, 8), font=font(42))
    draw.text((120, 250), "Payouts today", fill=(210, 200, 180), font=font(18))
    draw.text((120, 290), "OFW wallet  ·  14 transfers  ·  on time", fill=(160, 150, 140), font=font(16))
    draw.text((120, 330), "InstaPay cash-in  ·  queued for Phase 2", fill=(160, 150, 140), font=font(16))
    if highlight:
        draw.rectangle((96, 430, 900, 500), fill=(180, 70, 40))
        draw.text((120, 452), "FINANCE WANTS 2.9%", fill=(255, 236, 214), font=font(28))
    return image


def main() -> None:
    scenario = json.loads((ROOT / "fixtures" / "scenario.json").read_text(encoding="utf-8"))
    for item in scenario["utterances"]:
        speak(item["text"], ROOT / item["audio"])
    speak(scenario["eval"]["text"], ROOT / scenario["eval"]["audio"])
    (ROOT / scenario["eval"]["reference"]).write_text(scenario["eval"]["text"] + "\n", encoding="utf-8")
    frames = ROOT / "fixtures" / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    waiting().save(frames / "01-waiting.png")
    dashboard(False).save(frames / "02-share-fee.png")
    dashboard(True).save(frames / "03-share-highlight.png")


if __name__ == "__main__":
    main()
