"""Cover image race + judge + inject service.

Pipeline 在 Improver 產 cover_image_prompt 後、render slides 前呼叫
race_cover_image() 並行 call gpt-image-2 + PixAI、vision review 挑一張、
inject 進 cover slide HTML。失敗 fallback 保留原 placeholder div、不破 pipeline。
"""

from app.services.cover_image.prompt_gen import generate_cover_image_prompt
from app.services.cover_image.race import race_cover_image
from app.services.cover_image.inject import inject_cover_image

__all__ = ["generate_cover_image_prompt", "race_cover_image", "inject_cover_image"]
