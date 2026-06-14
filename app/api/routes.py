"""API routes — 比賽平台打這裡。"""

import asyncio
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.schemas import GenerateRequest, GenerateResponse
from app.models.domain import TeachingRequest
from app.orchestrator.pipeline import get_pipeline
from config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# 同時間只允許一個 pipeline 執行，其他排隊等
_pipeline_semaphore = asyncio.Semaphore(1)

# Connection Test Topic 的關鍵字
_TEST_KEYWORDS = ("connection test", "test connection")


async def _get_test_video_url() -> str:
    """取得或建立連線測試用的 MP4（含 mascot 圖片驗證），回傳下載 URL。"""
    output_dir = Path(settings.output_dir)
    test_dir = output_dir / "test_connection"
    test_video = test_dir / "output.mp4"

    # 每次都重新生成（驗證 mascot 圖片是否可用）
    test_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 用 Playwright 渲染含 mascot 的測試 slide
    from app.services.slides.html_renderer import _wrap_html
    test_slide_html = _wrap_html(
        '<div class="slide phase-hook" style="display:flex; align-items:center; '
        'justify-content:center; gap:40px; background:linear-gradient(135deg,#1a237e,#283593);">'
        '<img src="{{MASCOT_DIR}}/greeting.png" style="width:200px; height:auto;" />'
        '<div style="text-align:center;">'
        '<div style="font-size:56px; font-weight:bold; color:white; '
        'text-shadow:2px 2px 4px rgba(0,0,0,0.5);">tsunumon</div>'
        '<div style="font-size:28px; color:#90caf9; margin-top:12px;">'
        'Connection Test OK ✓</div>'
        '</div></div>'
    )
    html_path = test_dir / "test_slide.html"
    html_path.write_text(test_slide_html, encoding="utf-8")

    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 1280, "height": 720})
    try:
        await page.goto(f"file:///{html_path.as_posix()}")
        png_path = test_dir / "test_slide.png"
        await page.screenshot(path=str(png_path), full_page=False)
        logger.info(f"Test slide rendered: {png_path}")
    finally:
        await page.close()
        await browser.close()
        await pw.stop()

    # Step 2: FFmpeg 把截圖轉成 3 秒影片
    cmd = [
        settings.ffmpeg_path, "-y",
        "-loop", "1", "-i", str(png_path),
        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", "3",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
        "-shortest",
        str(test_video),
    ]
    logger.info(f"Generating test video: {test_video}")
    await asyncio.get_running_loop().run_in_executor(
        None, lambda: subprocess.run(cmd, capture_output=True, timeout=30)
    )
    logger.info(f"Test video ready: {test_video.exists()}")

    base_url = settings.base_url or f"http://localhost:{settings.port}"
    return f"{base_url}/files/test_connection/output.mp4"


def _is_connection_test(req: GenerateRequest) -> bool:
    """偵測是否為平台的連線測試請求。"""
    if req.request_id == "test_connection":
        return True
    topic = req.course_requirement.lower()
    return any(kw in topic for kw in _TEST_KEYWORDS)


@router.post("/generate", response_model=GenerateResponse)
async def generate_video(req: GenerateRequest) -> GenerateResponse:
    """接收教學需求，同步生成影片後回傳 URL。"""
    logger.info(f"[{req.request_id}] Received: {req.course_requirement!r}")
    logger.info(f"[{req.request_id}] Student: {req.student_persona!r}")

    # 比賽平台的連線測試 → 含 mascot 的測試影片
    if _is_connection_test(req):
        url = await _get_test_video_url()
        logger.info(f"[{req.request_id}] Connection test → {url}")
        return GenerateResponse(video_url=url)

    teaching_req = TeachingRequest(
        request_id=req.request_id,
        course_requirement=req.course_requirement,
        student_persona=req.student_persona,
    )

    pipeline = get_pipeline()
    logger.info(f"[{req.request_id}] Waiting for pipeline slot...")
    async with _pipeline_semaphore:
        logger.info(f"[{req.request_id}] Pipeline slot acquired, starting...")
        try:
            result = await pipeline.execute(teaching_req)
        except Exception:
            logger.exception(f"[{req.request_id}] Pipeline failed")
            raise HTTPException(status_code=500, detail="Video generation failed")

    logger.info(f"[{req.request_id}] Done: {result.video_url}")
    return GenerateResponse(
        video_url=result.video_url,
        subtitle_url=result.subtitle_url,
        supplementary_url=result.supplementary_url,
    )
