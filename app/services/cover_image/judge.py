"""Vision review: validate that PixAI's output matches the lesson scenario.

PixAI Tsubaki.2 is an anime base model — even with a watercolor LoRA + topic
negative prompt, it sometimes draws random anime characters that have nothing
to do with the lesson. This judge is a binary pass/fail gate: if PixAI's image
matches the scenario, ship it; otherwise fall back to gpt-image-2.
"""

import base64
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


JUDGE_SYSTEM = (
    "You are a visual gatekeeper for an educational video cover slide. "
    "You will be shown one AI-generated image and the lesson scenario it was "
    "supposed to depict. Decide whether the image is on-topic and usable. "
    "Reject if it shows random anime characters unrelated to the lesson, "
    "off-topic content, dense text overlays, or distorted anatomy that ruins "
    "the read. Output ONLY a JSON object: "
    '{"pass": true | false, "reason": "<short>"}.'
)


def _sniff_media_type(image_bytes: bytes) -> str:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


def _img_block(image_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": _sniff_media_type(image_bytes),
            "data": base64.b64encode(image_bytes).decode("ascii"),
        },
    }


async def validate_pixai_cover(
    image_bytes: bytes,
    *,
    cover_prompt: str,
    anthropic_api_key: str,
    model: str = "claude-sonnet-4-6",
    timeout_s: int = 60,
) -> Optional[bool]:
    """Binary check whether the PixAI image matches the cover scenario.

    Returns:
        True  — image passes the on-topic gate, ship PixAI.
        False — image rejected (off-topic / anime-girl drift / unreadable).
        None  — judge call failed; caller chooses fail-policy (we fail-open
                to ship PixAI so a flaky judge doesn't waste the watercolor).
    """
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.warning("[cover_image] anthropic SDK missing, skipping veto")
        return None

    client = AsyncAnthropic(api_key=anthropic_api_key, timeout=timeout_s)
    user_content = [
        {
            "type": "text",
            "text": (
                "Lesson cover scenario the image should depict:\n"
                f"{cover_prompt}\n\n"
                "Image generated for that cover:"
            ),
        },
        _img_block(image_bytes),
        {
            "type": "text",
            "text": (
                "Does this image fit the lesson scenario? Reply JSON: "
                '{"pass": true | false, "reason": "<short>"}'
            ),
        },
    ]

    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=200,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "text", None))
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(text[start : end + 1])
            verdict = obj.get("pass")
            if isinstance(verdict, bool):
                logger.info(
                    f"[cover_image] PixAI veto: pass={verdict} "
                    f"({obj.get('reason', '')})"
                )
                return verdict
        logger.warning(f"[cover_image] PixAI veto: unparseable response {text[:200]!r}")
    except Exception as e:
        logger.warning(
            f"[cover_image] PixAI veto failed ({type(e).__name__}: {e})"
        )

    return None
