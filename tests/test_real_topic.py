"""用比賽網站的真實題目測試完整 pipeline。

題目來源：https://teaching.monster/watch/25
主題：Circular Motion / Kepler's Third Law
"""

import shutil
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pytest

from app.models.domain import TeachingRequest
from app.orchestrator.pipeline import create_pipeline
from config.settings import Settings

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
    reason="ANTHROPIC_API_KEY not set",
)


@pytest.fixture
def real_settings(tmp_path):
    return Settings(
        mode="dev",
        llm_backend="claude",
        llm_model="claude-haiku-4-5-20251001",
        tts_backend="kokoro",
        slides_backend="html",
        storage_backend="local",
        output_dir=str(tmp_path / "output"),
        ffmpeg_path=_find_ffmpeg(),
    )


@pytest.mark.asyncio
async def test_kepler_third_law(real_settings, tmp_path):
    """真實題目：Circular Motion / Kepler's Third Law for junior high school."""
    pipeline = create_pipeline(real_settings)

    request = TeachingRequest(
        request_id="kepler-001",
        course_requirement=(
            "Describe orbital motion using Kepler's third law. "
            "Students must understand that the centripetal acceleration is "
            "provided solely by gravitational attraction, and grasp how "
            "the period of the orbit is related to the radius and the mass "
            "of the central body. "
            "Unit: Force and Translational Dynamics - Circular Motion."
        ),
        student_persona=(
            "Junior high school student, slow learner requiring additional "
            "scaffolding. Enjoys derivation-based explanations showing "
            "mathematical development. Prefers application-first methodology, "
            "prioritizing practical uses before theoretical abstraction."
        ),
    )

    result = await pipeline.execute(request)

    assert result.video_url is not None

    output_dir = Path(real_settings.output_dir) / "kepler-001"
    mp4_files = list(output_dir.glob("*.mp4"))
    assert len(mp4_files) >= 1

    slides_dir = output_dir / "slides"
    slide_files = sorted(slides_dir.glob("*.png"))

    print(f"\n{'='*60}")
    print(f"Topic: Kepler's Third Law")
    print(f"Video: {mp4_files[0]} ({mp4_files[0].stat().st_size:,} bytes)")
    print(f"Slides: {len(slide_files)} images")
    for sf in slide_files:
        print(f"  - {sf.name} ({sf.stat().st_size:,} bytes)")
    print(f"{'='*60}")
