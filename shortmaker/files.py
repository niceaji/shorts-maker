"""파일 검색 유틸리티 — 날짜/확장자 기반 미디어 파일 탐색"""

from pathlib import Path


def find_media_files(
    src: Path,
    exts: list[str],
    date_str: str | None = None,
    recursive: bool = True,
) -> list[Path]:
    """확장자와 날짜 문자열로 미디어 파일을 검색한다.

    Args:
        src: 검색 디렉토리
        exts: 확장자 목록 (예: ["MP4", "MOV"])
        date_str: 파일명에 포함되어야 할 날짜 문자열 (None이면 전체)
        recursive: 하위 디렉토리 포함 여부
    """
    ext_set = {e.lower().lstrip(".") for e in exts}
    iterator = src.rglob("*") if recursive else src.iterdir()
    clips = []
    for p in iterator:
        if not p.is_file():
            continue
        if p.suffix.lower().lstrip(".") not in ext_set:
            continue
        if date_str is not None and date_str not in p.name:
            continue
        clips.append(p)
    return sorted(clips)


def unique_path(path: Path) -> Path:
    """파일이 이미 존재하면 _1, _2, ... 접미사를 붙여 고유한 경로를 반환한다."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1
