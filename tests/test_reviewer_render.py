"""渲染 reviewer 修正後的 HTML，確認不再溢出。"""
import asyncio
from pathlib import Path
from app.services.slides.html_renderer import HtmlSlideRenderer
from app.models.domain import ScriptSegment

async def main():
    renderer = HtmlSlideRenderer()
    refined_html = Path("output/test-reviewer/refined_004.html").read_text(encoding="utf-8")
    
    seg = ScriptSegment(
        segment_id=4,
        sequence_id=2,
        slide_title="Gravity as Centripetal Force",
        slide_html=refined_html,
        narration_text="test",
        estimated_duration_sec=60,
        teaching_phase="core",
    )
    
    out_dir = Path("output/test-reviewer/slides")
    result = await renderer.generate_slide(seg, out_dir)
    print(f"Output: {result.image_path}")
    await renderer.close()

asyncio.run(main())
