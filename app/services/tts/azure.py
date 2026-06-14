"""Azure Speech Service TTS — REST API，支援完整 SSML。"""

import asyncio
import logging
import wave
from pathlib import Path
from typing import List
from xml.sax.saxutils import escape as xml_escape

import re

import httpx

from app.models.domain import AudioSegment, ScriptSegment
from app.services.tts.base import BaseTTSService

# tsunumon 發音修正：台語 Tsùn-Ú + monster
# IPA: tsʊn.wuː.mɒn（需要 w 滑音才能讓 TTS 正確斷開 n 和 u）
_TSUNUMON_RE = re.compile(r"\btsunumon\b", re.IGNORECASE)

# Decimal numbers (e.g. 0.05, 3.14) → Azure TTS 偶爾會把句點當 sentence break。
# 解法：用 inline spoken form 取代（"zero point zero five"），avoid period 觸發切句。
_DECIMAL_RE = re.compile(r"(?<![\w.])(\d{1,4})\.(\d{1,6})(?![\w])")
# Fractions (e.g. 3/4, 1/2) → Azure 預設可能唸 "three slash four"。
# 解法：spoken-form idiom（"three fourths", "one half"），fallback "n over d"。
_FRACTION_RE = re.compile(r"(?<![\w/])(\d{1,3})/(\d{1,3})(?![\w/])")
_ELLIPSIS_RE = re.compile(r"\.{3,}")
_ELLIPSIS_PLACEHOLDER = "__TSUNUMON_ELLIPSIS_BREAK__"
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
          "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
# Fraction denominators → ordinal singular ("half", "third", ...)
_ORDINAL_SINGULAR = {
    2: "half", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth",
    10: "tenth", 11: "eleventh", 12: "twelfth",
    13: "thirteenth", 14: "fourteenth", 15: "fifteenth",
    16: "sixteenth", 17: "seventeenth", 18: "eighteenth",
    19: "nineteenth", 20: "twentieth",
    30: "thirtieth", 40: "fortieth", 50: "fiftieth",
    100: "hundredth", 1000: "thousandth",
}


def _int_to_words(n: int) -> str:
    if n < 10:
        return _ONES[n]
    if n < 20:
        return _TEENS[n - 10]
    if n < 100:
        if n % 10 == 0:
            return _TENS[n // 10]
        return f"{_TENS[n // 10]} {_ONES[n % 10]}"
    if n < 1000:
        if n % 100 == 0:
            return f"{_ONES[n // 100]} hundred"
        return f"{_ONES[n // 100]} hundred {_int_to_words(n % 100)}"
    return str(n)


def _decimal_to_spoken(int_part: str, dec_part: str) -> str:
    int_word = _int_to_words(int(int_part))
    dec_words = " ".join(_ONES[int(d)] for d in dec_part)
    return f"{int_word} point {dec_words}"


def _fraction_to_spoken(num_str: str, den_str: str) -> str:
    n, d = int(num_str), int(den_str)
    if d == 0:
        return f"{num_str}/{den_str}"  # leave division-by-zero alone
    num_word = _int_to_words(n)
    if d in _ORDINAL_SINGULAR:
        denom_word = _ORDINAL_SINGULAR[d]
        if n > 1:
            denom_word = "halves" if denom_word == "half" else denom_word + "s"
        return f"{num_word} {denom_word}"
    # fallback: "n over d" (covers awkward denominators like 17, 23, 250)
    return f"{num_word} over {_int_to_words(d)}"


def _sanitize_for_tts(text: str) -> str:
    """前處理 narration_text 避開 Azure TTS 切句陷阱。

    1) decimal numbers (e.g. 0.05) → spoken form ("zero point zero five")
    2) fractions (e.g. 3/4) → spoken idiom ("three fourths", "one half")
    3) ellipsis (... or 更多) → placeholder（之後在 SSML inject 階段換成 <break>）
    """
    text = _DECIMAL_RE.sub(
        lambda m: _decimal_to_spoken(m.group(1), m.group(2)),
        text,
    )
    text = _FRACTION_RE.sub(
        lambda m: _fraction_to_spoken(m.group(1), m.group(2)),
        text,
    )
    text = _ELLIPSIS_RE.sub(_ELLIPSIS_PLACEHOLDER, text)
    return text


logger = logging.getLogger(__name__)


class AzureTTSService(BaseTTSService):
    """Azure Cognitive Services Speech — REST API。"""

    def __init__(
        self,
        speech_key: str,
        speech_region: str = "eastus",
        voice_name: str = "en-US-DerekMultilingualNeural",
        pitch: str = "0%",
        volume: str = "0%",
        role: str = "YoungAdultMale",
        max_concurrent: int = 5,
    ):
        self._speech_key = speech_key
        self._speech_region = speech_region
        self._voice_name = voice_name
        self._pitch = pitch
        self._volume = volume
        self._role = role
        self._max_concurrent = max_concurrent
        self._tts_url = (
            f"https://{speech_region}.tts.speech.microsoft.com"
            f"/cognitiveservices/v1"
        )

    def _synth_one(self, text: str, output_path: str) -> float:
        """同步呼叫 Azure TTS REST API，回傳音訊長度（秒）。"""
        # Pre-sanitize narration before XML escape — protect against TTS sentence-break artifacts
        # (decimal numbers like "0.05" being split into "0" + "05", ellipsis "..." being lost as period)
        text = _sanitize_for_tts(text)

        escaped = xml_escape(text)

        # tsunumon 發音修正（xml_escape 後再替換，phoneme tag 不能被 escape）
        escaped = _TSUNUMON_RE.sub(
            '<phoneme alphabet="ipa" ph="tsʊn.wuː.mɒn">tsunumon</phoneme>',
            escaped,
        )

        # Inject SSML <break> for ellipsis placeholder (placeholder survives xml_escape unchanged)
        escaped = escaped.replace(_ELLIPSIS_PLACEHOLDER, '<break time="500ms"/>')

        # 組合 SSML：支援 role（如 YoungAdultMale）和 prosody 調整
        inner = (
            f'<break time="200ms"/>'
            f'{escaped}'
            f'<break time="200ms"/>'
        )

        # 套用 role（如果有設定且非空）
        if self._role:
            inner = (
                f'<mstts:express-as role="{self._role}">'
                f'{inner}'
                f'</mstts:express-as>'
            )

        # 套用 prosody（如果有調整）
        if self._pitch != "0%" or self._volume != "0%":
            inner = (
                f'<prosody pitch="{self._pitch}" volume="{self._volume}">'
                f'{inner}'
                f'</prosody>'
            )

        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xmlns:mstts="http://www.w3.org/2001/mstts" '
            f'xml:lang="en-US">'
            f'<voice name="{self._voice_name}">'
            f'{inner}'
            f'</voice>'
            f'</speak>'
        )

        resp = httpx.post(
            self._tts_url,
            headers={
                "Ocp-Apim-Subscription-Key": self._speech_key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
            },
            content=ssml.encode("utf-8"),
            timeout=60.0,
        )
        resp.raise_for_status()

        # Azure 回傳完整 WAV（含 header），直接寫入
        with open(output_path, "wb") as f:
            f.write(resp.content)

        # 計算音訊長度（WAV header 44 bytes）
        audio_bytes = len(resp.content) - 44
        sample_rate = 24000
        duration = max(0.1, audio_bytes / (sample_rate * 2))
        return duration

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
            f"({len(segment.narration_text)} chars) [Azure]"
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
            f"Azure TTS: synthesizing {len(segments)} segments "
            f"(voice={self._voice_name}, concurrency={self._max_concurrent})"
        )

        sem = asyncio.Semaphore(self._max_concurrent)

        async def _do_one(seg: ScriptSegment) -> AudioSegment:
            async with sem:
                return await self.synthesize_segment(seg, output_dir)

        results = await asyncio.gather(*[_do_one(seg) for seg in segments])

        total_dur = sum(a.duration_sec for a in results)
        logger.info(f"Azure TTS done: {len(results)} segments, {total_dur:.1f}s total audio")
        return list(results)
