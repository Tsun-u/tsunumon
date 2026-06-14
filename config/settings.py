from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # General
    mode: Literal["dev", "production"] = "dev"

    # LLM
    llm_backend: Literal["mock", "claude"] = "mock"
    llm_model: str = "claude-haiku-4-5-20251001"
    outline_model: str = "claude-opus-4-6"  # 教學主任：大綱設計（深度、範圍、ZPD）
    review_model: str = "claude-sonnet-4-6"  # Sonnet improve + Vision 審核粗篩輪
    # Vision 審核精審輪（原 hardcode claude-opus-4-7，2026-06-10 換 Fable 5）。
    # env OPUS_REVIEW_MODEL 可覆蓋，要退回 4.7 改 .env 即可、不用重新 deploy code。
    opus_review_model: str = "claude-fable-5"
    # Fable 5 classifier refusal 時的重審 model（空字串 = 不重審、直接 fail-open）
    opus_review_fallback_model: str = "claude-opus-4-7"
    fact_check_backend: Literal["claude", "gemini"] = "claude"  # 事實審核引擎
    fact_check_model: str = "claude-sonnet-4-6"  # Claude 用：可換 Opus
    fact_check_temperature: float = 0.3  # 審核重穩定性，低 temperature
    anthropic_api_key: SecretStr = SecretStr("")

    # Gemini (fact-check 外包用)
    gemini_api_key: SecretStr = SecretStr("")
    gemini_fact_check_model: str = "gemini-3.5-flash"  # Google Search grounding + code execution（2026-06-07: gemini-2.5-flash 將於 2026-10-16 退役，官方建議後繼 gemini-3.5-flash）

    # Cover image judge — Gemini head-to-head A/B between gpt-image-2 vs PixAI.
    # 對齊 AI Student 評審 model（gemini-3-flash），讓 cover 選擇跟同一個 grader
    # 視角一致；gemini-2.5-flash 是穩 fallback（vision multimodal、已驗證可用）。
    cover_judge_model: str = "gemini-3.5-flash"  # 2026-06-07: gemini-3-flash-preview（preview 版）→ gemini-3.5-flash，官方建議更換

    # Expand backend（實習老師：按大綱寫完整 slide + narration）
    expand_backend: Literal["claude", "openai"] = "claude"  # claude = Haiku, openai = GPT
    expand_model: str = "gpt-5.4-mini"  # OpenAI expand 用的模型（5.5 沒推 mini tier）

    # Consultants（教學顧問：Expand 後、Improve 前，兩個他家模型給 advisory notes）
    consultant_enabled: bool = False  # 預設關閉，需 CONSULTANT_ENABLED=true 啟用
    consultant_gpt_model: str = "gpt-5.4"  # OpenAI Sonnet 同級（A/B test：5.4 vs 5.5 advisory style）
    consultant_gemini_model: str = "gemini-3.5-flash"  # Gemini Sonnet 同級（2026-06-07: 原 default gemini-3.1 不在官方 model 列表、生產實走 .env override gemini-3-flash-preview，統一遷移 gemini-3.5-flash）
    consultant_timeout_sec: int = 120  # 兩個 consultant 各自 120s timeout（平行）

    # Router（Outline 大模型路由器 — GPT-5.4-Mini 判斷難度；5.5 沒 mini tier）
    openai_api_key: SecretStr = SecretStr("")
    router_enabled: bool = False  # 預設關閉，需 ROUTER_ENABLED=true 啟用
    router_model: str = "gpt-5.4-mini"
    router_threshold: int = 4  # difficulty >= 4 → Opus, else Sonnet

    # Subject Classifier（GPT-5.4-mini 獨立做 subject 分類 + 挑相關 KB unit URL）
    # Router 後與 Outline 平行跑：給 mini 一份 KB catalog（各科 unit 標題+URL）+
    # course_requirement，回 {subject, unit_urls}。subject 優先級 mini > Outline 填 >
    # 關鍵字 fallback；unit_urls 注入 Improve/Expand 的 KB hint（明列 URL → 進
    # web_fetch prior_context → model 搆得到 unit KB，解 KB-miss）。mini 失敗
    # （timeout/error）優雅退回關鍵字矩陣 + 現行「只列 index URL」行為、不阻塞。
    subject_classifier_enabled: bool = False  # 需 SUBJECT_CLASSIFIER_ENABLED=true
    subject_classifier_model: str = "gpt-5.4-mini"
    # unit_urls 注入上限（cap 白名單校驗後的前 N 個；mini 依相關性排序、砍尾留頭）。
    # 每多 1 個 URL ≈ Improve 多一次 web_fetch + 讀檔時間；單題逼近 30min budget 時
    # 用這個旋鈕收緊。0 = 不限制。env SUBJECT_CLASSIFIER_MAX_URLS 可調、不用改 code。
    subject_classifier_max_urls: int = 2

    # Cover image race（gpt-image-2 + PixAI 並行 → vision review pick）
    # Re-enabled 2026-05-07: pilot t82 + t85 retrigger with 小蝶 v2 cover template
    # (cover-image-slot hero visual, no greeting mascot on cover) to test whether
    # the t93 watercolor hallucination is reproducible across other topics.
    cover_image_enabled: bool = True
    cover_image_budget_s: int = 150  # race 兩家 wait ALL_COMPLETED timeout（gpt-image-2 1024² medium 60-90s）
    cover_image_grace_s: int = 15   # 一家成功時給另一家 grace
    cover_image_size: str = "1024x1024"      # gpt-image-2 size
    cover_image_quality: str = "medium"       # low / medium / high
    pixai_api_key: SecretStr = SecretStr("")  # dev key、避 JWT 過期
    pixai_model_id: str = ""                  # PixAI model id（空字串=skip PixAI）
    image_prompt_gen_model: str = "gpt-5.4-mini"  # ImagePromptGen Stage（5.5 沒 mini tier）

    # TTS
    tts_backend: Literal["mock", "kokoro", "gcp", "azure"] = "mock"
    gcp_api_key: str = ""  # GCP API Key（for Cloud TTS REST API）
    gcp_tts_voice: str = "en-US-Neural2-D"  # GCP TTS voice name
    azure_speech_key: SecretStr = SecretStr("")
    azure_speech_region: str = "eastus"
    azure_tts_voice: str = "en-US-RyanMultilingualNeural"
    azure_tts_pitch: str = "0%"
    azure_tts_volume: str = "0%"
    azure_tts_role: str = "YoungAdultMale"

    # Slides
    slides_backend: Literal["mock", "pillow", "html"] = "html"
    # 投影片渲染併發數：同時跑截圖/動畫錄影的投影片張數上限。
    # 動畫錄影最耗時（每張要跑完整段 narration 時長，如 30s），序列會累加；
    # 平行後只等最長那張。受 CPU/記憶體限制——每張錄影開一個獨立 Chromium
    # context（1280x720 video），e2-standard-4（4 vCPU/16GB）建議 4，
    # 升 e2-standard-8 可調高（env: SLIDE_RENDER_CONCURRENCY）。
    slide_render_concurrency: int = 4
    # Vision Review 併發數：同一輪內多張投影片平行送 Claude Vision 審核。
    # review 是 I/O-bound API 等待（每張 ~40s），序列 15 張要 ~10 分鐘；平行後
    # 只等最慢那批。Tier 4 額度寬裕（Fable 5 RPM=4000 / OTPM=800K），瓶頸不在
    # 額度而在不要瞬間爆量，所以配 stagger 錯開。e2-standard-4 建議 8，可調高
    # （env: VISION_REVIEW_CONCURRENCY）。
    vision_review_concurrency: int = 8
    # 每張 review 進場前的錯開間隔（毫秒 × 在批次中的位置），避免同一毫秒爆量。
    vision_review_stagger_ms: int = 200

    # Storage
    storage_backend: Literal["local", "gcs"] = "local"
    gcs_bucket: str = ""
    gcs_project_id: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = ""  # 對外 URL，空值時自動用 http://localhost:{port}

    # Pipeline
    pipeline_timeout_sec: int = 1700  # 28 min 20 sec, leave margin from 30 min limit
    output_dir: str = "output"
    ffmpeg_path: str = "ffmpeg"  # Full path to ffmpeg if not in PATH

    # Stock photo（Unsplash）— 已停用：Unsplash 商業攝影圖庫對學術 specimen 覆蓋差，
    # 即使 hit 也常是視覺像但內容不對的 false positive（v9 看到 sunflower query
    # hit 圖片實為 Liriodendron 樹切片）。所有視覺改走純 SVG。flag 保留為 False
    # 讓 pipeline.py Step 1d-extra 自動 skip、未來若改學術圖庫 (Wikimedia / OpenStax)
    # 可重啟並換 resolver。
    stock_image_enabled: bool = False
    unsplash_access_key: SecretStr = SecretStr("")


settings = Settings()
