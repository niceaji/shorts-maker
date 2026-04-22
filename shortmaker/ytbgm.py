"""YouTube URL을 BGM으로 쓸 수 있게 yt-dlp로 일부 구간만 다운로드."""

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

_YT_PATTERNS = [
    r"^https?://(?:www\.)?youtube\.com/",
    r"^https?://(?:m\.)?youtube\.com/",
    r"^https?://youtu\.be/",
    r"^https?://(?:www\.)?youtube-nocookie\.com/",
]


def is_youtube_url(s: str) -> bool:
    """문자열이 YouTube URL인지 검사한다."""
    if not s:
        return False
    return any(re.match(p, s) for p in _YT_PATTERNS)


def _cache_dir() -> Path:
    d = Path.home() / ".cache" / "shorts-maker" / "ytbgm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_youtube_bgm(url: str, duration: float = 50.0, start: float = 0.0) -> Path:
    """YouTube URL에서 오디오를 잘라 BGM용 m4a 파일 경로를 반환한다.

    start초부터 duration초만 추출한다. 동일 URL/구간은 캐시를 재사용한다.
    """
    if not shutil.which("yt-dlp"):
        print("오류: yt-dlp가 설치되어 있지 않습니다. (brew install yt-dlp)", file=sys.stderr)
        sys.exit(1)

    key = hashlib.sha1(f"{url}|{start}|{duration}".encode()).hexdigest()[:16]
    out_path = _cache_dir() / f"{key}.m4a"
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    end = start + duration
    section = f"*{start:g}-{end:g}"
    template = str(out_path.with_suffix(""))

    cmd = [
        "yt-dlp",
        "-x", "--audio-format", "m4a",
        "--download-sections", section,
        "-q", "--no-warnings",
        "-o", f"{template}.%(ext)s",
        url,
    ]
    print(f"  YouTube BGM 다운로드: {url}  ({start:g}~{end:g}초)")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        print("오류: YouTube 다운로드에 실패했습니다.", file=sys.stderr)
        stderr = (result.stderr or result.stdout or "").strip()
        for line in stderr.splitlines()[-5:]:
            print(f"  {line}", file=sys.stderr)
        sys.exit(1)
    return out_path
