"""API 層的 Request / Response 模型。"""

from typing import List, Optional

from pydantic import BaseModel


class GenerateRequest(BaseModel):
    request_id: str
    course_requirement: str
    student_persona: str


class GenerateResponse(BaseModel):
    video_url: str
    subtitle_url: Optional[str] = None
    supplementary_url: Optional[List[str]] = None
