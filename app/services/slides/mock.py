"""Mock slide generator — Pillow 畫簡單色底 + 文字，用於開發測試。"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.models.domain import ScriptSegment, SlideImage
from app.services.slides.base import BaseSlideGenerator

WIDTH = 1280
HEIGHT = 720
BG_COLOR = (30, 30, 60)
TITLE_COLOR = (255, 255, 255)
BULLET_COLOR = (200, 200, 220)


class MockSlideGenerator(BaseSlideGenerator):
    async def generate_slide(
        self, segment: ScriptSegment, output_dir: Path
    ) -> SlideImage:
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"slide_{segment.segment_id:03d}.png"
        filepath = output_dir / filename

        img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
        draw = ImageDraw.Draw(img)

        try:
            title_font = ImageFont.truetype("arial.ttf", 48)
            body_font = ImageFont.truetype("arial.ttf", 28)
        except OSError:
            title_font = ImageFont.load_default()
            body_font = ImageFont.load_default()

        draw.text((80, 60), segment.slide_title, fill=TITLE_COLOR, font=title_font)
        draw.line([(80, 130), (WIDTH - 80, 130)], fill=(100, 100, 140), width=2)

        y = 170
        for bullet in segment.slide_bullets:
            draw.text((100, y), f"•  {bullet}", fill=BULLET_COLOR, font=body_font)
            y += 50

        draw.text(
            (WIDTH - 100, HEIGHT - 50),
            f"#{segment.segment_id + 1}",
            fill=(100, 100, 140),
            font=body_font,
        )

        img.save(str(filepath))

        return SlideImage(
            segment_id=segment.segment_id,
            image_path=str(filepath),
            width=WIDTH,
            height=HEIGHT,
        )
