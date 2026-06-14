"""Mock TTS — 產生靜音 WAV，用於開發測試 pipeline。"""

import struct
import wave
from pathlib import Path

from app.models.domain import AudioSegment, ScriptSegment
from app.services.tts.base import BaseTTSService


class MockTTSService(BaseTTSService):
    SAMPLE_RATE = 16000
    CHANNELS = 1
    SAMPLE_WIDTH = 2  # 16-bit

    async def synthesize_segment(
        self, segment: ScriptSegment, output_dir: Path
    ) -> AudioSegment:
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"audio_{segment.segment_id:03d}.wav"
        filepath = output_dir / filename

        duration = segment.estimated_duration_sec
        num_samples = int(self.SAMPLE_RATE * duration)

        with wave.open(str(filepath), "w") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(self.SAMPLE_WIDTH)
            wf.setframerate(self.SAMPLE_RATE)
            silence = struct.pack(f"<{num_samples}h", *([0] * num_samples))
            wf.writeframes(silence)

        return AudioSegment(
            segment_id=segment.segment_id,
            audio_path=str(filepath),
            duration_sec=duration,
        )
