"""Barcode decoding with OpenCV (EAN-8/13, UPC-A/E) and QR codes.

A barcode is treated purely as a product identifier. It is never used as a
source of expiry information.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class BarcodeResult:
    value: str
    format: str
    checksum_valid: bool | None


def gtin_checksum_valid(code: str) -> bool:
    """Validate the check digit of EAN-8, UPC-A (12), EAN-13 and GTIN-14."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(d) for d in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check


def decode_barcodes(image: Image.Image) -> list[BarcodeResult]:
    array = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    results: list[BarcodeResult] = []
    try:
        detector = cv2.barcode.BarcodeDetector()
        ok, infos, types, _ = detector.detectAndDecodeWithType(array)
        if ok:
            for info, kind in zip(infos, types, strict=False):
                if info:
                    results.append(
                        BarcodeResult(info, str(kind), gtin_checksum_valid(info) if info.isdigit() else None)
                    )
    except cv2.error as exc:
        logger.debug("barcode detection failed: %s", exc)
    try:
        qr_value, _, _ = cv2.QRCodeDetector().detectAndDecode(array)
        if qr_value:
            results.append(BarcodeResult(qr_value[:64], "QR_CODE", None))
    except cv2.error as exc:
        logger.debug("QR detection failed: %s", exc)
    # Prefer valid retail barcodes first.
    results.sort(key=lambda r: (r.checksum_valid is not True, r.format == "QR_CODE"))
    return results
