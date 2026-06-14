"""Storage service 抽象介面。"""

from abc import ABC, abstractmethod
from pathlib import Path


class BaseStorageService(ABC):
    @abstractmethod
    async def upload(self, local_path: Path, remote_name: str) -> str:
        """上傳檔案，回傳公開 URL（需有效 48 小時以上）。"""
        ...
