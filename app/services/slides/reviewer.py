"""Slide Reviewer — 用 Claude Vision 審核投影片截圖，偵測溢出和版面問題。

流程：PNG 截圖 → Claude Vision → ok 或回傳修正 HTML
最多 max_rounds 輪，fail-open（錯誤視為通過）。
"""

import base64
import logging
from pathlib import Path

import anthropic

from config.settings import Settings

logger = logging.getLogger(__name__)

REVIEW_SYSTEM_PROMPT = """\
You are a slide design reviewer for educational presentations.
These slides will be projected in a classroom — every student, even those in the back row, must be able to read all text clearly.
Look at the rendered slide image and its HTML source. The viewport is exactly 1280×720px.

A slide is OK if ALL of the following are true:
1. No content is clipped or cut off (nothing extends beyond the visible area)
2. All text is at least 20px and readable
3. No elements overlap or collide with each other — especially mascot/character images must NOT cover any text or cards
4. The page number in the bottom-right corner is fully visible
5. All equations, formulas, and mathematical expressions are correct
6. ALL text has strong contrast against its background — dark text on light backgrounds or light text on dark backgrounds. NO white/light text on pastel or light-colored backgrounds.
7. Content has comfortable breathing room at the bottom — no text or cards within 80px of the slide bottom edge (y > 640px). The bottom area is reserved for page numbers only.

A slide NEEDS FIXING if ANY of the following are true:
1. Text or visual elements are cut off at the bottom or sides
2. Text is too small to read comfortably
3. Elements overlap, making content hard to understand — especially if a mascot/character image covers text or cards. Fix by moving the image to `top: 10px; right: 20px;` (top-right corner) or removing it if no safe position exists
4. The layout is visually broken (e.g., columns misaligned, cards overlapping)
5. An equation or formula is wrong (e.g., missing terms, wrong operators, incorrect subscripts/superscripts)
6. Text has poor contrast — e.g., white text on light/pastel backgrounds, light gray text on white, or any combination where text is hard to read. Fix by changing text color to a dark color (#333 or darker) or changing the background to be darker.
7. Content is too close to the bottom edge — text or cards appear in the bottom 80px of the slide. Fix by reducing font sizes, tightening spacing, or reorganizing the layout to keep content above y=640px.
8. **SVG text hidden behind shapes (z-order)**. SVG renders elements in document order — later elements appear on top. If text labels are hidden behind rectangles, circles, or filled shapes, the fix is to move the text `append()` calls AFTER the shape `append()` calls in the D3/SVG code.
9. **SVG animation text readability**. If the slide has a JS animation (D3 transitions, setInterval highlight loops), check that text labels remain readable in ALL animation states. A common bug: the highlight function sets shape opacity to 1 but leaves text at 0.3-0.5, making labels invisible during playback. Fix by ensuring highlighted node text gets opacity 1 alongside its shape, and dimmed node text stays at minimum 0.6.
9. **Math graph curves are drawn in the wrong direction**. When the slide contains a graph of a mathematical function (e.g., error vs. complexity, y = f(x) curves), check the HTML's SVG `<path d="...">` coordinates against the conceptual shape the slide describes (read the title/legend/narration context). Remember: SVG `y=0` is at the TOP; larger y = visually LOWER. Chart convention: top = high value, bottom = low value. A "decreasing" curve has SVG y going from SMALL (top) to LARGE (bottom). A "U-shape (valley)" has SVG y going SMALL → LARGE → SMALL. If the path is inverted relative to the intended shape, fix it — either correct the path coordinates, or replace with a `function-plot.js` call (pre-loaded, auto-renders Cartesian y-up). This check ONLY applies to mathematical function graphs; physics diagrams, circuit paths, vector arrows, and illustrative curves don't need this direction check.
10. **Spelled-out (TTS) notation visible on the slide**. Spoken-form notation like "A-I", "L-L-M", "H-2-O-2", "A-T-P", "K-N-N" (uppercase letters/digits joined by hyphens) belongs ONLY in the narration script, never on a slide. If you see it in the slide text or in JS-generated labels, fix it to the proper visual form: AI, LLM, H₂O₂, ATP, KNN. (Legitimate hyphenated terms like "X-ray" or "e-mail" are fine — only flag spelled-out acronym/formula patterns.)

RESPONSE FORMAT:
- If the slide is OK, respond with exactly: ok
- If the slide needs fixing, respond with ONLY the corrected HTML (a complete <div class="slide phase-xxx">...</div>).
  No explanation, no markdown fences — just the raw HTML.
- The fixed HTML must fit within 1280×720px. Reduce spacing, padding, margins, or font sizes to make it fit.
- NEVER delete or remove content. All original text, bullet points, and elements must be preserved.
- Keep the same teaching content and phase class."""


# 2026-06-10 起精審輪全部走 Fable 5 + refusal fallback，取消科目路由。
# 理由：Vision Review 平行化後，生物題 100% refusal 的 fallback round trip（~10s）
# 不再序列累加（平行了）、不再是瓶頸；refusal 零輸出不計費；萬一沒 refuse 反而賺到
# Fable 5 vision 品質。所以不為任何科目特別路由 4.7，統一 Fable 5、refuse 時
# review() 內 fallback 回 Opus 4.7。
# （歷史：原本生物→4.7、其他→Fable5 的 `_REFUSAL_PRONE_SUBJECTS` 科目路由已移除；
#  實測 refusal 率：生物 100% / 化學 ~3-10% / 物理 0% / CS 偶發。）


class SlideReviewer:
    """用 Claude Vision 審核投影片截圖。Sonnet 粗篩 + 精審輪。

    Loop 由 pipeline._render_and_review 管理（每輪重新截圖），
    本類只負責單次 review call + 模型選擇：
    - 粗篩輪 → Sonnet 4.6
    - 精審輪 → Fable 5（refusal 時 review() 內 fallback 回 Opus 4.7）
    所有科目統一走 Fable 5（2026-06-10 取消科目路由，見上方註解）。
    """

    def __init__(self, config: Settings, sonnet_rounds: int = 1, opus_rounds: int = 2):
        self.client = anthropic.Anthropic(
            api_key=config.anthropic_api_key.get_secret_value(),
            timeout=120.0,
        )
        self.sonnet_model = config.review_model
        self.opus_model = config.opus_review_model  # 精審輪（Fable 5，env OPUS_REVIEW_MODEL 可覆蓋）
        self.opus_fallback_model = config.opus_review_fallback_model  # refusal 時重審用
        self.sonnet_rounds = sonnet_rounds
        self.opus_rounds = opus_rounds
        self.max_rounds = sonnet_rounds + opus_rounds

    # JS library detection → KB file mapping
    _LIB_KB_MAP = {
        "jsxgraph": ("jsxgraph_api.md", lambda h: "JXG." in h or ".create('slider'" in h or ".create('point'" in h),
        "d3": ("d3_api.md", lambda h: "d3." in h.lower() or ".transition()" in h),
        "function-plot": ("function_plot_api.md", lambda h: "functionPlot" in h or "function-plot" in h),
    }

    def _load_lib_references(self, slide_html: str) -> str:
        """Opus 輪限定：偵測 slide 用到哪些 JS lib，載入對應 KB。"""
        kb_dir = Path(__file__).resolve().parent.parent.parent / "config" / "curriculum" / "tools"
        sections = []
        for lib_name, (kb_file, detector) in self._LIB_KB_MAP.items():
            if detector(slide_html):
                kb_path = kb_dir / kb_file
                if kb_path.exists():
                    sections.append(
                        f"--- {lib_name} API Reference ---\n"
                        f"{kb_path.read_text(encoding='utf-8')}"
                    )
        if not sections:
            return ""
        return (
            "\n\n⚠️ This slide uses JavaScript visualization libraries. "
            "Before judging visual correctness, verify that the JS API calls "
            "follow the correct syntax documented below. Fix any syntax errors "
            "you find — they cause silent rendering failures.\n\n"
            + "\n\n".join(sections)
        )

    def review(
        self,
        image_path: str,
        slide_html: str,
        slide_title: str,
        round_num: int = 1,
        subject: str | None = None,
    ) -> str | None:
        """審核一張投影片截圖（單次呼叫）。

        round_num: 目前第幾輪（1-based），決定用 Sonnet 或精審 model。
        subject: 偵測到的科目；2026-06-10 起 reviewer 不再用於 model 選擇
                 （取消科目路由、全 Fable 5），保留參數向後相容、pipeline 仍傳。
        Returns: None=ok, str=修正 HTML。
        """
        use_opus = round_num > self.sonnet_rounds
        # 精審輪統一 Fable 5（refusal 時下方 fallback 回 Opus 4.7）；不再科目路由。
        model = self.opus_model if use_opus else self.sonnet_model
        label = "Opus" if use_opus else "Sonnet"

        img_bytes = Path(image_path).read_bytes()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")

        # Opus rounds: attach library KB when relevant JS libs detected
        lib_ref = self._load_lib_references(slide_html) if use_opus else ""

        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64,
                },
            },
            {
                "type": "text",
                "text": (
                    f"Slide: \"{slide_title}\"\n"
                    f"HTML:\n```html\n{slide_html}\n```"
                    f"{lib_ref}"
                ),
            },
        ]

        # max_tokens 設為 Sonnet 4.6 的 max output（64K，兩種 model 都合法），實質不設限。
        # 原本的 4096 有截斷風險：adaptive thinking 的 thinking tokens 算在 max_tokens 內，
        # thinking 吃掉預算後修正 HTML 會被砍半，下方 "<div" 判斷會把截斷的 HTML 拿去用。
        # 官方 rate-limits 文件明載 OTPM 即時計實際產出 token、max_tokens 不參與計算，
        # 所以調高沒有 rate limit 與速度副作用（2026-06-10 查證）。
        kwargs = {
            "model": model,
            "max_tokens": 64000,
            "system": REVIEW_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_content}],
        }
        if use_opus:
            kwargs["output_config"] = {"effort": "high"}
            kwargs["thinking"] = {"type": "adaptive"}

        try:
            response = self.client.messages.create(**kwargs)

            # Fable 5 classifier refusal：HTTP 200 + stop_reason="refusal" + content 空陣列，
            # 不會丟 exception。處理策略：換 fallback model（Opus 4.7，沒有 classifier）
            # 重審同一張 slide、用它的結果；fallback 也 refuse 或未設定才 fail-open
            # （該 slide 已過 Sonnet 粗篩輪，有品質底線）。
            # stop_details 在舊版 SDK 是未型別化的 dict（extra field）、新版是 object，
            # 兩種形態都要支援，不然 category 會永遠 log 成 None（2026-06-10 實測踩到）。
            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                if isinstance(details, dict):
                    category = details.get("category")
                else:
                    category = getattr(details, "category", None)
                fallback = self.opus_fallback_model
                if fallback and model != fallback:
                    logger.warning(
                        f"Slide review refused by classifier ({label}/{model}, "
                        f"category={category}), retrying on {fallback}"
                    )
                    kwargs["model"] = fallback
                    response = self.client.messages.create(**kwargs)
                if response.stop_reason == "refusal":
                    logger.warning(
                        f"Slide review refused by classifier ({label}/{model}, "
                        f"category={category}), no usable fallback, auto-approving"
                    )
                    return None

            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text
            text = text.strip()

            if text.lower() == "ok":
                return None

            if text.startswith("```html"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            if "<div" in text:
                div_start = text.index("<div")
                if div_start > 0:
                    logger.info(f"Stripping {div_start} chars of preamble before <div")
                return text[div_start:]

            logger.warning(f"Unexpected {label} review response, auto-approving: {text[:100]}")
            return None

        except Exception as e:
            logger.warning(f"Slide review failed ({label}/{model}): {e}, auto-approving")
            return None
