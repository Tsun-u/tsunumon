"""TTS service 抽象介面。"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from app.models.domain import AudioSegment, ScriptSegment


class BaseTTSService(ABC):
    @abstractmethod
    async def synthesize_segment(
        self, segment: ScriptSegment, output_dir: Path
    ) -> AudioSegment:
        """合成單一段落的語音。"""
        ...

    async def synthesize_all(
        self, segments: List[ScriptSegment], output_dir: Path
    ) -> List[AudioSegment]:
        """合成所有段落。預設逐一處理，子類別可覆寫為並行。"""
        results = []
        for seg in segments:
            audio = await self.synthesize_segment(seg, output_dir)
            results.append(audio)
        return results
