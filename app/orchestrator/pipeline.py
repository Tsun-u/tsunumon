"""Pipeline Orchestrator — 核心控制流程，協調所有模組在 30 分鐘內完成。"""

import asyncio
import hashlib
import json
import logging
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

from config.settings import Settings, settings
from app.models.domain import AudioSegment, PipelineResult, ScriptSegment, SlideImage, TeachingRequest, TeachingScript
from app.services.llm.base import BaseLLMService
from app.services.tts.base import BaseTTSService
from app.services.slides.base import BaseSlideGenerator
from app.services.slides.reviewer import SlideReviewer
from app.services.video.composer import VideoComposer
from app.services.storage.base import BaseStorageService

logger = logging.getLogger(__name__)


def _split_sentences(text: str) -> list[str]:
    """將旁白文字按句號切分成短句，用於 SRT 字幕。

    半形句點獨立處理：後面緊跟數字時視為小數點不切分，
    避免「1.8 metres」被切成「1. / 8 metres」（數學/物理題災難）。
    """
    import re
    # 按中英文句號、問號、驚嘆號切分；半形 . 加 (?!\d) 保護小數、(?!\.) 保護省略號
    # （"..." 逐點切會產生孤立「.」碎片條目，決賽 120 實證 8 條）
    parts = re.split(r'(?<=[。!！?？])\s*|(?<=\.)(?!\d)(?!\.)\s*', text.strip())
    # 過濾空字串與純標點碎片（belt-and-suspenders，配合上面的省略號保護）
    return [p.strip() for p in parts if p.strip() and not re.fullmatch(r"[.…。!！?？\s]+", p)]


def _generate_srt(
    segments: List[ScriptSegment],
    audio_segments: List[AudioSegment],
    output_path: Path,
) -> Path:
    """從旁白文字和音訊時長生成 SRT 字幕檔，按句號切分短句。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 建立 segment_id → actual duration 的對照
    duration_map = {a.segment_id: a.duration_sec for a in audio_segments}

    def fmt(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    idx = 1
    current_time = 0.0
    for seg in segments:
        duration = duration_map.get(seg.segment_id, seg.estimated_duration_sec)
        seg_start = current_time
        seg_end = seg_start + duration

        # 按句號切分
        sentences = _split_sentences(seg.narration_text)
        if not sentences:
            sentences = [seg.narration_text]

        # 按字數比例分配時間
        total_chars = sum(len(s) for s in sentences)
        if total_chars == 0:
            total_chars = 1

        t = seg_start
        for sent in sentences:
            sent_duration = duration * len(sent) / total_chars
            s_start = t
            s_end = min(t + sent_duration, seg_end)

            lines.append(f"{idx}")
            lines.append(f"{fmt(s_start)} --> {fmt(s_end)}")
            lines.append(sent)
            lines.append("")
            idx += 1
            t = s_end

        current_time = seg_end

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _zip_slides(slides_dir: Path, output_path: Path) -> Path:
    """將投影片 HTML 檔案打包成 zip。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_files = sorted(slides_dir.glob("*.html"))
    if not html_files:
        return None

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in html_files:
            zf.write(f, f.name)

    return output_path


# Singleton pipeline instance
_pipeline = None


def _cache_key(course_requirement: str, student_persona: str) -> str:
    """生成快取 key：SHA-256 前 16 碼。"""
    raw = f"{course_requirement.strip()}|{student_persona.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class TeachingPipeline:
    def __init__(
        self,
        llm: BaseLLMService,
        tts: BaseTTSService,
        slides: BaseSlideGenerator,
        video: VideoComposer,
        storage: BaseStorageService,
        config: Settings,
        reviewer: Optional[SlideReviewer] = None,
        gemini_fact_checker=None,
        router=None,
        consultant=None,
        subject_classifier=None,
    ):
        self.llm = llm
        self.tts = tts
        self.slides = slides
        self.video = video
        self.storage = storage
        self.config = config
        self.reviewer = reviewer
        self.gemini_fact_checker = gemini_fact_checker
        self.router = router
        self.consultant = consultant
        self.subject_classifier = subject_classifier
        self.fallback_tts = None  # Azure → GCP fallback
        self.cache_dir = Path(config.output_dir) / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)


    async def _classify_subject_kb(
        self, request: TeachingRequest, rid: str, stage: str
    ) -> tuple:
        """跑 mini subject classifier（resume 分支用；只需 course_requirement、
        沒有 outline 也能跑）。回 (subject, kb_unit_urls)；未啟用或失敗回 (None, [])，
        由 improve_script 內部的 keyword fallback + index-only 行為接手。"""
        if not self.subject_classifier:
            return None, []
        try:
            mini_subject, kb_unit_urls = await asyncio.get_running_loop().run_in_executor(
                None, self.subject_classifier.classify, request.course_requirement
            )
            logger.info(
                f"[{rid}] Subject classifier ({stage}): subject={mini_subject}, "
                f"kb_unit_urls={len(kb_unit_urls)}"
            )
            return mini_subject, kb_unit_urls
        except Exception as e:
            logger.warning(
                f"[{rid}] Subject classifier ({stage}) failed ({type(e).__name__}: {e})"
            )
            return None, []


    async def _render_and_review(
        self,
        segments: List[ScriptSegment],
        slides_dir: Path,
        rid: str,
        pre_rendered: Optional[List[SlideImage]] = None,
        subject: Optional[str] = None,
    ) -> List[SlideImage]:
        """渲染投影片，若有 reviewer 則分輪審核：每輪只檢查未通過的投影片。

        pre_rendered: 如果提供，跳過初始渲染，直接用這些 images 進入 review。
        subject: 偵測到的科目，傳給 reviewer 做精審輪 model 選擇（生化走 4.7）。
        """
        if pre_rendered is not None:
            slide_images = pre_rendered
        else:
            slide_images = await self.slides.generate_all(segments, slides_dir)

        if not self.reviewer:
            return slide_images

        total = len(slide_images)
        max_rounds = self.reviewer.max_rounds
        total_reviews = 0
        total_refinements = 0

        # 待審清單：index → (segment, image)
        pending = {i: (seg, img) for i, (seg, img) in enumerate(zip(segments, slide_images))}

        # Vision Review 平行化：同一輪內多張投影片平行送 Claude Vision 審核
        # （asyncio.gather + Semaphore + 進場 stagger）。review 是 I/O-bound API
        # 等待（每張 ~40s），序列累加；平行後只等最慢那批。reviewer.review 是同步
        # 方法、跑在 executor，用 sized ThreadPoolExecutor 提供足夠 thread（default
        # executor 在 4-cpu 上限 ~8，會卡住更高併發）。
        # 注意：同一輪內平行、輪與輪之間仍序列——review 發現問題要重渲染再下一輪重審。
        concurrency = max(1, self.config.vision_review_concurrency)
        stagger_ms = max(0, self.config.vision_review_stagger_ms)
        loop = asyncio.get_running_loop()

        with ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="vision-review"
        ) as review_executor:
            for round_num in range(1, max_rounds + 1):
                # Opus confirmation: when entering Opus phase with empty pending,
                # re-check all slides once so Opus validates Sonnet's approvals
                if not pending:
                    if round_num == self.reviewer.sonnet_rounds + 1:
                        pending = {i: (seg, img) for i, (seg, img) in enumerate(zip(segments, slide_images))}
                        logger.info(f"[{rid}] Opus confirmation: re-checking all {total} slides")
                    else:
                        break

                logger.info(
                    f"[{rid}] Vision review round {round_num}/{max_rounds}: "
                    f"checking {len(pending)}/{total} slides (concurrency={concurrency})..."
                )

                sem = asyncio.Semaphore(concurrency)

                async def _review_one(pos, idx, seg, img):
                    # 進場錯開（de-burst 第一波），semaphore 控同時併發數
                    if stagger_ms:
                        await asyncio.sleep(pos * stagger_ms / 1000)
                    try:
                        async with sem:
                            refined = await loop.run_in_executor(
                                review_executor,
                                self.reviewer.review,
                                img.image_path,
                                seg.slide_html,
                                seg.slide_title,
                                round_num,
                                subject,
                            )
                        return idx, seg, img, refined
                    except Exception as e:
                        # fail-open（對齊 reviewer.review 內部設計）：單張 review 意外
                        # 失敗（如 image 讀取例外）不拖垮整輪 gather，該張視為 ok。
                        logger.warning(
                            f"[{rid}]   Slide {seg.segment_id}: review task failed "
                            f"({type(e).__name__}: {e}), treating as ok"
                        )
                        return idx, seg, img, None

                # gather 保留順序，但下游用 idx 對齊、不依賴順序
                items = list(pending.items())  # [(idx, (seg, img)), ...]
                review_results = await asyncio.gather(*[
                    _review_one(pos, idx, seg, img)
                    for pos, (idx, (seg, img)) in enumerate(items)
                ])
                total_reviews += len(review_results)

                # review 結果處理：refined 的重新渲染（下一輪重審）。重渲染量通常
                # 很小（只有需修的張），維持序列、保持改動聚焦在 review 平行化。
                still_pending = {}
                for idx, seg, img, refined_html in review_results:
                    if refined_html is None:
                        logger.info(f"[{rid}]   Slide {seg.segment_id}: ok")
                        continue

                    # Claude 回傳修正 HTML → 重新渲染，下一輪再檢查
                    logger.info(
                        f"[{rid}]   Slide {seg.segment_id}: "
                        f"refined ({len(refined_html)} chars)"
                    )
                    seg.slide_html = refined_html
                    new_img = await self.slides.generate_slide(seg, slides_dir)
                    slide_images[idx] = new_img
                    total_refinements += 1
                    still_pending[idx] = (seg, new_img)

                pending = still_pending

        if pending:
            logger.warning(
                f"[{rid}] {len(pending)} slides still not approved after "
                f"{max_rounds} rounds: {[segments[i].segment_id for i in pending]}"
            )

        logger.info(
            f"[{rid}] Review done: {total_reviews} reviews, "
            f"{total_refinements} fixes, "
            f"{total - len(pending)}/{total} approved"
        )
        return slide_images


    async def execute(self, request: TeachingRequest) -> PipelineResult:
        # 每題從頭完整跑（Outline→Expand→Improve→Fact-check→render→compose）。
        start = time.time()
        rid = request.request_id

        # 準備工作目錄
        base_dir = Path(self.config.output_dir) / rid
        slides_dir = base_dir / "slides"
        audio_dir = base_dir / "audio"
        video_path = base_dir / "output.mp4"

        detected_subject = None  # 由 Outline 設定，用於 Tool KB routing + Cover routing

        # ── Script 生成：Router → Subject Classifier → Outline → Expand → Consultant → Improve ──
        # Step 0: Persona classifier — 決定 Outline 模型（Opus 只在 depth+normal 時觸發）
        outline_model_override = None
        focus_level = "normal"
        if self.router:
            loop = asyncio.get_running_loop()
            routed_model, depth_seeking, focus_level = await loop.run_in_executor(
                None, self.router.route,
                request.course_requirement, request.student_persona,
            )
            logger.info(
                f"[{rid}] Router: depth_seeking={depth_seeking}, "
                f"focus_level={focus_level}, outline_model={routed_model}"
            )
            outline_model_override = routed_model

        # Step 0b: Subject Classifier（GPT-5.4-mini）與 Outline 平行——只需
        # course_requirement，不等 Outline。回 (subject, unit_urls)，失敗 (None, [])。
        classifier_task = None
        if self.subject_classifier:
            classifier_task = asyncio.get_running_loop().run_in_executor(
                None, self.subject_classifier.classify, request.course_requirement
            )

        # Step 1a: LLM 生成課程大綱（含 web_search 事實查核）
        logger.info(f"[{rid}] Step 1a: Generating course outline...")
        outline = await asyncio.wait_for(
            self.llm.generate_outline(request, outline_model=outline_model_override, focus_level=focus_level),
            timeout=120,
        )

        # 收 mini 分類結果（best-effort）
        mini_subject, kb_unit_urls = None, []
        if classifier_task is not None:
            try:
                mini_subject, kb_unit_urls = await classifier_task
            except Exception as e:
                logger.warning(f"[{rid}] Subject classifier task failed ({type(e).__name__}: {e})")

        # Subject 優先級：mini > Outline 填的 > 關鍵字 fallback
        detected_subject = outline.subject
        if mini_subject:
            if mini_subject != outline.subject:
                logger.info(f"[{rid}] Subject: mini classifier '{mini_subject}' (outline said '{outline.subject}')")
            detected_subject = mini_subject
            outline.subject = mini_subject
        elif detected_subject == "other" or detected_subject not in ("physics", "math", "biology", "cs"):
            from app.services.llm.claude import _detect_subject
            fallback = _detect_subject(request.course_requirement)
            if fallback != "other":
                logger.info(f"[{rid}] Outline subject '{detected_subject}' overridden by keyword match → '{fallback}'")
                detected_subject = fallback
                outline.subject = fallback
        logger.info(
            f"[{rid}] Outline: {outline.course_topic}, "
            f"{len(outline.sequences)} sequences, subject={detected_subject}, "
            f"kb_unit_urls={len(kb_unit_urls)} ({time.time() - start:.1f}s)"
        )

        # Step 1b: LLM 展開大綱為投影片內容 + 旁白
        logger.info(f"[{rid}] Step 1b: Expanding outline to script...")
        script = await asyncio.wait_for(
            self.llm.expand_outline(request, outline, focus_level=focus_level, kb_unit_urls=kb_unit_urls),
            timeout=180,
        )
        logger.info(
            f"[{rid}] Script: {len(script.segments)} segments "
            f"({time.time() - start:.1f}s)"
        )

        # Step 1b-consult: 兩位教學顧問（GPT + Gemini）平行讀草稿，給 advisory notes
        # Improver 看這些建議時有完全專業判斷權，可採可棄。
        consultant_payload = None
        if self.consultant is not None:
            logger.info(f"[{rid}] Step 1b-consult: Teaching consultants reviewing draft...")
            try:
                raw_notes = await asyncio.wait_for(
                    self.consultant.consult(request, script),
                    timeout=self.config.consultant_timeout_sec * 2 + 30,
                )
                gpt_count = len(raw_notes.get("gpt", []))
                gemini_count = len(raw_notes.get("gemini", []))
                logger.info(
                    f"[{rid}] Consultant notes: GPT={gpt_count}, Gemini={gemini_count} "
                    f"({time.time() - start:.1f}s)"
                )
                consultant_payload = {
                    "gpt": self.consultant.format_for_improver(raw_notes.get("gpt", [])),
                    "gemini": self.consultant.format_for_improver(raw_notes.get("gemini", [])),
                }
            except Exception as e:
                logger.warning(
                    f"[{rid}] Consultant step failed "
                    f"({type(e).__name__}: {e}); proceeding without notes"
                )
                consultant_payload = None

        # Step 1c: 資深教師審修（Sonnet 改良 Haiku 草稿）
        logger.info(f"[{rid}] Step 1c: Senior teacher reviewing draft...")
        draft_before_review = script
        try:
            script = await asyncio.wait_for(
                self.llm.improve_script(
                    request, script, consultant_notes=consultant_payload,
                    focus_level=focus_level, subject=outline.subject,
                    kb_unit_urls=kb_unit_urls,
                ),
                # 有 KB 的 Improve 實測 12-15min，timeout 設 1200 避免 300s 誤殺
                timeout=1200,
            )
            logger.info(
                f"[{rid}] Reviewed script: {len(script.segments)} segments "
                f"({time.time() - start:.1f}s)"
            )
            if not script.segments:
                logger.warning(f"[{rid}] Review returned 0 segments, falling back to Haiku draft")
                script = draft_before_review
        except Exception as e:
            logger.warning(f"[{rid}] Senior review failed ({type(e).__name__}: {e}), falling back to Haiku draft")
            script = draft_before_review

        # Step 1d: 事實審核（獨立於教學品質，只修正事實錯誤）
        logger.info(f"[{rid}] Step 1d: Fact-checking script...")
        try:
            if self.gemini_fact_checker:
                # Gemini: Google Search grounding + code execution（便宜且強力）
                checked = await asyncio.wait_for(
                    self.gemini_fact_checker.fact_check_script(request, script),
                    timeout=300,
                )
            else:
                # Claude: web_fetch KB + code execution（fallback）
                checked = await asyncio.wait_for(
                    self.llm.fact_check_script(request, script),
                    timeout=300,
                )
            if checked and checked.segments:
                logger.info(
                    f"[{rid}] Fact-check done: {len(checked.segments)} segments "
                    f"({time.time() - start:.1f}s)"
                )
                script = checked
            else:
                logger.warning(f"[{rid}] Fact-check returned empty, keeping original")
        except Exception as e:
            logger.warning(f"[{rid}] Fact-check failed ({type(e).__name__}: {e}), keeping original")

        # Step 1d-extra: Resolve stock-image markers in body slides.
        # Improver emits <img class="stock-image" data-stock-query="..."> only
        # when SVG genuinely cannot represent the visual (real microscope
        # cross-sections, real lab equipment). This stage replaces the marker
        # with a real photo from Unsplash; SVG remains the default for
        # everything else.
        if self.config.stock_image_enabled:
            unsplash_key = self.config.unsplash_access_key.get_secret_value()
            if unsplash_key:
                from app.services.stock_image import resolve_stock_images
                try:
                    n_resolved = await asyncio.wait_for(
                        resolve_stock_images(
                            script.segments,
                            slides_dir,
                            access_key=unsplash_key,
                        ),
                        timeout=180,
                    )
                    if n_resolved:
                        logger.info(
                            f"[{rid}] Stock image: resolved {n_resolved} "
                            f"marker(s) ({time.time() - start:.1f}s)"
                        )
                except Exception as e:
                    logger.warning(
                        f"[{rid}] Stock image resolve failed "
                        f"({type(e).__name__}: {e}), keeping markers"
                    )

        # Step 1d-2: ImagePromptGen — extract cover image prompt from cover narration
        # (Cover race moved to Step 2 to run in parallel with TTS+Slides)
        from app.services.cover_image import generate_cover_image_prompt

        cover_seg = None
        cover_negative_prompt = ""
        if self.config.cover_image_enabled:
            cover_seg = next(
                (s for s in script.segments if s.teaching_phase == "hook"),
                script.segments[0] if script.segments else None,
            )
            if cover_seg is None:
                logger.info(f"[{rid}] cover_image: no segments, skip")
            else:
                openai_key = self.config.openai_api_key.get_secret_value()
                if not cover_seg.cover_image_prompt and openai_key:
                    logger.info(
                        f"[{rid}] Step 1d-2: ImagePromptGen for cover segment "
                        f"{cover_seg.segment_id}"
                    )
                    pair = await generate_cover_image_prompt(
                        cover_seg,
                        api_key=openai_key,
                        model=self.config.image_prompt_gen_model,
                        subject=detected_subject,
                    )
                    if pair:
                        cover_seg.cover_image_prompt, cover_negative_prompt = pair

        # Step 2: Cover race + TTS + Slides 三者並行
        # Cover race 與 TTS 完全獨立；Slides 渲染 slide 1 需要等 cover 注入，
        # 但 slides 2-N 和 TTS 可以先跑。為簡化實作，cover race 完成後注入
        # slide 1 HTML，然後 slides renderer 在渲染 slide 1 時自然讀到新 HTML。

        async def _cover_race_and_inject():
            """Cover image race + inject，獨立於 TTS/Slides。"""
            if cover_seg is None or not cover_seg.cover_image_prompt:
                if cover_seg and not cover_seg.cover_image_prompt:
                    logger.info(
                        f"[{rid}] cover_image: no cover_image_prompt available, "
                        "skip race (F1 placeholder kept)"
                    )
                return
            from app.services.cover_image import (
                inject_cover_image,
                race_cover_image,
            )
            logger.info(
                f"[{rid}] Step 1e: cover image race for segment "
                f"{cover_seg.segment_id}"
            )
            try:
                race_result = await race_cover_image(
                    cover_seg.cover_image_prompt,
                    settings=self.config,
                    request_id=rid,
                    negative_prompt=cover_negative_prompt,
                )
                if race_result is None:
                    logger.info(
                        f"[{rid}] cover_image: both failed, F1 fallback "
                        "(placeholder kept)"
                    )
                else:
                    provider, image_bytes = race_result
                    ok = inject_cover_image(cover_seg, image_bytes, slides_dir)
                    logger.info(
                        f"[{rid}] cover_image: injected {provider} image "
                        f"({len(image_bytes)} bytes, slot_replaced={ok})"
                    )
            except Exception as e:
                logger.warning(
                    f"[{rid}] cover_image failed "
                    f"({type(e).__name__}: {e}), F1 fallback"
                )

        async def _tts_with_fallback():
            try:
                return await self.tts.synthesize_all(script.segments, audio_dir)
            except Exception as tts_err:
                if self.fallback_tts:
                    logger.warning(
                        f"[{rid}] Primary TTS failed "
                        f"({type(tts_err).__name__}: {tts_err}), "
                        f"falling back to secondary TTS"
                    )
                    return await self.fallback_tts.synthesize_all(
                        script.segments, audio_dir
                    )
                raise

        # Cover race + render slide 1 | TTS | Slides 2-N 三者並行
        # Cover race 完成後注入 cover image 再渲染 slide 1，確保 slide 1 有封面圖
        # Slides 2-N 不依賴 cover，可以同時渲染

        async def _cover_slide1_with_review():
            """Cover race → inject → render slide 1 → review slide 1。"""
            await _cover_race_and_inject()
            if not script.segments:
                return []
            seg = script.segments[0]
            img = await self.slides.generate_slide(seg, slides_dir)
            # Quick review for cover slide (template-based, usually passes)
            if self.reviewer:
                refined = await asyncio.get_running_loop().run_in_executor(
                    None, self.reviewer.review,
                    img.image_path, seg.slide_html, seg.slide_title,
                )
                if refined:
                    logger.info(f"[{rid}]   Slide {seg.segment_id}: cover refined")
                    seg.slide_html = refined
                    img = await self.slides.generate_slide(seg, slides_dir)
                else:
                    logger.info(f"[{rid}]   Slide {seg.segment_id}: cover ok")
            return [img]

        async def _render_remaining_with_review():
            """渲染 slides 2-N + vision review。"""
            if len(script.segments) <= 1:
                return []
            remaining = script.segments[1:]
            # checkpoint resume 路徑沒跑 Outline、detected_subject 會是 None，
            # 補一次 keyword fallback，讓生物題 resume 也能正確選精審 model。
            review_subject = detected_subject
            if review_subject is None:
                from app.services.llm.claude import _detect_subject
                review_subject = _detect_subject(request.course_requirement)
            return await self._render_and_review(
                remaining, slides_dir, rid, subject=review_subject
            )

        logger.info(f"[{rid}] Step 2: Cover+Slide1+Review | TTS | Slides 2-N+Review in parallel...")
        slide1_task = _cover_slide1_with_review()
        tts_task = _tts_with_fallback()
        remaining_task = _render_remaining_with_review()
        slide1_images, audio_segments, remaining_images = await asyncio.gather(
            slide1_task, tts_task, remaining_task
        )

        slide_images = slide1_images + remaining_images
        logger.info(f"[{rid}] Cover + Slides + TTS done ({time.time() - start:.1f}s)")

        # Step 3: FFmpeg 影片合成
        logger.info(f"[{rid}] Step 3: Composing video...")
        video_result = await self.video.compose(slide_images, audio_segments, video_path)
        logger.info(f"[{rid}] Video composed ({time.time() - start:.1f}s)")

        # Step 3b: 字幕 + 講義打包
        logger.info(f"[{rid}] Step 3b: Generating SRT subtitles + slides zip...")
        srt_path = _generate_srt(
            script.segments, audio_segments, base_dir / "subtitles.srt"
        )
        zip_path = _zip_slides(slides_dir, base_dir / "slides.zip")
        logger.info(f"[{rid}] SRT + zip done ({time.time() - start:.1f}s)")

        # Step 4: 上傳
        logger.info(f"[{rid}] Step 4: Uploading...")
        video_url = await self.storage.upload(
            Path(video_result.video_path), f"{rid}/output.mp4"
        )

        subtitle_url = None
        if srt_path:
            subtitle_url = await self.storage.upload(srt_path, f"{rid}/subtitles.srt")

        supplementary_urls = []
        if zip_path:
            url = await self.storage.upload(zip_path, f"{rid}/slides.zip")
            supplementary_urls.append(url)

        logger.info(
            f"[{rid}] Pipeline complete in {time.time() - start:.1f}s"
        )

        return PipelineResult(
            video_url=video_url,
            subtitle_url=subtitle_url,
            supplementary_url=supplementary_urls or None,
        )


def create_pipeline(config: Settings | None = None) -> TeachingPipeline:
    """根據設定檔建立 pipeline，自動選擇各模組的實作。"""
    if config is None:
        config = settings

    # LLM
    if config.llm_backend == "mock":
        from app.services.llm.mock import MockLLMService
        llm = MockLLMService()
    elif config.llm_backend == "claude":
        from app.services.llm.claude import ClaudeLLMService
        llm = ClaudeLLMService(config)
    else:
        raise ValueError(f"Unknown LLM backend: {config.llm_backend}")

    # TTS
    if config.tts_backend == "mock":
        from app.services.tts.mock import MockTTSService
        tts = MockTTSService()
    elif config.tts_backend == "kokoro":
        from app.services.tts.kokoro import KokoroTTSService
        tts = KokoroTTSService()
    elif config.tts_backend == "gcp":
        from app.services.tts.gcp import GCPTTSService
        tts = GCPTTSService(
            api_key=config.gcp_api_key,
            voice_name=config.gcp_tts_voice,
        )
    elif config.tts_backend == "azure":
        from app.services.tts.azure import AzureTTSService
        tts = AzureTTSService(
            speech_key=config.azure_speech_key.get_secret_value(),
            speech_region=config.azure_speech_region,
            voice_name=config.azure_tts_voice,
            pitch=config.azure_tts_pitch,
            volume=config.azure_tts_volume,
            role=config.azure_tts_role,
        )
    else:
        raise ValueError(f"Unknown TTS backend: {config.tts_backend}")

    # Slides
    if config.slides_backend == "mock":
        from app.services.slides.mock import MockSlideGenerator
        slides_gen = MockSlideGenerator()
    elif config.slides_backend == "pillow":
        from app.services.slides.pillow_renderer import PillowSlideRenderer
        slides_gen = PillowSlideRenderer()
    elif config.slides_backend == "html":
        from app.services.slides.html_renderer import HtmlSlideRenderer
        slides_gen = HtmlSlideRenderer(max_concurrent=config.slide_render_concurrency)
    else:
        raise ValueError(f"Unknown slides backend: {config.slides_backend}")

    # Video
    video = VideoComposer(ffmpeg_path=config.ffmpeg_path)

    # Storage
    if config.storage_backend == "local":
        from app.services.storage.local import LocalStorageService
        base_url = config.base_url or f"http://localhost:{config.port}"
        storage = LocalStorageService(base_url=base_url)
    elif config.storage_backend == "gcs":
        raise NotImplementedError("GCS storage not yet implemented")
    else:
        raise ValueError(f"Unknown storage backend: {config.storage_backend}")

    # Slide Reviewer（只有 claude backend 才啟用 Vision 審核）
    reviewer = None
    if config.llm_backend == "claude":
        reviewer = SlideReviewer(config)

    # Gemini Fact-Checker（可選，用 Google Search grounding + code execution）
    gemini_fact_checker = None
    if config.fact_check_backend == "gemini" and config.gemini_api_key.get_secret_value():
        from app.services.llm.gemini_fact_checker import GeminiFactChecker
        gemini_fact_checker = GeminiFactChecker(
            api_key=config.gemini_api_key.get_secret_value(),
            model=config.gemini_fact_check_model,
        )

    # Outline Router（GPT-5.4-Mini 判斷難度 → Opus 或 Sonnet）
    router = None
    if config.router_enabled and config.openai_api_key.get_secret_value():
        from app.services.llm.outline_router import OutlineRouter
        router = OutlineRouter(
            api_key=config.openai_api_key.get_secret_value(),
            model=config.router_model,
            opus_model="claude-opus-4-6",
            sonnet_model="claude-sonnet-4-6",
        )

    # Subject Classifier（GPT-5.4-mini 做 subject 分類 + 挑 KB unit URL，Router 後與
    # Outline 平行；mini 失敗時 pipeline 退回關鍵字矩陣 + 現行 index-only 行為）
    subject_classifier = None
    if config.subject_classifier_enabled and config.openai_api_key.get_secret_value():
        from app.services.llm.subject_classifier import SubjectClassifier
        subject_classifier = SubjectClassifier(
            api_key=config.openai_api_key.get_secret_value(),
            model=config.subject_classifier_model,
            max_urls=config.subject_classifier_max_urls,
        )
        logger.info(
            f"Subject classifier enabled: {config.subject_classifier_model} "
            f"(max_urls={config.subject_classifier_max_urls})"
        )

    # Teaching Consultants（GPT Sonnet 同級 + Gemini Sonnet 同級，Expand 後給建議）
    consultant = None
    if (
        config.consultant_enabled
        and config.openai_api_key.get_secret_value()
        and config.gemini_api_key.get_secret_value()
    ):
        from app.services.llm.consultant import ConsultantService
        consultant = ConsultantService(
            openai_api_key=config.openai_api_key.get_secret_value(),
            gemini_api_key=config.gemini_api_key.get_secret_value(),
            gpt_model=config.consultant_gpt_model,
            gemini_model=config.consultant_gemini_model,
            timeout_sec=config.consultant_timeout_sec,
        )
        logger.info(
            f"Consultants enabled: GPT={config.consultant_gpt_model}, "
            f"Gemini={config.consultant_gemini_model}"
        )

    pipeline = TeachingPipeline(
        llm, tts, slides_gen, video, storage, config,
        reviewer, gemini_fact_checker, router, consultant,
        subject_classifier,
    )

    # Azure TTS → GCP fallback（Azure 失敗時自動切換到 GCP）
    if config.tts_backend == "azure" and config.gcp_api_key:
        from app.services.tts.gcp import GCPTTSService
        pipeline.fallback_tts = GCPTTSService(
            api_key=config.gcp_api_key,
            voice_name=config.gcp_tts_voice,
        )
        logger.info("TTS fallback: GCP Neural2 registered as Azure backup")

    return pipeline


def get_pipeline() -> TeachingPipeline:
    """取得 singleton pipeline instance。"""
    global _pipeline
    if _pipeline is None:
        _pipeline = create_pipeline()
    return _pipeline
