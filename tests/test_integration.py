"""整合測試 — Claude LLM + Pillow slides + mock TTS → FFmpeg → MP4。

需要 ANTHROPIC_API_KEY 環境變數。
執行：pytest tests/test_integration.py -v -s
"""

import shutil
import sys
from pathlib import Path

# Windows cp950 編碼問題
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pytest

from app.models.domain import TeachingRequest
from app.orchestrator.pipeline import create_pipeline
from config.settings import Settings

# 透過 Settings 載入 .env，檢查是否有 API key
_settings = Settings()
_has_api_key = _settings.anthropic_api_key.get_secret_value() != ""

FFMPEG_WINGET = (
    r"C:\Users\ATone\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe"
)


def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    if Path(FFMPEG_WINGET).exists():
        return FFMPEG_WINGET
    raise RuntimeError("ffmpeg not found")


pytestmark = pytest.mark.skipif(
    not _has_api_key,
    reason="ANTHROPIC_API_KEY not set in .env",
)


@pytest.fixture
def integration_settings(tmp_path):
    return Settings(
        mode="dev",
        llm_backend="claude",
        llm_model="claude-haiku-4-5-20251001",
        tts_backend="mock",
        slides_backend="html",
        storage_backend="local",
        output_dir=str(tmp_path / "output"),
        ffmpeg_path=_find_ffmpeg(),
    )


@pytest.mark.asyncio
async def test_claude_pillow_pipeline(integration_settings, tmp_path):
    """Claude LLM → Pillow slides → mock TTS → FFmpeg → MP4。"""
    pipeline = create_pipeline(integration_settings)

    request = TeachingRequest(
        request_id="integ-001",
        course_requirement="Explain the concept of recursion in programming, "
        "including base cases, recursive calls, and common examples like "
        "factorial and Fibonacci.",
        student_persona="I am a first-year computer science student. "
        "I understand loops and functions but have never seen recursion before.",
    )

    result = await pipeline.execute(request)

    # 確認有回傳 video_url
    assert result.video_url is not None
    assert "integ-001" in result.video_url

    # 確認 MP4 存在
    output_dir = Path(integration_settings.output_dir) / "integ-001"
    mp4_files = list(output_dir.glob("*.mp4"))
    assert len(mp4_files) >= 1

    mp4_path = mp4_files[0]
    assert mp4_path.stat().st_size > 0

    # 確認投影片 PNG 存在
    slides_dir = output_dir / "slides"
    slide_files = sorted(slides_dir.glob("*.png"))
    assert len(slide_files) >= 3, f"Expected at least 3 slides, got {len(slide_files)}"

    print(f"\n{'='*60}")
    print(f"Integration test passed!")
    print(f"Video: {mp4_path} ({mp4_path.stat().st_size:,} bytes)")
    print(f"Slides: {len(slide_files)} images in {slides_dir}")
    for sf in slide_files:
        print(f"  - {sf.name} ({sf.stat().st_size:,} bytes)")
    print(f"{'='*60}")
