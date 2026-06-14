"""Pillow Slide Renderer — 用 Pillow 直接繪製教學投影片 PNG。

支援版型：
- title:      標題頁（大標題 + 副標題）
- bullets:    條列式重點
- code:       程式碼區塊
- equation:   數學公式/方程式
- comparison: 左右對比
- diagram:    圖解說明（文字模擬）
- summary:    總結頁
"""

import textwrap
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw, ImageFont

from app.models.domain import ScriptSegment, SlideImage
from app.services.slides.base import BaseSlideGenerator

# === 尺寸常數 ===
WIDTH = 1280
HEIGHT = 720
MARGIN = 60
CONTENT_TOP = 160  # 標題下方內容起始 Y

# === 配色 — 依 teaching_phase 區分 ===
PHASE_COLORS = {
    "hook":       {"bg": (20, 20, 50),   "accent": (255, 180, 60),  "title": (255, 255, 255)},
    "intro":      {"bg": (15, 35, 60),   "accent": (80, 180, 255),  "title": (255, 255, 255)},
    "introduction": {"bg": (15, 35, 60), "accent": (80, 180, 255),  "title": (255, 255, 255)},
    "core":       {"bg": (10, 25, 45),   "accent": (100, 220, 180), "title": (255, 255, 255)},
    "core_concepts": {"bg": (10, 25, 45), "accent": (100, 220, 180), "title": (255, 255, 255)},
    "example":    {"bg": (25, 15, 45),   "accent": (200, 140, 255), "title": (255, 255, 255)},
    "worked_example": {"bg": (25, 15, 45), "accent": (200, 140, 255), "title": (255, 255, 255)},
    "summary":    {"bg": (10, 35, 30),   "accent": (120, 230, 160), "title": (255, 255, 255)},
}
DEFAULT_COLORS = {"bg": (20, 20, 40), "accent": (150, 150, 200), "title": (255, 255, 255)}

# === 字體 ===
def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """載入字體。Windows 有 Arial，Linux fallback 到預設。"""
    names = ["arialbd.ttf", "arial.ttf"] if bold else ["arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT_TITLE = _load_font(42, bold=True)
FONT_SUBTITLE = _load_font(28)
FONT_BODY = _load_font(26)
FONT_BODY_SMALL = _load_font(22)
FONT_CODE = _load_font(20)  # monospace 在 Pillow 不太好做，先用 arial
FONT_PAGE_NUM = _load_font(18)


def _get_colors(phase: str) -> dict:
    return PHASE_COLORS.get(phase, DEFAULT_COLORS)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """自動斷行：根據字體和最大寬度計算每行可放幾個字。"""
    # 估算每個字的平均寬度
    avg_char_w = font.getlength("M")
    chars_per_line = max(10, int(max_width / avg_char_w))
    return textwrap.wrap(text, width=chars_per_line)


class PillowSlideRenderer(BaseSlideGenerator):
    """多版型 Pillow 投影片渲染器。"""

    async def generate_slide(
        self, segment: ScriptSegment, output_dir: Path
    ) -> SlideImage:
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"slide_{segment.segment_id:03d}.png"
        filepath = output_dir / filename

        colors = _get_colors(segment.teaching_phase)
        img = Image.new("RGB", (WIDTH, HEIGHT), colors["bg"])
        draw = ImageDraw.Draw(img)

        # 繪製標題區
        self._draw_header(draw, segment, colors)

        # 依 visual_hint 選擇版型
        hint = (segment.slide_visual_hint or "bullets").lower()
        if segment.teaching_phase == "summary":
            self._draw_summary(draw, segment, colors)
        elif hint in ("code", "code_block"):
            self._draw_code(draw, segment, colors)
        elif hint in ("equation", "formula"):
            self._draw_equation(draw, segment, colors)
        elif hint == "comparison":
            self._draw_comparison(draw, segment, colors)
        elif hint == "diagram":
            self._draw_diagram(draw, segment, colors)
        elif hint == "title":
            self._draw_title_page(draw, segment, colors)
        else:
            self._draw_bullets(draw, segment, colors)

        # 頁碼
        self._draw_page_number(draw, segment, colors)

        img.save(str(filepath))
        return SlideImage(
            segment_id=segment.segment_id,
            image_path=str(filepath),
            width=WIDTH, height=HEIGHT,
        )

    # ------------------------------------------------------------------ #
    #  Header
    # ------------------------------------------------------------------ #

    def _draw_header(self, draw: ImageDraw.Draw, seg: ScriptSegment, colors: dict):
        """繪製標題 + 底線 + phase badge。"""
        # Phase badge
        badge_text = seg.teaching_phase.upper().replace("_", " ")
        badge_w = FONT_BODY_SMALL.getlength(badge_text) + 20
        draw.rounded_rectangle(
            [MARGIN, 25, MARGIN + badge_w, 55],
            radius=12,
            fill=colors["accent"],
        )
        draw.text(
            (MARGIN + 10, 28),
            badge_text,
            fill=colors["bg"],
            font=FONT_BODY_SMALL,
        )

        # 標題文字
        title = seg.slide_title
        draw.text((MARGIN, 70), title, fill=colors["title"], font=FONT_TITLE)

        # 底線
        draw.line(
            [(MARGIN, 125), (WIDTH - MARGIN, 125)],
            fill=(*colors["accent"], 120),
            width=2,
        )

    # ------------------------------------------------------------------ #
    #  版型：Bullets（預設）
    # ------------------------------------------------------------------ #

    def _draw_bullets(self, draw: ImageDraw.Draw, seg: ScriptSegment, colors: dict):
        y = CONTENT_TOP
        bullet_color = (220, 220, 240)
        max_w = WIDTH - MARGIN * 2 - 40

        for bullet in seg.slide_bullets:
            # 繪製圓點
            draw.ellipse(
                [MARGIN + 5, y + 8, MARGIN + 15, y + 18],
                fill=colors["accent"],
            )
            # 繪製文字（支援自動斷行）
            lines = _wrap_text(bullet, FONT_BODY, max_w)
            for line in lines:
                draw.text((MARGIN + 30, y), line, fill=bullet_color, font=FONT_BODY)
                y += 38
            y += 12  # bullet 間距

    # ------------------------------------------------------------------ #
    #  版型：Title Page
    # ------------------------------------------------------------------ #

    def _draw_title_page(self, draw: ImageDraw.Draw, seg: ScriptSegment, colors: dict):
        """大標題頁面 — 標題居中顯示。"""
        # 清除原本的 header
        # 用中央大字取代
        title_y = HEIGHT // 2 - 60
        title_lines = _wrap_text(seg.slide_title, FONT_TITLE, WIDTH - MARGIN * 2)
        for line in title_lines:
            w = FONT_TITLE.getlength(line)
            draw.text(((WIDTH - w) / 2, title_y), line, fill=colors["title"], font=FONT_TITLE)
            title_y += 55

        # 副標題（用 bullets 的第一項）
        if seg.slide_bullets:
            sub = seg.slide_bullets[0]
            sub_w = FONT_SUBTITLE.getlength(sub)
            draw.text(
                ((WIDTH - sub_w) / 2, title_y + 20),
                sub, fill=colors["accent"], font=FONT_SUBTITLE,
            )

    # ------------------------------------------------------------------ #
    #  版型：Code Block
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_code_line(text: str) -> bool:
        """判斷一行文字是否像程式碼。"""
        t = text.strip()
        # 常見程式碼起始
        if t.startswith(("def ", "class ", "import ", "from ", "for ", "if ", "elif ",
                         "else:", "while ", "return ", "print(", ">>>", "//", "#",
                         "```", "let ", "const ", "var ", "function ", "async ",
                         "try:", "except ", "with ", "yield ", "raise ")):
            return True
        # 包含賦值、函式呼叫等程式碼特徵
        if "=" in t and not t.endswith("?") and len(t) < 100:
            return True
        if t.endswith((";", "{", "}", ")", "]")) and len(t) < 100:
            return True
        return False

    def _draw_code(self, draw: ImageDraw.Draw, seg: ScriptSegment, colors: dict):
        y = CONTENT_TOP
        code_bg = (15, 15, 30)
        code_fg = (180, 230, 180)

        # 分離程式碼行和說明文字
        non_code_bullets = []
        code_bullets = []
        for b in seg.slide_bullets:
            if self._is_code_line(b):
                code_bullets.append(b)
            else:
                non_code_bullets.append(b)

        # 如果完全沒有偵測到程式碼，全部放進 code block（不重複顯示 bullets）
        if not code_bullets:
            code_bullets = seg.slide_bullets
            non_code_bullets = []

        # 普通 bullets（只有在有明確分離時才顯示）
        for bullet in non_code_bullets:
            draw.ellipse([MARGIN + 5, y + 8, MARGIN + 15, y + 18], fill=colors["accent"])
            lines = _wrap_text(bullet, FONT_BODY, WIDTH - MARGIN * 2 - 40)
            for line in lines:
                draw.text((MARGIN + 30, y), line, fill=(220, 220, 240), font=FONT_BODY)
                y += 38
            y += 8

        # Code block 背景
        y += 10
        code_x = MARGIN + 20
        line_h = 30
        code_h = len(code_bullets) * line_h + 30
        # 防止 code block 超出畫面
        max_code_h = HEIGHT - y - 60
        code_h = min(code_h, max_code_h)
        draw.rounded_rectangle(
            [MARGIN, y, WIDTH - MARGIN, y + code_h],
            radius=10,
            fill=code_bg,
        )
        y += 15
        for line in code_bullets:
            if y + line_h > HEIGHT - 50:
                break  # 防止溢出
            draw.text((code_x, y), line, fill=code_fg, font=FONT_CODE)
            y += line_h

    # ------------------------------------------------------------------ #
    #  版型：Equation
    # ------------------------------------------------------------------ #

    def _draw_equation(self, draw: ImageDraw.Draw, seg: ScriptSegment, colors: dict):
        y = CONTENT_TOP
        eq_color = (255, 240, 180)

        for i, bullet in enumerate(seg.slide_bullets):
            # 偵測公式（包含 =, +, -, *, /, ^, \\）
            is_eq = any(c in bullet for c in "=+*/^\\") and len(bullet) < 80
            if is_eq:
                # 公式：用較大字體居中
                w = FONT_TITLE.getlength(bullet)
                x = max(MARGIN, (WIDTH - w) / 2)
                draw.text((x, y + 5), bullet, fill=eq_color, font=FONT_TITLE)
                y += 65
            else:
                # 普通文字
                lines = _wrap_text(bullet, FONT_BODY, WIDTH - MARGIN * 2 - 40)
                draw.ellipse([MARGIN + 5, y + 8, MARGIN + 15, y + 18], fill=colors["accent"])
                for line in lines:
                    draw.text((MARGIN + 30, y), line, fill=(220, 220, 240), font=FONT_BODY)
                    y += 38
                y += 10

    # ------------------------------------------------------------------ #
    #  版型：Comparison（左右兩欄）
    # ------------------------------------------------------------------ #

    def _draw_comparison(self, draw: ImageDraw.Draw, seg: ScriptSegment, colors: dict):
        mid = WIDTH // 2
        y_start = CONTENT_TOP

        # 中線
        draw.line([(mid, y_start), (mid, HEIGHT - 60)], fill=(*colors["accent"], 80), width=1)

        # 左右分配 bullets
        left_bullets = []
        right_bullets = []
        for i, b in enumerate(seg.slide_bullets):
            if i < len(seg.slide_bullets) // 2:
                left_bullets.append(b)
            else:
                right_bullets.append(b)

        # 繪製左欄
        y = y_start
        max_w = mid - MARGIN - 30
        for b in left_bullets:
            draw.ellipse([MARGIN + 5, y + 8, MARGIN + 15, y + 18], fill=colors["accent"])
            lines = _wrap_text(b, FONT_BODY_SMALL, max_w)
            for line in lines:
                draw.text((MARGIN + 30, y), line, fill=(220, 220, 240), font=FONT_BODY_SMALL)
                y += 32
            y += 8

        # 繪製右欄
        y = y_start
        right_x = mid + 20
        for b in right_bullets:
            draw.ellipse([right_x + 5, y + 8, right_x + 15, y + 18], fill=colors["accent"])
            lines = _wrap_text(b, FONT_BODY_SMALL, max_w)
            for line in lines:
                draw.text((right_x + 30, y), line, fill=(220, 220, 240), font=FONT_BODY_SMALL)
                y += 32
            y += 8

    # ------------------------------------------------------------------ #
    #  版型：Diagram（用文字方塊模擬流程圖）
    # ------------------------------------------------------------------ #

    def _draw_diagram(self, draw: ImageDraw.Draw, seg: ScriptSegment, colors: dict):
        bullets = seg.slide_bullets
        if not bullets:
            return self._draw_bullets(draw, seg, colors)

        n = len(bullets)
        box_h = 50
        gap = 20
        total_h = n * box_h + (n - 1) * gap
        y_start = max(CONTENT_TOP, (HEIGHT - total_h) // 2)
        box_w = min(600, WIDTH - MARGIN * 2 - 40)
        x = (WIDTH - box_w) // 2

        for i, bullet in enumerate(bullets):
            y = y_start + i * (box_h + gap)
            # 方塊
            draw.rounded_rectangle(
                [x, y, x + box_w, y + box_h],
                radius=10,
                fill=(*colors["accent"], 40),
                outline=colors["accent"],
                width=2,
            )
            # 文字
            text_lines = _wrap_text(bullet, FONT_BODY_SMALL, box_w - 20)
            text_y = y + (box_h - len(text_lines) * 28) // 2
            for line in text_lines:
                tw = FONT_BODY_SMALL.getlength(line)
                draw.text(((WIDTH - tw) / 2, text_y), line, fill=(255, 255, 255), font=FONT_BODY_SMALL)
                text_y += 28

            # 箭頭（除了最後一個）
            if i < n - 1:
                arrow_y = y + box_h + 2
                arrow_x = WIDTH // 2
                draw.polygon(
                    [(arrow_x - 8, arrow_y + 4), (arrow_x + 8, arrow_y + 4), (arrow_x, arrow_y + gap - 4)],
                    fill=colors["accent"],
                )

    # ------------------------------------------------------------------ #
    #  版型：Summary
    # ------------------------------------------------------------------ #

    def _draw_summary(self, draw: ImageDraw.Draw, seg: ScriptSegment, colors: dict):
        y = CONTENT_TOP
        # 用帶數字的列表，突出重點
        for i, bullet in enumerate(seg.slide_bullets, 1):
            # 數字圓圈
            cx = MARGIN + 20
            cy = y + 15
            draw.ellipse([cx - 15, cy - 15, cx + 15, cy + 15], fill=colors["accent"])
            num_w = FONT_BODY_SMALL.getlength(str(i))
            draw.text(
                (cx - num_w / 2, cy - 12),
                str(i), fill=colors["bg"], font=FONT_BODY_SMALL,
            )
            # 文字
            lines = _wrap_text(bullet, FONT_BODY, WIDTH - MARGIN * 2 - 60)
            for line in lines:
                draw.text((MARGIN + 50, y), line, fill=(230, 230, 245), font=FONT_BODY)
                y += 38
            y += 18

    # ------------------------------------------------------------------ #
    #  頁碼
    # ------------------------------------------------------------------ #

    def _draw_page_number(self, draw: ImageDraw.Draw, seg: ScriptSegment, colors: dict):
        text = f"{seg.segment_id}"
        w = FONT_PAGE_NUM.getlength(text)
        draw.text(
            (WIDTH - MARGIN - w, HEIGHT - 45),
            text, fill=(*colors["accent"], 150), font=FONT_PAGE_NUM,
        )
