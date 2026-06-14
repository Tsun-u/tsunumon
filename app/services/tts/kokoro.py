"""Kokoro TTS — 使用 kokoro-onnx 本地合成語音。

加速策略：
1. 長文切句：把旁白拆成句子分別合成再拼接（Kokoro 處理短句比長段快）
2. 多 process 並行：用 ProcessPoolExecutor 同時合成多段旁白
"""

import asyncio
import logging
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import List

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

from app.models.domain import AudioSegment, ScriptSegment
from app.services.tts.base import BaseTTSService

logger = logging.getLogger(__name__)

# 句子分割用的正則：句號、問號、驚嘆號、分號後切開
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?;])\s+')

# 句間靜音（0.15 秒，避免句子黏在一起）
_SILENCE_SAMPLES = 3600  # 0.15s * 24000Hz


def _split_sentences(text: str) -> List[str]:
    """把長文拆成句子。太短的句子合併到前一句，避免碎片化。"""
    raw = _SENTENCE_SPLIT.split(text.strip())
    if not raw:
        return [text.strip()]

    sentences = []
    buf = ""
    for s in raw:
        s = s.strip()
        if not s:
            continue
        if buf and len(buf) + len(s) < 80:
            # 太短的句子合併
            buf = buf + " " + s
        else:
            if buf:
                sentences.append(buf)
            buf = s
    if buf:
        sentences.append(buf)

    return sentences if sentences else [text.strip()]


def _synth_one_segment(
    model_path: str,
    voices_path: str,
    voice: str,
    speed: float,
    lang: str,
    text: str,
    output_path: str,
    segment_id: int,
) -> tuple:
    """在子 process 中合成一段旁白（含切句 + 拼接）。回傳 (segment_id, audio_path, duration)。"""
    kokoro = Kokoro(model_path, voices_path)

    sentences = _split_sentences(text)
    chunks = []
    sample_rate = 24000
    silence = np.zeros(_SILENCE_SAMPLES, dtype=np.float32)

    for sent in sentences:
        audio, sr = kokoro.create(text=sent, voice=voice, speed=speed, lang=lang)
        sample_rate = sr
        chunks.append(audio)
        chunks.append(silence)

    # 去掉最後多餘的靜音
    if chunks:
        chunks.pop()

    full_audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
    sf.write(output_path, full_audio, sample_rate)
    duration = len(full_audio) / sample_rate
    return (segment_id, output_path, duration)


class KokoroTTSService(BaseTTSService):
    """Kokoro-82M ONNX 本地 TTS。CPU-only 友善，適合開發階段。"""

    def __init__(
        self,
        model_path: str = "models/kokoro-v1.0.int8.onnx",
        voices_path: str = "models/voices-v1.0.bin",
        voice: str = "af_heart",
        speed: float = 1.0,
        lang: str = "en-us",
        max_workers: int = 2,
    ):
        self._model_path = model_path
        self._voices_path = voices_path
        self._voice = voice
        self._speed = speed
        self._lang = lang
        self._max_workers = max_workers
        self._kokoro: Kokoro | None = None

    def _ensure_model(self):
        """懶載入模型（主 process 用，單段合成時使用）。"""
        if self._kokoro is None:
            logger.info("Loading Kokoro model...")
            self._kokoro = Kokoro(self._model_path, self._voices_path)
            logger.info("Kokoro model loaded")

    def _synth_with_split(self, text: str) -> tuple:
        """切句合成：把長文拆句、各自合成、拼接。"""
        sentences = _split_sentences(text)
        chunks = []
        sample_rate = 24000
        silence = np.zeros(_SILENCE_SAMPLES, dtype=np.float32)

        for sent in sentences:
            audio, sr = self._kokoro.create(
                text=sent, voice=self._voice, speed=self._speed, lang=self._lang,
            )
            sample_rate = sr
            chunks.append(audio)
            chunks.append(silence)

        if chunks:
            chunks.pop()

        full_audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
        return full_audio, sample_rate

    async def synthesize_segment(
        self, segment: ScriptSegment, output_dir: Path
    ) -> AudioSegment:
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"audio_{segment.segment_id:03d}.wav"
        filepath = output_dir / filename

        self._ensure_model()

        loop = asyncio.get_running_loop()
        audio, sample_rate = await loop.run_in_executor(
            None, self._synth_with_split, segment.narration_text,
        )

        sf.write(str(filepath), audio, sample_rate)
        duration = len(audio) / sample_rate
        logger.info(
            f"Segment {segment.segment_id}: {duration:.1f}s audio "
            f"({len(segment.narration_text)} chars)"
        )

        return AudioSegment(
            segment_id=segment.segment_id,
            audio_path=str(filepath),
            duration_sec=duration,
        )

    async def synthesize_all(
        self, segments: List[ScriptSegment], output_dir: Path
    ) -> List[AudioSegment]:
        """多 process 並行合成所有段落。"""
        output_dir.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_running_loop()
        futures = []

        logger.info(
            f"TTS: synthesizing {len(segments)} segments "
            f"with {self._max_workers} workers (sentence splitting enabled)"
        )

        with ProcessPoolExecutor(max_workers=self._max_workers) as pool:
            for seg in segments:
                filepath = str(output_dir / f"audio_{seg.segment_id:03d}.wav")
                fut = loop.run_in_executor(
                    pool,
                    _synth_one_segment,
                    self._model_path,
                    self._voices_path,
                    self._voice,
                    self._speed,
                    self._lang,
                    seg.narration_text,
                    filepath,
                    seg.segment_id,
                )
                futures.append(fut)

            results = await asyncio.gather(*futures)

        # 依 segment_id 排序
        results_map = {sid: (path, dur) for sid, path, dur in results}
        audio_segments = []
        for seg in segments:
            path, dur = results_map[seg.segment_id]
            logger.info(
                f"Segment {seg.segment_id}: {dur:.1f}s audio "
                f"({len(seg.narration_text)} chars)"
            )
            audio_segments.append(AudioSegment(
                segment_id=seg.segment_id,
                audio_path=path,
                duration_sec=dur,
            ))

        total_dur = sum(a.duration_sec for a in audio_segments)
        logger.info(f"TTS done: {len(audio_segments)} segments, {total_dur:.1f}s total audio")
        return audio_segments
