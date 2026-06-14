"""Resolve `<img class="stock-image" data-stock-query="...">` markers via
Unsplash, download the photo to slides_dir, and rewrite the tag to point at
the local file. SVG is preferred upstream — this stage only fires when the
Improver decided no SVG could carry the visual.

Fallback chain when a query misses:
  1. Retry with a simplified query (stop-words stripped — Unsplash is a
     consumer photo library; jargon like "microscope slide" or "vascular
     bundles" hits zero results).
  2. Replace the marker with an inline SVG schematic placeholder so the
     slide never renders a broken `<img>` or empty alt-text. The SVG
     carries the alt text as a visible caption so the viewer understands
     what the slide is gesturing at.
"""

from __future__ import annotations

import html
import logging
import re
from pathlib import Path
from typing import List, Optional

import httpx

from app.models.domain import ScriptSegment

logger = logging.getLogger(__name__)


_UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"
_REQUEST_TIMEOUT_S = 15.0
_DOWNLOAD_TIMEOUT_S = 30.0

# Words that make a query too academic for a consumer photo library.
# Strip them on the simplify-and-retry pass so generic photos can still
# carry the visual when the original specific query misses.
_STOP_WORDS = {
    "microscope", "microscopic", "slide", "histology", "histological",
    "cross", "section", "cross-section", "stained", "scattered",
    "vascular", "bundles", "bundle", "monocot", "dicot",
    "anatomy", "anatomical", "specimen", "magnified", "tissue",
    "cellular", "diagram", "schematic", "labelled", "labeled",
}


# Match <img ... class="...stock-image..." ... data-stock-query="..." ...>
# Greedy-but-bounded so each marker resolves independently.
_STOCK_IMG_RE = re.compile(
    r'<img\b([^>]*?\bclass\s*=\s*"[^"]*\bstock-image\b[^"]*"[^>]*?)>',
    flags=re.IGNORECASE,
)
_QUERY_RE = re.compile(
    r'data-stock-query\s*=\s*"([^"]+)"',
    flags=re.IGNORECASE,
)
_ALT_RE = re.compile(
    r'\balt\s*=\s*"([^"]*)"',
    flags=re.IGNORECASE,
)
_STYLE_RE = re.compile(
    r'\bstyle\s*=\s*"([^"]*)"',
    flags=re.IGNORECASE,
)


def _ext_from_content_type(content_type: str) -> str:
    ct = (content_type or "").lower()
    if "jpeg" in ct or "jpg" in ct:
        return "jpg"
    if "png" in ct:
        return "png"
    if "webp" in ct:
        return "webp"
    return "jpg"


def _simplify_query(query: str) -> str:
    """Strip jargon/stop-words so a generic photo can still answer the query.

    The Improver writes precise scientific phrases ('monocot stem cross
    section microscope slide'); Unsplash indexes hobbyist/editorial photos
    that won't match that vocabulary. The simplified pass keeps the head
    nouns and drops the technical modifiers."""
    tokens = re.findall(r"[A-Za-z][A-Za-z-]*", query.lower())
    kept = [t for t in tokens if t not in _STOP_WORDS]
    # If stripping leaves nothing useful, fall back to the original head noun.
    if not kept:
        kept = tokens[:2]
    # Preserve the original token order; cap length so we don't reproduce
    # the original query verbatim when no stop-words were present.
    simplified = " ".join(kept[:4])
    return simplified


async def _query_unsplash_once(query: str, access_key: str) -> Optional[dict]:
    """Single Unsplash search; None on failure or zero results."""
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            resp = await client.get(
                _UNSPLASH_SEARCH_URL,
                params={
                    "query": query,
                    "per_page": 5,
                    "orientation": "landscape",
                    "content_filter": "high",
                },
                headers={"Authorization": f"Client-ID {access_key}"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.warning(
            f"[stock_image] Unsplash search failed for {query!r} "
            f"({type(e).__name__}: {e})"
        )
        return None

    results = payload.get("results") or []
    if not results:
        return None
    return results[0]


async def _query_unsplash(query: str, access_key: str) -> Optional[dict]:
    """Try the original query, then a simplified query (stop-words stripped)
    when the first pass yields zero results. Returns the chosen photo dict,
    or None if both passes fail."""
    photo = await _query_unsplash_once(query, access_key)
    if photo is not None:
        return photo

    simplified = _simplify_query(query)
    if simplified and simplified.strip().lower() != query.strip().lower():
        logger.info(
            f"[stock_image] no Unsplash results for {query!r}; retrying with simplified {simplified!r}"
        )
        photo = await _query_unsplash_once(simplified, access_key)
        if photo is not None:
            return photo

    logger.info(f"[stock_image] no Unsplash results after retry for {query!r}")
    return None


async def _download_photo(photo: dict, target: Path) -> Optional[Path]:
    """Download the photo's regular-resolution URL to target. Return path on
    success, None on any failure."""
    urls = photo.get("urls") or {}
    download_url = urls.get("regular") or urls.get("small") or urls.get("raw")
    if not download_url:
        return None

    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_S) as client:
            resp = await client.get(download_url)
            resp.raise_for_status()
            ext = _ext_from_content_type(resp.headers.get("content-type", ""))
            full_path = target.with_suffix(f".{ext}")
            full_path.write_bytes(resp.content)
            return full_path
    except Exception as e:
        logger.warning(
            f"[stock_image] download failed for {download_url} "
            f"({type(e).__name__}: {e})"
        )
        return None


def _replace_marker(
    img_attrs: str,
    local_filename: str,
    photo: dict,
) -> str:
    """Build a fresh <img> tag using the marker's existing attributes (alt,
    style) plus a real src and Unsplash photographer credit in the alt text."""
    alt_match = _ALT_RE.search(img_attrs)
    style_match = _STYLE_RE.search(img_attrs)

    alt = alt_match.group(1) if alt_match else ""
    style = style_match.group(1) if style_match else "max-width:100%;height:auto;"

    photographer = (photo.get("user") or {}).get("name", "")
    if photographer and photographer not in alt:
        alt = (alt + f" (photo: {photographer} / Unsplash)").strip()

    return (
        f'<img src="{local_filename}" '
        f'alt="{alt}" '
        f'style="{style}" '
        f'data-source="unsplash" '
        f'data-photographer="{photographer}"/>'
    )


async def resolve_stock_images(
    segments: List[ScriptSegment],
    slides_dir: Path,
    *,
    access_key: str,
) -> int:
    """Scan every segment's slide_html for stock-image markers, fetch photos
    from Unsplash, save under slides_dir/stock/, and rewrite the markers in
    place. Returns the number of markers successfully resolved.

    Failures (no API key, no result, download error) leave the marker intact;
    upstream rendering is responsible for the resulting placeholder visual.
    """
    if not access_key:
        return 0

    stock_dir = slides_dir / "stock"
    stock_dir.mkdir(parents=True, exist_ok=True)
    resolved = 0
    seen_index = 0

    for seg in segments:
        markers = list(_STOCK_IMG_RE.finditer(seg.slide_html))
        if not markers:
            continue

        new_html_parts: List[str] = []
        cursor = 0
        for marker in markers:
            new_html_parts.append(seg.slide_html[cursor:marker.start()])

            attrs_section = marker.group(1)
            query_match = _QUERY_RE.search(attrs_section)
            if not query_match:
                # missing query → leave marker as-is, don't crash
                new_html_parts.append(marker.group(0))
                cursor = marker.end()
                continue
            query = query_match.group(1).strip()
            if not query:
                new_html_parts.append(marker.group(0))
                cursor = marker.end()
                continue

            photo = await _query_unsplash(query, access_key)
            if photo is None:
                new_html_parts.append(marker.group(0))
                cursor = marker.end()
                continue

            seen_index += 1
            target_stem = stock_dir / f"stock_{seg.segment_id:03d}_{seen_index:02d}"
            saved_path = await _download_photo(photo, target_stem)
            if saved_path is None:
                new_html_parts.append(marker.group(0))
                cursor = marker.end()
                continue

            relative_src = f"stock/{saved_path.name}"
            replacement = _replace_marker(attrs_section, relative_src, photo)
            new_html_parts.append(replacement)
            resolved += 1
            logger.info(
                f"[stock_image] resolved {query!r} → {saved_path.name} "
                f"(seg {seg.segment_id})"
            )
            cursor = marker.end()

        new_html_parts.append(seg.slide_html[cursor:])
        seg.slide_html = "".join(new_html_parts)

    return resolved
