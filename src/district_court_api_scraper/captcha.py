from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional dependency behavior
    cv2 = None

try:
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover - optional dependency behavior
    pytesseract = None


@dataclass(slots=True)
class CaptchaAttemptPolicy:
    local_attempts: int = 5
    gemini_attempts: int = 5

    @property
    def max_attempts(self) -> int:
        return self.local_attempts + self.gemini_attempts

    def stage_for_attempt(self, attempt_number: int) -> str:
        if attempt_number <= self.local_attempts:
            return "OCR"
        if attempt_number <= self.max_attempts:
            return "GEMINI"
        return "INCOMPLETE"


class CaptchaSolver:
    GEMINI_MODEL = "gemini-2.5-flash-lite"
    GEMINI_PROMPT = (
        "Read the captcha image and return only the captcha text. "
        "Return only alphanumeric characters, no punctuation."
    )

    def __init__(self, gemini_api_key: str | None = None):
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        self._gemini_client: Any | None = None
        self.last_stage: str | None = None
        self.last_raw_text: str | None = None

    def solve_captcha(
        self,
        image_bytes: bytes,
        *,
        stage: str | None = None,
        variant_index: int = 0,
    ) -> str | None:
        target_stage = (stage or "OCR").upper()
        self.last_stage = target_stage
        if target_stage == "OCR":
            token = self._solve_with_ocr(image_bytes, variant_index=variant_index)
            self.last_raw_text = token
            return token
        if target_stage == "GEMINI":
            token = self._solve_with_gemini(image_bytes)
            self.last_raw_text = token
            return token
        self.last_raw_text = None
        return None

    def _solve_with_ocr(self, image_bytes: bytes, *, variant_index: int) -> str | None:
        if pytesseract is None:
            return None
        image = _load_image(image_bytes)
        processed = _preprocess_variant(image, variant_index)
        config = "--oem 3 --psm 8 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyz0123456789"
        try:
            text = pytesseract.image_to_string(processed, config=config)
        except Exception:
            return None
        return _normalize_token(text)

    def _solve_with_gemini(self, image_bytes: bytes) -> str | None:
        client = self._get_gemini_client()
        if client is None:
            return None
        mime = "image/png"
        if image_bytes[:2] == b"\xff\xd8":
            mime = "image/jpeg"
        try:
            from google.genai import types  # type: ignore

            response = client.models.generate_content(
                model=self.GEMINI_MODEL,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=self.GEMINI_PROMPT),
                            types.Part.from_bytes(data=image_bytes, mime_type=mime),
                        ],
                    )
                ],
            )
            return _normalize_token(response.text or "")
        except Exception:
            return None

    def _get_gemini_client(self) -> Any | None:
        if self._gemini_client is not None:
            return self._gemini_client
        if not self.gemini_api_key:
            return None
        try:
            from google import genai  # type: ignore

            self._gemini_client = genai.Client(api_key=self.gemini_api_key)
        except Exception:
            self._gemini_client = None
        return self._gemini_client


def _load_image(image_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def _preprocess_variant(image: Image.Image, variant_index: int) -> Image.Image:
    variant = variant_index % 5
    gray = ImageOps.grayscale(image)
    if variant == 0:
        return gray.point(lambda p: 255 if p > 150 else 0)
    if variant == 1:
        return ImageOps.autocontrast(gray.filter(ImageFilter.MedianFilter(size=3)))
    if variant == 2:
        inv = ImageOps.invert(gray)
        return inv.point(lambda p: 255 if p > 120 else 0)
    if variant == 3:
        enlarged = gray.resize((gray.width * 2, gray.height * 2), Image.Resampling.BICUBIC)
        return enlarged.point(lambda p: 255 if p > 145 else 0)
    if cv2 is None:
        return gray
    arr = np.array(gray)
    arr = cv2.GaussianBlur(arr, (3, 3), 0)
    arr = cv2.adaptiveThreshold(arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    return Image.fromarray(arr)


def _normalize_token(text: str) -> str | None:
    token = re.sub(r"[^A-Za-z0-9]", "", (text or "")).lower().strip()
    if len(token) < 5:
        return None
    if len(token) > 6:
        match = re.search(r"[a-z0-9]{6}", token)
        token = match.group(0) if match else token[:6]
    return token

