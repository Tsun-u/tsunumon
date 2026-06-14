"""Gemini Fact-Checker — 用 Google Search grounding + code execution 驗證教案事實。

比 Claude fact-check 便宜 ~90%，且有 Google 搜尋引擎級別的事實查核能力。
"""

import json
import logging
from pathlib import Path

from google import genai
from google.genai import types

from app.models.domain import TeachingRequest, TeachingScript, ScriptSegment

logger = logging.getLogger(__name__)

# __file__ = /app/app/services/llm/gemini_fact_checker.py → .parent x4 = /app/
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "prompts"


class GeminiFactChecker:
    """用 Gemini + Google Search grounding 做事實審核。"""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.fact_check_template = (PROMPTS_DIR / "fact_checker.txt").read_text(
            encoding="utf-8"
        )

    async def fact_check_script(
        self, request: TeachingRequest, script: TeachingScript
    ) -> TeachingScript:
        """用 Gemini 審核教案事實，回傳修正後的 script。"""
        # 組裝 script JSON
        script_data = {
            "title": script.title,
            "scaffolding_strategy": script.scaffolding_strategy,
            "target_duration_min": script.target_duration_min,
            "segments": [
                {
                    "segment_id": s.segment_id,
                    "sequence_id": s.sequence_id,
                    "slide_title": s.slide_title,
                    "slide_html": s.slide_html,
                    "narration_text": s.narration_text,
                    "estimated_duration_sec": s.estimated_duration_sec,
                    "teaching_phase": s.teaching_phase,
                    "animated": s.animated,
                }
                for s in script.segments
            ],
        }

        # 組裝 prompt
        prompt = self.fact_check_template.format(
            course_requirement=request.course_requirement,
            student_persona=request.student_persona,
            script_json=json.dumps(script_data, ensure_ascii=False, indent=2),
            kb_urls="Use Google Search grounding to find authoritative references.",
        )

        logger.info(
            f"[{request.request_id}] Fact-check: Calling Gemini ({self.model})..."
        )

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[
                        types.Tool(google_search=types.GoogleSearch()),
                        types.Tool(code_execution=types.ToolCodeExecution),
                    ],
                    temperature=0.3,
                ),
            )

            result_text = response.text
            logger.info(
                f"[{request.request_id}] Fact-check: Gemini responded "
                f"({len(result_text)} chars)"
            )

            # 嘗試從回應中提取 JSON
            checked_data = self._extract_json(result_text)
            if checked_data is None:
                logger.warning(
                    f"[{request.request_id}] Fact-check: "
                    f"Could not parse Gemini response as JSON, keeping original"
                )
                return script

            # 記錄修正
            corrections = checked_data.get("corrections_made", [])
            if corrections:
                logger.info(
                    f"[{request.request_id}] Fact-check corrections: {corrections}"
                )
            else:
                logger.info(
                    f"[{request.request_id}] Fact-check corrections: "
                    f"No corrections needed"
                )

            # 解析修正後的 segments
            segments_data = checked_data.get("segments", [])
            if not segments_data:
                logger.warning(
                    f"[{request.request_id}] Fact-check: "
                    f"No segments in response, keeping original"
                )
                return script

            # 解析 Gemini 回傳的 segments
            checked_segments_raw = []
            for seg_data in segments_data:
                checked_segments_raw.append(
                    ScriptSegment(
                        segment_id=seg_data.get("segment_id", 0),
                        sequence_id=seg_data.get("sequence_id", 0),
                        slide_title=seg_data.get("slide_title", ""),
                        slide_html=seg_data.get("slide_html", ""),
                        narration_text=seg_data.get("narration_text", ""),
                        estimated_duration_sec=seg_data.get(
                            "estimated_duration_sec", 60.0
                        ),
                        teaching_phase=seg_data.get("teaching_phase", "core"),
                        animated=seg_data.get("animated", False),
                    )
                )

            # 安全閥：Gemini 回傳的 segments 數量 vs 原始
            if len(checked_segments_raw) >= len(script.segments) * 0.8:
                # 數量接近 → 直接使用完整回傳
                final_segments = checked_segments_raw
                logger.info(
                    f"[{request.request_id}] Fact-check: full replacement "
                    f"({len(final_segments)} segments)"
                )
            else:
                # 數量大幅減少 → 局部合併：用 segment_id 比對，只替換修正過的
                checked_by_id = {s.segment_id: s for s in checked_segments_raw}
                final_segments = []
                merged_count = 0
                for orig_seg in script.segments:
                    if orig_seg.segment_id in checked_by_id:
                        final_segments.append(checked_by_id[orig_seg.segment_id])
                        merged_count += 1
                    else:
                        final_segments.append(orig_seg)
                logger.info(
                    f"[{request.request_id}] Fact-check: partial merge — "
                    f"Gemini returned {len(checked_segments_raw)}/{len(script.segments)} segments, "
                    f"merged {merged_count} corrections into original"
                )

            checked = TeachingScript(
                request_id=script.request_id,
                title=checked_data.get("title", script.title),
                segments=final_segments,
                scaffolding_strategy=script.scaffolding_strategy,
                target_duration_min=script.target_duration_min,
            )
            checked.outline = script.outline

            logger.info(
                f"[{request.request_id}] Fact-checked script: "
                f"{len(checked.segments)} segments"
            )
            return checked

        except Exception as e:
            logger.warning(
                f"[{request.request_id}] Gemini fact-check failed ({e}), "
                f"keeping original"
            )
            return script

    def _extract_json(self, text: str) -> dict | None:
        """從 Gemini 回應中提取 JSON（可能混有自然語言）。"""
        # 嘗試直接解析
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        # 嘗試找 JSON block（```json ... ```）
        import re

        json_match = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 嘗試找第一個 { 到最後一個 }
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            try:
                return json.loads(text[first_brace : last_brace + 1])
            except json.JSONDecodeError:
                pass

        # 用 json_repair 嘗試
        try:
            import json_repair

            return json_repair.loads(text)
        except Exception:
            pass

        return None
