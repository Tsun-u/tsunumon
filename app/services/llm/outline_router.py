"""Persona Classifier — GPT-5.4-Mini 判斷學生專注力 + 是否鑽研型，決定 Outline 模型。

路由規則：Opus 只在 depth_seeking=True 且 focus_level=normal 時觸發（鑽研型學生在 normal 專注下，Opus 提供額外深度加成）。其餘一律 Sonnet。
"""

import json
import logging
from typing import Tuple

import httpx

logger = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

_SYSTEM_PROMPT = """\
You classify a student's attention and learning motivation for an AI tutoring system.

## Focus level (binary)
- "low": explicit "Focus Level: Low" / focus duration <=10 minutes / phrases like "easily distracted" / "gets bored quickly"
- "normal": everything else. Default "normal" when in doubt.

## Depth-seeking (binary)
Whether the student actively wants rigor/derivation beyond the minimum needed.
- true: explicit signals — (a) tags "Depth Preference: Principles-first" / "Preferred Explanation Style: Derivation" / "Learning Motivation: Research papers", or natural-language equivalents ("analytical/model-based learners", "wants derivation", "research-oriented", "enjoys proof", "reasoning from conceptual principles"); OR (b) **course requirement Bloom's Taxonomy includes "analyze" + "create"** (any 2 of analyze/evaluate/create alongside understand/apply means the lesson must reach higher-order thinking, which benefits from Opus's deeper outline planning).
- false: everything else. High prior knowledge alone is NOT enough. Bloom understand+apply alone is NOT enough. Default "false" when in doubt.

Respond with ONLY a JSON object:
{"focus_level": "low"|"normal", "depth_seeking": true|false, "reasoning": "<one short sentence>"}"""


class OutlineRouter:
    """判斷 persona → 決定 Outline 用 Opus 還是 Sonnet。"""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.4-mini",
        opus_model: str = "claude-opus-4-6",
        sonnet_model: str = "claude-sonnet-4-6",
    ):
        self._api_key = api_key
        self._model = model
        self._opus_model = opus_model
        self._sonnet_model = sonnet_model

    def route(
        self, course_requirement: str, student_persona: str
    ) -> Tuple[str, bool, str]:
        """判斷 persona 並回傳 (outline_model, depth_seeking, focus_level)。
        失敗時回傳 (Sonnet, False, "normal")。"""
        try:
            user_msg = f"Topic: {course_requirement}\nStudent: {student_persona}"

            resp = httpx.post(
                _OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                    "max_completion_tokens": 150,
                },
                timeout=10.0,
            )
            resp.raise_for_status()

            content = resp.json()["choices"][0]["message"]["content"]
            result = json.loads(content)
            focus_level = str(result.get("focus_level", "normal")).lower()
            depth_seeking = bool(result.get("depth_seeking", False))
            reasoning = result.get("reasoning", "")

            if focus_level not in ("low", "normal"):
                logger.warning(f"Router: invalid focus_level={focus_level!r}, defaulting to normal")
                focus_level = "normal"

            # Opus only when the student wants depth AND can sustain attention.
            use_opus = depth_seeking and focus_level == "normal"
            chosen = self._opus_model if use_opus else self._sonnet_model
            logger.info(
                f"Router: depth_seeking={depth_seeking}, focus_level={focus_level}, "
                f"model={chosen}, reason={reasoning}"
            )
            return chosen, depth_seeking, focus_level

        except Exception as e:
            logger.warning(f"Router failed ({type(e).__name__}: {e}), defaulting to Sonnet + focus=normal")
            return self._sonnet_model, False, "normal"
