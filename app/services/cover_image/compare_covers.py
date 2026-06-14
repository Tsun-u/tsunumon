"""Cover image A/B head-to-head judge using Gemini 3 Flash.

Replaces the old single-image PixAI veto with a true competition: both
gpt-image-2 and PixAI outputs are shown side by side, the judge picks the
one that fits the lesson scenario best. Tie is not allowed — the judge is
forced to choose the more natural, less AI-slop-looking image.

Mirrors the AI Student grader's model so the cover the user sees lines up
with what the same family of model would prefer.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal, Optional

logger = logging.getLogger(__name__)


JUDGE_SYSTEM = (
    "You are the visual gatekeeper for an educational video cover slide. "
    "You will be shown TWO candidate AI-generated images for the same lesson "
    "scenario, labeled IMAGE A and IMAGE B. You must pick exactly one — ties "
    "are not allowed. Choose the image that:\n"
    "  1. Best matches the lesson scenario (on-topic, not random anime / "
    "off-topic clutter / mismatched objects).\n"
    "  2. Looks the most natural and the LEAST like generic 'AI slop' "
    "(unnatural blob hands, distorted text, melted fingers, oversaturated "
    "amateur 3D shading, stock-illustration kitsch).\n"
    "When both are roughly comparable, prefer the image whose composition "
    "feels designed by a human (clean focal point, deliberate negative "
    "space, restrained palette) over the one that feels machine-decorated.\n"
    'Output ONLY a JSON object: {"winner": "A" | "B", "reason": "<short>"}.'
)


def _sniff_mime(image_bytes: bytes) -> str:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


async def compare_covers(
    *,
    gpt_bytes: bytes,
    pixai_bytes: bytes,
    cover_prompt: str,
    google_api_key: str,
    model: str = "gemini-3-flash-preview",
    fallback_model: str = "gemini-2.5-flash",
    timeout_s: int = 60,
) -> Optional[Literal["gpt", "pixai"]]:
    """Side-by-side judge: pick whichever image fits the cover scenario better.

    Returns 'gpt' or 'pixai'. Returns None only if both models fail (caller
    should default to a safe pick — typically 'gpt' for stability).
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("[cover_image] google-genai SDK missing, skipping compare")
        return None

    client = genai.Client(api_key=google_api_key)

    # IMAGE A = gpt, IMAGE B = pixai (label is just for the judge prompt)
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=(
                    "Lesson cover scenario the image should depict:\n"
                    f"{cover_prompt}\n\n"
                    "IMAGE A (candidate 1):"
                )),
                types.Part.from_bytes(
                    data=gpt_bytes,
                    mime_type=_sniff_mime(gpt_bytes),
                ),
                types.Part.from_text(text="IMAGE B (candidate 2):"),
                types.Part.from_bytes(
                    data=pixai_bytes,
                    mime_type=_sniff_mime(pixai_bytes),
                ),
                types.Part.from_text(text=(
                    'Pick exactly one — no ties. Output JSON: '
                    '{"winner": "A" | "B", "reason": "<short>"}'
                )),
            ],
        ),
    ]

    config = types.GenerateContentConfig(
        system_instruction=JUDGE_SYSTEM,
        max_output_tokens=400,
        response_mime_type="application/json",
    )

    for try_model in (model, fallback_model):
        try:
            response = client.models.generate_content(
                model=try_model, contents=contents, config=config,
            )
            text = (response.text or "").strip()
            start = text.find("{")
            end = text.rfind("}")
            obj = None
            if start >= 0 and end > start:
                try:
                    obj = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            if obj is None:
                match = re.search(r'"winner"\s*:\s*"([AB])"', text)
                if match:
                    obj = {"winner": match.group(1), "reason": "(truncated)"}
                else:
                    logger.warning(
                        f"[cover_image] compare ({try_model}): "
                        f"unparseable {text[:200]!r}"
                    )
                    continue
            winner_label = obj.get("winner")
            reason = obj.get("reason", "")
            if winner_label == "A":
                logger.info(
                    f"[cover_image] compare ({try_model}): winner=gpt ({reason})"
                )
                return "gpt"
            if winner_label == "B":
                logger.info(
                    f"[cover_image] compare ({try_model}): winner=pixai ({reason})"
                )
                return "pixai"
            logger.warning(
                f"[cover_image] compare ({try_model}): invalid winner {winner_label!r}"
            )
        except Exception as e:
            logger.warning(
                f"[cover_image] compare ({try_model}) failed "
                f"({type(e).__name__}: {e}); trying fallback if any"
            )

    return None
