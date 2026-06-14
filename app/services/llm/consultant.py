"""Teaching Consultants — Expand 後、Improve 前跑兩個他家 LLM 給 advisory notes。

GPT 顧問（gpt-5.4）+ Gemini 顧問（gemini-3.1）各自讀 Expander 初稿，
輸出 segment-level 建議（tone / example / adaptability 三個維度），
提供給 Improver (Sonnet) 作為參考。Improver 有完全專業判斷權，可採可棄。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from google import genai
from google.genai import types as gtypes

from app.models.domain import TeachingRequest, TeachingScript

logger = logging.getLogger(__name__)

# __file__ = /app/app/services/llm/consultant.py → parent x4 = /app/
PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "config" / "prompts"
)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# 每則建議的必要欄位
_REQUIRED_KEYS = {"segment_id", "issue_type", "what_you_see", "suggestion"}
_ALLOWED_ISSUE_TYPES = {"tone", "example", "adaptability"}

_GPT_STYLE = (
    "   Your strength as a consultant: warm, approachable narration. "
    "When flagging tone issues, suggest specific ways to make the narration "
    "feel like chatting with a knowledgeable friend — casual observations, "
    "relatable reactions, playful asides, small moments of shared experience "
    "that make the student smile."
)

_GEMINI_STYLE = (
    "   Your strength as a consultant: sharp, memorable phrasing. "
    "When flagging tone issues, suggest specific punchlines, unexpected "
    "analogies, or one-liner reframings that would make the segment stick "
    "in the student's memory — the kind of line they'd quote to a friend later."
)


class ConsultantService:
    """Expand 後的 advisory step — 平行跑兩個他家 LLM consultant。"""

    def __init__(
        self,
        openai_api_key: str,
        gemini_api_key: str,
        gpt_model: str = "gpt-5.4",
        gemini_model: str = "gemini-3.1",
        timeout_sec: int = 120,
    ):
        self._openai_api_key = openai_api_key
        self._gemini_client = genai.Client(api_key=gemini_api_key)
        self._gpt_model = gpt_model
        self._gemini_model = gemini_model
        self._timeout_sec = timeout_sec
        self._prompt_template = (PROMPTS_DIR / "consultant.txt").read_text(
            encoding="utf-8"
        )

    async def consult(
        self,
        request: TeachingRequest,
        draft: TeachingScript,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Run both consultants in parallel. Returns {"gpt": [...], "gemini": [...]}.

        失敗的那邊會回 [] 並記 warning — 不應該 block pipeline。
        """
        gpt_prompt = self._build_prompt(request, draft, consultant_style=_GPT_STYLE)
        gemini_prompt = self._build_prompt(request, draft, consultant_style=_GEMINI_STYLE)

        gpt_task = asyncio.create_task(self._run_gpt(gpt_prompt))
        gemini_task = asyncio.create_task(self._run_gemini(gemini_prompt))

        gpt_notes, gemini_notes = await asyncio.gather(
            gpt_task, gemini_task, return_exceptions=True
        )

        def _unwrap(res: Any, label: str) -> List[Dict[str, Any]]:
            if isinstance(res, Exception):
                logger.warning(
                    f"Consultant {label} failed ({type(res).__name__}: {res}), "
                    f"returning empty notes"
                )
                return []
            return res

        return {
            "gpt": _unwrap(gpt_notes, "gpt"),
            "gemini": _unwrap(gemini_notes, "gemini"),
        }

    def _build_prompt(
        self, request: TeachingRequest, draft: TeachingScript,
        consultant_style: str = "",
    ) -> str:
        draft_data = {
            "title": draft.title,
            "scaffolding_strategy": draft.scaffolding_strategy,
            "target_duration_min": draft.target_duration_min,
            "segments": [
                {
                    "segment_id": s.segment_id,
                    "sequence_id": s.sequence_id,
                    "slide_title": s.slide_title,
                    "slide_html": s.slide_html,
                    "narration_text": s.narration_text,
                    "estimated_duration_sec": s.estimated_duration_sec,
                    "teaching_phase": s.teaching_phase,
                }
                for s in draft.segments
            ],
        }
        draft_json = json.dumps(draft_data, ensure_ascii=False, indent=2)
        return self._prompt_template.format(
            course_requirement=request.course_requirement,
            student_persona=request.student_persona,
            previous_script_json=draft_json,
            consultant_style=consultant_style,
        )

    async def _run_gpt(self, prompt: str) -> List[Dict[str, Any]]:
        """Call OpenAI chat completions with JSON response format."""
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            resp = await client.post(
                _OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {self._openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._gpt_model,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    # JSON array 不是 object — OpenAI 要求 prompt 包 "json" 字才能用
                    # json_object mode。我們已在 prompt 要求 JSON array，直接收字串自己 parse。
                    # gpt-5.5 only supports default temperature (1); omit to stay compatible.
                    "max_completion_tokens": 2000,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            notes = self._parse_notes(content)
            logger.info(
                f"GPT consultant ({self._gpt_model}): {len(notes)} notes"
            )
            return notes

    async def _run_gemini(self, prompt: str) -> List[Dict[str, Any]]:
        """Call Gemini with the same prompt; extract JSON array from response.

        Gemini 3.x Pro spends a big chunk of the output budget on internal
        reasoning tokens before emitting JSON, so the visible JSON can get
        truncated. We ask for 8192 output tokens and fall back to json_repair
        on malformed output (see _parse_notes).
        """
        response = await self._gemini_client.aio.models.generate_content(
            model=self._gemini_model,
            contents=prompt,
            config=gtypes.GenerateContentConfig(
                temperature=0.4,
                max_output_tokens=8192,
                automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(
                    disable=True,
                ),
            ),
        )
        content = response.text or ""
        notes = self._parse_notes(content)
        logger.info(
            f"Gemini consultant ({self._gemini_model}): {len(notes)} notes"
        )
        return notes

    @staticmethod
    def _parse_notes(raw: str) -> List[Dict[str, Any]]:
        """Parse JSON array from LLM output. Strip markdown fences if present.

        Keeps only well-formed entries (required keys present, allowed issue_type).
        Silently drops malformed ones — Consultant is advisory, not load-bearing.
        On JSON decode failure, falls back to json_repair to recover truncated
        or near-valid JSON (Gemini 3 Pro often truncates mid-string due to
        reasoning-token budget).
        """
        text = raw.strip()
        # strip ```json ... ``` or ``` ... ```
        if text.startswith("```"):
            lines = text.splitlines()
            # remove first fence line and find closing fence
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        if not text:
            return []

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(
                f"Consultant returned non-JSON ({e}), attempting json_repair; "
                f"first 200 chars: {text[:200]!r}"
            )
            try:
                import json_repair
                parsed = json_repair.loads(text)
                logger.info("Consultant JSON auto-repaired by json_repair")
            except Exception as repair_err:
                logger.warning(f"json_repair also failed: {repair_err}, returning empty notes")
                return []

        if not isinstance(parsed, list):
            logger.warning(
                f"Consultant returned non-array ({type(parsed).__name__}); "
                f"ignoring"
            )
            return []

        valid: List[Dict[str, Any]] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            if not _REQUIRED_KEYS.issubset(entry.keys()):
                continue
            if entry.get("issue_type") not in _ALLOWED_ISSUE_TYPES:
                continue
            try:
                seg_id = int(entry["segment_id"])
            except (TypeError, ValueError):
                continue
            valid.append(
                {
                    "segment_id": seg_id,
                    "issue_type": entry["issue_type"],
                    "what_you_see": str(entry["what_you_see"]),
                    "suggestion": str(entry["suggestion"]),
                }
            )
        return valid

    @staticmethod
    def format_for_improver(notes: List[Dict[str, Any]]) -> str:
        """Render consultant notes as a human-readable block for the improver prompt."""
        if not notes:
            return "_(no issues flagged)_"
        lines = []
        for n in notes:
            lines.append(
                f"- **[segment {n['segment_id']}] ({n['issue_type']})** "
                f"_See_: {n['what_you_see']} "
                f"_Suggest_: {n['suggestion']}"
            )
        return "\n".join(lines)
