"""HTML Slide Renderer — 用 Playwright 把 LLM 生成的 HTML 截圖為 PNG。

流程：slide_html → 包裝完整 HTML（加入 base CSS） → Playwright 截圖 → PNG
"""

import asyncio
import logging
from pathlib import Path
from typing import List

from playwright.async_api import async_playwright, Browser

from app.models.domain import ScriptSegment, SlideImage
from app.services.slides.base import BaseSlideGenerator

logger = logging.getLogger(__name__)

# 投影片基礎 CSS — 定義版型元件和階段配色
SLIDE_BASE_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  width: 1280px;
  height: 720px;
  overflow: hidden;
  font-family: 'Segoe UI', 'Arial', sans-serif;
  background: #fdf6f0;
  background-image: radial-gradient(circle, rgba(190,160,140,0.3) 1px, transparent 1px);
  background-size: 20px 20px;
}

.slide {
  width: 1280px;
  height: 720px;
  padding: 48px 60px 80px;
  position: relative;
}
/* Bottom safe area: content should not enter the last 80px */

/* ── 預定義元件 ── */
.phase-badge {
  display: inline-block;
  border: 2px dashed;
  border-radius: 20px;
  padding: 4px 18px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 1px;
  margin-bottom: 12px;
}

.title {
  font-size: 42px;
  font-weight: 700;
  color: #37474f;
  margin-bottom: 8px;
  line-height: 1.3;
  max-width: 85%;
}

.divider {
  width: 120px;
  height: 4px;
  border-radius: 2px;
  margin-bottom: 28px;
}

.card {
  background: white !important;
  border: 2px solid #e0e0e0 !important;
  border-radius: 16px;
  padding: 24px 20px;
  box-shadow: 3px 4px 0 rgba(0,0,0,0.04);
  position: relative;
}

.card__num {
  position: absolute;
  top: -14px;
  left: 16px;
  width: 28px;
  height: 28px;
  background: #5c6bc0;
  color: white;
  font-size: 15px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  border: 2px solid white;
}

.card__title {
  font-size: 24px;
  font-weight: 700;
  margin-bottom: 10px;
}

.card__text {
  font-size: 22px;
  line-height: 1.6;
  color: #546e7a;
}

.code-block {
  background: #263238;
  border-radius: 12px;
  padding: 20px 24px;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 22px;
  line-height: 1.7;
  color: #a5d6a7;
  overflow: hidden;
}

.code-block .keyword { color: #ce93d8; }
.code-block .string { color: #fff59d; }
.code-block .comment { color: #78909c; }
.code-block .function { color: #80cbc4; }
.code-block .number { color: #f48fb1; }

.bullet-list, .bullet-list ul {
  list-style: none;
  padding: 0;
}

.bullet-list li {
  font-size: 24px;
  line-height: 1.5;
  color: #455a64;
  padding: 10px 0 10px 36px;
  position: relative;
}

.bullet-list li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 16px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
}

.page-num {
  position: absolute;
  bottom: 24px;
  right: 40px;
  font-size: 20px;
  color: #90a4ae;
  z-index: 999;
  background: rgba(253,246,240,0.85);
  padding: 2px 10px;
  border-radius: 4px;
  font-weight: 600;
}

/* ── 階段配色 ── */
.phase-hook .phase-badge { background: linear-gradient(135deg, #fff3e0, #ffe0b2) !important; border-color: #ff9800 !important; color: #e65100 !important; }
.phase-hook .divider { background: linear-gradient(90deg, #ff9800, #ffb74d); }
.phase-hook .bullet-list li::before { background: #ff9800; }
.phase-hook .card { border-color: #ffcc80 !important; }
.phase-hook .card__num { background: #ff9800 !important; }
.phase-hook .card__title { color: #e65100 !important; }

.phase-core .phase-badge { background: linear-gradient(135deg, #e8f5e9, #c8e6c9) !important; border-color: #66bb6a !important; color: #2e7d32 !important; }
.phase-core .divider { background: linear-gradient(90deg, #66bb6a, #a5d6a7); }
.phase-core .bullet-list li::before { background: #66bb6a; }
.phase-core .card { border-color: #a5d6a7 !important; }
.phase-core .card__num { background: #66bb6a !important; }
.phase-core .card__title { color: #2e7d32 !important; }

.phase-example .phase-badge { background: linear-gradient(135deg, #f3e5f5, #e1bee7) !important; border-color: #ab47bc !important; color: #6a1b9a !important; }
.phase-example .divider { background: linear-gradient(90deg, #ab47bc, #ce93d8); }
.phase-example .bullet-list li::before { background: #ab47bc; }
.phase-example .card { border-color: #ce93d8 !important; }
.phase-example .card__num { background: #ab47bc !important; }
.phase-example .card__title { color: #6a1b9a !important; }

.phase-summary .phase-badge { background: linear-gradient(135deg, #e0f2f1, #b2dfdb) !important; border-color: #26a69a !important; color: #00695c !important; }
.phase-summary .divider { background: linear-gradient(90deg, #26a69a, #80cbc4); }
.phase-summary .bullet-list li::before { background: #26a69a; }
.phase-summary .card { border-color: #80cbc4 !important; }
.phase-summary .card__num { background: #26a69a !important; }
.phase-summary .card__title { color: #00695c !important; }

/* ── intro phase（同 core 配色） ── */
.phase-intro .phase-badge { background: linear-gradient(135deg, #e3f2fd, #bbdefb) !important; border-color: #42a5f5 !important; color: #1565c0 !important; }
.phase-intro .divider { background: linear-gradient(90deg, #42a5f5, #90caf9); }
.phase-intro .bullet-list li::before { background: #42a5f5; }
.phase-intro .card { border-color: #90caf9 !important; }
.phase-intro .card__num { background: #42a5f5 !important; }
.phase-intro .card__title { color: #1565c0 !important; }

/* ── Card modifier classes（卡片級別配色，不依賴 phase class） ── */
/* 泛用類 */
.card--success { border-color: #a5d6a7 !important; border-left: 6px solid #2e7d32 !important; }
.card--success .card__title { color: #2e7d32 !important; }
.card--error { border-color: #ffcdd2 !important; border-left: 6px solid #c62828 !important; }
.card--error .card__title { color: #c62828 !important; }
.card--warning { border-color: #ffe0b2 !important; border-left: 6px solid #e65100 !important; }
.card--warning .card__title { color: #e65100 !important; }
.card--info { border-color: #bbdefb !important; border-left: 6px solid #1565c0 !important; }
.card--info .card__title { color: #1565c0 !important; }
.card--primary { border-color: #b2dfdb !important; border-left: 6px solid #00695c !important; }
.card--primary .card__title { color: #00695c !important; }
.card--secondary { border-color: #cfd8dc !important; }
.card--secondary .card__title { color: #546e7a !important; }

/* 相近概念比較用 */
.card--compare-a { border-color: #90caf9 !important; }
.card--compare-a .card__title { color: #1565c0 !important; }
.card--compare-b { border-color: #ffcc80 !important; }
.card--compare-b .card__title { color: #e65100 !important; }
.card--compare-c { border-color: #ce93d8 !important; }
.card--compare-c .card__title { color: #6a1b9a !important; }

/* ── function-plot.js 字體強制放大（預設太小、y 軸 label 旋轉難讀） ── */
.function-plot text, [class*="function-plot"] text { font-size: 16px !important; font-family: 'Segoe UI', Arial, sans-serif !important; }
.function-plot .x.axis .label, .function-plot .y.axis .label { font-size: 18px !important; font-weight: 600 !important; }

/* ── function-plot annotation 可見性保底（預設線條跟 grid 同色不可見） ── */
.function-plot .annotations path { stroke: #ff9800; stroke-width: 2; stroke-dasharray: 5,5; }
.function-plot .annotations text { fill: #37474f; font-size: 14px; font-weight: 600; stroke: #fff; stroke-width: 3px; paint-order: stroke fill; dominant-baseline: hanging; }

/* ── function-plot scatter 點半徑保底（source 硬寫死 r=1 太小幾乎不可見；CSS geometry property 蓋過 presentation attr、無需 !important；個別圖可用 #id 覆蓋） ── */
.function-plot circle.scatter { r: 4px; }

/* ── SVG text 可讀性 ── */
.slide svg text { paint-order: stroke fill; }
"""


# 形象圖目錄（Playwright 用 file:// 載入，需要絕對路徑）
# __file__ = /app/app/services/slides/html_renderer.py → .parent x4 = /app/
_MASCOT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "mascot"


_KATEX_INIT = """
<script>
document.addEventListener("DOMContentLoaded", function() {
  if (typeof renderMathInElement !== "undefined") {
    renderMathInElement(document.body, {
      delimiters: [
        {left: "$$", right: "$$", display: true},
        {left: "\\\\(", right: "\\\\)", display: false},
        {left: "\\\\[", right: "\\\\]", display: true}
      ],
      throwOnError: false
    });
  }
});
</script>
"""

_GSAP_INIT = """
<script>
document.addEventListener("DOMContentLoaded", function() {
  if (typeof gsap !== "undefined") {
    if (typeof MotionPathPlugin !== "undefined") gsap.registerPlugin(MotionPathPlugin);
    if (typeof DrawSVGPlugin !== "undefined") gsap.registerPlugin(DrawSVGPlugin);
  }
});
</script>
"""

# function-plot.js: 數學函數繪圖（座標系永遠正確，不受 CSS y-axis 影響）
_LIBS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "libs"


def _load_valid_phosphor_icons() -> set:
    """從本地 phosphor-regular.css 抽合法 icon 名（渲染 ground truth）。"""
    import re as _re
    css_path = _LIBS_DIR / "phosphor-regular.css"
    if not css_path.exists():
        return set()
    return set(_re.findall(r"\.ph\.ph-([a-z0-9-]+):before", css_path.read_text(encoding="utf-8")))


# LLM 憑記憶猜 icon 名會猜到本地版本沒有的（決賽實證：telescope/loop/
# circuit-board/map/reflect-horizontal → 畫面空格）。已知錯名 → 近義正名；
# 其餘未知名 → 中性 fallback（不能比空格更突兀）。
_VALID_PHOSPHOR_ICONS = _load_valid_phosphor_icons()
_PHOSPHOR_ALIASES = {
    "telescope": "binoculars",
    "loop": "repeat",
    "circuit-board": "circuitry",
    "map": "map-trifold",
    "reflect-horizontal": "flip-horizontal",
    "filing-cabinet": "archive",
    "zzz": "moon-stars",
    "stopwatch": "timer",
    "mirror": "flip-horizontal",
    "gears": "gear",
}
_PHOSPHOR_FALLBACK = "dot-outline"  # 中性萬用
_PHOSPHOR_WEIGHTS = {"bold", "fill", "light", "thin", "duotone"}


def _fix_unknown_phosphor_icons(html: str, segment_id: int) -> str:
    """class 裡的 ph-<name> 不在本地白名單 → 換 alias 或中性 fallback（Layer 2 同款防線）。"""
    import re as _re
    if not _VALID_PHOSPHOR_ICONS or "ph-" not in html:
        return html
    fixed: list = []

    def _sub(m):
        name = m.group(1)
        if name in _PHOSPHOR_WEIGHTS or name in _VALID_PHOSPHOR_ICONS:
            return m.group(0)
        repl = _PHOSPHOR_ALIASES.get(name, _PHOSPHOR_FALLBACK)
        fixed.append(f"{name}->{repl}")
        return f"ph-{repl}"

    def _class_sub(cm):
        return 'class="' + _re.sub(r"\bph-([a-z0-9-]+)", _sub, cm.group(1)) + '"'

    html = _re.sub(r'class="([^"]*\bph-[^"]*)"', _class_sub, html)
    if fixed:
        logger.info(
            f"Slide {segment_id}: fixed unknown Phosphor icon(s): {', '.join(fixed[:8])}"
        )
    return html


def _fix_entity_in_text_api(html: str, segment_id: int) -> str:
    """D3 .text() / textContent 是純文本 API、HTML entity 不解析（畫面字面顯示
    &amp;，決賽 126[10] 實證）。把這些 JS 字串裡的 &amp;/&lt;/&gt; 反轉義。
    quot/apos 不動（反轉義會撞字串引號），交給 P6 檢查人工看。"""
    import re as _re
    if "&" not in html:
        return html
    fixed: list = []

    def _unescape(s: str) -> str:
        return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

    def _sub(m):
        head, s = m.group(1), m.group(2)
        if "&amp;" in s or "&lt;" in s or "&gt;" in s:
            fixed.append(s[:30])
            return head + _unescape(s)
        return m.group(0)

    html = _re.sub(
        r"(\.text\(\s*|textContent\s*=\s*)('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")",
        _sub, html,
    )
    if fixed:
        logger.info(f"Slide {segment_id}: unescaped entity in text API: {'; '.join(fixed[:5])}")
    return html


# 口語拆寫（narration 專用）漏進 slide 視覺的已知形（決賽 126 實證 A-I/L-L-M）。
# 只用已知對照表替換、零誤殺風險；新形先由 P5 掃描抓、確認後再入表。
_SPOKEN_FORM_MAP = {
    "A-I": "AI", "L-L-M": "LLM", "K-N-N": "KNN", "C-N-N": "CNN", "R-A-G": "RAG",
    "G-P-T": "GPT", "A-P-I": "API", "C-P-U": "CPU", "G-P-U": "GPU",
    "H-T-M-L": "HTML", "U-R-L": "URL", "S-Q-L": "SQL", "I-O-T": "IoT",
    "A-T-P": "ATP", "D-N-A": "DNA", "R-N-A": "RNA",
    "H-2-O-2": "H₂O₂", "C-O-2": "CO₂", "H-2-O": "H₂O", "O-2": "O₂",
}


def _fix_spoken_forms(html: str, segment_id: int) -> str:
    """已知口語拆寫形 → 正常視覺寫法（A-I→AI、H-2-O-2→H₂O₂）。
    lookbehind/lookahead 防 JS 運算式誤動（cy-A-I 之類不碰）。"""
    import re as _re
    fixed: list = []
    for spoken, visual in _SPOKEN_FORM_MAP.items():
        pattern = _re.compile(rf"(?<![-\w.]){_re.escape(spoken)}(?![-\w])")
        new_html = pattern.sub(visual, html)
        if new_html != html:
            fixed.append(f"{spoken}->{visual}")
            html = new_html
    if fixed:
        logger.info(f"Slide {segment_id}: fixed spoken-form text: {', '.join(fixed)}")
    return html


def _fix_d3_div_containers(html: str, segment_id: int) -> str:
    """d3 SVG 元素 append 進 <div> 容器 = 無聲全滅（HTML namespace 不渲染圖形、
    <text> 退化成行內文字黏連、零 console error；決賽 121[4,9,11] 實證）。

    修復：空的 <div id="X"> 容器、且 script 對 #X 做 d3.select 後 append SVG
    元素、且整檔沒有 .append('svg') → 把 <div> 轉成 <svg>（d3 append 跟隨父
    元素 namespace，轉成 svg 後圖形正常渲染；style 的 width/height 對 svg
    一樣有效）。只動空容器、有子元素的 div 不碰。"""
    import re as _re
    if "d3.select" not in html or ".append('svg'" in html:
        return html
    if not _re.search(
        r"\.append\(\s*'(?:circle|rect|line|path|text|polygon|polyline|ellipse|g)'\s*\)", html
    ):
        return html
    fixed: list = []
    for tid in set(_re.findall(r"d3\.select\(\s*'#([a-z0-9_-]+)'\s*\)", html)):
        # 只轉「空」div 容器：<div ...id="X"...></div>（中間至多空白）
        pattern = _re.compile(
            rf"<div([^>]*\bid=[\"']{_re.escape(tid)}[\"'][^>]*)>\s*</div>"
        )
        new_html = pattern.sub(rf"<svg\1></svg>", html)
        if new_html != html:
            html = new_html
            fixed.append(f"#{tid}")
    if fixed:
        logger.info(
            f"Slide {segment_id}: fixed d3 <div> container(s) -> <svg>: {', '.join(fixed)}"
        )
    return html


def _fix_title_wrapper_padding(html: str, segment_id: int = 0) -> str:
    """Improve 階段 Sonnet 有時把 title+divider+內容包進一個 inline flex wrapper
    （padding:40px 60px;height:600px;display:flex;flex-direction:column;gap:NNpx），
    上下 padding 40px 把標題往下推、整頁標題上下留白過大（決賽 t112_v2 全 10 內容頁
    實證：一行標題佔近三行垂直空間；舊定案版 t119_v_final 直接用 .title、無此 wrapper）。

    精準修復：偵測這個內容 wrapper（特徵＝padding Npx 60px 緊接 height:6NNpx +
    display:flex;flex-direction:column），把上下 padding 縮到 10px（保留左右 60px、
    height、flex、gap）。封面 height:720px、padding/height 順序不同，不匹配（safe）；
    內層子 flex 無 padding:Npx 60px;height:6NN 前綴，不誤傷；base CSS 無此 inline
    pattern。本地 playwright 實證：badge→title 間距 52→22px、內容上移 ~26px。"""
    import re as _re
    orig = html
    # 兩值 padding: "padding:40px 60px;height:600px;display:flex;flex-direction:column"
    html = _re.sub(
        r'padding:\d+px 60px;(height:6\d\dpx;display:flex;flex-direction:column)',
        r'padding:10px 60px;\1', html)
    # 四值 padding: "padding:30px 60px 20px 60px;height:660px;display:flex;flex-direction:column"
    html = _re.sub(
        r'padding:\d+px 60px \d+px 60px;(height:6\d\dpx;display:flex;flex-direction:column)',
        r'padding:10px 60px 10px 60px;\1', html)
    if html != orig:
        n = len(_re.findall(
            r'padding:10px 60px[^;]*;height:6\d\dpx;display:flex;flex-direction:column', html))
        logger.info(f"Slide {segment_id}: title-wrapper top/bottom padding shrunk ({n} wrapper(s))")
    return html


def _fix_card_gradient_legibility(html: str, segment_id: int) -> str:
    """class="card" + inline 深色/gradient background + 淺色字(color:white) 時，
    inline background 被 SLIDE_BASE_CSS 的 `.card{background:white!important}` 蓋掉
    → 白底 + 白字 = 內容隱形（決賽 122 v3 slide14 實證：CSS specificity 確定性 bug、
    本地 playwright 100% 重現，非 render race）。

    `.card{background:white!important}` 是深色字保護（深字+忘設底 → 白底兜底），
    不能全域移除。精準修復：只在「淺色字（color:white/#fff）」時給 inline background
    補 `!important`（inline !important 勝過 external !important、gradient 還原）；
    深色字 card 不動（白底保護維持、深字可讀）。深底白字才修、淡底深字（仍可讀）不碰。"""
    import re as _re
    if "card" not in html:
        return html
    cnt = [0]

    def _sub(m):
        tag = m.group(0)
        if not _re.search(r'class="[^"]*\bcard\b[^"]*"', tag):
            return tag
        style_m = _re.search(r'style="([^"]*)"', tag)
        if not style_m:
            return tag
        style = style_m.group(1)
        bg_m = _re.search(r"background\s*:\s*[^;]*", style, _re.I)
        if not bg_m or "!important" in bg_m.group(0):
            return tag
        # 只處理彩色/漸層 background（gradient 或 hex/rgb 色值），純白/transparent 不碰
        if not _re.search(r"gradient|#[0-9a-fA-F]{3,6}|rgb", bg_m.group(0)):
            return tag
        # 只在淺色字（深底白字 case）補救；深色字維持 .card 白底保護
        if not _re.search(
            r"color\s*:\s*(?:white|#fff(?:fff)?\b|rgb\(\s*255\s*,\s*255\s*,\s*255)",
            style, _re.I,
        ):
            return tag
        new_style = style.replace(bg_m.group(0), bg_m.group(0).rstrip() + " !important", 1)
        cnt[0] += 1
        return tag.replace(f'style="{style}"', f'style="{new_style}"')

    html = _re.sub(r"<div\b[^>]*>", _sub, html)
    if cnt[0]:
        logger.info(
            f"Slide {segment_id}: card gradient legibility fix -> {cnt[0]} card(s)"
        )
    return html


_FUNCTIONPLOT_INIT = """
<script>
document.addEventListener("DOMContentLoaded", function() {
  if (typeof functionPlot === "undefined") return;
  document.querySelectorAll(".math-plot[data-functions]").forEach(function(div) {
    try {
      var config = JSON.parse(div.getAttribute("data-functions"));
      config.target = div;
      if (!config.width) config.width = div.offsetWidth || 600;
      if (!config.height) config.height = div.offsetHeight || 400;
      functionPlot(config);
    } catch (e) {
      console.error("function-plot render error:", e);
    }
  });
  // --- post-process: un-rotate text in function-plot (preserve translate) ---
  document.querySelectorAll(".function-plot").forEach(function(fp) {
    fp.querySelectorAll("text").forEach(function(text) {
      var tr = text.getAttribute("transform");
      if (!tr || !tr.includes("rotate")) return;
      var cleaned = tr.replace(/rotate\([^)]*\)/g, "").trim();
      text.setAttribute("transform", cleaned || "");
      if (text.closest(".annotations")) {
        text.style.dominantBaseline = "hanging";
      } else {
        text.setAttribute("x", 0);
        text.setAttribute("y", -8);
        text.setAttribute("text-anchor", "start");
      }
    });
  });
});
</script>
"""

_SLIDE_POSTPROCESS = """
<script>
// Canvas height padding — runs immediately (before DOMContentLoaded drawing code)
document.querySelectorAll('canvas').forEach(function(c) {
  c.height = parseInt(c.getAttribute('height') || c.height) + 20;
});
</script>
<script>
document.addEventListener("DOMContentLoaded", function() {
  // --- SVG height auto-fit is handled by Playwright evaluate before screenshot ---
  // --- break title at colon only when wide enough to risk mascot overlap ---
  document.querySelectorAll(".title").forEach(function(t) {
    if (t.innerHTML.includes(":") && t.scrollWidth > t.parentElement.clientWidth * 0.85) {
      t.innerHTML = t.innerHTML.replace(/:/, ":<br>");
    }
  });
  // --- fix nested <li> (LLM sometimes generates <li><li>...</li></li>) ---
  document.querySelectorAll("li > li").forEach(function(inner) {
    var outer = inner.parentElement;
    outer.innerHTML = inner.innerHTML;
  });
});
</script>
"""


def _local_asset_tag(filename: str, *, kind: str) -> str:
    """Emit a <link>/<script> tag pointing at a local libs/ asset, or "" if missing.

    kind: "css" | "js"
    """
    path = _LIBS_DIR / filename
    if not path.exists():
        return ""
    uri = path.as_uri()
    if kind == "css":
        return f'<link rel="stylesheet" href="{uri}">'
    return f'<script defer src="{uri}"></script>'


def _wrap_html(slide_html: str) -> str:
    """將 slide body HTML 包裝成完整的 HTML 文件（含 KaTeX + GSAP + function-plot）。"""
    # 替換形象圖 placeholder → 本地絕對路徑（支援單括號和雙括號）
    mascot_uri = _MASCOT_DIR.as_uri()  # file:///app/assets/mascot
    slide_html = slide_html.replace("{{MASCOT_DIR}}", mascot_uri)
    slide_html = slide_html.replace("{MASCOT_DIR}", mascot_uri)
    # All third-party libs are served from assets/libs/ via file:// — keeps
    # Playwright's networkidle wait off the public internet so transient
    # CDN flaps (jsdelivr/cdnjs) cannot stall the pipeline.
    katex_head = (
        _local_asset_tag("katex.min.css", kind="css")
        + _local_asset_tag("katex.min.js", kind="js")
        + _local_asset_tag("katex-auto-render.min.js", kind="js")
    )
    gsap_head = (
        _local_asset_tag("gsap.min.js", kind="js")
        + _local_asset_tag("MotionPathPlugin.min.js", kind="js")
        + _local_asset_tag("DrawSVGPlugin.min.js", kind="js")
    )
    fp_script = ""
    fp_path = _LIBS_DIR / "function-plot.js"
    if fp_path.exists():
        fp_script = f'<script src="{fp_path.as_uri()}"></script>'
    diagram_libs = ""
    for lib_file in ["jsxgraphcore.js", "d3.min.js", "rough.js"]:
        lib_path = _LIBS_DIR / lib_file
        if lib_path.exists():
            diagram_libs += f'<script src="{lib_path.as_uri()}"></script>'
    jsxgraph_css = ""
    jsxgraph_css_path = _LIBS_DIR / "jsxgraph.css"
    if jsxgraph_css_path.exists():
        jsxgraph_css = f'<link rel="stylesheet" href="{jsxgraph_css_path.as_uri()}">'
    phosphor_css = (
        _local_asset_tag("phosphor-regular.css", kind="css")
        + _local_asset_tag("phosphor-bold.css", kind="css")
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">{katex_head}{gsap_head}{fp_script}{diagram_libs}{jsxgraph_css}{phosphor_css}<style>{SLIDE_BASE_CSS}</style></head>
<body>{slide_html}{_KATEX_INIT}{_GSAP_INIT}{_FUNCTIONPLOT_INIT}{_SLIDE_POSTPROCESS}</body></html>"""


_SVG_VIEWBOX_FIX_JS = """(() => {
    document.querySelectorAll('.slide svg').forEach(svg => {
        // KaTeX 內部 SVG（根號 radical/vinculum 等）尺寸是精確排版的，
        // getBBox 重設 height 會把根號擠到消失（123 實證 5 張 10 處 √ 缺字、
        // 數學式直讀變錯）。KaTeX 容器內的 SVG 一律不碰。
        if (svg.closest('.katex')) return;
        try {
            // Note: SVG text z-order is controlled by document order.
            // Do NOT use appendChild here — it breaks D3 transition animations.
            const bbox = svg.getBBox();
            if (bbox.width <= 0 || bbox.height <= 0) return;
            let changed = false;
            // Bottom overflow: content extends below SVG height
            const neededH = bbox.y + bbox.height + 20;
            if (neededH > svg.clientHeight) {
                svg.style.height = neededH + 'px';
                changed = true;
            }
            // Top overflow: content extends above y=0
            if (bbox.y < 0) {
                const pad = Math.abs(bbox.y) + 10;
                svg.style.marginTop = pad + 'px';
                svg.style.height = (parseInt(svg.style.height || svg.clientHeight) + pad) + 'px';
                changed = true;
            }
            if (changed) svg.style.overflow = 'visible';
        } catch(e) {}
    });
})()"""


_SVG_TEXT_ZORDER_JS = """(() => {
    // SVG text z-order 提升：把每個 <text> 移到其父節點末尾（最上層），避免被後畫的
    // 圖形（如軌道 circle）蓋住（決賽 122 v4 slide8「nucleus 被 n=1 軌道圓穿過」實證）。
    // SVG 無 z-index、z-order 純靠 document order；paint-order 只管 text 元素內部
    // stroke/fill 繪製先後、不能讓 text 浮到其他元素之上（白框不能防遮蓋）。
    // ⚠️ 只在「靜態截圖」路徑跑，動畫錄製路徑不跑：animated slide 的 D3 transition
    // 錄製時靠自己管 z-order，這裡 appendChild 會打斷它（見 _SVG_VIEWBOX_FIX_JS L585
    // 當初移除 appendChild 的原因）。re-append 到 parentNode 末尾（非 svg 末尾）以
    // 保留 <g transform> context。
    document.querySelectorAll('.slide svg').forEach(svg => {
        if (svg.closest('.katex')) return;
        svg.querySelectorAll('text').forEach(t => {
            const p = t.parentNode;
            if (p && p.lastChild !== t) p.appendChild(t);
        });
    });
})()"""


_SVG_TEXT_HCLIP_FIX_JS = """(() => {
    // SVG text 水平超界修：text 右端超出 svg 右界 → anchor=end 縮回界內；左端 <0 →
    // anchor=start 縮回。決賽 122 v6 slide10「hf = 1.89 eV」eV 被裁成「e」（嚴重物理
    // 單位錯：e=電子電荷 ≠ eV）實證——start-anchored 標籤 x=arrowX+8、文字右端超 svg
    // 520 右界、eV 出界被截。`_SVG_VIEWBOX_FIX_JS` 只 fit 垂直（height/y<0）、不管水平
    // x，這條補水平兜底。
    // 改 text-anchor + x 是改屬性（不像 z-order 的 appendChild 重排 DOM）→ 不打斷 D3
    // transition，靜態截圖 + 動畫錄製兩路徑都可安全套。
    document.querySelectorAll('.slide svg').forEach(svg => {
        if (svg.closest('.katex')) return;
        let svgW = (svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width)
                   || parseFloat(svg.getAttribute('width')) || svg.clientWidth;
        if (!svgW) return;
        svg.querySelectorAll('text').forEach(t => {
            try {
                const bb = t.getBBox();
                if (bb.x + bb.width > svgW) {
                    t.setAttribute('text-anchor', 'end');
                    t.setAttribute('x', svgW - 2);
                } else if (bb.x < 0) {
                    t.setAttribute('text-anchor', 'start');
                    t.setAttribute('x', 2);
                }
            } catch(e) {}
        });
    });
})()"""


class HtmlSlideRenderer(BaseSlideGenerator):
    """用 Playwright headless Chromium 將 HTML 投影片截圖為 PNG。"""

    def __init__(self, max_concurrent: int = 4):
        self._playwright = None
        self._browser: Browser | None = None
        # 同時渲染的投影片張數上限。每張動畫錄影開一個獨立 Chromium context，
        # 過多會吃滿 CPU/記憶體，所以用 semaphore 限流（見 generate_all）。
        self._max_concurrent = max_concurrent
        # 保護 browser 懶啟動：generate_all 平行呼叫 generate_slide 時，
        # 多個 coroutine 可能同時看到 self._browser is None 而重複啟動，用 lock 擋住。
        self._browser_lock = asyncio.Lock()

    async def _ensure_browser(self):
        """懶啟動 Playwright browser（整個 pipeline 共用一個 instance）。

        用 double-checked locking 確保平行渲染時只啟動一次。
        """
        if self._browser is not None:
            return
        async with self._browser_lock:
            # 進 lock 後再檢查一次：可能在等 lock 時別的 coroutine 已啟動好了
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
                logger.info("Playwright browser launched")

    async def close(self):
        """關閉 browser（pipeline 結束時呼叫）。"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def generate_all(
        self, segments: List[ScriptSegment], output_dir: Path
    ) -> List[SlideImage]:
        """平行生成所有投影片（受 max_concurrent 限制）。

        覆寫 base 的逐張序列實作。瓶頸在動畫錄影——每張要跑完整段 narration
        時長（如 30s），序列會累加（5 張 = 150s）；平行後只等最長那張。
        每張 generate_slide 各自開獨立 page/context，彼此隔離；用 semaphore
        限制同時數量，避免一次開太多 Chromium context 吃爆 CPU/記憶體。

        asyncio.gather 保留順序 → 回傳的 SlideImage 順序對齊 segments。
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        # 先啟動 browser，避免 N 個 coroutine 同時觸發懶啟動（雖然 _ensure_browser
        # 有 lock 保護，先啟動可省去搶 lock 的開銷）。
        await self._ensure_browser()

        logger.info(
            f"HTML renderer: generating {len(segments)} slides "
            f"(concurrency={self._max_concurrent})"
        )

        sem = asyncio.Semaphore(self._max_concurrent)

        async def _do_one(seg: ScriptSegment) -> SlideImage:
            async with sem:
                return await self.generate_slide(seg, output_dir)

        results = await asyncio.gather(*[_do_one(seg) for seg in segments])
        return list(results)

    _ANIM_TIMEOUT_SEC = 120  # 動畫錄影最長 120 秒，超時 fallback 到靜態

    async def _record_animation(
        self, html_path: Path, segment: ScriptSegment, output_dir: Path
    ) -> str | None:
        """錄製 animated slide 的 CSS 動畫為 MP4。

        Playwright video recording 需要獨立的 browser context。
        錄影時長 = min(narration 的 estimated_duration_sec, _ANIM_TIMEOUT_SEC)。
        """
        context = None
        page = None
        try:
            # 限制動畫時長，避免 hang
            duration_ms = min(
                int(segment.estimated_duration_sec * 1000),
                self._ANIM_TIMEOUT_SEC * 1000,
            )
            video_dir = output_dir / "anim_raw"
            video_dir.mkdir(parents=True, exist_ok=True)
            mp4_path = output_dir / f"slide_{segment.segment_id:03d}_anim.mp4"

            logger.info(
                f"Slide {segment.segment_id}: starting animation recording "
                f"({duration_ms}ms, timeout={self._ANIM_TIMEOUT_SEC}s)"
            )

            # 建立帶 video recording 的 context
            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                record_video_dir=str(video_dir),
                record_video_size={"width": 1280, "height": 720},
            )
            page = await context.new_page()

            await page.goto(
                f"file:///{html_path.as_posix()}",
                wait_until="networkidle",
            )
            logger.debug(f"Slide {segment.segment_id}: page loaded, waiting {duration_ms}ms")

            # 確保 GSAP 載入（如果 slide 使用 GSAP）
            if "gsap." in (segment.slide_html or ""):
                try:
                    await page.wait_for_function(
                        "typeof gsap !== 'undefined'",
                        timeout=10000,
                    )
                    logger.debug(f"Slide {segment.segment_id}: GSAP loaded")
                except Exception:
                    logger.warning(
                        f"Slide {segment.segment_id}: GSAP not loaded after 10s, "
                        f"recording anyway"
                    )

            # SVG viewBox auto-fit（與靜態截圖 page.evaluate(_SVG_VIEWBOX_FIX_JS) 對齊）：
            # 靜態截圖前會跑這個讓超出 svg 邊界的元素（如貼邊的軌道標籤 x<0）被 viewBox
            # 容納，但動畫錄製路徑原本沒跑 → svg 維持原 viewBox → 超界元素被影片 crop 裁掉
            # （決賽 122 v4 slide11「整個 n=3 標籤不見」實證：靜態可見、動畫全裁）。錄製前
            # 套用同一 fit，讓動畫畫面與靜態截圖一致、超界元素不被裁。
            try:
                await page.evaluate(_SVG_VIEWBOX_FIX_JS)
                # 水平超界修（改屬性不打斷 D3 transition、動畫路徑可安全套）
                await page.evaluate(_SVG_TEXT_HCLIP_FIX_JS)
            except Exception as e:
                logger.warning(
                    f"Slide {segment.segment_id}: anim viewBox/hclip fix failed ({e})"
                )

            # 等待動畫播完（有 timeout 保護）
            await asyncio.wait_for(
                page.wait_for_timeout(duration_ms),
                timeout=self._ANIM_TIMEOUT_SEC + 10,  # 多給 10 秒緩衝
            )

            # 取得錄影檔路徑
            video = page.video
            await page.close()
            page = None
            await context.close()
            context = None

            if video:
                raw_video_path = await video.path()
                logger.debug(f"Slide {segment.segment_id}: raw video at {raw_video_path}")
                # Playwright 輸出 webm，用 FFmpeg 轉成 mp4
                cmd = [
                    "ffmpeg", "-y",
                    "-i", str(raw_video_path),
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-preset", "ultrafast",
                    "-an",
                    str(mp4_path),
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode == 0:
                    logger.info(
                        f"Slide {segment.segment_id}: animation recorded "
                        f"({duration_ms}ms → {mp4_path})"
                    )
                    return str(mp4_path)
                else:
                    logger.warning(
                        f"Slide {segment.segment_id}: FFmpeg conversion failed, "
                        f"falling back to static"
                    )
        except asyncio.TimeoutError:
            logger.warning(
                f"Slide {segment.segment_id}: animation recording TIMEOUT "
                f"(>{self._ANIM_TIMEOUT_SEC}s), falling back to static"
            )
        except Exception as e:
            logger.warning(
                f"Slide {segment.segment_id}: animation recording failed ({e}), "
                f"falling back to static"
            )
        finally:
            # 確保 Playwright context 正確關閉，避免 hang
            try:
                if page and not page.is_closed():
                    await page.close()
                if context:
                    await context.close()
            except Exception:
                pass
        return None

    async def generate_slide(
        self, segment: ScriptSegment, output_dir: Path
    ) -> SlideImage:
        await self._ensure_browser()

        output_dir.mkdir(parents=True, exist_ok=True)
        png_path = output_dir / f"slide_{segment.segment_id:03d}.png"

        # 如果有 slide_html 就用 HTML renderer，否則 fallback
        if segment.slide_html:
            from app.services.slides.svg_normalizer import normalize_svg_in_html
            slide_html = normalize_svg_in_html(segment.slide_html)
            if segment.segment_id == 1:
                import re
                slide_html = re.sub(
                    r'<span\s+class="page-num">[^<]*</span>',
                    '',
                    slide_html,
                )
            html = _wrap_html(slide_html)
        else:
            # Fallback: 簡單的 bullets 頁面
            bullets_html = "".join(
                f"<li>{b}</li>" for b in (segment.slide_bullets or [])
            )
            phase = segment.teaching_phase or "core"
            html = _wrap_html(
                f'<div class="slide phase-{phase}">'
                f'<div class="phase-badge">{phase.upper()}</div>'
                f'<div class="title">{segment.slide_title}</div>'
                f'<div class="divider"></div>'
                f'<ul class="bullet-list">{bullets_html}</ul>'
                f'</div>'
            )

        # JSXGraph double-bracket point fix — Sonnet writes
        # create('point', [[x,y]], opts) instead of create('point', [x,y], opts)
        # The extra nesting causes a silent JS error that kills all subsequent elements
        if ".create('point'" in html:
            import re as _re
            _orig = html
            html = _re.sub(
                r"create\('point',\s*\[\[([^\]]+)\]\]",
                r"create('point', [\1]",
                html,
            )
            if html != _orig:
                _fixes = len(_re.findall(r"create\('point'", html))
                logger.info(
                    f"Slide {segment.segment_id}: fixed JSXGraph double-bracket point ({_fixes} points)"
                )

        # JSXGraph text syntax fix — Sonnet consistently writes
        # board.create('text',[x,y], content, opts) instead of [x,y,content]
        if (".create('text'" in html):
            import re as _re
            _orig = html
            # Pattern: create('text', [x,y], 'string' → [x,y,'string']
            html = _re.sub(
                r"create\('text',\s*\[([^\]]+)\],\s*('(?:[^'\\]|\\.)*')",
                r"create('text', [\1, \2]",
                html,
            )
            # Pattern: create('text', [x,y], "string" → [x,y,"string"]
            html = _re.sub(
                r'create\(\'text\',\s*\[([^\]]+)\],\s*("(?:[^"\\]|\\.)*")',
                r"create('text', [\1, \2]",
                html,
            )
            # Pattern: create('text', [x,y], variable, → [x,y,variable],
            html = _re.sub(
                r"create\('text',\s*\[([^\]]+)\],\s*([a-zA-Z_]\w*(?:\.\w+)*),",
                r"create('text', [\1, \2],",
                html,
            )
            # Pattern: create('text', [x,y], [fn → [x,y, fn (function in array)
            html = _re.sub(
                r"create\('text',\s*\[([^\]]+)\],\s*\[",
                r"create('text', [\1,",
                html,
            )
            if html != _orig:
                _fixes = html.count("create('text'")
                logger.info(
                    f"Slide {segment.segment_id}: fixed JSXGraph text syntax ({_fixes} text elements)"
                )

        # JSXGraph slider auto-play safety net
        # Only injects if Sonnet didn't write its own autoplay loop.
        # Uses JXG.boards global API (separate script block can't access local vars).
        # Uses requestAnimationFrame + accumulator _t (matches KB/POC pattern).
        _lo = html.lower()
        _has_autoplay = (
            "requestAnimationFrame" in html
            or "setInterval" in html
            or "_autoplay" in html
        )
        if ("slider" in _lo
                and ("JXG." in html or ".create('slider'" in html)
                and not _has_autoplay):
            _autoplay_js = (
                '\n<script>document.addEventListener("DOMContentLoaded",function(){'
                'if(typeof JXG==="undefined")return;'
                "setTimeout(function(){"
                "for(var id in JXG.boards){var b=JXG.boards[id];if(!b)continue;"
                "for(var oid in b.objects){var obj=b.objects[oid];"
                "if(obj.elType==='slider'){"
                "var s=obj,_min=s._smin,_max=s._smax,"
                "_dt=(_max-_min)/300,_t=s.Value();"
                "function _ap(){_t+=_dt;if(_t>_max)_t=_min;"
                "s.setValue(_t);b.update();requestAnimationFrame(_ap);}_ap();"
                "break;}}break;}"
                "},200);});</script>"
            )
            html = html.replace("</body>", _autoplay_js + "\n</body>")
            logger.info(
                f"Slide {segment.segment_id}: injected JSXGraph auto-play "
                f"(safety net via JXG.boards global API)"
            )

        # Phosphor icon 名白名單修復（unknown 名→alias/中性 fallback，防畫面空格）
        html = _fix_unknown_phosphor_icons(html, segment.segment_id)

        # d3 <div> 容器修復（SVG 元素 append 進 div = 無聲全滅 → 轉 <svg>）
        html = _fix_d3_div_containers(html, segment.segment_id)

        # entity 反轉義（純文本 API）+ 已知口語拆寫形修復
        html = _fix_entity_in_text_api(html, segment.segment_id)
        html = _fix_spoken_forms(html, segment.segment_id)

        # card 漸層卡可讀性（.card!important 蓋 inline gradient + 白字 → 隱形；淺字補 inline !important）
        html = _fix_card_gradient_legibility(html, segment.segment_id)

        # 標題 wrapper 上下 padding 過大（Sonnet inline flex wrapper 撐開標題留白）
        html = _fix_title_wrapper_padding(html, segment.segment_id)

        # 寫入暫存 HTML 並截圖
        html_path = output_dir / f"slide_{segment.segment_id:03d}.html"
        html_path.write_text(html, encoding="utf-8")

        page = await self._browser.new_page(
            viewport={"width": 1280, "height": 720}
        )
        try:
            await page.goto(
                f"file:///{html_path.as_posix()}",
                wait_until="networkidle",  # 等 KaTeX CDN 載入完成
            )

            # Wait for JS diagram libraries (JSXGraph, D3, Rough.js) to finish rendering.
            # Only wait if the slide actually uses a diagram lib — saves ~3s per normal slide.
            if any(m in html for m in ("JXG.", "d3.select", "rough.canvas", "board.create", "functionPlot(")):
                await page.wait_for_timeout(3000)

            # Wait for DOM to stabilize (KaTeX, diagram libs, post-process JS)
            await page.evaluate("""new Promise(resolve => {
                const observer = new MutationObserver((mutations, obs) => {
                    clearTimeout(window._domStableTimer);
                    window._domStableTimer = setTimeout(() => { obs.disconnect(); resolve(); }, 500);
                });
                observer.observe(document.body, { childList: true, subtree: true, characterData: true });
                setTimeout(() => { observer.disconnect(); resolve(); }, 3000);
            })""")

            # 溢出偵測：如果內容超過 720px，自動縮放到剛好塞得下
            scroll_height = await page.evaluate(
                "document.querySelector('.slide').scrollHeight"
            )
            if scroll_height and scroll_height > 720:
                scale = 720 / scroll_height
                logger.warning(
                    f"Slide {segment.segment_id} overflows "
                    f"({scroll_height}px > 720px), scaling to {scale:.2f}"
                )
                await page.evaluate(f"""(() => {{
                    const slide = document.querySelector('.slide');
                    slide.style.transform = 'scale({scale})';
                    slide.style.transformOrigin = 'top left';
                    slide.style.width = '{1280 / scale:.0f}px';
                    slide.style.height = '{720 / scale:.0f}px';
                }})()""")

            # 對比度保底：用 APCA (Accessible Perceptual Contrast Algorithm) 檢查
            # APCA 是 WCAG 3.0 草案的對比度演算法，比 WCAG 2.x 更精確
            # |Lc| < 60 → 正文不可讀，強制改深色
            fixed = await page.evaluate("""(() => {
                let fixed = 0;
                const skip = (el) => {
                    if (el.closest('.code-block')) return true;
                    if (el.classList && el.classList.contains('page-num')) return true;
                    if (el.classList && el.classList.contains('card__num')) return true;
                    return false;
                };
                const sRGBtoY = (r, g, b) => {
                    return 0.2126729 * Math.pow(r / 255, 2.4)
                         + 0.7151522 * Math.pow(g / 255, 2.4)
                         + 0.0721750 * Math.pow(b / 255, 2.4);
                };
                const APCAcontrast = (txtY, bgY) => {
                    const normBG = Math.pow(bgY, 0.56);
                    const normTXT = Math.pow(txtY, 0.57);
                    const raw = (normBG - normTXT) * 1.14;
                    if (Math.abs(raw) < 0.1) return 0;
                    return raw > 0 ? raw - 0.027 : raw + 0.027;
                };
                const getEffectiveBg = (el) => {
                    let node = el;
                    while (node && node !== document.documentElement) {
                        const bg = getComputedStyle(node).backgroundColor;
                        const m = bg.match(/(\d+)/g);
                        if (m) {
                            const [r, g, b, a] = m.map(Number);
                            if (a === undefined || a > 0.1) {
                                return [r, g, b];
                            }
                        }
                        node = node.parentElement;
                    }
                    return [255, 255, 255];
                };
                // Skip auto-contrast when any ancestor uses a gradient background.
                // Gradient colors aren't resolvable from computed style, so the naive
                // bg walk falls back to body color (often light cream #fdf6f0) and
                // misjudges "light bg + light text", then darkens LLM-set white text
                // onto a visually dark gradient card → ruins contrast. Trust LLM.
                const hasGradientAncestor = (el) => {
                    let node = el;
                    while (node && node !== document.documentElement) {
                        const bgImg = getComputedStyle(node).backgroundImage;
                        if (bgImg && bgImg !== 'none' && bgImg.includes('gradient')) return true;
                        node = node.parentElement;
                    }
                    return false;
                };
                const els = document.querySelectorAll('.slide *');
                for (const el of els) {
                    if (skip(el)) continue;
                    if (hasGradientAncestor(el)) continue;
                    if (!el.childNodes.length) continue;
                    const hasText = Array.from(el.childNodes).some(
                        n => n.nodeType === 3 && n.textContent.trim()
                    );
                    if (!hasText) continue;
                    const style = getComputedStyle(el);
                    const cm = style.color.match(/(\d+)/g);
                    if (!cm) continue;
                    const [tr, tg, tb] = cm.map(Number);
                    const txtY = sRGBtoY(tr, tg, tb);
                    const [br, bg2, bb] = getEffectiveBg(el);
                    const bgY = sRGBtoY(br, bg2, bb);
                    // Simple luminance contrast check
                    const ratio = (Math.max(txtY, bgY) + 0.05)
                                / (Math.min(txtY, bgY) + 0.05);
                    if (ratio >= 4.5) continue;
                    // Dark text on dark bg → lighten; light text on light bg → darken
                    if (bgY < 0.2 && txtY < 0.2) {
                        el.style.color = '#f5f5f5';
                    } else if (bgY > 0.4 && txtY > 0.4) {
                        el.style.color = '#37474f';
                    } else {
                        continue;
                    }
                    fixed++;
                }
                return fixed;
            })()""")
            if fixed:
                logger.info(
                    f"Slide {segment.segment_id}: fixed {fixed} low-contrast text elements"
                )

            await page.evaluate(_SVG_VIEWBOX_FIX_JS)
            # SVG text z-order 提升（只在靜態截圖路徑、不在動畫錄製）：text 移到父末尾、
            # 避免被軌道圓等後畫圖形蓋住（122 v4 slide8 nucleus 被 n=1 圓穿過實證）
            await page.evaluate(_SVG_TEXT_ZORDER_JS)
            # SVG text 水平超界修：右端/左端出 svg 界 → anchor 縮回（122 v6 slide10 hf eV 被裁實證）
            await page.evaluate(_SVG_TEXT_HCLIP_FIX_JS)
            await page.screenshot(path=str(png_path), full_page=False)
        finally:
            await page.close()

        # animated slide → 額外用 Playwright video recording 錄製 CSS 動畫
        # 自動偵測：LLM 可能忘了設 animated: true 但有寫動畫程式碼
        video_path_str = None
        _html = segment.slide_html or ""
        _has_css_anim = "@keyframes" in _html
        _has_gsap = "gsap." in _html
        _has_d3_transition = ".transition()" in _html or "setInterval" in _html or "requestAnimationFrame" in _html
        _has_jxg_slider = "slider" in _html.lower() and ("JXG." in _html or ".create('slider'" in _html)
        is_animated = segment.animated or _has_css_anim or _has_gsap or _has_d3_transition or _has_jxg_slider
        if is_animated:
            logger.info(
                f"Slide {segment.segment_id}: detected animation "
                f"(flag={segment.animated}, css={_has_css_anim}, gsap={_has_gsap}, d3={_has_d3_transition}, jxg_slider={_has_jxg_slider})"
            )
            video_path_str = await self._record_animation(
                html_path, segment, output_dir
            )

        logger.debug(f"Rendered slide {segment.segment_id} → {png_path}"
                      f"{' + video' if video_path_str else ''}")
        return SlideImage(
            segment_id=segment.segment_id,
            image_path=str(png_path),
            video_path=video_path_str,
        )
