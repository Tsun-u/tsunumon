"""Claude LLM service — 兩階段教學腳本生成。

Stage 1 (Outline): 課程大綱規劃，含 web_search 事實查核 + code_execution 驗算
Stage 2 (Expand):  大綱展開為投影片內容 + 旁白腳本

支援 server-side tools（Anthropic 自動執行）:
- web_search: 讓 Claude 搜尋網路驗證知識正確性（零幻覺）
- code_execution: 讓 Claude 執行程式碼驗算數學/科學公式
"""

import copy
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import List, Optional

import anthropic

from app.models.domain import (
    CourseOutline,
    PersonaAnalysis,
    ScriptSegment,
    SequenceOutline,
    TeachingRequest,
    TeachingScript,
)
from app.services.llm.base import BaseLLMService
from config.settings import Settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "config" / "prompts"

# Low-focus rule block — Router 決定 focus_level 為 "low" 時才插入三階段 prompt。
# Router 已經針對「Focus Level: Low / focus ≤10 min / distractibility 描述」做過判斷，
# 這段規則本身不用再 re-describe 觸發條件，直接應用即可。
_LOW_FOCUS_RULE = (
    "- **Low-focus rule**: For low-attention-span personas, keep the Next Steps "
    "narration tight — about 2 sentences, homework/explore-later tone "
    "(e.g. \"If you're curious, look up centroid and orthocenter — built from "
    "medians and altitudes.\"). The on-screen Next Steps slide stays full; only "
    "the narration is condensed."
)


def _low_focus_block(focus_level: str) -> str:
    """Router 判 low 時回傳規則；否則空字串（baseline 不受影響）。"""
    return _LOW_FOCUS_RULE if focus_level == "low" else ""

SYSTEM_PROMPT_OUTLINE = (
    "You are an expert curriculum designer for educational video lessons.\n\n"
    "Guidelines:\n"
    "- Use web search to verify facts, dates, statistics, or any claims you are not 100% certain about.\n"
    "- Use code execution to verify mathematical calculations or scientific formulas.\n"
    "- Your final response MUST be a valid JSON object (no markdown fences, no extra text).\n"
    "- Do NOT fabricate information. If unsure, verify first."
)

SYSTEM_PROMPT_EXPAND = (
    "You are an expert educational content writer who turns course outlines into engaging slide content and narration.\n\n"
    "Guidelines:\n"
    "- Use web search only if you need to verify a specific detail not covered in the outline.\n"
    "- Use code execution if you need to verify a calculation for a worked example.\n"
    "- Your final response MUST be a valid JSON object (no markdown fences, no extra text).\n"
    "- Stick to the outline's structure and facts. Do not invent new major concepts."
)

# Server-side tools: Anthropic 自動執行，不需要我們處理 tool_result
# Haiku 4.5 / Sonnet / Opus 全部支援 web_search + web_fetch + code_execution
# Server-side tools — 根據模型能力動態組裝
# Haiku: web_search 需要 allowed_callers=["direct"]（不支援 dynamic filtering）
# Sonnet+: web_search 支援 dynamic filtering（預設 allowed_callers）
MODELS_WITH_DYNAMIC_FILTERING = ()  # 全部用 web_fetch_20250910 穩定版

# web_fetch 網域白名單：教學知識庫（Tool API docs）host。預設只信任
# GitHub Pages KB；若自建 KB host（如自架伺服器），用環境變數
# WEB_FETCH_EXTRA_DOMAINS（逗號分隔）加入，例：WEB_FETCH_EXTRA_DOMAINS=my-kb.example.com
WEB_FETCH_ALLOWED_DOMAINS = ["tsun-u.github.io"]
_extra_domains = os.environ.get("WEB_FETCH_EXTRA_DOMAINS", "").strip()
if _extra_domains:
    WEB_FETCH_ALLOWED_DOMAINS += [d.strip() for d in _extra_domains.split(",") if d.strip()]

_TOOL_KB_BASE = "https://tsun-u.github.io/tsunumon-kb/tools"
_TOOL_KB_MAP = {
    "jsxgraph": f"{_TOOL_KB_BASE}/jsxgraph_api.md",
    "function-plot": f"{_TOOL_KB_BASE}/function_plot_api.md",
    "d3": f"{_TOOL_KB_BASE}/d3_api.md",
    "rough": f"{_TOOL_KB_BASE}/rough_api.md",
    # icon 名單（全科目）：LLM 憑記憶猜 icon 名會猜到不存在的（決賽實證
    # telescope/loop/circuit-board/reflect-horizontal...→畫面空格），必須查表
    "phosphor": f"{_TOOL_KB_BASE}/phosphor_icons.md",
}
_SUBJECT_TOOLS = {
    "physics": ["d3", "function-plot", "phosphor"],
    "math": ["jsxgraph", "function-plot", "phosphor"],
    "biology": ["d3", "function-plot", "phosphor"],
    "cs": ["rough", "d3", "function-plot", "phosphor"],
}
_PHYSICS_KEYWORDS = ["physics", "pendulum", "energy", "force", "momentum", "wave",
                     "circuit", "estimation", "dimensional", "fermi", "projectile",
                     "velocity", "acceleration", "gravity", "centripetal", "friction",
                     "electric", "magnetic", "optic", "thermodynamic", "photoelectric",
                     "photon", "quantum", "relativity", "newton", "kinetic",
                     # report 宇分析新增（AP Physics 1/2 詞彙覆蓋）
                     "kinematics", "torque", "rotation", "rotational", "oscillation",
                     "fluid", "thermal", "greenhouse", "gas law", "induction",
                     "radioactive", "fission", "fusion", "atom", "doppler", "standing wave"]
_MATH_KEYWORDS = ["math", "calculus", "algebra", "geometry", "triangle", "circle",
                  "quadratic", "function", "equation", "regression", "statistics",
                  "trigonometric", "trigonometry", "sine", "cosine", "taylor", "polynomial",
                  "integral", "derivative", "limit", "matrix", "vector", "probability",
                  "logarithm", "exponential", "series", "convergence",
                  # report 宇分析新增（AP Statistics / Calculus 詞彙覆蓋）
                  # mean/sequence 用 \b 邊界避免子字串誤觸（means/meaning、consequence/subsequent）
                  "distribution", "normal", r"\bproportions?\b", "percentile", "sampling",
                  "inference", "chi-square", "hypothesis", "confidence", r"\bmean\b", "median",
                  "deviation", "variance", "parameter", "polar", "parametric",
                  "differential", "accumulation", r"\bsequences?\b", "continuity"]
_BIO_KEYWORDS = ["biology", "cell", "plant", "animal", "evolution", "speciation",
                 "anatomy", "vascular", "tissue", "organism", "ecology",
                 "digestive", "nutrient", "organ", "absorption", "homeostasis",
                 "blood", "glucose", "hormone", "insulin", "photosynthesis",
                 "protein", "enzyme", r"\bdna\b", r"\brna\b", "gene", "chromosome",
                 "mitosis", "meiosis", "respiration", "ecosystem",
                 # report 宇分析新增（AP Biology 詞彙覆蓋；energetics 而非 energy 避免撞 physics）
                 "heredity", "natural selection", "signal", "transduction", "apoptosis",
                 "communication", "energetics", "diversity", "interdependence",
                 "population", "community", "genetic", "mutation", "phenotype",
                 "genotype", "allele"]
_CS_KEYWORDS = ["computer", r"\bai\b", "artificial intelligence", "algorithm",
                "machine learning", "neural", "programming", "software", "coding",
                r"\brag\b", "fine-tuning", "network", "training data", r"\bknn\b",
                "deep learning", "retrieval", r"\bllm\b", "language model",
                "data structure", "database", "encryption", "cybersecurity",
                "operating system", "compiler", "binary", "recursion",
                # report 宇分析新增（AP CSP / CSA 詞彙覆蓋）
                # program/object/class/method 用 \b 邊界避免子字串誤觸——尤其
                # "object" 會命中每題都有的 "learning objective"（系統性 +1 CS、
                # 造成平手）、"program" 會命中 bio 的 "programmed cell death"
                r"\bprograms?\b", r"\bclasses?\b", r"\bobjects?\b", r"\bmethods?\b",
                "instance", "inheritance",
                "array", "boolean", "iteration", "loop", "variable", "primitive",
                "abstraction", "development", "internet", "protocol", "simulation", "cyber"]

def _kw_match(kw: str, text: str) -> bool:
    if kw.startswith(r"\b"):
        return bool(re.search(kw, text))
    return kw in text

def _detect_subject(course_requirement: str) -> str:
    text = course_requirement.lower()
    scores = {
        "physics": sum(1 for kw in _PHYSICS_KEYWORDS if _kw_match(kw, text)),
        "math": sum(1 for kw in _MATH_KEYWORDS if _kw_match(kw, text)),
        "biology": sum(1 for kw in _BIO_KEYWORDS if _kw_match(kw, text)),
        "cs": sum(1 for kw in _CS_KEYWORDS if _kw_match(kw, text)),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


def _log_web_fetch_results(content_blocks, request_id: str, step_name: str) -> None:
    """記錄一次回應裡的 web_fetch tool results：成功記 url、失敗記 url + error_code。

    web_fetch error block（SDK type=web_fetch_tool_result_error）本身只帶 error_code、
    不帶 url，所以先從同批 server_tool_use block（web_fetch 呼叫）建
    tool_use_id → 請求 url 的對照，讓 error log 直接顯示「哪個 URL 失敗」，
    方便診斷 model 自拼的子頁 URL 是否畸形（url_not_accessible / url_not_allowed）。
    """
    # 先建 tool_use_id → 請求 url
    url_by_id = {}
    for block in content_blocks:
        if getattr(block, "type", None) == "server_tool_use" and getattr(block, "name", None) == "web_fetch":
            inp = getattr(block, "input", None)
            url = None
            if isinstance(inp, dict):
                url = inp.get("url")
            elif inp is not None:
                url = getattr(inp, "url", None)
            if url:
                url_by_id[getattr(block, "id", None)] = url

    for block in content_blocks:
        if getattr(block, "type", None) != "web_fetch_tool_result":
            continue
        req_url = url_by_id.get(getattr(block, "tool_use_id", None), "?")
        content = getattr(block, "content", None)
        if content and getattr(content, "type", None) == "web_fetch_tool_result_error":
            logger.warning(
                f"[{request_id}] {step_name}: web_fetch error: "
                f"url={req_url} code={getattr(content, 'error_code', '?')}"
            )
        else:
            url = (getattr(content, "url", None) if content else None) or req_url
            logger.info(f"[{request_id}] {step_name}: web_fetch ok: {url}")


_TOOL_DESCRIPTIONS = {
    "jsxgraph": "Math/physics geometry (triangles, circles, circumcenter, vectors, coordinate geometry)",
    "function-plot": "Math function graphs (parabolas, regression lines, error curves, distributions)",
    "d3": "Biology/physics/CS phenomena (cell diagrams, energy diagrams, pendulums, force arrows, circuits, data flow diagrams, comparison charts, process visualizations)",
    "rough": "Concept/architecture diagrams (neural networks, system diagrams, hand-drawn-feel concept maps)",
    "phosphor": (
        "Icon name reference. ICON NAMES MUST COME FROM THIS PAGE — any name not "
        "listed renders as a BLANK SPACE (no error). Never guess icon names from memory; "
        "before using any icon beyond the 6 phase-badge icons, web_fetch this reference"
    ),
}

def _build_tool_kb_hint(course_requirement: str, subject_override: str = None) -> str:
    subject = subject_override if subject_override and subject_override in _SUBJECT_TOOLS else _detect_subject(course_requirement)
    tool_names = _SUBJECT_TOOLS.get(subject, list(_TOOL_KB_MAP.keys()))
    lines = ["\n## Diagram Tool References"]
    lines.append("Before writing diagram code, web_fetch the relevant API reference:")
    for name in tool_names:
        url = _TOOL_KB_MAP[name]
        desc = _TOOL_DESCRIPTIONS[name]
        lines.append(f"- **{name}** — {desc}. Reference: `{url}`")
    return "\n".join(lines)


def _build_unit_url_hint(unit_urls) -> str:
    """SubjectClassifier 選的相關 KB unit URL → 明列進 prompt。

    把具體 unit URL 寫進 prompt = 進 web_fetch 的 prior_context = model 搆得到
    （見 memory reference_web_fetch_prior_context）。解決「只列 index URL、model
    從 index 內容拼 unit URL 被 url_not_in_prior_context 擋」的 KB-miss。
    """
    if not unit_urls:
        return ""
    lines = "\n".join(f"- {u}" for u in unit_urls)
    return (
        "\n## Most Relevant Curriculum KB Files (fetch these directly)\n"
        "For this specific topic, the following curriculum reference files are the most "
        "relevant. web_fetch them directly to cross-check terminology and facts "
        "(prefer these over the subject index):\n"
        + lines + "\n"
    )


def _build_server_tools(model: str) -> list:
    """根據模型組裝 server-side tools。"""
    supports_dynamic = any(model.startswith(p) for p in MODELS_WITH_DYNAMIC_FILTERING)
    if supports_dynamic:
        web_search = {"type": "web_search_20260209", "name": "web_search"}
        web_fetch_type = "web_fetch_20260209"
    else:
        web_search = {
            "type": "web_search_20260209",
            "name": "web_search",
            "allowed_callers": ["direct"],
        }
        web_fetch_type = "web_fetch_20250910"
    return [
        web_search,
        {"type": web_fetch_type, "name": "web_fetch", "max_uses": 5, "allowed_domains": WEB_FETCH_ALLOWED_DOMAINS},
        {"type": "code_execution_20260120", "name": "code_execution"},
    ]

MAX_CONTINUATIONS = 10  # 安全上限（實際由 JSON 完整性判斷停止）
MAX_META_RETRIES = 2  # Haiku 純 meta 開新 session 重試次數上限（首次 response 沒 { 才算）

# Last-line defense: Opus 修補 broken JSON。出現條件：所有 inline parse + raw_decode +
# json_repair 都救不起來 accumulated_text。Opus 純文字推理修 JSON，harness 層驗證。
SYSTEM_PROMPT_REPAIR = (
    "You are a JSON repair tool for a teaching pipeline. The input was generated by another LLM "
    "that produced a JSON document containing teaching content (narration, slide_html, etc.) but "
    "introduced SYNTACTIC errors that break JSON parsing.\n\n"
    "Your job: produce a syntactically valid JSON that contains the SAME content as the input.\n\n"
    "APPROACH:\n"
    "- Reason through the repair in text. Identify the specific syntax errors, fix them, "
    "and emit the corrected JSON.\n"
    "- The calling system will validate your output with json.loads(); you do not need to verify it yourself.\n"
    "- Be fast and focused — this runs on a 30-minute platform timeout.\n\n"
    "OUTPUT FORMAT — strict, no exceptions:\n"
    "- Wrap your final corrected JSON between `<repaired_json>` and `</repaired_json>` tags.\n"
    "- The content between the tags MUST be a single valid JSON document, nothing else.\n"
    "- BEFORE the opening `<repaired_json>` tag you may briefly note what you changed (1-2 lines max), or nothing.\n"
    "- AFTER the closing `</repaired_json>` tag, output nothing.\n\n"
    "Common error patterns to actively look for and FIX:\n"
    "1. **Unescaped `\"` inside a JSON string** — any `\"` inside an HTML attribute, HTML text, or any "
    "string value must be escaped as `\\\"`. If you see `style=\\\"foo\"bar`, the `\"` after `foo` is "
    "unescaped and must become `\\\"`.\n"
    "2. **Token-prediction hiccups producing self-repeating prefixes**: e.g. `\\\"margin\"margin-bottom` "
    "is the model writing `margin` twice with an unescaped `\"` — collapse to `\\\"margin-bottom`.\n"
    "3. Unclosed strings, missing commas between objects, trailing garbage after the last `}`.\n"
    "4. Markdown code fences or commentary text outside the JSON — strip them.\n"
    "5. The last segment may be truncated mid-string. In that case, drop the truncated segment "
    "entirely and close the array properly.\n\n"
    "CRITICAL RULES:\n"
    "- **Preserve content verbatim where the input is intact**. Do NOT paraphrase, summarize, or "
    "\"improve\" narration or slide_html.\n"
    "- **If the input seems to parse cleanly, look harder — the pipeline only invokes you when "
    "parsing fails.**\n"
    "- Do not invent new segments. Do not add or remove fields beyond what's needed to fix syntax."
)

REPAIR_TOOLS: list = []  # no server-side tools; harness validates JSON after repair


def _deepcopy_api_kwargs(api_kwargs: dict) -> dict:
    """複製 api_kwargs 給 fresh-session retry 用，client 不可 deepcopy 因此外面包一層。"""
    return copy.deepcopy(api_kwargs)


class ClaudeLLMService(BaseLLMService):
    def __init__(self, config: Settings):
        self.client = anthropic.Anthropic(
            api_key=config.anthropic_api_key.get_secret_value(),
            timeout=600.0,  # 10 分鐘 timeout，避免 SDK 強制要求 streaming
        )
        self.model = config.llm_model
        self.outline_model = config.outline_model
        self.review_model = config.review_model
        self.fact_check_model = config.fact_check_model
        self.fact_check_temperature = config.fact_check_temperature
        self.base_url = config.base_url
        self.expand_backend = config.expand_backend
        self.expand_model = config.expand_model
        self.openai_api_key = config.openai_api_key.get_secret_value()
        self.outline_template = (PROMPTS_DIR / "outline_generator.txt").read_text(encoding="utf-8")
        self.expand_template = (PROMPTS_DIR / "script_expander.txt").read_text(encoding="utf-8")
        self.review_template = (PROMPTS_DIR / "script_reviewer.txt").read_text(encoding="utf-8")
        self.improve_template = (PROMPTS_DIR / "script_improver.txt").read_text(encoding="utf-8")

        # 根據模型組裝 tools（Haiku 限制 web_search direct 模式）
        self.tools = _build_server_tools(self.model)

        # 課程知識庫：掃描 config/ap/ 和 config/ib/ 目錄，建立可用檔案清單
        self._curriculum_knowledge_hint = self._build_curriculum_knowledge_hint()

    def _build_curriculum_knowledge_hint(self) -> str:
        """生成課程知識庫提示：列各科線上 index URL，讓 Claude 自行查找具體 unit。

        URL 指向 GitHub Pages KB（公開靜態網站），不綁本地 curriculum——
        本地無 curriculum 時也能用學科 KB。
        """
        subjects = {
            "biology": "Biology",
            "physics": "Physics",
            "cs": "Computer Science (AP CS A + AP CSP + IB CS)",
            "math": "Mathematics (Calculus + Statistics + IB Math AA)",
        }

        # GitHub Pages 為主要來源（公開靜態網站，不受 GCE pipeline 影響）
        gh_pages_base = "https://tsun-u.github.io/tsunumon-kb"
        index_lines = [
            f"- **{subject_name}**: {gh_pages_base}/{subdir}/index.md"
            for subdir, subject_name in subjects.items()
        ]

        return (
            "\n## Curriculum Knowledge Base\n"
            "We have AP/IB curriculum reference files organized by subject. To fact-check your content:\n"
            "1. **Identify the subject** (Biology, Physics, CS, or Math) from the topic.\n"
            "2. **Fetch the subject index** from the list below — it contains all units/topics with file URLs.\n"
            "3. **Fetch the specific unit file** that matches the teaching topic.\n\n"
            "Subject indexes:\n"
            + "\n".join(index_lines) + "\n\n"
            "Always fetch the relevant index first, then the specific unit file. "
            "Prioritize the curriculum knowledge base over web search for curriculum-aligned content."
        )

    # ------------------------------------------------------------------ #
    #  共用：呼叫 Claude API（含 pause_turn 續傳）
    # ------------------------------------------------------------------ #

    def _call_claude(
        self,
        system: str,
        user_prompt: str,
        request_id: str,
        step_name: str,
        max_tokens: int = 64000,
        model_override: str | None = None,
        force_tools: bool | list | None = None,
        temperature: float | None = None,
    ) -> dict:
        """呼叫 Claude API 並回傳解析後的 JSON dict。

        支援 pause_turn / max_tokens 自動續傳，並合併所有 text block。
        model_override: 指定不同模型（例如 review 用 Sonnet）
        force_tools: None=自動判斷, True=全部tools, False=無tools, list=自訂tools
        """
        model = model_override or self.model

        logger.info(f"[{request_id}] {step_name}: Calling Claude ({model})...")

        messages = [{"role": "user", "content": user_prompt}]

        api_kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        # Adaptive thinking：僅 Opus 啟用（Sonnet + adaptive 會導致 API timeout）
        if model.startswith("claude-opus-4-7"):
            api_kwargs["thinking"] = {"type": "adaptive"}
            api_kwargs["output_config"] = {"effort": "medium"}
        elif model.startswith("claude-opus-4-6"):
            api_kwargs["thinking"] = {"type": "adaptive"}
            api_kwargs["output_config"] = {"effort": "medium"}
        else:
            if temperature is not None:
                api_kwargs["temperature"] = temperature

        # 決定使用哪些 tools
        if isinstance(force_tools, list):
            api_kwargs["tools"] = force_tools
            tool_names = [t.get("name", t.get("type", "?")) for t in force_tools]
            logger.info(f"[{request_id}] {step_name}: Using tools: {tool_names}")
        elif force_tools is False:
            pass  # 不帶任何 tools
        elif force_tools is True:
            api_kwargs["tools"] = _build_server_tools(model)
            logger.info(f"[{request_id}] {step_name}: Using full tools for {model}")
        else:
            # None = 自動：用 self.tools（根據模型自動選擇）
            api_kwargs["tools"] = self.tools
            tool_names = [t.get("name", t.get("type", "?")) for t in self.tools]
            logger.info(f"[{request_id}] {step_name}: Using tools: {tool_names}")

        # 累積所有 text（跨多次續傳）
        accumulated_text = ""
        response = None
        _small_output_streak = 0  # 連續小 output 計數（迷路偵測）
        # 保留初始 api_kwargs 給 meta-retry 用：純 meta 失敗時 fresh session 要從頭重 call。
        _original_api_kwargs = _deepcopy_api_kwargs(api_kwargs)

        for i in range(MAX_CONTINUATIONS + 1):
            response = self.client.messages.create(**api_kwargs)

            logger.info(
                f"[{request_id}] {step_name} #{i}: "
                f"stop_reason={response.stop_reason}, "
                f"blocks={len(response.content)}, "
                f"usage=in:{response.usage.input_tokens}/out:{response.usage.output_tokens}"
            )

            # 收集這次回應的 text
            for block in response.content:
                if block.type == "text":
                    accumulated_text += block.text
            # log web_fetch results（成功記 url、失敗記 url + error_code）
            _log_web_fetch_results(response.content, request_id, step_name)

            # Haiku 有時會在 JSON 前寫 meta preamble（"I'll create...", "Let me start..."），
            # 試圖用 prompt 規範會讓 prompt 變長反而讓 Haiku 更不穩。直接用程式 trim：
            # 找 first {、把前面的 preamble 丟掉、只保 JSON 區段繼續累積。
            #
            # 如果 first response 純 meta（沒有 {），不要把 meta 當 context 餵回 Haiku
            # （會讓它反覆 meta），改成清空後重 call 一次 fresh session。每題本來就在賭
            # Haiku，meta 失敗應該再賭一次，而不是立刻升級 Sonnet。
            if accumulated_text:
                first_brace = accumulated_text.find("{")
                if first_brace > 0:
                    preamble_len = first_brace
                    while preamble_len > 0 and accumulated_text[preamble_len - 1].isspace():
                        preamble_len -= 1
                    if preamble_len > 0:
                        logger.info(
                            f"[{request_id}] {step_name}: trimmed {preamble_len} chars "
                            f"of preamble before first {{"
                        )
                    accumulated_text = accumulated_text[first_brace:]
                elif first_brace == -1 and i == 0 and accumulated_text.strip():
                    fresh_retries = 0
                    while fresh_retries < MAX_META_RETRIES:
                        fresh_retries += 1
                        logger.warning(
                            f"[{request_id}] {step_name}: pure meta in first response "
                            f"({len(accumulated_text)} chars), retrying with fresh session "
                            f"(meta_retry {fresh_retries}/{MAX_META_RETRIES})"
                        )
                        accumulated_text = ""
                        api_kwargs = _deepcopy_api_kwargs(_original_api_kwargs)
                        response = self.client.messages.create(**api_kwargs)
                        logger.info(
                            f"[{request_id}] {step_name} #0(meta-retry-{fresh_retries}): "
                            f"stop_reason={response.stop_reason}, "
                            f"blocks={len(response.content)}, "
                            f"usage=in:{response.usage.input_tokens}/out:{response.usage.output_tokens}"
                        )
                        for block in response.content:
                            if block.type == "text":
                                accumulated_text += block.text
                        _log_web_fetch_results(response.content, request_id, step_name)
                        new_first_brace = accumulated_text.find("{")
                        if new_first_brace >= 0:
                            if new_first_brace > 0:
                                accumulated_text = accumulated_text[new_first_brace:]
                            break
                    else:
                        raise ValueError(
                            f"Pure meta preamble after {MAX_META_RETRIES} fresh retries; "
                            f"last 200 chars: {accumulated_text[:200]!r}"
                        )

            # 早期偵測：如果已有大量文字但不像我們的 JSON schema，提前中斷
            # （Sonnet web_fetch 失敗時會輸出自然語言而非 JSON）
            # 注意：使用 server-side tools 時，text 可能混入 tool 說明文字，不觸發偵測
            has_tools = "tools" in api_kwargs
            stripped = accumulated_text.strip()
            if not has_tools and stripped and len(stripped) > 500:
                first_brace = stripped.find("{")
                is_our_json = False
                if first_brace != -1:
                    # 找到 { 後，看接下來 200 chars 有沒有我們認識的欄位
                    after_brace = stripped[first_brace:first_brace + 200]
                    known_fields = ('"title"', '"course_topic"', '"segments"',
                                    '"scaffolding_strategy"', '"segment_id"',
                                    '"corrections_made"')
                    is_our_json = any(field in after_brace for field in known_fields)
                if not is_our_json:
                    logger.error(
                        f"[{request_id}] {step_name}: detected non-JSON output "
                        f"({len(stripped)} chars, no known JSON fields found, "
                        f"starts with: {stripped[:80]!r}) — aborting early"
                    )
                    raise ValueError(
                        f"No valid JSON found in Claude response. "
                        f"Text length: {len(stripped)}, first 200 chars: {stripped[:200]}"
                    )

            if response.stop_reason == "end_turn":
                # 檢查 JSON 是否完整：Haiku 有時會提前 end_turn 但 JSON 沒寫完
                if accumulated_text.strip() and not self._is_json_complete(accumulated_text):
                    # end_turn = 模型自認輸出完成。_is_json_complete 只做輕量 raw_decode，
                    # 對混入 tool artifacts（web_fetch 續傳）/ 未轉義控制字元的「其實
                    # 完整」JSON 會誤判 incomplete。過去只在 >80K 才用權威 parser 複驗，
                    # 77K 的完整 JSON 剛好漏接 → 強制 continuation，但模型無從補起
                    # （連續空輸出 → lost direction 中止，浪費 ~12s + 2 次空 API call）。
                    # 解法：不論長度先用 _extract_json_from_text（含完整清理 + 多重
                    # fallback、純解析無 API 成本）拍板——解得出來就視為完整、break。
                    try:
                        self._extract_json_from_text(accumulated_text)
                        logger.info(
                            f"[{request_id}] {step_name}: end_turn, _is_json_complete=False "
                            f"but robust parser recovered valid JSON "
                            f"({len(accumulated_text)} chars) — treating as complete, "
                            f"no continuation"
                        )
                        break
                    except (ValueError, json.JSONDecodeError):
                        # 權威 parser 也解不出 → 可能真截斷。安全閥：>80K 仍解不出就
                        # abort 避免無限續傳；否則當截斷走續傳補完。
                        if len(accumulated_text) > 80_000:
                            logger.error(
                                f"[{request_id}] {step_name}: accumulated "
                                f"{len(accumulated_text)} chars but still not valid JSON "
                                f"— aborting continuation"
                            )
                            break
                        logger.warning(
                            f"[{request_id}] {step_name}: end_turn but JSON incomplete "
                            f"({len(accumulated_text)} chars), forcing continuation..."
                        )
                        # 當作截斷處理，走續傳邏輯
                else:
                    break

            if response.stop_reason in ("pause_turn", "max_tokens", "end_turn"):
                # 迷路偵測：連續 2 次 output < 500 tokens → 模型迷失方向
                if hasattr(response, 'usage') and response.usage.output_tokens < 500:
                    _small_output_streak += 1
                    logger.warning(
                        f"[{request_id}] {step_name}: continuation #{i} produced only "
                        f"{response.usage.output_tokens} tokens (streak={_small_output_streak})"
                    )
                    if _small_output_streak >= 2:
                        logger.error(
                            f"[{request_id}] {step_name}: model lost direction "
                            f"(2 consecutive small outputs), aborting continuation"
                        )
                        break
                else:
                    _small_output_streak = 0

                # 續傳：把已生成文字放進 user message，請 Claude 接續
                # server-side tools 模式不支援 assistant prefill，
                # 所以改用 user message 傳遞上下文，並關掉 tools（research 已完成）
                # Haiku/Sonnet 都有 200K context window，直接送全文讓模型看到完整 JSON 結構
                context_note = (
                    f"You were generating a JSON response but got cut off after "
                    f"{len(accumulated_text)} chars. Here is what you have generated so far "
                    f"(DO NOT repeat any of it):\n\n"
                    f"{accumulated_text}\n\n"
                )
                api_kwargs["messages"] = [
                    {"role": "user", "content": (
                        f"{user_prompt}\n\n"
                        f"---\n"
                        f"{context_note}"
                        f"Output ONLY the remaining part that comes after the text above. "
                        f"I will append your output directly to the existing text. "
                        f"Do NOT repeat any content already shown above."
                    )},
                ]
                # 關掉 tools：research 階段已完成，續傳只需要完成 JSON 輸出
                api_kwargs.pop("tools", None)
                logger.info(
                    f"[{request_id}] {step_name}: {response.stop_reason}, "
                    f"continuing without tools ({i+1}/{MAX_CONTINUATIONS})..."
                )
                continue

            logger.warning(
                f"[{request_id}] {step_name}: Unexpected stop_reason: {response.stop_reason}"
            )
            break

        if response is None:
            raise RuntimeError(f"No response from Claude API ({step_name})")

        # 先試 inline 多重 fallback parser（raw_decode + json_repair 等）
        try:
            return self._extract_json_from_text(accumulated_text)
        except (ValueError, json.JSONDecodeError) as parse_err:
            # Last-line defense: Opus + code_execution 修補 broken JSON、保留內容。
            # 失敗才 raise 原 error 進入呼叫方的 from-scratch fallback (e.g. Sonnet retry)。
            try:
                repaired_text = self._repair_json_with_opus(
                    request_id=request_id,
                    step_name=step_name,
                    broken_text=accumulated_text,
                )
                return self._extract_json_from_text(repaired_text)
            except (ValueError, json.JSONDecodeError, RuntimeError, anthropic.AnthropicError) as repair_err:
                logger.error(
                    f"[{request_id}] {step_name}: Opus repair also failed ({repair_err}), "
                    f"raising original parse error"
                )
                raise parse_err

    # ------------------------------------------------------------------ #
    #  Step 1: Outline
    # ------------------------------------------------------------------ #

    async def generate_outline(
        self,
        request: TeachingRequest,
        outline_model: str | None = None,
        focus_level: str = "normal",
    ) -> CourseOutline:
        user_prompt = (
            self.outline_template
            .replace("{course_requirement}", request.course_requirement)
            .replace("{student_persona}", request.student_persona)
            .replace("{target_duration_min}", "15")
            .replace("{low_focus_rule}", _low_focus_block(focus_level))
        )
        # 附加 AP 知識庫提示（如果有的話）
        if self._curriculum_knowledge_hint:
            user_prompt += "\n" + self._curriculum_knowledge_hint

        data = self._call_claude(
            system=SYSTEM_PROMPT_OUTLINE,
            user_prompt=user_prompt,
            request_id=request.request_id,
            step_name="Outline",
            model_override=outline_model or self.outline_model,
        )

        outline = self._parse_outline(data)
        logger.info(
            f"[{request.request_id}] Outline: "
            f"topic={outline.course_topic}, "
            f"{len(outline.sequences)} sequences"
        )
        return outline

    # ------------------------------------------------------------------ #
    #  Step 2: Expand (supports Claude Haiku or OpenAI GPT)
    # ------------------------------------------------------------------ #

    def _call_openai_expand(
        self, system: str, user_prompt: str, request_id: str, max_tokens: int = 21000
    ) -> dict:
        """Call OpenAI API for expand step (GPT-5.4-mini etc.)."""
        import httpx
        import json as _json

        logger.info(
            f"[{request_id}] Expand: Calling OpenAI ({self.expand_model})..."
        )
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.expand_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                "max_completion_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=600.0,
        )
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        usage = result.get("usage", {})
        logger.info(
            f"[{request_id}] Expand #0: "
            f"model={self.expand_model}, "
            f"usage=in:{usage.get('prompt_tokens', '?')}/out:{usage.get('completion_tokens', '?')}"
        )
        # Strip markdown fences if present
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        # Parse JSON
        try:
            return _json.loads(text)
        except _json.JSONDecodeError:
            import json_repair
            return _json.loads(json_repair.repair_json(text))

    async def expand_outline(
        self,
        request: TeachingRequest,
        outline: CourseOutline,
        focus_level: str = "normal",
        kb_unit_urls: Optional[List[str]] = None,
    ) -> TeachingScript:
        outline_json = outline.model_dump_json(indent=2)
        user_prompt = (
            self.expand_template
            .replace("{outline_json}", outline_json)
            .replace("{target_duration_min}", "15")
            .replace("{low_focus_rule}", _low_focus_block(focus_level))
        )
        if self._curriculum_knowledge_hint:
            user_prompt += "\n" + self._curriculum_knowledge_hint
        user_prompt += _build_unit_url_hint(kb_unit_urls)
        user_prompt += "\n" + _build_tool_kb_hint(request.course_requirement, subject_override=outline.subject)

        if self.expand_backend == "openai" and self.openai_api_key:
            # OpenAI GPT expand path
            try:
                data = self._call_openai_expand(
                    system=SYSTEM_PROMPT_EXPAND,
                    user_prompt=user_prompt,
                    request_id=request.request_id,
                )
            except Exception as e:
                logger.warning(
                    f"[{request.request_id}] Expand: OpenAI failed ({e}), "
                    f"falling back to Claude Haiku..."
                )
                data = self._call_claude(
                    system=SYSTEM_PROMPT_EXPAND,
                    user_prompt=user_prompt,
                    request_id=request.request_id,
                    step_name="Expand-Fallback",
                    max_tokens=64000,
                    force_tools=False,
                )
        else:
            # Claude Haiku expand path (default)
            # 2026-04-20: Expand 階段需要帶 tools 讓 LLM 能 fetch curriculum KB。
            # 一開始改成 force_tools=True（全部 tools）會讓 Haiku 在抽象主題上
            # 過度 brainstorm（code_execution + web_search 亂跑，75 blocks / 1M input tokens），
            # 所以改成只給 web_fetch（max 5 次）—— 這正好對應 KB hint 要求的「fetch index
            # → fetch unit file」兩三次呼叫，足夠且不會失控。
            # 只給 web_fetch（避免 code_execution/web_search 亂跑），max_uses=5 容錯
            _expand_tools = [
                {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 5, "allowed_domains": WEB_FETCH_ALLOWED_DOMAINS},
            ]
            try:
                data = self._call_claude(
                    system=SYSTEM_PROMPT_EXPAND,
                    user_prompt=user_prompt,
                    request_id=request.request_id,
                    step_name="Expand",
                    max_tokens=64000,
                    force_tools=_expand_tools,
                )
            except (ValueError, RuntimeError) as e:
                logger.warning(
                    f"[{request.request_id}] Expand: Haiku failed ({e}), "
                    f"retrying with Sonnet ({self.review_model})..."
                )
                data = self._call_claude(
                    system=SYSTEM_PROMPT_EXPAND,
                    user_prompt=user_prompt,
                    request_id=request.request_id,
                    step_name="Expand-Sonnet",
                    max_tokens=64000,
                    force_tools=_expand_tools,
                    model_override=self.review_model,
                )

        script = self._parse_script(request.request_id, data)
        script.outline = outline
        logger.info(
            f"[{request.request_id}] Script: "
            f"title={script.title}, "
            f"{len(script.segments)} segments"
        )
        return script

    # ------------------------------------------------------------------ #
    #  Step 2b: Review（Sonnet 審修：事實查核 + 旁白品質）
    # ------------------------------------------------------------------ #

    async def review_script(self, request: TeachingRequest, script: TeachingScript) -> TeachingScript:
        """用 review_model (Sonnet) + web_search + code_execution 審修腳本。"""
        # 把 script 轉成 JSON 給 reviewer
        script_data = {
            "title": script.title,
            "segments": [
                {
                    "segment_id": seg.segment_id,
                    "sequence_id": seg.sequence_id,
                    "slide_title": seg.slide_title,
                    "slide_html": seg.slide_html,
                    "narration_text": seg.narration_text,
                    "estimated_duration_sec": seg.estimated_duration_sec,
                    "teaching_phase": seg.teaching_phase,
                }
                for seg in script.segments
            ],
        }
        import json
        script_json = json.dumps(script_data, indent=2, ensure_ascii=False)

        user_prompt = self.review_template.replace("{script_json}", script_json)

        data = self._call_claude(
            system=(
                "You are an expert educational content reviewer and fact-checker.\n\n"
                "Guidelines:\n"
                "- Use web search to verify ALL factual claims — names, dates, formulas, definitions.\n"
                "- Use code execution to verify ALL calculations and numerical examples.\n"
                "- Your final response MUST be a valid JSON object (no markdown fences, no extra text).\n"
                "- Preserve the exact same segment structure. Only fix content errors and improve narration."
            ),
            user_prompt=user_prompt,
            request_id=request.request_id,
            step_name="Review",
            max_tokens=64000,
            model_override=self.review_model,
            force_tools=True,  # review 用全部 tools
        )

        changes = data.get("changes_summary", "no summary")
        logger.info(f"[{request.request_id}] Review changes: {changes}")

        # 用審修結果更新 script 的每個 segment
        reviewed = self._parse_script(request.request_id, data)
        reviewed.outline = script.outline
        reviewed.scaffolding_strategy = script.scaffolding_strategy
        reviewed.target_duration_min = script.target_duration_min

        logger.info(
            f"[{request.request_id}] Reviewed script: "
            f"{len(reviewed.segments)} segments"
        )
        return reviewed

    # ------------------------------------------------------------------ #
    #  Step 3: Improve（進化版教師：基於舊教案改良）
    # ------------------------------------------------------------------ #

    async def improve_script(
        self,
        request: TeachingRequest,
        previous_script: TeachingScript,
        consultant_notes: dict | None = None,
        focus_level: str = "normal",
        subject: str = None,
        kb_unit_urls: Optional[List[str]] = None,
    ) -> TeachingScript:
        """基於舊教案，讓 Sonnet 改良教學品質（不重新查事實）。

        consultant_notes: optional {"gpt": "...markdown...", "gemini": "...markdown..."}
        from the Consultant step. If provided, injected into the prompt as advisory
        notes (Sonnet has full professional judgment to adopt/adapt/ignore).
        """
        # 把舊 script 轉成 JSON
        script_data = {
            "title": previous_script.title,
            "scaffolding_strategy": previous_script.scaffolding_strategy,
            "target_duration_min": previous_script.target_duration_min,
            "segments": [
                {
                    "segment_id": seg.segment_id,
                    "sequence_id": seg.sequence_id,
                    "slide_title": seg.slide_title,
                    "slide_html": seg.slide_html,
                    "narration_text": seg.narration_text,
                    "estimated_duration_sec": seg.estimated_duration_sec,
                    "teaching_phase": seg.teaching_phase,
                }
                for seg in previous_script.segments
            ],
        }
        previous_json = json.dumps(script_data, indent=2, ensure_ascii=False)

        # Consultant notes — 如果 pipeline 沒跑 consultant 或兩邊都失敗，放 no-op
        gpt_notes_md = (consultant_notes or {}).get("gpt") or "_(no notes)_"
        gemini_notes_md = (consultant_notes or {}).get("gemini") or "_(no notes)_"

        # 注意：previous_json 含有大量 { } 花括號，不能直接用 .format()
        # 先把 template 中的 placeholder 替換，避免 JSON 內容被誤解為 format spec
        user_prompt = (
            self.improve_template
            .replace("{previous_script_json}", previous_json)
            .replace("{course_requirement}", request.course_requirement)
            .replace("{student_persona}", request.student_persona)
            .replace("{gpt_consultant_notes}", gpt_notes_md)
            .replace("{gemini_consultant_notes}", gemini_notes_md)
            .replace("{target_duration_min}", str(previous_script.target_duration_min))
            .replace("{low_focus_rule}", _low_focus_block(focus_level))
        )

        # 注入 AP 知識庫提示，讓 Sonnet 用 web_fetch 比對術語正確性
        if self._curriculum_knowledge_hint:
            user_prompt += "\n" + self._curriculum_knowledge_hint

        # 注入 SubjectClassifier 選的相關 unit URL（明列 → 進 web_fetch prior_context）
        user_prompt += _build_unit_url_hint(kb_unit_urls)

        # 注入工具 KB routing（只揭露科目相關的工具 KB URL）
        tool_kb_hint = _build_tool_kb_hint(request.course_requirement, subject_override=subject)
        user_prompt += "\n" + tool_kb_hint

        data = self._call_claude(
            system=(
                "You are a senior teacher with 20+ years of experience reviewing an intern's lesson script.\n\n"
                "Guidelines:\n"
                "- Use web_fetch to retrieve the relevant AP knowledge base file(s) and cross-check "
                "ALL terminology, formulas, and definitions. Interns make mistakes — find and fix them.\n"
                "- Use code_execution to VERIFY any numerical calculations, worked examples, or formula applications "
                "in the script. Run the actual math to confirm the numbers are correct. Do NOT trust mental math.\n"
                "- Do NOT use web_search — the AP knowledge base is your sole authoritative reference.\n"
                "- **If the knowledge base is unreachable** (permission error, timeout, or any fetch failure), "
                "DO NOT describe the error in natural language. Instead, trust the intern's draft — "
                "it was already fact-checked with web_search during the outline stage. "
                "Skip fact-checking and focus your improvements on teaching quality: engagement, "
                "adaptability, visual clarity, and narration.\n"
                "- After fact-checking (or skipping it if KB is unavailable), improve engagement, "
                "adaptability, visual clarity, and narration quality.\n"
                "- Your final response MUST be a valid JSON object (no markdown fences, no extra text). "
                "NEVER output natural language — always output JSON.\n"
                "- Keep the same segment structure."
            ),
            user_prompt=user_prompt,
            request_id=request.request_id,
            step_name="Improve",
            max_tokens=64000,
            model_override=self.review_model,  # 資深教師 = Sonnet
            temperature=0.9,
            # web_fetch: 比對 AP 知識庫（驗算交給 fact-check 階段的 code_execution）
            force_tools=[
                {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 10, "allowed_domains": WEB_FETCH_ALLOWED_DOMAINS},
            ],
        )

        improvements = data.get("improvements_summary", "no summary")
        logger.info(f"[{request.request_id}] Improvements: {improvements}")

        improved = self._parse_script(request.request_id, data)
        # 保留舊的 outline 和 metadata
        improved.outline = previous_script.outline
        improved.scaffolding_strategy = data.get(
            "scaffolding_strategy", previous_script.scaffolding_strategy
        )
        improved.target_duration_min = data.get(
            "target_duration_min", previous_script.target_duration_min
        )

        logger.info(
            f"[{request.request_id}] Improved script: "
            f"{len(improved.segments)} segments"
        )

        # Segment 完整性 guard：JSON repair 可能靠「截肢壞 segment」讓輸出合法
        # （122 實證：_is_json_complete=False → robust parser 救回、但 segment 14
        # 連同 misconception 單元整段消失、敘事斷裂 + 頁碼跳號）。
        improved = self._repair_segment_holes(request, improved, previous_script)
        return improved

    def _repair_segment_holes(
        self,
        request: TeachingRequest,
        improved: TeachingScript,
        previous_script: TeachingScript,
    ) -> TeachingScript:
        """偵測 Improve 輸出的 segment_id 洞並定向補洞。

        洞 = ids 非連續（如 1-20 缺 14），多半是 json_repair 截肢壞 segment 的
        結果。補法：一次小型 call，給模型「洞號 + 鄰居完整內容 + 原稿全段
        標題/旁白」，只生成缺失段再拼回。失敗則響亮警告、保持現狀（人工
        決定是否重跑），不阻塞 pipeline。"""
        rid = request.request_id
        ids = sorted(s.segment_id for s in improved.segments)
        if not ids:
            return improved
        id_set = set(ids)
        holes = [i for i in range(ids[0], ids[-1]) if i not in id_set]
        if not holes:
            return improved
        logger.warning(
            f"[{rid}] Improve: SEGMENT INTEGRITY — id holes {holes} in output "
            f"({len(ids)} segments, range {ids[0]}-{ids[-1]}); "
            f"repair amputation suspected, attempting targeted regeneration"
        )
        try:
            by_id = {s.segment_id: s for s in improved.segments}
            # 鄰居（完整內容，當風格與銜接基準）
            neighbor_ids = sorted({n for h in holes for n in (h - 1, h + 1) if n in by_id})
            neighbors = [
                {
                    "segment_id": by_id[n].segment_id,
                    "slide_title": by_id[n].slide_title,
                    "slide_html": by_id[n].slide_html,
                    "narration_text": by_id[n].narration_text,
                    "teaching_phase": by_id[n].teaching_phase,
                }
                for n in neighbor_ids
            ]
            # 原稿全段（標題+旁白，找回被丟的教學內容用）
            draft_outline = [
                {
                    "segment_id": s.segment_id,
                    "slide_title": s.slide_title,
                    "narration_text": s.narration_text,
                    "teaching_phase": s.teaching_phase,
                }
                for s in previous_script.segments
            ]
            repair_prompt = (
                f"An improved lesson script is missing segment_id(s) {holes} — the JSON "
                f"output had these segments amputated during repair. Reconstruct the "
                f"missing segment(s).\n\n"
                f"## Adjacent improved segments (match their style, quality, and flow)\n"
                f"{json.dumps(neighbors, ensure_ascii=False, indent=2)}\n\n"
                f"## Original draft segments (find the teaching content that is missing "
                f"from the improved flow — typically the draft unit not represented by "
                f"any improved segment)\n"
                f"{json.dumps(draft_outline, ensure_ascii=False, indent=2)}\n\n"
                f"## Requirements\n"
                f"- Output ONLY a JSON object: {{\"segments\": [ ... ]}} containing exactly "
                f"the missing segment(s) with segment_id(s) {holes}.\n"
                f"- Each segment needs: segment_id, sequence_id (fit between neighbors), "
                f"slide_title, slide_html (same design system as the adjacent segments), "
                f"narration_text (TTS-safe spoken form, flows naturally from the previous "
                f"segment into the next), estimated_duration_sec, teaching_phase.\n"
                f"- slide_html: proper visual notation (AI not A-I); narration: spoken form.\n"
                f"- No markdown fences, no extra text."
            )
            data = self._call_claude(
                system=(
                    "You are a senior teacher reconstructing a missing lesson segment. "
                    "Return ONLY valid JSON."
                ),
                user_prompt=repair_prompt,
                request_id=rid,
                step_name="Improve-hole-repair",
                max_tokens=16000,
                model_override=self.review_model,
                temperature=0.7,
            )
            added = 0
            for seg_data in data.get("segments", []):
                sid = seg_data.get("segment_id")
                if sid not in holes or sid in id_set:
                    continue
                try:
                    seg = ScriptSegment(
                        segment_id=sid,
                        sequence_id=seg_data.get("sequence_id", sid),
                        slide_title=seg_data.get("slide_title", ""),
                        slide_html=seg_data.get("slide_html", ""),
                        narration_text=seg_data.get("narration_text", ""),
                        estimated_duration_sec=seg_data.get("estimated_duration_sec", 45),
                        teaching_phase=seg_data.get("teaching_phase", "core"),
                        animated=bool(seg_data.get("animated", False)),
                    )
                except Exception as seg_err:
                    logger.warning(f"[{rid}] hole-repair segment {sid} invalid: {seg_err}")
                    continue
                if seg.slide_html and seg.narration_text:
                    improved.segments.append(seg)
                    id_set.add(sid)
                    added += 1
            improved.segments.sort(key=lambda s: s.segment_id)
            remaining = [h for h in holes if h not in id_set]
            if remaining:
                logger.warning(
                    f"[{rid}] Improve: hole repair incomplete — still missing {remaining}, "
                    f"RERUN review advised"
                )
            else:
                logger.info(
                    f"[{rid}] Improve: hole repair OK — regenerated {added} segment(s) "
                    f"{holes}, total {len(improved.segments)} segments"
                )
        except Exception as e:
            logger.warning(
                f"[{rid}] Improve: hole repair failed ({type(e).__name__}: {e}) — "
                f"proceeding with missing segments {holes}, RERUN review advised"
            )
        return improved

    # ------------------------------------------------------------------ #
    #  Step 4: Fact-Check（獨立事實審核，不改風格）
    # ------------------------------------------------------------------ #

    async def fact_check_script(self, request: TeachingRequest, script: TeachingScript) -> TeachingScript:
        """獨立事實審核：只修正事實錯誤，不改風格。Temperature 低，重穩定性。"""
        # 讀取 prompt 模板
        prompt_path = PROMPTS_DIR / "fact_checker.txt"
        prompt_template = prompt_path.read_text(encoding="utf-8")

        # 準備 KB URLs
        kb_urls = self._curriculum_knowledge_hint or "No curriculum knowledge base available."

        # 準備 script JSON（只傳 segments，不傳整個 script）
        segments_data = [
            {
                "segment_id": s.segment_id,
                "sequence_id": s.sequence_id,
                "slide_title": s.slide_title,
                "slide_html": s.slide_html,
                "narration_text": s.narration_text,
                "estimated_duration_sec": s.estimated_duration_sec,
                "teaching_phase": s.teaching_phase,
                "animated": s.animated,
            }
            for s in script.segments
        ]
        script_json = json.dumps(segments_data, ensure_ascii=False, indent=2)

        prompt = prompt_template.replace("{kb_urls}", kb_urls).replace("{script_json}", script_json)

        data = self._call_claude(
            system="You are a fact-checker. Return ONLY valid JSON. No markdown fences.",
            user_prompt=prompt,
            request_id=request.request_id,
            step_name="Fact-check",
            max_tokens=64000,
            model_override=self.fact_check_model,
            temperature=self.fact_check_temperature,
            force_tools=[
                {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 10, "allowed_domains": WEB_FETCH_ALLOWED_DOMAINS},
                {"type": "code_execution_20260120", "name": "code_execution"},
            ],
        )

        corrections = data.get("corrections_made", "unknown")
        logger.info(f"[{request.request_id}] Fact-check corrections: {corrections}")

        checked = self._parse_script(request.request_id, data)
        # 保留 metadata
        checked.outline = script.outline
        checked.scaffolding_strategy = script.scaffolding_strategy
        checked.target_duration_min = script.target_duration_min

        logger.info(
            f"[{request.request_id}] Fact-checked script: "
            f"{len(checked.segments)} segments"
        )
        return checked

    # ------------------------------------------------------------------ #
    #  JSON 提取 + 解析
    # ------------------------------------------------------------------ #

    @staticmethod
    def _clean_tool_artifacts(text: str) -> str:
        """清理續傳時混入的 tool XML 標記和 preamble。"""
        import re
        # 移除 <tool_call>...</tool_call> 和 <tool_response>...</tool_response> 區塊
        text = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL)
        text = re.sub(r"<tool_response>.*?</tool_response>", "", text, flags=re.DOTALL)
        # 移除未閉合的 <tool_call> 或 <tool_response>（續傳截斷）
        text = re.sub(r"<tool_call>.*", "", text, flags=re.DOTALL)
        text = re.sub(r"<tool_response>.*", "", text, flags=re.DOTALL)
        return text.strip()

    def _repair_json_with_opus(
        self,
        request_id: str,
        step_name: str,
        broken_text: str,
    ) -> str:
        """Last-line defense: 用 Opus + code_execution 修補 broken JSON、保留內容。

        回傳 raw response text（含 marker）、需要進一步走 ``_extract_json_from_text``
        + marker-aware 抓取邏輯才取得 dict。raise ``ValueError`` 或
        ``anthropic.AnthropicError`` if Opus 也無法修。
        """
        # 動態估算 max_tokens：output 大致跟 input 同長、留 20% repair overhead；
        # ~3 chars/token (HTML-heavy content) + 上限 cap 128K (Opus 4.6/4.7 max output)
        estimated_output_tokens = int((len(broken_text) / 3) * 1.2)
        max_tokens = max(8000, min(128_000, estimated_output_tokens))

        logger.info(
            f"[{request_id}] {step_name}: Opus repair attempt "
            f"(input {len(broken_text)} chars, max_tokens {max_tokens})..."
        )
        user_msg = (
            "Fix the following JSON. Iterate via text reasoning, then call code_execution AT MOST ONCE "
            "to verify your final candidate parses, then output ONLY the corrected JSON wrapped in "
            "<repaired_json>...</repaired_json> tags as your final reply.\n\n"
            + broken_text
        )
        t0 = time.time()
        try:
            response = self.client.messages.create(
                model=self.outline_model,  # claude-opus-4-6 (Opus 在 settings)
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT_REPAIR,
                tools=REPAIR_TOOLS,
                messages=[{"role": "user", "content": user_msg}],
            )
        except anthropic.AnthropicError as e:
            elapsed = time.time() - t0
            logger.error(
                f"[{request_id}] {step_name}: Opus repair API call failed "
                f"after {elapsed:.1f}s: {e}"
            )
            raise
        elapsed = time.time() - t0

        text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        repaired = "".join(text_blocks)
        tool_uses = [b for b in response.content if getattr(b, "type", None) == "server_tool_use"]
        logger.info(
            f"[{request_id}] {step_name}: Opus repair returned in {elapsed:.1f}s "
            f"(in:{response.usage.input_tokens}/out:{response.usage.output_tokens}, "
            f"stop={response.stop_reason}, code_exec={len(tool_uses)}, "
            f"text_len={len(repaired)})"
        )

        # 抓 marker 內容（首選）；marker 沒 match 時 raw 仍可能含 JSON、讓
        # _extract_json_from_text 走後續 fallback。
        marker_match = re.search(r"<repaired_json>(.*?)</repaired_json>", repaired, re.DOTALL)
        if marker_match:
            extracted = marker_match.group(1).strip()
            logger.info(
                f"[{request_id}] {step_name}: Opus repair marker matched "
                f"(extracted {len(extracted)} chars between tags)"
            )
            return extracted

        logger.warning(
            f"[{request_id}] {step_name}: Opus repair output had no marker, "
            f"returning raw text for downstream parsing"
        )
        return repaired

    @staticmethod
    def _is_json_complete(text: str) -> bool:
        """快速檢查 text 中是否有完整的 JSON object。

        用 ``json.JSONDecoder().raw_decode`` 從第一個 ``{`` 開始解；只要解出第一個
        完整 object 就算 complete，後面任何 extra data（重複輸出 / markdown 包裝
        / 額外註解）都忽略——這個 case 是 Sonnet continuation 把整 JSON 重新寫
        一遍造成的 false-negative。

        先對齊 ``_extract_json_from_text`` 的前處理 ``_clean_tool_artifacts``：
        web_fetch / 續傳會混入 ``<tool_call>``/``<tool_response>`` artifacts，殘留在
        raw text 上會讓 raw_decode 對「其實完整」的 JSON 誤判 incomplete。清理只
        移除 tool XML、不補括號，所以對真截斷的 JSON 仍會 False（不會 false positive）。
        """
        text = ClaudeLLMService._clean_tool_artifacts(text)
        first = text.find("{")
        if first == -1:
            return False
        try:
            json.JSONDecoder().raw_decode(text, first)
            return True
        except (json.JSONDecodeError, ValueError):
            return False

    def _extract_json_from_text(self, text: str) -> dict:
        """從累積的 text 中提取 JSON。嘗試多種格式 + 自動修復。"""
        text = self._clean_tool_artifacts(text)
        text = text.strip()
        if not text:
            raise ValueError("Empty text from Claude response")

        # 1. 直接解析整段 text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 1.5 raw_decode：處理 "JSON + extra data" 的情況
        # Sonnet continuation 偶爾會把整個 JSON 重複輸出兩次，造成第一個完整 JSON
        # 後面接 markdown 收尾、第二個 JSON 開頭等噪音。raw_decode 從第一個 ``{``
        # 開始解，只 return 第一個完整 JSON object，忽略後續 extra data。
        first_brace_for_raw = text.find("{")
        if first_brace_for_raw != -1:
            try:
                obj, _end = json.JSONDecoder().raw_decode(text, first_brace_for_raw)
                if isinstance(obj, dict):
                    logger.info(
                        f"JSON recovered via raw_decode "
                        f"(extracted {_end - first_brace_for_raw} chars from {len(text)} total)"
                    )
                    return obj
            except (ValueError, json.JSONDecodeError):
                pass

        # 2. 從 ```json ... ``` code block 提取
        if "```json" in text:
            try:
                start = text.index("```json") + 7
                end = text.index("```", start)
                return json.loads(text[start:end].strip())
            except (ValueError, json.JSONDecodeError):
                pass

        # 3. 從 ``` ... ``` code block 提取
        if "```" in text:
            try:
                start = text.index("```") + 3
                if start < len(text) and text[start] == "\n":
                    start += 1
                end = text.index("```", start)
                return json.loads(text[start:end].strip())
            except (ValueError, json.JSONDecodeError, IndexError):
                pass

        # 4. 找 { ... } 之間的內容
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            candidate = text[first_brace:last_brace + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"JSON parse failed at position {e.pos}: {e.msg}. "
                    f"Context: ...{candidate[max(0,e.pos-50):e.pos+50]}..."
                )

                # 5. 清理 JSON string 內的控制字元後重試
                import re
                # 在 JSON string value 內部，將裸換行/tab 替換為合法轉義
                cleaned = candidate.replace("\\\n", "\\n")  # 已轉義的保留
                cleaned = re.sub(
                    r'(?<=": ")(.*?)(?="[,\s*}])',
                    lambda m: m.group(0).replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"),
                    cleaned,
                    flags=re.DOTALL,
                )
                # 更簡單的方式：直接把 JSON string 裡的控制字元清掉
                def _clean_control_chars(s: str) -> str:
                    """移除 JSON string value 中的非法控制字元。"""
                    result = []
                    in_string = False
                    escape = False
                    for ch in s:
                        if escape:
                            result.append(ch)
                            escape = False
                            continue
                        if ch == '\\' and in_string:
                            result.append(ch)
                            escape = True
                            continue
                        if ch == '"':
                            in_string = not in_string
                        if in_string and ord(ch) < 32 and ch not in ('\n',):
                            # 跳過控制字元
                            continue
                        if in_string and ch == '\n':
                            result.append('\\n')
                            continue
                        result.append(ch)
                    return ''.join(result)

                try:
                    cleaned2 = _clean_control_chars(candidate)
                    return json.loads(cleaned2)
                except json.JSONDecodeError:
                    logger.warning("Control char cleanup also failed, trying json_repair...")

                # 6. 用 json_repair 嘗試自動修復
                try:
                    import json_repair
                    repaired = json_repair.loads(candidate)
                    if isinstance(repaired, dict):
                        logger.info("JSON auto-repaired by json_repair")
                        return repaired
                except Exception as repair_err:
                    logger.warning(f"json_repair also failed: {repair_err}")

        raise ValueError(
            f"No valid JSON found in Claude response. "
            f"Text length: {len(text)}, first 200 chars: {text[:200]}"
        )

    def _parse_outline(self, data: dict) -> CourseOutline:
        """將 JSON dict 轉為 CourseOutline domain model。"""
        persona_data = data.get("persona_analysis", {})
        persona = PersonaAnalysis(
            prior_knowledge=persona_data.get("prior_knowledge", ""),
            knowledge_gaps=persona_data.get("knowledge_gaps", ""),
            depth_decisions=persona_data.get("depth_decisions", ""),
        )

        sequences = []
        for seq_data in data.get("sequences", []):
            sequences.append(SequenceOutline(
                sequence_id=seq_data["sequence_id"],
                title=seq_data["title"],
                teaching_phase=seq_data.get("teaching_phase", "core"),
                objectives=seq_data.get("objectives", []),
                key_concepts=seq_data.get("key_concepts", []),
                visual_strategy=seq_data.get("visual_strategy", "bullets"),
                estimated_duration_min=seq_data.get("estimated_duration_min", 2.0),
            ))

        return CourseOutline(
            course_topic=data.get("course_topic", "Untitled"),
            target_audience=data.get("target_audience", ""),
            learning_objectives=data.get("learning_objectives", []),
            scaffolding_strategy=data.get("scaffolding_strategy", ""),
            persona_analysis=persona,
            sequences=sequences,
            references=data.get("references", []),
        )

    @staticmethod
    def _clean_html(html: str) -> str:
        """清除 LLM 偶爾加的 markdown fence。"""
        h = html.strip()
        if h.startswith("```html"):
            h = h[7:]
        elif h.startswith("```"):
            h = h[3:]
        if h.endswith("```"):
            h = h[:-3]
        return h.strip()

    def _parse_script(self, request_id: str, data: dict) -> TeachingScript:
        """將 JSON dict 轉為 TeachingScript domain model。"""
        segments = []
        raw_segments = data.get("segments", [])
        for idx, seg_data in enumerate(raw_segments):
            # 防禦：跳過非 dict 的 segment（json_repair 或截斷可能產生）
            if not isinstance(seg_data, dict):
                logger.warning(
                    f"[{request_id}] Segment #{idx} is not a dict "
                    f"(type={type(seg_data).__name__}, value={str(seg_data)[:100]}), skipping"
                )
                continue

            # 檢查必要欄位
            segment_id = seg_data.get("segment_id")
            slide_title = seg_data.get("slide_title")
            narration_text = seg_data.get("narration_text")

            if segment_id is None or narration_text is None:
                logger.warning(
                    f"[{request_id}] Segment #{idx} missing required fields "
                    f"(segment_id={segment_id}, has_title={slide_title is not None}, "
                    f"has_narration={narration_text is not None}), "
                    f"keys={list(seg_data.keys())[:10]}, skipping"
                )
                continue

            # 新格式: slide_html；舊格式 fallback: slide_bullets
            slide_html = seg_data.get("slide_html", "")
            if slide_html:
                slide_html = self._clean_html(slide_html)

            try:
                segments.append(ScriptSegment(
                    segment_id=int(segment_id),
                    sequence_id=seg_data.get("sequence_id", 0),
                    slide_title=slide_title or f"Slide {segment_id}",
                    slide_html=slide_html,
                    slide_bullets=seg_data.get("slide_bullets"),
                    slide_visual_hint=seg_data.get("slide_visual_hint"),
                    narration_text=narration_text,
                    estimated_duration_sec=seg_data.get("estimated_duration_sec", 60.0),
                    teaching_phase=seg_data.get("teaching_phase", "core"),
                    animated=seg_data.get("animated", False),
                    cover_image_prompt=seg_data.get("cover_image_prompt"),
                ))
            except Exception as e:
                logger.warning(
                    f"[{request_id}] Failed to create segment #{idx}: "
                    f"{type(e).__name__}: {e}, keys={list(seg_data.keys())[:10]}"
                )

        return TeachingScript(
            request_id=request_id,
            title=data.get("title", "Untitled"),
            segments=segments,
            scaffolding_strategy=data.get("scaffolding_strategy", ""),
            target_duration_min=data.get("target_duration_min", 15),
        )
