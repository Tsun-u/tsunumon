"""Image generation providers: gpt-image-2 (OpenAI SDK) + PixAI (GraphQL+REST)."""

import asyncio
import base64
import logging
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

PIXAI_BASE = "https://api.pixai.art"
PIXAI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)
PIXAI_SUBMIT_QUERY = """
mutation createGenerationTask($parameters: JSONObject!) {
    createGenerationTask(parameters: $parameters) { id status }
}
"""


def _pixai_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Origin": "https://pixai.art",
        "Referer": "https://pixai.art/",
        "User-Agent": PIXAI_UA,
    }


_GPT_IMAGE_2_NEGATIVE_CUES = (
    "no text, no captions, no letters, no labels, no watermark, "
    "no signatures, no logos, no photorealism, no photographs, "
    "no people, no humans, no characters, no faces, no students, "
    "no anime characters, no cartoon avatars"
)


async def generate_gpt_image_2(
    prompt: str,
    *,
    api_key: str,
    size: str = "1024x1024",
    quality: str = "medium",
    timeout_s: int = 90,
) -> bytes:
    """Call OpenAI gpt-image-2 via raw HTTP (project does not depend on
    openai SDK). Returns PNG bytes. Raises on failure; caller wraps.

    Retries once on 5xx (transient gateway errors like 502 Bad Gateway).

    gpt-image-2 (DALL-E family) parses negative cues correctly — OpenAI's
    own prompting guide recommends phrases like "no text, no watermark"
    to suppress common artifacts. PixAI's Tsubaki.2 (DiT) does the
    opposite (negation summons the concept), so the shared positive prompt
    stays free of negation and these cues are appended only here.
    """
    t0 = time.time()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    full_prompt = f"{prompt.rstrip(' .')}. {_GPT_IMAGE_2_NEGATIVE_CUES}."
    body = {
        "model": "gpt-image-2",
        "prompt": full_prompt,
        "size": size,
        "quality": quality,
        "output_format": "png",
        "background": "auto",
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for attempt in (0, 1):
            resp = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers=headers,
                json=body,
            )
            if resp.status_code < 500 or attempt == 1:
                break
            logger.warning(
                f"[cover_image] gpt-image-2 transient {resp.status_code}, "
                "retrying once after 1.5s"
            )
            await asyncio.sleep(1.5)
        resp.raise_for_status()
        payload = resp.json()
    elapsed = time.time() - t0
    data = payload.get("data") or []
    if not data:
        raise RuntimeError(f"gpt-image-2 returned empty data: {payload}")
    b64 = data[0].get("b64_json")
    if not b64:
        raise RuntimeError("gpt-image-2 returned no b64_json")
    png_bytes = base64.b64decode(b64)
    logger.info(
        f"[cover_image] gpt-image-2 done: {len(png_bytes)} bytes in {elapsed:.1f}s"
    )
    return png_bytes


PIXAI_BASE_NEGATIVE = (
    "text, captions, letters, words, labels, typography, watermark, signature, "
    "logo, lowres, blurry, distorted hands, extra limbs, "
    "photorealistic, photograph, photo, realistic skin texture, "
    "people, person, human figures, characters, faces, students, "
    "anime characters, cartoon avatars, mascot characters"
)
# Default style: no LoRA — the base model + the style anchor in the
# generated prompt (flat editorial vector illustration) gives the cleanest
# match to "AI student + human" preference. Set these to a real LoRA id
# if a future style needs explicit weight tuning.
PIXAI_WATERCOLOR_LORA_ID = ""
PIXAI_WATERCOLOR_LORA_WEIGHT = 0.0
PIXAI_WATERCOLOR_TRIGGER = ""


def _merge_negatives(*parts: str) -> str:
    """Combine negative-prompt fragments, dropping empties and de-duping commas."""
    chunks = []
    for p in parts:
        if p:
            chunks.append(p.strip().strip(","))
    return ", ".join(c for c in chunks if c)


# Tsubaki.2 is a DiT model — it parses natural language but is dull on
# negation ("no", "not", "without"). "no text" gets read as the concept "text"
# and can summon what we wanted to ban. Strip every "no <word>" phrase from
# the positive before sending; the same concepts live in the negative prompt
# where they are read correctly.
_NO_PHRASE_RE = re.compile(r"\bno\s+[a-z\s]+?(?=,|$)", re.IGNORECASE)


def _strip_no_phrases(prompt: str) -> str:
    cleaned = _NO_PHRASE_RE.sub("", prompt)
    cleaned = re.sub(r",\s*,", ",", cleaned)
    cleaned = re.sub(r",\s*$", "", cleaned)
    cleaned = re.sub(r"^\s*,\s*", "", cleaned)
    return cleaned.strip()


async def generate_pixai(
    prompt: str,
    *,
    api_key: str,
    model_id: str,
    negative_prompt: str = "",
    lora_id: str = PIXAI_WATERCOLOR_LORA_ID,
    lora_weight: float = PIXAI_WATERCOLOR_LORA_WEIGHT,
    lora_trigger: str = PIXAI_WATERCOLOR_TRIGGER,
    width: int = 1024,
    height: int = 1024,
    timeout_s: int = 90,
) -> bytes:
    """Submit (GraphQL) + poll (REST) PixAI generation task, download image bytes.

    Schema verified against D:/tsunu_plan/mcp-servers/pixai/server.py:
    - Submit: POST /graphql, mutation `createGenerationTask(parameters: $parameters)`
      with variables {"parameters": {modelId, prompts, width, height}}
    - Poll: GET /v1/task/{task_id}, returns {status, outputs: {mediaUrls: [...]}}
    Headers must include Origin/Referer/User-Agent (anti-bot).
    """
    if not model_id:
        raise RuntimeError("PixAI model_id not configured")

    keyword_prompt = _strip_no_phrases(prompt)
    full_prompt = (
        f"{lora_trigger}, {keyword_prompt}"
        if lora_trigger and lora_trigger not in keyword_prompt
        else keyword_prompt
    )
    full_negative = _merge_negatives(PIXAI_BASE_NEGATIVE, negative_prompt)

    parameters: dict = {
        "modelId": model_id,
        "prompts": full_prompt,
        "negativePrompts": full_negative,
        "width": width,
        "height": height,
    }
    if lora_id:
        parameters["lora"] = {lora_id: lora_weight}
        parameters["loraParameters"] = [
            {"versionId": lora_id, "weight": lora_weight}
        ]

    headers = _pixai_headers(api_key)
    t0 = time.time()
    task_id: Optional[str] = None

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        try:
            r = await client.post(
                f"{PIXAI_BASE}/graphql",
                headers=headers,
                json={
                    "operationName": "createGenerationTask",
                    "query": PIXAI_SUBMIT_QUERY,
                    "variables": {"parameters": parameters},
                },
            )
            r.raise_for_status()
            payload = r.json()
            if "errors" in payload:
                raise RuntimeError(f"PixAI submit errors: {payload['errors']}")
            task_id = payload.get("data", {}).get("createGenerationTask", {}).get("id")
            if not task_id:
                raise RuntimeError(f"PixAI submit returned no task_id: {payload}")
            logger.info(f"[cover_image] PixAI task submitted: {task_id}")

            deadline = t0 + timeout_s
            while time.time() < deadline:
                await asyncio.sleep(3)
                r = await client.get(
                    f"{PIXAI_BASE}/v1/task/{task_id}",
                    headers=headers,
                )
                r.raise_for_status()
                result = r.json()
                status = result.get("status")
                if status == "completed":
                    media_urls = result.get("outputs", {}).get("mediaUrls", [])
                    if not media_urls:
                        raise RuntimeError(f"PixAI task {task_id} completed without mediaUrls")
                    img_r = await client.get(media_urls[0])
                    img_r.raise_for_status()
                    elapsed = time.time() - t0
                    logger.info(
                        f"[cover_image] PixAI done: "
                        f"{len(img_r.content)} bytes in {elapsed:.1f}s"
                    )
                    return img_r.content
                if status in ("failed", "cancelled"):
                    raise RuntimeError(f"PixAI task {task_id} status={status}")
            raise asyncio.TimeoutError(f"PixAI task {task_id} did not complete in {timeout_s}s")
        except asyncio.CancelledError:
            # PixAI public REST has no cancel endpoint; task self-times-out server-side
            logger.info(
                f"[cover_image] PixAI task {task_id} cancelled by race "
                "(no cancel API; relying on server-side timeout)"
            )
            raise
