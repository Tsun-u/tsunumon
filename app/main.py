"""tsunumon — FastAPI entry point."""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import router
from config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

class NoCacheFilesMiddleware(BaseHTTPMiddleware):
    """防止 Cloudflare 等 CDN 快取 /files/ 下的影片，確保審查讀到最新版。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/files/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response


app = FastAPI(title="tsunumon", version="0.1.0")
app.add_middleware(NoCacheFilesMiddleware)
app.include_router(router)

# 課程知識庫：統一掛載到 /curriculum/，供 Claude web_fetch 查詢
curriculum_path = Path(__file__).resolve().parent.parent / "config" / "curriculum"
if curriculum_path.exists():
    app.mount("/curriculum", StaticFiles(directory=str(curriculum_path)), name="curriculum_knowledge")

# 靜態檔案服務：掛載 output 目錄到 /files，提供影片、字幕等下載
output_path = Path(settings.output_dir)
output_path.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(output_path)), name="files")


@app.get("/health")
async def health():
    return {"status": "ok"}
