"""本地跑 pipeline — 不需要 FastAPI server，直接呼叫 pipeline.execute()"""

import asyncio
import logging
import sys

from dotenv import load_dotenv
load_dotenv()

from config.settings import settings
from app.models.domain import TeachingRequest
from app.orchestrator.pipeline import get_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main():
    # 巴拿赫不動點定理教學
    request = TeachingRequest(
        request_id="banach_fixed_point",
        course_requirement=(
            "Topic: Banach Fixed-Point Theorem (巴拿赫不動點定理)\n\n"
            "Please teach this topic entirely in Traditional Chinese (繁體中文). "
            "All narration, slide text, and explanations must be in Traditional Chinese.\n\n"
            "Content structure:\n"
            "1. Start with an intuitive analogy: the map fixed-point example "
            "(若把某國的地圖縮小後印在該國領土內部，那麼在地圖上有且僅有一個點，"
            "它在地圖中的位置恰巧表示它所落在的土地位置)\n"
            "2. Explain the concept of contraction mapping (壓縮映射) with visual examples\n"
            "3. Present the full formal proof using Cauchy sequence convergence\n"
            "4. Show real-world applications (GPS positioning, numerical methods, economics equilibrium)\n\n"
            "Style: Engaging, start from intuition and daily-life analogies before formal proofs. "
            "The teacher persona is tsunumon sensei (竣宇獸老師)."
        ),
        student_persona=(
            "A curious learner with a basic mathematics background who has forgotten most "
            "real analysis but remembers the general concepts. Prefers explanations that "
            "start from intuition and everyday analogies before diving into formal proofs. "
            "Language: Traditional Chinese (繁體中文)."
        ),
    )

    logger.info(f"Starting pipeline for: {request.request_id}")
    logger.info(f"Topic: {request.course_requirement[:80]}...")
    logger.info(f"LLM backend: {settings.llm_backend}")
    logger.info(f"TTS backend: {settings.tts_backend}")
    logger.info(f"Azure voice: {settings.azure_tts_voice}")

    pipeline = get_pipeline()
    result = await pipeline.execute(request)

    logger.info(f"Done! Video: {result.video_url}")
    if result.subtitle_url:
        logger.info(f"Subtitles: {result.subtitle_url}")
    if result.supplementary_url:
        logger.info(f"Supplementary: {result.supplementary_url}")


if __name__ == "__main__":
    asyncio.run(main())
