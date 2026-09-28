# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
EXIF metadata extractor for image files.

This module extracts EXIF metadata from common image formats using Pillow,
producing searchable text and structured metadata for indexing.

Responsibilities:
- Extract EXIF tags (camera, date, exposure, GPS, etc.)
- Convert GPS coordinates from DMS to decimal degrees
- Build searchable text representation of image metadata
"""

from pathlib import Path
from typing import Any, Dict

from aitao.core.plugin_registry import register
from aitao.indexation.text_extractor import ExtractionResult, BaseExtractor

# ---------------------------------------------------------------------------
# Lazy import for Pillow
# ---------------------------------------------------------------------------

_pillow = None


def _get_pillow():
    """Lazy load Pillow for EXIF extraction."""
    global _pillow
    if _pillow is None:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
        _pillow = {"Image": Image, "TAGS": TAGS, "GPSTAGS": GPSTAGS}
    return _pillow


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


@register("extractor", "exif")
class EXIFExtractor(BaseExtractor):
    """Extractor for image EXIF metadata using Pillow."""

    SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}

    def extract(self, file_path: Path) -> ExtractionResult:
        """Extract EXIF metadata from image file."""
        try:
            pillow = _get_pillow()
            Image = pillow["Image"]
            TAGS = pillow["TAGS"]
            GPSTAGS = pillow["GPSTAGS"]
        except ImportError:
            return ExtractionResult(
                text="",
                success=False,
                error="Pillow not installed. Run: uv pip install Pillow",
            )

        try:
            with Image.open(str(file_path)) as img:
                # Basic image info
                width, height = img.size
                format_type = img.format or file_path.suffix.upper().lstrip(".")

                # Extract EXIF data
                exif_data = {}
                raw_exif = img._getexif() if hasattr(img, "_getexif") else None

                if raw_exif:
                    for tag_id, value in raw_exif.items():
                        tag_name = TAGS.get(tag_id, str(tag_id))
                        # Handle GPS data separately
                        if tag_name == "GPSInfo":
                            gps_data = {}
                            for gps_tag_id, gps_value in value.items():
                                gps_tag_name = GPSTAGS.get(gps_tag_id, str(gps_tag_id))
                                gps_data[gps_tag_name] = self._convert_value(gps_value)
                            exif_data["gps"] = gps_data
                        else:
                            exif_data[tag_name] = self._convert_value(value)

                # Extract key metadata
                date_taken = exif_data.get("DateTimeOriginal") or exif_data.get("DateTime")
                camera_make = exif_data.get("Make", "")
                camera_model = exif_data.get("Model", "")
                camera = (
                    f"{camera_make} {camera_model}".strip()
                    if camera_make or camera_model
                    else None
                )

                # Extract GPS coordinates
                gps_lat, gps_lon = self._extract_gps(exif_data.get("gps", {}))

                # Build searchable text from metadata
                text_parts = [f"Image: {file_path.name}"]
                text_parts.append(f"Dimensions: {width}x{height}")
                text_parts.append(f"Format: {format_type}")

                if date_taken:
                    text_parts.append(f"Date: {date_taken}")
                if camera:
                    text_parts.append(f"Camera: {camera}")
                if gps_lat is not None and gps_lon is not None:
                    text_parts.append(f"GPS: {gps_lat:.6f}, {gps_lon:.6f}")

                # Add other interesting EXIF fields
                if exif_data.get("ExposureTime"):
                    text_parts.append(f"Exposure: {exif_data['ExposureTime']}")
                if exif_data.get("FNumber"):
                    text_parts.append(f"Aperture: f/{exif_data['FNumber']}")
                if exif_data.get("ISOSpeedRatings"):
                    text_parts.append(f"ISO: {exif_data['ISOSpeedRatings']}")

                text = "\n".join(text_parts)

                metadata = {
                    "width": width,
                    "height": height,
                    "format": format_type,
                    "file_type": "image",
                    "has_exif": bool(raw_exif),
                }

                if date_taken:
                    metadata["date_taken"] = date_taken
                if camera:
                    metadata["camera"] = camera
                if gps_lat is not None:
                    metadata["gps_lat"] = gps_lat
                    metadata["gps_lon"] = gps_lon

                return ExtractionResult(text=text, metadata=metadata)

        except Exception as e:
            return ExtractionResult(
                text="",
                success=False,
                error=f"Failed to extract EXIF: {e}",
            )

    def _convert_value(self, value: Any) -> Any:
        """Convert EXIF value to JSON-serializable type."""
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", errors="ignore")
            except Exception:
                return str(value)
        elif hasattr(value, "numerator"):
            # Handle Ratio/Fraction types
            if value.denominator == 1:
                return value.numerator
            return float(value)
        elif isinstance(value, tuple):
            return [self._convert_value(v) for v in value]
        return value

    def _extract_gps(self, gps_data: Dict) -> tuple:
        """Extract GPS coordinates from EXIF GPS data."""
        if not gps_data:
            return None, None

        try:
            lat = gps_data.get("GPSLatitude")
            lat_ref = gps_data.get("GPSLatitudeRef", "N")
            lon = gps_data.get("GPSLongitude")
            lon_ref = gps_data.get("GPSLongitudeRef", "E")

            if lat and lon:
                lat_decimal = self._dms_to_decimal(lat)
                lon_decimal = self._dms_to_decimal(lon)

                if lat_ref == "S":
                    lat_decimal = -lat_decimal
                if lon_ref == "W":
                    lon_decimal = -lon_decimal

                return lat_decimal, lon_decimal
        except Exception:
            pass

        return None, None

    def _dms_to_decimal(self, dms: list) -> float:
        """Convert degrees/minutes/seconds to decimal degrees."""
        if len(dms) >= 3:
            degrees = float(dms[0])
            minutes = float(dms[1])
            seconds = float(dms[2])
            return degrees + minutes / 60 + seconds / 3600
        return 0.0
