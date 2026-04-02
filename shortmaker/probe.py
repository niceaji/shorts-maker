"""ffprobe 래퍼 — 영상 길이, 오디오 유무 감지"""

import json
import subprocess
import sys
from pathlib import Path


def get_video_info(filepath: Path) -> dict | None:
    """ffprobe로 영상 정보를 반환한다.

    반환값: {"duration": float, "has_audio": bool} 또는 None (실패 시)
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(filepath),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None

    duration = None
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, ValueError):
        for stream in data.get("streams", []):
            if "duration" in stream:
                try:
                    duration = float(stream["duration"])
                    break
                except ValueError:
                    pass

    if duration is None or duration <= 0:
        return None

    has_audio = any(
        s.get("codec_type") == "audio" for s in data.get("streams", [])
    )

    width = None
    height = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = stream.get("width")
            height = stream.get("height")
            if width is not None:
                width = int(width)
            if height is not None:
                height = int(height)
            break

    return {"duration": duration, "has_audio": has_audio, "width": width, "height": height}
