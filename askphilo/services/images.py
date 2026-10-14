"""Image attachments: check, shrink and split uploads for base64 storage in Firestore.

A Firestore document holds at most 1 MiB and Claude reads images at about
1.15 megapixels anyway, so uploads (up to 100 MB per message) are re-encoded
here instead of being stored as sent:

  * the file must decode as JPEG/PNG/WebP/GIF (extensions and MIME types are
    not trusted); EXIF orientation is applied and all metadata (GPS, camera,
    …) is dropped by re-encoding
  * width ≤ 900 px; tall chat captures are cut into parts ≤ 1300 px high
    (≈ 1.17 MP each, what Claude reads without further scaling) so the text
    stays legible, at most 4 parts per image
  * each part is a JPEG ≤ 700 KB, i.e. ≤ ~933 KB once base64-encoded, which
    fits one Firestore document with room for the other fields
"""

from __future__ import annotations

import io
import math
import threading

from PIL import Image, ImageOps

MAX_IMAGES = 5  # per message; each photo uses one more daily question
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # per photo (each is uploaded on its own)
MAX_PIXELS = 40_000_000  # decoded-size guard: Render's free instance has 512 MB RAM
MAX_WIDTH = 900
PART_HEIGHT = 1300
MAX_PARTS = 4
MAX_PART_BYTES = 700_000
JPEG_QUALITIES = (85, 75, 65, 55)
FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "GIF"}

MSG_TOO_MANY = f"사진은 한 번에 {MAX_IMAGES}장까지 보낼 수 있어요."
MSG_TOO_BIG = "사진은 한 장에 100MB까지 보낼 수 있어요."
MSG_NOT_IMAGE = "사진 파일(JPG, PNG, WEBP, GIF)만 보낼 수 있어요."
MSG_TOO_MANY_PIXELS = "사진 해상도가 너무 커요. 조금 줄여서 보내 주세요."

# Sizes are checked here against MAX_PIXELS (JPEGs after draft-mode reduction),
# so Pillow's own header check — which would refuse big phone photos before
# draft mode can shrink them — is turned off.
Image.MAX_IMAGE_PIXELS = None
# One decode at a time keeps peak memory bounded on the single small worker.
_DECODE = threading.Semaphore(1)


class ImageError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


def _open(fileobj) -> Image.Image:
    try:
        img = Image.open(fileobj)
    except Exception:  # noqa: BLE001 — anything Pillow can't identify is not an image
        raise ImageError("not_image", MSG_NOT_IMAGE) from None
    if img.format not in FORMATS:
        raise ImageError("not_image", MSG_NOT_IMAGE)
    if img.format in ("JPEG", "MPO"):
        w, h = img.size
        # Decode big photos at 1/2, 1/4 or 1/8 scale straight away (cheap, low memory).
        img.draft("RGB", (MAX_WIDTH, max(1, round(h * MAX_WIDTH / w))))
    w, h = img.size
    if w * h > MAX_PIXELS:
        raise ImageError("too_many_pixels", MSG_TOO_MANY_PIXELS)
    return img


def _flatten(img: Image.Image) -> Image.Image:
    if getattr(img, "is_animated", False):
        img.seek(0)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return img.convert("RGB")


def _encode(part: Image.Image) -> bytes:
    for quality in JPEG_QUALITIES:
        buf = io.BytesIO()
        part.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        if buf.tell() <= MAX_PART_BYTES:
            return buf.getvalue()
    smaller = part.resize((max(1, part.width * 3 // 4), max(1, part.height * 3 // 4)), Image.LANCZOS)
    return _encode(smaller)


def process_image(fileobj) -> list[dict]:
    """One upload → its parts, top to bottom: [{"data": jpeg bytes, "width", "height"}]."""
    with _DECODE:
        img = _flatten(_open(fileobj))
        w, h = img.size
        if w > MAX_WIDTH:
            img = img.resize((MAX_WIDTH, max(1, round(h * MAX_WIDTH / w))), Image.LANCZOS)
            w, h = img.size
        if h > PART_HEIGHT * MAX_PARTS:  # extremely long capture: shrink to fit the parts
            scale = PART_HEIGHT * MAX_PARTS / h
            img = img.resize((max(1, round(w * scale)), PART_HEIGHT * MAX_PARTS), Image.LANCZOS)
            w, h = img.size
        count = max(1, math.ceil(h / PART_HEIGHT))
        step = math.ceil(h / count)  # equal parts, no sliver at the end
        parts = []
        for top in range(0, h, step):
            piece = img.crop((0, top, w, min(h, top + step)))
            parts.append({"data": _encode(piece), "width": piece.width, "height": piece.height})
        return parts

