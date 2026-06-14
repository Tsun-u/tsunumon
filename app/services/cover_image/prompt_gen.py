"""ImagePromptGen: extract a concrete visual scene description from the cover
slide narration, using a small OpenAI model via httpx (same pattern as
llm/consultant.py — the project does not depend on the openai SDK).

Returns (positive, negative) prompts. The negative prompt names topic-specific
distractors the model should avoid (e.g. random anime characters on an AI
lesson cover); a generic style-anchor negative is added by providers.py.
"""

import json
import logging
import time
from typing import Optional, Tuple

import httpx

from app.models.domain import ScriptSegment

logger = logging.getLogger(__name__)


_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

_STYLE_RISOGRAPH = (
    "risograph print style, limited 2-3 spot color palette, "
    "halftone dot texture, clean modern composition, educational diagram feel"
)
_STYLE_FLAT = (
    "flat editorial vector illustration, clean line art, simplified shapes, "
    "limited and selective color palette, neutral background, minimal noise, "
    "stylized illustration aesthetic"
)

_RISOGRAPH_SUBJECT_CODES = {"physics", "cs"}

def _detect_cover_style(title: str, narration: str, subject: str = None) -> str:
    if subject and subject in _RISOGRAPH_SUBJECT_CODES:
        return _STYLE_RISOGRAPH
    return _STYLE_FLAT

SYSTEM_PROMPT = (
    "You convert an educational video's cover-slide narration into a pair of "
    "image-generation prompts (positive + negative) for an editorial "
    "illustration. The cover is character-free: describe objects, "
    "environments, instruments, diagrams, and conceptual visuals — never "
    "people, students, faces, or character avatars. Output ONLY a JSON "
    "object with keys 'positive' and 'negative'. No preamble, no markdown "
    "fences, no quotes around the JSON."
)

USER_PROMPT_TEMPLATE = """The cover slide of an educational video opens with this real-world scenario.

Title: {title}

Narration:
{narration}

Output a JSON object with two fields:

"positive": one sentence (under 80 words) describing the cover scenario in concrete visual terms — objects, instruments, environments, diagrams, conceptual visuals. Reuse the scenario from the narration but recast any human action into the objects involved (e.g. "a basketball arcing toward a hoop" instead of "a player shooting a basketball"). Do NOT describe people, students, faces, or character avatars. End the sentence with this exact style anchor:
{style_anchor}

"negative": a short comma-separated list of topic-specific distractors to keep out of the image — things the model might otherwise hallucinate that would distract from the lesson. Examples: a microscope appearing on an abstract math topic, dense text overlays, off-topic charts. Tailor the list to this lesson; do not duplicate the generic "no people/no text/no watermark" anchors already covered elsewhere.

Output only the JSON object."""


def _parse_json_pair(raw: str) -> Optional[Tuple[str, str]]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    pos = (obj.get("positive") or "").strip()
    neg = (obj.get("negative") or "").strip()
    if not pos:
        return None
    return pos, neg


async def generate_cover_image_prompt(
    cover_segment: ScriptSegment,
    *,
    api_key: str,
    model: str = "gpt-5.4-mini",
    timeout_s: int = 60,
    subject: str = None,
) -> Optional[Tuple[str, str]]:
    """Extract (positive, negative) cover image prompts from cover segment.

    Returns None on failure (caller falls back to skipping image race;
    placeholder div remains in the cover slide).
    """
    title = cover_segment.slide_title or "(untitled)"
    narration = cover_segment.narration_text or "(no narration)"
    style_anchor = _detect_cover_style(title, narration, subject=subject)
    style_label = "risograph" if style_anchor == _STYLE_RISOGRAPH else "flat"
    logger.info(f"[ImagePromptGen] Cover style routing: {style_label}")
    user_prompt = USER_PROMPT_TEMPLATE.format(
        title=title,
        narration=narration,
        style_anchor=style_anchor,
    )
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                _OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_completion_tokens": 400,
                },
            )
            resp.raise_for_status()
            raw = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        elapsed = time.time() - t0
        parsed = _parse_json_pair(raw)
        if parsed is None:
            logger.warning(
                f"[ImagePromptGen] {model} unparseable in {elapsed:.1f}s; "
                f"first 200 chars: {raw[:200]!r}"
            )
            return None
        pos, neg = parsed
        logger.info(
            f"[ImagePromptGen] {model} done in {elapsed:.1f}s "
            f"(pos {len(pos)} chars, neg {len(neg)} chars): {pos[:100]}..."
        )
        return pos, neg
    except Exception as e:
        elapsed = time.time() - t0
        logger.warning(
            f"[ImagePromptGen] {model} failed in {elapsed:.1f}s "
            f"({type(e).__name__}: {e})"
        )
        return None
