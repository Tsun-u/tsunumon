"""測試投影片溢出自動縮放。"""
import asyncio
from pathlib import Path
from app.services.slides.html_renderer import HtmlSlideRenderer
from app.models.domain import ScriptSegment

async def main():
    renderer = HtmlSlideRenderer()
    
    # 讀取之前溢出的第 4 頁 HTML
    html_path = Path("output/kepler-real-001/slides/slide_004.html")
    full_html = html_path.read_text(encoding="utf-8")
    
    # 擷取 <body>...</body> 之間的內容
    import re
    match = re.search(r'<body>(.*)</body>', full_html, re.DOTALL)
    slide_html = match.group(1) if match else full_html
    
    seg = ScriptSegment(
        segment_id=4,
        sequence_id=2,
        slide_title="Test overflow",
        slide_html=slide_html,
        narration_text="test",
        estimated_duration_sec=60,
        teaching_phase="core",
    )
    
    out_dir = Path("output/test-overflow/slides")
    result = await renderer.generate_slide(seg, out_dir)
    print(f"Output: {result.image_path}")
    await renderer.close()

asyncio.run(main())
