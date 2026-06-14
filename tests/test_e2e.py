"""端到端測試 — 送一個 request 進 pipeline，確認能產出 MP4。"""

import shutil
from pathlib import Path

import pytest

from app.models.domain import TeachingRequest
from app.orchestrator.pipeline import create_pipeline
from config.settings import Settings

# winget 安裝的 ffmpeg 路徑（PATH 可能還沒更新）
FFMPEG_WINGET = (
    r"C:\Users\ATone\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe"
)


def _find_ffmpeg() -> str:
    """找到可用的 ffmpeg 路徑。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    if Path(FFMPEG_WINGET).exists():
        return FFMPEG_WINGET
    raise RuntimeError("ffmpeg not found")


@pytest.fixture
def test_settings(tmp_path):
    return Settings(
        mode="dev",
        llm_backend="mock",
        tts_backend="mock",
        storage_backend="local",
        output_dir=str(tmp_path / "output"),
        ffmpeg_path=_find_ffmpeg(),
    )


@pytest.mark.asyncio
async def test_full_pipeline(test_settings, tmp_path):
    """完整 pipeline：mock LLM → mock slides → mock TTS → FFmpeg → local storage。"""
    pipeline = create_pipeline(test_settings)

    request = TeachingRequest(
        request_id="test-001",
        course_requirement="Newton's Laws of Motion",
        student_persona="High school student, 16 years old, basic algebra knowledge",
    )

    result = await pipeline.execute(request)

    # 確認有回傳 video_url
    assert result.video_url is not None
    assert "test-001" in result.video_url

    # 確認 MP4 檔案存在
    output_dir = Path(test_settings.output_dir) / "test-001"
    mp4_files = list(output_dir.glob("*.mp4"))
    assert len(mp4_files) >= 1, f"Expected MP4 file in {output_dir}"

    # 確認檔案大小 > 0
    mp4_path = mp4_files[0]
    assert mp4_path.stat().st_size > 0, "MP4 file is empty"

    print(f"\nPipeline success! Video: {mp4_path} ({mp4_path.stat().st_size} bytes)")


@pytest.fixture
def kokoro_settings(tmp_path):
    return Settings(
        mode="dev",
        llm_backend="mock",
        tts_backend="kokoro",
        slides_backend="html",
        storage_backend="local",
        output_dir=str(tmp_path / "output"),
        ffmpeg_path=_find_ffmpeg(),
    )


@pytest.mark.asyncio
async def test_pipeline_with_kokoro(kokoro_settings, tmp_path):
    """mock LLM + HTML slides + Kokoro TTS → FFmpeg → MP4 with real audio."""
    pipeline = create_pipeline(kokoro_settings)

    request = TeachingRequest(
        request_id="kokoro-001",
        course_requirement="Newton's Laws of Motion",
        student_persona="High school student, 16 years old, basic algebra knowledge",
    )

    result = await pipeline.execute(request)

    assert result.video_url is not None

    output_dir = Path(kokoro_settings.output_dir) / "kokoro-001"
    mp4_files = list(output_dir.glob("*.mp4"))
    assert len(mp4_files) >= 1

    mp4_path = mp4_files[0]
    assert mp4_path.stat().st_size > 0

    # Kokoro 產生的影片應該比靜音版大很多（有真實音訊）
    print(f"\nKokoro pipeline success! Video: {mp4_path} ({mp4_path.stat().st_size:,} bytes)")
