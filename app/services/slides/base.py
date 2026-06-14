"""Slide generator 抽象介面。"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from app.models.domain import ScriptSegment, SlideImage


class BaseSlideGenerator(ABC):
    @abstractmethod
    async def generate_slide(
        self, segment: ScriptSegment, output_dir: Path
    ) -> SlideImage:
        """生成單張投影片圖片。"""
        ...

    async def generate_all(
        self, segments: List[ScriptSegment], output_dir: Path
    ) -> List[SlideImage]:
        """生成所有投影片。預設逐一處理。"""
        results = []
        for seg in segments:
            slide = await self.generate_slide(seg, output_dir)
            results.append(slide)
        return results
