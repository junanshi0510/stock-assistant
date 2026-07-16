# -*- coding: utf-8 -*-
"""Decode, orient and strip metadata from OCR uploads before object storage."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_OCR_PIXELS = 50_000_000
MAX_OCR_DIMENSION = 8192
TARGET_OCR_DIMENSION = 4096
MAX_OCR_BYTES = 8 * 1024 * 1024


def normalize_ocr_image(data: bytes) -> tuple[bytes, dict[str, Any]]:
    if not data:
        raise ValueError("图片为空")
    if len(data) > MAX_OCR_BYTES:
        raise ValueError("图片不能超过 8MB")
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_OCR_PIXELS
    try:
        with Image.open(BytesIO(data)) as source:
            if getattr(source, "is_animated", False):
                source.seek(0)
            source_width, source_height = source.size
            if source_width < 5 or source_height < 5:
                raise ValueError("图片宽高不能小于 5 像素")
            if source_width * source_height > MAX_OCR_PIXELS:
                raise ValueError("图片总像素过大，请先裁剪无关区域")
            source.load()
            image = ImageOps.exif_transpose(source).copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("无法解码图片，请上传有效的 JPEG、PNG 或 WebP") from error
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    width, height = image.size
    if width > MAX_OCR_DIMENSION or height > MAX_OCR_DIMENSION:
        image.thumbnail(
            (TARGET_OCR_DIMENSION, TARGET_OCR_DIMENSION), Image.Resampling.LANCZOS
        )

    has_alpha = image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    output = BytesIO()
    if has_alpha:
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        image.save(output, format="PNG", optimize=True)
        content_type, extension = "image/png", "png"
    else:
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(
            output,
            format="JPEG",
            quality=92,
            optimize=True,
            progressive=True,
        )
        content_type, extension = "image/jpeg", "jpg"
    normalized = output.getvalue()
    if len(normalized) > MAX_OCR_BYTES:
        raise ValueError("图片规范化后仍超过 8MB，请先裁剪无关区域")
    return normalized, {
        "content_type": content_type,
        "extension": extension,
        "width": image.width,
        "height": image.height,
        "byte_size": len(normalized),
        "metadata_stripped": True,
    }
