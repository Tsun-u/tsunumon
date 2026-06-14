"""Stock photo lookup (Unsplash) for slides where SVG can't carry the visual.

Improver emits markers in slide_html when SVG is genuinely insufficient
(real microscope cross-sections, real lab equipment, real biological
specimens). The pipeline stage scans these markers, queries Unsplash, and
swaps the marker for a real `<img>` pointing at the downloaded photo.
"""

from .resolver import resolve_stock_images

__all__ = ["resolve_stock_images"]
