"""Inject AI-generated cover image into the cover slide HTML."""

import logging
import re
from pathlib import Path

from app.models.domain import ScriptSegment

logger = logging.getLogger(__name__)


# Wrapper-mode: keep the <div class="cover-image-slot"> wrapper, only swap
# its inner content. This survives a cache-hit second inject because the
# wrapper is still present in the cached slide HTML.
_WRAPPER_RE = re.compile(
    r'(<div\b[^>]*\bclass="[^"]*\bcover-image-slot\b[^"]*"[^>]*>)(.*?)(</div>)',
    flags=re.IGNORECASE | re.DOTALL,
)
# Legacy id-mode (older cached scripts): match the full <div id="cover-image-slot">
# block and replace it entirely. Used as fallback only.
_LEGACY_ID_RE = re.compile(
    r'<div\s+id="cover-image-slot"[^>]*>.*?</div>',
    flags=re.IGNORECASE | re.DOTALL,
)


def _ext_from_bytes(image_bytes: bytes) -> str:
    """Pick file extension matching the actual image format. PixAI returns
    webp even when the URL ends in .png; saving with the right extension keeps
    the HTML <img src> honest."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "webp"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "jpg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return "png"


def inject_cover_image(
    cover_segment: ScriptSegment,
    image_bytes: bytes,
    slides_dir: Path,
    *,
    filename: str = "cover_image.png",
) -> bool:
    """Write image to slides_dir and inject `<img>` into the cover slot.

    Strategy:
    1. Wrapper-mode (preferred): keep `<div class="cover-image-slot">` wrapper,
       replace only its inner. Survives cache-hit re-inject.
    2. Legacy id-mode (fallback): replace the whole `<div id="cover-image-slot">`
       block. Used for older cached scripts that haven't been re-improved.

    Returns True on success, False if no slot pattern matched.
    """
    slides_dir.mkdir(parents=True, exist_ok=True)
    ext = _ext_from_bytes(image_bytes)
    stem = Path(filename).stem
    actual_filename = f"{stem}.{ext}"
    image_path = slides_dir / actual_filename
    image_path.write_bytes(image_bytes)

    # object-fit:contain so the whole image is shown letterboxed inside the
    # slot (no cropping). cover image gen returns 1:1 squares, slot is 1.89:1
    # — contain shows the full square with horizontal padding rather than
    # silently cutting the image.
    inner_img_html = (
        f'<img src="{actual_filename}" '
        f'style="width:100%;height:100%;border-radius:16px;'
        f'object-fit:contain;display:block;" alt="cover scenario"/>'
    )

    # Try wrapper-mode first
    new_html, count = _WRAPPER_RE.subn(
        lambda m: f"{m.group(1)}{inner_img_html}{m.group(3)}",
        cover_segment.slide_html,
    )
    if count > 0:
        cover_segment.slide_html = new_html
        logger.info(
            f"[cover_image] inject: wrapper-mode swapped {count} slot inner, "
            f"image saved to {image_path}"
        )
        return True

    # Fallback: legacy id-mode (replace whole block)
    legacy_img_html = (
        f'<img src="{actual_filename}" '
        f'style="width:55%;height:50%;margin:0 auto;border-radius:16px;'
        f'object-fit:contain;display:block;" alt="cover scenario"/>'
    )
    new_html, count = _LEGACY_ID_RE.subn(legacy_img_html, cover_segment.slide_html)
    if count > 0:
        cover_segment.slide_html = new_html
        logger.info(
            f"[cover_image] inject: legacy id-mode replaced {count} slot div, "
            f"image saved to {image_path}"
        )
        return True

    logger.warning(
        f"[cover_image] inject: cover-image-slot not found in segment "
        f"{cover_segment.segment_id}; keeping placeholder"
    )
    return False
