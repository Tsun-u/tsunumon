"""測試 SlideReviewer — 用真實的投影片截圖測試 Vision 審核。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Settings
from app.services.slides.reviewer import SlideReviewer


def test_review_overflow_slide():
    """測試溢出的 slide 004 是否能被 reviewer 偵測到。"""
    # 用原始（未 auto-scale）的截圖
    image_path = "output/kepler-real-001/slides/slide_004.png"
    if not Path(image_path).exists():
        print(f"SKIP: {image_path} not found")
        return

    # 讀取 HTML
    html_path = Path("output/kepler-real-001/slides/slide_004.html")
    html_content = html_path.read_text(encoding="utf-8")

    config = Settings(llm_backend="claude")  # 需要 .env 有 API key
    reviewer = SlideReviewer(config, max_rounds=2, score_threshold=8.0)

    result = reviewer.review(
        image_path=str(Path(image_path).resolve()),
        slide_html=html_content,
        slide_title="Gravity as Centripetal Force",
    )

    print(f"Score:    {result['score']}/10")
    print(f"Approved: {result['approved']}")
    print(f"Reason:   {result['reason']}")
    print(f"Issues:   {result['issues']}")
    print(f"Has refined HTML: {result['refined_html'] is not None}")
    if result['refined_html']:
        print(f"Refined HTML length: {len(result['refined_html'])} chars")
        # 存下來看看
        Path("output/test-reviewer/refined_004.html").parent.mkdir(parents=True, exist_ok=True)
        Path("output/test-reviewer/refined_004.html").write_text(
            result['refined_html'], encoding="utf-8"
        )
        print("Saved refined HTML to output/test-reviewer/refined_004.html")


if __name__ == "__main__":
    test_review_overflow_slide()
