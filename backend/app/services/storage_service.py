"""Private file storage for uploaded invoices and product photos.

Files are stored outside any public web root under
`<UPLOAD_DIR>/<business_id>/<kind>/<uuid>.<ext>` and are only served through
authenticated, tenant-checked API endpoints. The content type is derived from
the file's magic bytes, never from the client-provided header or filename.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from PIL import Image

from app.core.config import get_settings
from app.core.errors import AppError, ValidationFailed

logger = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 60_000_000  # reject decompression bombs

_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"%PDF-", "application/pdf", "pdf"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
]


@dataclass
class StoredFile:
    relative_path: str
    content_type: str
    size: int
    sha256: str
    original_filename: str


def sniff_content_type(data: bytes) -> tuple[str, str] | None:
    for signature, content_type, ext in _SIGNATURES:
        if data.startswith(signature):
            return content_type, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


async def read_upload(upload: UploadFile) -> bytes:
    limit = get_settings().max_upload_bytes
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise AppError(f"File is larger than {get_settings().max_upload_mb} MB", 413)
        chunks.append(chunk)
    if size == 0:
        raise ValidationFailed("The uploaded file is empty")
    return b"".join(chunks)


def validate_content(data: bytes, allowed: set[str]) -> tuple[str, str]:
    sniffed = sniff_content_type(data)
    if sniffed is None or sniffed[0] not in allowed:
        readable = ", ".join(sorted(a.split("/")[1].upper() for a in allowed))
        raise ValidationFailed(f"Unsupported file type. Allowed: {readable}")
    content_type, ext = sniffed
    if content_type.startswith("image/"):
        import io

        try:
            with Image.open(io.BytesIO(data)) as img:
                img.verify()
        except Exception as exc:  # includes DecompressionBombError
            raise ValidationFailed("The image file is corrupted or too large to process") from exc
    else:
        import pypdfium2 as pdfium

        try:
            pdf = pdfium.PdfDocument(data)
        except Exception as exc:
            raise ValidationFailed("The PDF is corrupted or password-protected") from exc
        pages = len(pdf)
        pdf.close()
        if pages == 0:
            raise ValidationFailed("The PDF has no pages")
        if pages > get_settings().max_pdf_pages:
            raise ValidationFailed(f"The PDF has {pages} pages; the limit is {get_settings().max_pdf_pages}")
    return content_type, ext


def safe_filename(name: str | None) -> str:
    name = Path(name or "upload").name  # drop any client-supplied directories
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "._- ").strip()
    return (cleaned or "upload")[:200]


def store_bytes(business_id: uuid.UUID, kind: str, data: bytes, ext: str) -> str:
    root = get_settings().upload_dir.resolve()
    relative = Path(str(business_id)) / kind / f"{uuid.uuid4().hex}.{ext}"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative.as_posix()


def resolve_path(relative_path: str) -> Path:
    root = get_settings().upload_dir.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise AppError("Invalid file path", 400)
    return path


def delete_stored(relative_path: str) -> None:
    """Remove a stored upload. Called after the database commit, so a failure only leaves an orphaned file."""
    try:
        resolve_path(relative_path).unlink(missing_ok=True)
    except (OSError, AppError) as exc:
        logger.warning("Could not delete stored file %s: %s", relative_path, exc)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
