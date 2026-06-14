"""Video Composer — 用 FFmpeg 把投影片圖片 + 音訊合成為 MP4。"""

import asyncio
import logging
from pathlib import Path
from typing import List

from app.models.domain import AudioSegment, SlideImage, VideoResult

logger = logging.getLogger(__name__)


class VideoComposer:
    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path

    async def compose(
        self,
        slides: List[SlideImage],
        audio_segments: List[AudioSegment],
        output_path: Path,
    ) -> VideoResult:
        """將投影片 + 音訊合成為一支 MP4 影片。

        策略：
        1. 為每個 segment 建立一段「靜態圖片 + 音訊」的短片
        2. 用 FFmpeg concat 把所有短片接起來
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        work_dir = output_path.parent / "segments"
        work_dir.mkdir(parents=True, exist_ok=True)

        segment_files = []
        total_duration = 0.0

        for slide, audio in zip(slides, audio_segments):
            seg_output = work_dir / f"seg_{slide.segment_id:03d}.mp4"
            segment_files.append(seg_output)
            total_duration += audio.duration_sec

            # animated slide → 用錄製的動畫影片；靜態 → 用圖片 loop
            if slide.video_path:
                # 動畫影片 + 音訊合併
                cmd = [
                    self.ffmpeg, "-y",
                    "-i", slide.video_path,
                    "-i", audio.audio_path,
                    "-c:v", "libx264",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-pix_fmt", "yuv420p",
                    "-shortest",
                    "-preset", "ultrafast",
                    str(seg_output),
                ]
            else:
                # 靜態圖片 + 音訊合成
                cmd = [
                    self.ffmpeg, "-y",
                    "-loop", "1",
                    "-i", slide.image_path,
                    "-i", audio.audio_path,
                    "-c:v", "libx264",
                    "-tune", "stillimage",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-pix_fmt", "yuv420p",
                    "-shortest",
                    "-preset", "ultrafast",
                    str(seg_output),
                ]

            logger.info(f"Encoding segment {slide.segment_id}...")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"FFmpeg failed for segment {slide.segment_id}: "
                    f"{stderr.decode(errors='replace')}"
                )

        # 建立 concat list
        concat_list = work_dir / "concat.txt"
        with open(concat_list, "w") as f:
            for seg_file in segment_files:
                f.write(f"file '{seg_file.resolve().as_posix()}'\n")

        # 用 concat demuxer 合併
        concat_cmd = [
            self.ffmpeg, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(output_path),
        ]

        logger.info("Concatenating all segments...")
        proc = await asyncio.create_subprocess_exec(
            *concat_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg concat failed: {stderr.decode(errors='replace')}")

        logger.info(f"Video composed: {output_path} ({total_duration:.1f}s)")

        return VideoResult(
            video_path=str(output_path),
            duration_sec=total_duration,
        )
