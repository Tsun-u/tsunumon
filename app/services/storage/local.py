"""Local storage — 透過 FastAPI 靜態檔案服務提供下載 URL。"""

from pathlib import Path

from app.services.storage.base import BaseStorageService


class LocalStorageService(BaseStorageService):
    def __init__(self, base_url: str = ""):
        self._base_url = base_url

    async def upload(self, local_path: Path, remote_name: str) -> str:
        # remote_name 格式: "{request_id}/output.mp4"
        # 對應 FastAPI 靜態掛載 /files → output/
        return f"{self._base_url}/files/{remote_name}"
