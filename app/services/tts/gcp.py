"""GCP Cloud Text-to-Speech — REST API + API Key 認證，免 service account。"""

import asyncio
import base64
import logging
import re
import struct
import wave
from xml.sax.saxutils import escape as xml_escape
from pathlib import Path
from typing import List

import httpx

from app.models.domain import AudioSegment, ScriptSegment
from app.services.tts.base import BaseTTSService

logger = logging.getLogger(__name__)

_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

# ------------------------------------------------------------------ #
#  SSML 自動轉換：讓專有名詞和數字的發音更清晰
# ------------------------------------------------------------------ #

# 化學式：H₂O, CO₂, H₂O₂, C₆H₁₂O₆, NAD+, NADH, ATP, ADP 等
_CHEMICAL_FORMULAS = {
    "H₂O₂": "H 2 O 2",
    "H₂O": "H 2 O",
    "CO₂": "C O 2",
    "C₆H₁₂O₆": "C 6 H 12 O 6",
    "O₂": "O 2",
    "N₂": "N 2",
}

# tsunumon 發音修正：台語 Tsùn-Ú + monster
# IPA: tsʊn.wuː.mɒn（需要 w 滑音才能讓 TTS 正確斷開 n 和 u）
_TSUNUMON_RE = re.compile(r"\btsunumon\b", re.IGNORECASE)

# 單字母變數：獨立出現的單字母（排除 a/A/I 等常見英文單字）
# 匹配前後為空格、標點或字串邊界的單字母，用 say-as 強制念成字母
# 例如 "m times v" 中的 v 不會被 TTS 念成 "versus"
_SINGLE_LETTER_VAR = re.compile(
    r'(?<![a-zA-Z])'           # 前面不是字母
    r'([b-hj-zB-HJ-Z])'       # 單字母（排除 a/A 冠詞和 i/I 代名詞）
    r'(?![a-zA-Z])'            # 後面不是字母
)

# 座標 / 數學表達式中的負號：(-2, 1) → "negative 2, 1"
_NEGATIVE_NUM = re.compile(r"[−–-](\d+(?:\.\d+)?)")

# 括號內的座標：(3, 5) or (-2, 1) — 前後加停頓
_COORDINATE = re.compile(
    r"\(([−–\-]?\d+(?:\.\d+)?)\s*,\s*([−–\-]?\d+(?:\.\d+)?)\)"
)

# 向量角括號 ⟨4, 3⟩
_VECTOR_BRACKET = re.compile(
    r"[⟨<]([−–\-]?\d+(?:\.\d+)?)\s*,\s*([−–\-]?\d+(?:\.\d+)?)[⟩>]"
)


def _escape_xml(text: str) -> str:
    """Escape XML special characters for SSML."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    text = text.replace("'", "&apos;")
    return text


def _spoken_number(match: re.Match) -> str:
    """將負號轉成 'negative'，讓 TTS 不會含糊帶過。"""
    return f"negative {match.group(1)}"


def _spoken_coordinate(match: re.Match) -> str:
    """座標加停頓和明確的負號發音。"""
    x = _NEGATIVE_NUM.sub(_spoken_number, match.group(1))
    y = _NEGATIVE_NUM.sub(_spoken_number, match.group(2))
    return f'<break time="200ms"/>({x}, {y})<break time="200ms"/>'


def _spoken_vector(match: re.Match) -> str:
    """向量表示加停頓。"""
    x = _NEGATIVE_NUM.sub(_spoken_number, match.group(1))
    y = _NEGATIVE_NUM.sub(_spoken_number, match.group(2))
    return f'<break time="200ms"/>{x}, {y}<break time="200ms"/>'


def _text_to_ssml(text: str) -> str:
    """將 plain text 轉成 SSML，加強專有名詞和數字的發音清晰度。"""
    # XML escape：避免 &, <, > 等字元破壞 SSML 結構
    text = xml_escape(text)

    # tsunumon 發音修正（xml_escape 後再替換，phoneme tag 不能被 escape）
    text = _TSUNUMON_RE.sub(
        '<phoneme alphabet="ipa" ph="tsʊn.wuː.mɒn">tsunumon</phoneme>',
        text,
    )

    # 單字母變數強制念成字母，避免 TTS 誤判（v→"versus", r→"are" 等）
    text = _SINGLE_LETTER_VAR.sub(
        lambda m: f'<say-as interpret-as="characters">{m.group(1)}</say-as>',
        text
    )

    # 先處理座標和向量（在其他 SSML 標記之前）
    text = _VECTOR_BRACKET.sub(_spoken_vector, text)
    text = _COORDINATE.sub(_spoken_coordinate, text)

    # 化學式替換成口語化發音
    for formula, spoken in _CHEMICAL_FORMULAS.items():
        if formula in text:
            text = text.replace(
                formula,
                f'<break time="150ms"/>'
                f'<say-as interpret-as="characters">{spoken}</say-as>'
                f'<break time="150ms"/>'
            )

    # 句子之間加微停頓（句號後已有自然停頓，分號和冒號後加一點）
    text = text.replace("; ", ';<break time="200ms"/> ')
    text = text.replace(": ", ':<break time="200ms"/> ')

    # 破折號（em dash）前後加停頓，讓語氣更清楚
    text = text.replace("—", '<break time="250ms"/>—<break time="250ms"/>')

    # 問句升調：讓問句聽起來更自然
    text = re.sub(
        r'([^.!?]{10,}\?)',
        lambda m: f'<prosody pitch="+1st" rate="102%">{m.group(1)}</prosody>',
        text
    )

    # 驚嘆句 / 強調句加一點力度
    text = re.sub(
        r'([^.!?]{10,}!)',
        lambda m: f'<prosody pitch="+0.5st" volume="+1dB">{m.group(1)}</prosody>',
        text
    )

    # 頭尾加靜音：開頭防 AAC encoder priming noise，結尾確保尾音完整 release
    return f"<speak><break time=\"200ms\"/>{text}<break time=\"200ms\"/></speak>"


class GCPTTSService(BaseTTSService):
    """Google Cloud Text-to-Speech（REST + API Key）。"""

    def __init__(
        self,
        api_key: str,
        voice_name: str = "en-US-Neural2-D",
        speaking_rate: float = 1.0,
        max_concurrent: int = 5,
    ):
        self._api_key = api_key
        self._voice_name = voice_name
        self._speaking_rate = speaking_rate
        self._max_concurrent = max_concurrent

    def _synth_one(self, text: str, output_path: str) -> float:
        """同步呼叫 GCP TTS REST API，回傳音訊長度（秒）。"""
        ssml = _text_to_ssml(text)
        payload = {
            "input": {"ssml": ssml},
            "voice": {
                "languageCode": self._voice_name[:5],  # "en-US"
                "name": self._voice_name,
            },
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": 24000,
                "speakingRate": self._speaking_rate,
            },
        }

        resp = httpx.post(
            _TTS_URL,
            params={"key": self._api_key},
            json=payload,
            timeout=60.0,
        )
        resp.raise_for_status()

        audio_b64 = resp.json()["audioContent"]
        audio_bytes = base64.b64decode(audio_b64)

        # LINEAR16 回傳的是 raw PCM（無 WAV header），自己包 WAV
        sample_rate = 24000
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(audio_bytes)

        duration = len(audio_bytes) / (sample_rate * 2)
        return max(0.1, duration)

    async def synthesize_segment(
        self, segment: ScriptSegment, output_dir: Path
    ) -> AudioSegment:
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"audio_{segment.segment_id:03d}.wav"
        filepath = output_dir / filename

        loop = asyncio.get_running_loop()
        duration = await loop.run_in_executor(
            None, self._synth_one, segment.narration_text, str(filepath),
        )

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
        """並行合成所有段落（受 max_concurrent 限制）。"""
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"GCP TTS: synthesizing {len(segments)} segments "
            f"(voice={self._voice_name}, concurrency={self._max_concurrent})"
        )

        sem = asyncio.Semaphore(self._max_concurrent)

        async def _do_one(seg: ScriptSegment) -> AudioSegment:
            async with sem:
                return await self.synthesize_segment(seg, output_dir)

        results = await asyncio.gather(*[_do_one(seg) for seg in segments])

        total_dur = sum(a.duration_sec for a in results)
        logger.info(f"GCP TTS done: {len(results)} segments, {total_dur:.1f}s total audio")
        return list(results)
