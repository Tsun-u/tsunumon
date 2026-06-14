"""Normalize inline SVG markup before rendering.

Adds `overflow="visible"` to every `<svg>` element so labels that anchor
near or outside the viewBox aren't silently clipped by the SVG renderer's
default `overflow:hidden` behavior. Cropped labels read as a broken
figure even when the diagram itself is correct, and vision review can't
detect this kind of clipping.

Implemented with regex (no extra dependency) — we only mutate the opening
`<svg ...>` tag, never the path data or child elements.
"""

from __future__ import annotations

import re

_SVG_OPEN_TAG = re.compile(r"<svg\b[^>]*>", flags=re.IGNORECASE)
_HAS_OVERFLOW_ATTR = re.compile(r"\boverflow\s*=", flags=re.IGNORECASE)


def normalize_svg_in_html(html: str) -> str:
    """Ensure every `<svg>` opening tag carries `overflow="visible"`.

    No-op for SVG tags that already declare an `overflow` attribute (so
    explicit `overflow="hidden"` from the author is preserved). All other
    tags get the attribute injected just before the closing `>`.
    """

    def _inject(match: "re.Match[str]") -> str:
        opening = match.group(0)
        if _HAS_OVERFLOW_ATTR.search(opening):
            return opening
        return opening[:-1] + ' overflow="visible">'

    return _SVG_OPEN_TAG.sub(_inject, html)
