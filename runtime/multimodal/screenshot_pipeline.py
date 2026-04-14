"""Screenshot ingestion and normalization pipeline for multimodal workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScreenshotCapture:
    capture_id: str
    source_id: str
    source_type: str
    image_format: str
    content_hash: str
    byte_size: int
    width: int
    height: int
    captured_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class NormalizedSceneContract:
    scene_id: str
    capture_id: str
    source_id: str
    source_type: str
    image_format: str
    original_width: int
    original_height: int
    normalized_width: int
    normalized_height: int
    scale_ratio: float
    orientation: str
    content_hash: str
    normalized_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ScreenshotBatchSummary:
    total_items: int
    ingested_items: int
    duplicate_items: int
    captures: tuple[ScreenshotCapture, ...]


class ScreenshotIngestionError(ValueError):
    """Raised when screenshot ingestion inputs are invalid."""


class ScreenshotIngestionPipeline:
    """Ingests screenshot content and emits normalized scene contracts."""

    def __init__(self, *, max_bytes: int = 8 * 1024 * 1024, target_max_dimension: int = 1920) -> None:
        if max_bytes < 1:
            raise ScreenshotIngestionError("max_bytes must be at least 1")
        if target_max_dimension < 1:
            raise ScreenshotIngestionError("target_max_dimension must be at least 1")

        self.max_bytes = max_bytes
        self.target_max_dimension = target_max_dimension

    def ingest_file(
        self,
        file_path: str | Path,
        *,
        source_id: str | None = None,
        source_type: str = "screenshot_file",
        metadata: dict[str, Any] | None = None,
    ) -> ScreenshotCapture:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise ScreenshotIngestionError(f"File does not exist: {path}")

        image_bytes = path.read_bytes()
        merged_metadata = dict(metadata or {})
        merged_metadata["path"] = str(path)
        merged_metadata["file_name"] = path.name

        return self.ingest_bytes(
            image_bytes,
            source_id=source_id or str(path),
            source_type=source_type,
            metadata=merged_metadata,
        )

    def ingest_bytes(
        self,
        image_bytes: bytes,
        *,
        source_id: str,
        source_type: str = "screenshot_capture",
        metadata: dict[str, Any] | None = None,
        captured_at: str | None = None,
    ) -> ScreenshotCapture:
        if not isinstance(image_bytes, (bytes, bytearray)):
            raise ScreenshotIngestionError("image_bytes must be bytes")
        if not image_bytes:
            raise ScreenshotIngestionError("image_bytes cannot be empty")
        if len(image_bytes) > self.max_bytes:
            raise ScreenshotIngestionError(
                f"image_bytes exceeds max_bytes ({len(image_bytes)} > {self.max_bytes})"
            )

        image_format, width, height = _detect_image_format_and_dimensions(bytes(image_bytes))
        normalized_source_id = _normalize_required(source_id, "source_id")
        normalized_source_type = _normalize_required(source_type, "source_type").lower()
        content_hash = _hash_bytes(bytes(image_bytes))

        assigned_captured_at = _to_iso(captured_at) if captured_at is not None else _utc_now_iso()
        capture_id = _hash_text(f"{normalized_source_type}:{normalized_source_id}:{content_hash}")
        normalized_metadata = _normalize_metadata(metadata)

        return ScreenshotCapture(
            capture_id=capture_id,
            source_id=normalized_source_id,
            source_type=normalized_source_type,
            image_format=image_format,
            content_hash=content_hash,
            byte_size=len(image_bytes),
            width=width,
            height=height,
            captured_at=assigned_captured_at,
            metadata=normalized_metadata,
        )

    def normalize_capture(
        self,
        capture: ScreenshotCapture,
        *,
        target_max_dimension: int | None = None,
    ) -> NormalizedSceneContract:
        if not isinstance(capture, ScreenshotCapture):
            raise ScreenshotIngestionError("capture must be a ScreenshotCapture")

        max_dimension = self.target_max_dimension if target_max_dimension is None else int(target_max_dimension)
        if max_dimension < 1:
            raise ScreenshotIngestionError("target_max_dimension must be at least 1")

        normalized_width, normalized_height, scale_ratio = _normalize_dimensions(
            width=capture.width,
            height=capture.height,
            max_dimension=max_dimension,
        )
        orientation = _orientation(capture.width, capture.height)
        scene_id = _hash_text(
            f"{capture.capture_id}:{normalized_width}:{normalized_height}:{scale_ratio:.6f}"
        )

        metadata = dict(capture.metadata)
        metadata["aspect_ratio"] = round(capture.width / capture.height, 4)
        metadata["aspect_bucket"] = _aspect_bucket(capture.width, capture.height)

        return NormalizedSceneContract(
            scene_id=scene_id,
            capture_id=capture.capture_id,
            source_id=capture.source_id,
            source_type=capture.source_type,
            image_format=capture.image_format,
            original_width=capture.width,
            original_height=capture.height,
            normalized_width=normalized_width,
            normalized_height=normalized_height,
            scale_ratio=round(scale_ratio, 6),
            orientation=orientation,
            content_hash=capture.content_hash,
            normalized_at=_utc_now_iso(),
            metadata=metadata,
        )

    def ingest_and_normalize_bytes(
        self,
        image_bytes: bytes,
        *,
        source_id: str,
        source_type: str = "screenshot_capture",
        metadata: dict[str, Any] | None = None,
    ) -> NormalizedSceneContract:
        capture = self.ingest_bytes(
            image_bytes,
            source_id=source_id,
            source_type=source_type,
            metadata=metadata,
        )
        return self.normalize_capture(capture)

    def ingest_and_normalize_file(
        self,
        file_path: str | Path,
        *,
        source_id: str | None = None,
        source_type: str = "screenshot_file",
        metadata: dict[str, Any] | None = None,
    ) -> NormalizedSceneContract:
        capture = self.ingest_file(
            file_path,
            source_id=source_id,
            source_type=source_type,
            metadata=metadata,
        )
        return self.normalize_capture(capture)

    def ingest_batch(
        self,
        items: list[bytes] | tuple[bytes, ...],
        *,
        source_type: str = "screenshot_batch",
        deduplicate: bool = True,
    ) -> ScreenshotBatchSummary:
        captures: list[ScreenshotCapture] = []
        duplicate_items = 0
        seen_hashes: set[str] = set()

        for index, raw in enumerate(items):
            capture = self.ingest_bytes(
                raw,
                source_id=f"batch:{index + 1}",
                source_type=source_type,
                metadata={"batch_index": index + 1},
            )

            if deduplicate and capture.content_hash in seen_hashes:
                duplicate_items += 1
                continue

            seen_hashes.add(capture.content_hash)
            captures.append(capture)

        return ScreenshotBatchSummary(
            total_items=len(items),
            ingested_items=len(captures),
            duplicate_items=duplicate_items,
            captures=tuple(captures),
        )


def _detect_image_format_and_dimensions(image_bytes: bytes) -> tuple[str, int, int]:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(image_bytes) < 24:
            raise ScreenshotIngestionError("PNG payload too short to parse dimensions")
        width = int.from_bytes(image_bytes[16:20], "big")
        height = int.from_bytes(image_bytes[20:24], "big")
        if width < 1 or height < 1:
            raise ScreenshotIngestionError("PNG dimensions must be positive")
        return "png", width, height

    if image_bytes.startswith(b"\xff\xd8"):
        width, height = _parse_jpeg_dimensions(image_bytes)
        return "jpeg", width, height

    raise ScreenshotIngestionError("Unsupported image format; expected PNG or JPEG")


def _parse_jpeg_dimensions(image_bytes: bytes) -> tuple[int, int]:
    index = 2
    length = len(image_bytes)

    while index + 1 < length:
        if image_bytes[index] != 0xFF:
            index += 1
            continue

        while index < length and image_bytes[index] == 0xFF:
            index += 1
        if index >= length:
            break

        marker = image_bytes[index]
        index += 1

        if marker in {0xD8, 0xD9}:
            continue
        if index + 1 >= length:
            break

        segment_length = int.from_bytes(image_bytes[index : index + 2], "big")
        if segment_length < 2:
            raise ScreenshotIngestionError("Invalid JPEG segment length")

        segment_end = index + segment_length
        if segment_end > length:
            raise ScreenshotIngestionError("JPEG segment exceeds payload length")

        sof_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        if marker in sof_markers:
            if index + 7 >= length:
                raise ScreenshotIngestionError("JPEG SOF segment too short")
            height = int.from_bytes(image_bytes[index + 3 : index + 5], "big")
            width = int.from_bytes(image_bytes[index + 5 : index + 7], "big")
            if width < 1 or height < 1:
                raise ScreenshotIngestionError("JPEG dimensions must be positive")
            return width, height

        index = segment_end

    raise ScreenshotIngestionError("Unable to parse JPEG dimensions")


def _normalize_dimensions(*, width: int, height: int, max_dimension: int) -> tuple[int, int, float]:
    if width <= max_dimension and height <= max_dimension:
        return width, height, 1.0

    scale_ratio = min(max_dimension / float(width), max_dimension / float(height))
    normalized_width = max(1, int(round(width * scale_ratio)))
    normalized_height = max(1, int(round(height * scale_ratio)))
    return normalized_width, normalized_height, scale_ratio


def _orientation(width: int, height: int) -> str:
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def _aspect_bucket(width: int, height: int) -> str:
    ratio = width / float(height)
    if ratio >= 1.7:
        return "widescreen"
    if ratio >= 1.1:
        return "landscape"
    if ratio <= 0.9:
        return "portrait"
    return "square-ish"


def _normalize_required(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ScreenshotIngestionError(f"{field_name} is required")
    return normalized


def _normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}

    normalized: dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = " ".join(str(key).split())
        if not key_text:
            continue
        normalized[key_text] = _coerce_metadata_value(value)
    return normalized


def _coerce_metadata_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return _normalize_metadata(value)
    if isinstance(value, list):
        return [_coerce_metadata_value(item) for item in value]
    if isinstance(value, tuple):
        return [_coerce_metadata_value(item) for item in value]
    return str(value)


def _hash_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _to_iso(value: str) -> str:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "NormalizedSceneContract",
    "ScreenshotBatchSummary",
    "ScreenshotCapture",
    "ScreenshotIngestionError",
    "ScreenshotIngestionPipeline",
]
