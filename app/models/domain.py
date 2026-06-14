"""tsunumon 核心資料模型 — 定義 pipeline 各模組之間的資料契約。"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# === Input from competition platform ===

class TeachingRequest(BaseModel):
    """比賽平台發送的教學需求。"""
    request_id: str
    course_requirement: str
    student_persona: str


# === Step 1: Course Outline (LLM 第一階段輸出) ===

class PersonaAnalysis(BaseModel):
    """學生背景分析結果。"""
    prior_knowledge: str
    knowledge_gaps: str
    depth_decisions: str


class SequenceOutline(BaseModel):
    """課程大綱中的一個教學段落。"""
    sequence_id: int
    title: str
    teaching_phase: str  # "hook", "intro", "core", "example", "summary"
    objectives: List[str]
    key_concepts: List[str]
    visual_strategy: str  # "bullets", "diagram", "code", "equation", "comparison", "timeline"
    estimated_duration_min: float


class CourseOutline(BaseModel):
    """LLM 第一階段輸出：課程大綱。"""
    course_topic: str
    target_audience: str
    subject: str = "other"  # "physics", "math", "biology", "cs", "other"
    learning_objectives: List[str]
    scaffolding_strategy: str
    persona_analysis: PersonaAnalysis
    sequences: List[SequenceOutline]
    references: List[str] = []  # 來源標示（web_search 查核的資料來源）


# === Step 2: Teaching Script (LLM 第二階段輸出) ===

class ScriptSegment(BaseModel):
    """一張投影片的完整內容：視覺 + 旁白。"""
    segment_id: int
    sequence_id: int = 0  # 對應 CourseOutline 的 sequence_id
    slide_title: str
    slide_html: str  # 投影片的 HTML body 內容（由 LLM 直接生成）
    narration_text: str
    estimated_duration_sec: float
    teaching_phase: str  # "hook", "intro", "core", "example", "summary"
    animated: bool = False  # True = CSS animation slide，Playwright 錄影而非截圖
    cover_image_prompt: Optional[str] = None  # 僅 cover segment 使用，餵給 image gen race
    # 舊欄位保留向下相容（mock 等仍可使用）
    slide_bullets: Optional[List[str]] = None
    slide_visual_hint: Optional[str] = None


class TeachingScript(BaseModel):
    """LLM 生成的完整教學腳本（兩階段整合結果）。"""
    request_id: str
    title: str
    outline: Optional[CourseOutline] = None  # 保留大綱供後續參考
    segments: List[ScriptSegment]
    scaffolding_strategy: str
    target_duration_min: int = 15


# === Intermediate artifacts ===

class SlideImage(BaseModel):
    """一張已渲染的投影片（靜態 PNG 或動畫 MP4）。"""
    segment_id: int
    image_path: str  # PNG 截圖路徑（靜態 slide 用）
    video_path: Optional[str] = None  # MP4 路徑（animated slide 用，優先於 image_path）
    width: int = 1280
    height: int = 720


class AudioSegment(BaseModel):
    """一段已合成的語音檔。"""
    segment_id: int
    audio_path: str
    duration_sec: float


# === Final output ===

class VideoResult(BaseModel):
    """FFmpeg 合成的影片結果。"""
    video_path: str
    subtitle_path: Optional[str] = None
    duration_sec: float


class PipelineResult(BaseModel):
    """回傳給比賽平台的最終結果。"""
    video_url: str
    subtitle_url: Optional[str] = None
    supplementary_url: Optional[List[str]] = None
