"""Race gpt-image-2 + PixAI in parallel, then run a true head-to-head judge:
both images go to Gemini side-by-side and the winner is shipped. Tie is not
allowed — the judge is forced to pick the more natural, less AI-slop image."""

import asyncio
import logging
import time
from typing import Optional

from app.services.cover_image.compare_covers import compare_covers
from app.services.cover_image.providers import generate_gpt_image_2, generate_pixai
from config.settings import Settings

logger = logging.getLogger(__name__)


async def race_cover_image(
    prompt: str,
    *,
    settings: Settings,
    request_id: str = "",
    negative_prompt: str = "",
) -> Optional[tuple[str, bytes]]:
    """Run gpt-image-2 + PixAI in parallel.

    PixAI is the preferred output (watercolor LoRA, on-brand). Claude Vision
    vetoes off-topic PixAI output (e.g. random anime girls on an AI lesson);
    on veto we ship gpt-image-2 as fallback. None means both failed.
    """
    if not settings.cover_image_enabled:
        logger.info(f"[{request_id}] cover_image disabled in settings")
        return None

    openai_key = settings.openai_api_key.get_secret_value()
    pixai_key = settings.pixai_api_key.get_secret_value()
    pixai_model = settings.pixai_model_id
    anth_key = settings.anthropic_api_key.get_secret_value()
    budget_s = settings.cover_image_budget_s
    grace_s = settings.cover_image_grace_s

    tasks: dict[str, asyncio.Task] = {}
    if openai_key:
        tasks["gpt"] = asyncio.create_task(
            generate_gpt_image_2(
                prompt,
                api_key=openai_key,
                size=settings.cover_image_size,
                quality=settings.cover_image_quality,
                timeout_s=budget_s,
            ),
            name="gpt-image-2",
        )
    if pixai_key and pixai_model:
        tasks["pixai"] = asyncio.create_task(
            generate_pixai(
                prompt,
                api_key=pixai_key,
                model_id=pixai_model,
                negative_prompt=negative_prompt,
                timeout_s=budget_s,
            ),
            name="pixai",
        )

    if not tasks:
        logger.warning(f"[{request_id}] cover_image: no providers configured")
        return None

    t0 = time.time()
    pending = set(tasks.values())
    try:
        await asyncio.wait(pending, timeout=budget_s, return_when=asyncio.ALL_COMPLETED)
    except Exception as e:
        logger.warning(f"[{request_id}] cover_image: race wait error: {e}")

    still_running = [t for t in pending if not t.done()]
    if still_running:
        any_succeeded = any(
            t.done() and not t.cancelled() and t.exception() is None
            for t in pending
        )
        if any_succeeded and grace_s > 0:
            logger.info(
                f"[{request_id}] cover_image: granting {grace_s}s grace to "
                f"{len(still_running)} pending"
            )
            try:
                await asyncio.wait(still_running, timeout=grace_s)
            except Exception:
                pass

    for t in pending:
        if not t.done():
            t.cancel()

    results: dict[str, bytes] = {}
    for name, t in tasks.items():
        if t.cancelled():
            logger.info(f"[{request_id}] cover_image: {name} cancelled")
            continue
        exc = t.exception() if t.done() else None
        if exc is not None:
            logger.warning(
                f"[{request_id}] cover_image: {name} failed "
                f"({type(exc).__name__}: {exc})"
            )
            continue
        if t.done():
            results[name] = t.result()

    elapsed = time.time() - t0
    logger.info(
        f"[{request_id}] cover_image: race done in {elapsed:.1f}s, "
        f"successes={list(results.keys())}"
    )

    if not results:
        return None

    # Both succeeded → true head-to-head Gemini judge picks the winner.
    if "gpt" in results and "pixai" in results:
        google_key = settings.gemini_api_key.get_secret_value()
        winner = None
        if google_key:
            winner = await compare_covers(
                gpt_bytes=results["gpt"],
                pixai_bytes=results["pixai"],
                cover_prompt=prompt,
                google_api_key=google_key,
                model=settings.cover_judge_model,
            )
        if winner == "pixai":
            logger.info(f"[{request_id}] cover_image: head-to-head winner=pixai")
            return ("pixai", results["pixai"])
        if winner == "gpt":
            logger.info(f"[{request_id}] cover_image: head-to-head winner=gpt")
            return ("gpt", results["gpt"])
        # Judge unavailable or unparseable → default to gpt (lower variance,
        # consistent flat editorial style anchor).
        logger.info(
            f"[{request_id}] cover_image: judge inconclusive → defaulting to gpt"
        )
        return ("gpt", results["gpt"])

    # Only one succeeded → ship that one.
    if "gpt" in results:
        logger.info(f"[{request_id}] cover_image: shipping gpt (PixAI unavailable)")
        return ("gpt", results["gpt"])
    logger.info(f"[{request_id}] cover_image: shipping pixai (gpt unavailable)")
    return ("pixai", results["pixai"])
