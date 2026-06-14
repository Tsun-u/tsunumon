"""Claude LLM 整合測試 — 用真實 API 測試兩階段腳本生成。

需要 ANTHROPIC_API_KEY 環境變數。
執行：pytest tests/test_claude_llm.py -v -s
"""

import json
import os
import sys

# Windows cp950 編碼問題：確保 stdout 能處理 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pytest

from app.models.domain import TeachingRequest
from config.settings import Settings

# 透過 Settings 載入 .env，檢查是否有 API key
_settings = Settings()
_has_api_key = _settings.anthropic_api_key.get_secret_value() != ""

pytestmark = pytest.mark.skipif(
    not _has_api_key,
    reason="ANTHROPIC_API_KEY not set in .env",
)


@pytest.fixture
def claude_settings():
    return Settings(
        mode="dev",
        llm_backend="claude",
        llm_model="claude-haiku-4-5-20251001",
        tts_backend="mock",
        storage_backend="local",
    )


@pytest.fixture
def claude_llm(claude_settings):
    from app.services.llm.claude import ClaudeLLMService
    return ClaudeLLMService(claude_settings)


@pytest.fixture
def sample_request():
    return TeachingRequest(
        request_id="test-claude-001",
        course_requirement="Explain Newton's Second Law of Motion (F=ma), including its derivation and real-world applications",
        student_persona="I am a 10th grade student. I understand basic algebra and have learned about velocity and acceleration, but I haven't studied forces in detail yet.",
    )


@pytest.mark.asyncio
async def test_step1_outline(claude_llm, sample_request):
    """Step 1: 測試課程大綱生成。"""
    outline = await claude_llm.generate_outline(sample_request)

    print(f"\n{'='*60}")
    print(f"Course Topic: {outline.course_topic}")
    print(f"Target Audience: {outline.target_audience}")
    print(f"Strategy: {outline.scaffolding_strategy}")
    print(f"\nLearning Objectives:")
    for obj in outline.learning_objectives:
        print(f"  - {obj}")
    print(f"\nPersona Analysis:")
    print(f"  Prior knowledge: {outline.persona_analysis.prior_knowledge}")
    print(f"  Knowledge gaps: {outline.persona_analysis.knowledge_gaps}")
    print(f"  Depth decisions: {outline.persona_analysis.depth_decisions}")
    print(f"\nSequences ({len(outline.sequences)}):")
    for seq in outline.sequences:
        print(f"  {seq.sequence_id}. [{seq.teaching_phase}] {seq.title} ({seq.estimated_duration_min}min)")
        print(f"     Concepts: {', '.join(seq.key_concepts)}")
        print(f"     Visual: {seq.visual_strategy}")
    print(f"{'='*60}")

    assert outline.course_topic, "course_topic should not be empty"
    assert len(outline.sequences) >= 3, f"Expected at least 3 sequences, got {len(outline.sequences)}"
    assert outline.persona_analysis.prior_knowledge, "persona_analysis should be filled"


@pytest.mark.asyncio
async def test_step2_expand(claude_llm, sample_request):
    """Step 2: 測試大綱展開為完整腳本。"""
    # 先生成大綱
    outline = await claude_llm.generate_outline(sample_request)
    print(f"\nOutline: {outline.course_topic}, {len(outline.sequences)} sequences")

    # 再展開
    script = await claude_llm.expand_outline(sample_request, outline)

    print(f"\n{'='*60}")
    print(f"Title: {script.title}")
    print(f"Segments: {len(script.segments)}")
    total_duration = sum(s.estimated_duration_sec for s in script.segments)
    total_words = sum(len(s.narration_text.split()) for s in script.segments)
    print(f"Total duration: {total_duration:.0f}s ({total_duration/60:.1f}min)")
    print(f"Total words: {total_words} (~{total_words/150:.1f}min at 150wpm)")
    print()
    for seg in script.segments:
        words = len(seg.narration_text.split())
        print(f"  Seg {seg.segment_id} [{seg.teaching_phase}] {seg.slide_title}")
        # 現 schema 用 slide_html（slide_bullets / slide_visual_hint 是舊欄位、預設 None）
        html_preview = (seg.slide_html or "").replace("\n", " ")[:80]
        print(f"    HTML: {html_preview}{'...' if seg.slide_html and len(seg.slide_html) > 80 else ''}")
        print(f"    Narration: {words} words, {seg.estimated_duration_sec:.0f}s")
        print(f"    Preview: {seg.narration_text[:100]}...")
        print()
    print(f"{'='*60}")

    assert script.title, "title should not be empty"
    assert len(script.segments) >= 3, f"Expected at least 3 segments, got {len(script.segments)}"
    for seg in script.segments:
        assert seg.narration_text, f"Segment {seg.segment_id} has empty narration"
        assert len(seg.narration_text.split()) > 10, f"Segment {seg.segment_id} narration too short"
