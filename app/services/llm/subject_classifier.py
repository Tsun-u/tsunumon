"""Subject Classifier — GPT-5.4-mini 獨立做 subject 分類 + 挑相關 KB unit URL。

Router 後與 Outline 平行跑。給 mini 一份 KB catalog（各科 unit 檔標題 + 絕對 URL）
+ course_requirement，回 {subject, unit_urls}：
- subject：biology/cs/math/physics/chemistry/other（pipeline 用 mini > Outline > keyword）
- unit_urls：catalog 裡最相關的 unit 子頁 URL（白名單校驗、只留真實存在的）

unit_urls 注入 Improve/Expand 的 KB hint（明列 URL → 進 web_fetch prior_context →
model 搆得到 unit KB，解 KB-miss、見 memory reference_web_fetch_prior_context）。

best-effort：mini 失敗（timeout/error/亂回）→ classify() 回 (None, [])，由 pipeline
退回關鍵字矩陣 + 現行「只列 index URL」行為，不阻塞。
"""

import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# KB 主來源（GitHub Pages，公開靜態網站）；catalog URL 用這個 base
_PAGES_BASE = "https://tsun-u.github.io/tsunumon-kb"
_SUBJECTS = ["biology", "cs", "math", "physics"]

_SYSTEM_PROMPT = """\
You are a curriculum subject classifier for an AI tutoring pipeline.
Given a course requirement and a catalog of available knowledge-base (KB) files
(grouped by subject, each line "title — URL"), do two things:

1. Classify the single best subject for the course: one of biology, cs, math, physics, chemistry, other.
2. From the catalog, list the full URLs of the KB unit files MOST relevant to teaching this
   specific topic, ordered from most to least relevant (pick from the chosen subject; 1-4 files;
   ONLY URLs that appear verbatim in the catalog — never invent or modify a URL).

Respond with ONLY a JSON object:
{"subject": "<subject>", "unit_urls": ["<full url>", ...], "reasoning": "<one short sentence>"}"""


class SubjectClassifier:
    """GPT-5.4-mini 做 subject 分類 + 挑 KB unit URL。"""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.4-mini",
        curriculum_dir: Optional[Path] = None,
        timeout: float = 15.0,
        max_urls: int = 2,
    ):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        # unit_urls 注入上限：每多 1 個 URL ≈ Improve 多一次 web_fetch + 讀檔時間。
        # mini 依相關性排序，cap 砍尾留頭、品質損失最小。0 = 不限制。
        self._max_urls = max_urls
        if curriculum_dir is None:
            # app/services/llm/subject_classifier.py → parent x4 = tsunumon root
            curriculum_dir = Path(__file__).resolve().parent.parent.parent.parent / "config" / "curriculum"
        self._catalog, self._catalog_urls = self._build_catalog(curriculum_dir)
        logger.info(
            f"SubjectClassifier: catalog built ({len(self._catalog)} chars, "
            f"{len(self._catalog_urls)} URLs, model={model})"
        )

    @staticmethod
    def _first_heading(path: Path) -> str:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith("#"):
                    return s.lstrip("#").strip()
        except Exception:
            pass
        return path.stem

    def _build_catalog(self, curriculum_dir: Path) -> Tuple[str, set]:
        """建 catalog（標題 + GitHub Pages URL）。

        線上優先：從 GitHub Pages 各科 index 解析 unit 連結建（本地無 curriculum
        時也能用學科 KB）；線上全失敗時 fallback 掃本地 config/curriculum。
        """
        catalog, urls = self._build_catalog_online()
        if catalog:
            logger.info(f"SubjectClassifier: catalog from online KB ({len(urls)} URLs)")
            return catalog, urls
        logger.warning(
            "SubjectClassifier: online KB unavailable, falling back to local curriculum"
        )
        return self._build_catalog_local(curriculum_dir)

    def _build_catalog_online(self) -> Tuple[str, set]:
        """從 GitHub Pages 各科 index.md 解析 markdown 連結 [標題](url) 建 catalog。

        fetch 由 httpx（pipeline 端）做、SubjectClassifier 的 gpt-mini 只讀 catalog
        文字不碰網路。per-subject best-effort：某科 fetch 失敗略過該科、不阻塞 startup；
        全失敗回 ("", set()) 交給 _build_catalog 走本地 fallback。
        """
        lines: List[str] = []
        urls: set = set()
        for subj in _SUBJECTS:
            index_url = f"{_PAGES_BASE}/{subj}/index.md"
            try:
                resp = httpx.get(index_url, timeout=8.0, follow_redirects=True)
                resp.raise_for_status()
                content = resp.text
            except Exception as e:
                logger.warning(
                    f"SubjectClassifier: online index fetch failed for {subj} "
                    f"({type(e).__name__}: {e})"
                )
                continue
            # index.md 是 markdown 表格：| Unit | Name | Weight | [檔名](url) |
            # 逐行解析：抓 link URL，標題優先取表格 Name 欄（人類可讀），fallback 檔名
            subj_lines: List[str] = []
            for line in content.splitlines():
                m = re.search(r"\[([^\]]+)\]\(([^)]+\.md)\)", line)
                if not m:
                    continue
                href = m.group(2).strip()
                # KB index 連結一律絕對 URL（見 memory reference_tsunumon_kb_links）；
                # 容錯相對連結也補成絕對
                url = href if href.startswith("http") else f"{_PAGES_BASE}/{subj}/{href.lstrip('./')}"
                # 只收同科 unit 子頁，排除 index/index_ap 自身
                if f"/{subj}/" not in url:
                    continue
                fname = url.rsplit("/", 1)[-1]
                if fname.startswith("index"):
                    continue
                if url in urls:
                    continue
                # 標題：表格 Name 欄優先（排除 Unit 編號 / 含 link / weight% / 分隔線 cell）
                title = None
                if "|" in line:
                    cells = [c.strip() for c in line.split("|")]
                    cand = [
                        c for c in cells
                        if c and "[" not in c and "%" not in c
                        and not c.replace("-", "").isdigit() and set(c) != {"-"}
                    ]
                    if cand:
                        title = cand[0]
                if not title:
                    title = fname[:-3] if fname.endswith(".md") else fname
                urls.add(url)
                subj_lines.append(f"- {title} — {url}")
            if subj_lines:
                lines.append(f"\n## Subject: {subj}")
                lines.extend(subj_lines)
        return "\n".join(lines), urls

    def _build_catalog_local(self, curriculum_dir: Path) -> Tuple[str, set]:
        """從本地 config/curriculum 各科非 index 的 unit 檔建 catalog（線上不可達時 fallback）。"""
        lines: List[str] = []
        urls: set = set()
        for subj in _SUBJECTS:
            d = curriculum_dir / subj
            if not d.exists():
                continue
            files = sorted(
                f for f in d.glob("*.md")
                if not f.name.startswith("index") and f.name.lower() != "readme.md"
            )
            if not files:
                continue
            lines.append(f"\n## Subject: {subj}")
            for f in files:
                url = f"{_PAGES_BASE}/{subj}/{f.name}"
                urls.add(url)
                lines.append(f"- {self._first_heading(f)} — {url}")
        return "\n".join(lines), urls

    def classify(self, course_requirement: str) -> Tuple[Optional[str], List[str]]:
        """回傳 (subject, unit_urls)。失敗回 (None, [])，由 pipeline fallback。

        unit_urls 已對 catalog 做白名單校驗：只留真實存在的 URL，drop 掉 mini
        hallucinate 的（避免 hallucinated URL 進 prompt → web_fetch 404 浪費）。
        """
        if not self._catalog or not self._api_key:
            return None, []
        try:
            user = (
                f"Course requirement:\n{course_requirement}\n\n"
                f"KB catalog:\n{self._catalog}"
            )
            resp = httpx.post(
                _OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                    "max_completion_tokens": 400,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            result = json.loads(resp.json()["choices"][0]["message"]["content"])

            subject = str(result.get("subject", "")).strip().lower()
            if subject not in ("biology", "cs", "math", "physics", "chemistry", "other"):
                logger.warning(f"SubjectClassifier: invalid subject={subject!r}, treating as None")
                subject = None
            elif subject == "other":
                subject = None  # other → 交給 pipeline 的 outline/keyword fallback

            # 白名單校驗：只留 catalog 裡真實存在的 URL
            raw_urls = result.get("unit_urls", []) or []
            valid_urls = [u for u in raw_urls if isinstance(u, str) and u in self._catalog_urls]
            dropped = len(raw_urls) - len(valid_urls)
            if dropped:
                logger.warning(
                    f"SubjectClassifier: dropped {dropped} URL(s) not in catalog "
                    f"(hallucination guard)"
                )

            # 注入上限：cap 前 N 個（mini 依相關性排序、砍尾留頭），控制 Improve
            # web_fetch 時間、守 30min budget
            if self._max_urls > 0 and len(valid_urls) > self._max_urls:
                logger.info(
                    f"SubjectClassifier: capped unit_urls {len(valid_urls)} -> "
                    f"{self._max_urls} (max_urls budget guard)"
                )
                valid_urls = valid_urls[: self._max_urls]
            logger.info(
                f"SubjectClassifier: subject={subject}, "
                f"{len(valid_urls)} unit URL(s), reason={result.get('reasoning', '')}"
            )
            return subject, valid_urls

        except Exception as e:
            logger.warning(
                f"SubjectClassifier failed ({type(e).__name__}: {e}), "
                f"falling back to keyword/index behavior"
            )
            return None, []
