"""LLM service 抽象介面 — 兩階段教學腳本生成。"""

from abc import ABC, abstractmethod
from typing import Optional

from app.models.domain import CourseOutline, TeachingRequest, TeachingScript


class BaseLLMService(ABC):
    @abstractmethod
    async def generate_outline(self, request: TeachingRequest, outline_model: Optional[str] = None, **kwargs) -> CourseOutline:
        """Step 1: 根據教學需求生成課程大綱（含事實查核）。outline_model 可覆蓋預設模型。

        **kwargs 吸收真 LLM 子類（claude）才用到的額外參數（如 focus_level）；
        mock 等簡易子類可忽略。"""
        ...

    @abstractmethod
    async def expand_outline(self, request: TeachingRequest, outline: CourseOutline, **kwargs) -> TeachingScript:
        """Step 2: 將課程大綱展開為投影片內容 + 旁白腳本。

        **kwargs 吸收真 LLM 子類才用的額外參數（如 focus_level、kb_unit_urls）。"""
        ...

    async def review_script(self, request: TeachingRequest, script: TeachingScript) -> TeachingScript:
        """Step 2b: 審修腳本（事實查核 + 旁白品質）。預設不做，子類別可覆寫。"""
        return script

    async def improve_script(
        self,
        request: TeachingRequest,
        previous_script: TeachingScript,
        consultant_notes: dict | None = None,
        **kwargs,
    ) -> TeachingScript:
        """進化版教師：基於舊教案改良。預設不做，子類別可覆寫。

        consultant_notes: optional advisory markdown from the Consultant step
        (keys: "gpt", "gemini"). Improver has full authority to use or ignore.
        """
        return previous_script

    async def fact_check_script(self, request: TeachingRequest, script: TeachingScript) -> TeachingScript:
        """Step 1d: 事實審核（修正事實錯誤）。預設原樣返回、不做查核；
        真 LLM 子類（claude）或 gemini fact checker 可覆寫。"""
        return script

    async def generate_teaching_script(self, request: TeachingRequest) -> TeachingScript:
        """兩階段生成完整教學腳本（預設實作，子類別可覆寫）。"""
        outline = await self.generate_outline(request)
        script = await self.expand_outline(request, outline)
        script.outline = outline
        return script
