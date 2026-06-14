# tsunumon — 教學影片自動生成 API Server
# 多階段建置：先裝依賴，再複製程式碼（利用 Docker cache）

FROM python:3.11-slim AS base

# 系統依賴：FFmpeg + Playwright 所需的 libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python 依賴（先裝，利用 cache）
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    fastapi "uvicorn[standard]" pydantic pydantic-settings \
    anthropic Pillow httpx python-dotenv \
    playwright kokoro-onnx soundfile json-repair \
    google-genai \
    && playwright install chromium --with-deps

# Kokoro TTS 模型不打包（檔案太大）。預設 TTS_BACKEND=mock 不需 models 即可
# 完整跑 pipeline（佔位音、驗證流程）。要真語音用 kokoro，runtime 掛載 models：
#   docker run -v /path/to/models:/app/models -e TTS_BACKEND=kokoro ...
# 模型下載方式見 README「真語音 TTS（kokoro）」段。

# 專案程式碼
COPY app/ ./app/
COPY config/ ./config/
COPY assets/ ./assets/

# 建立 output 目錄
RUN mkdir -p /app/output

# 環境變數預設值（可被 docker run -e 或 .env 覆蓋）
ENV HOST=0.0.0.0 \
    PORT=8000 \
    OUTPUT_DIR=/app/output \
    FFMPEG_PATH=ffmpeg

EXPOSE 8000

# 啟動 FastAPI
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
