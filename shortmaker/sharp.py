"""흐린 이미지 필터 — Laplacian Variance로 선명한 이미지만 골라낸다."""

import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from shortmaker.files import find_media_files


def sharpness_score(path: Path) -> float | None:
    """이미지 파일의 Laplacian Variance(선명도 점수)를 계산한다.

    Args:
        path: 이미지 파일 경로

    Returns:
        선명도 점수 (높을수록 선명), 실패 시 None
    """
    try:
        img = Image.open(path).convert("RGB")
        arr = np.array(img)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        return cv2.Laplacian(gray, cv2.CV_64F).var()
    except Exception:
        return None


def run(args) -> None:
    """선명도 필터를 실행한다."""
    src = Path(args.src)
    if not src.is_dir():
        print(f"오류: 소스 디렉토리를 찾을 수 없습니다 — {src}", file=sys.stderr)
        sys.exit(1)

    date_str = getattr(args, "date", None)
    exts = getattr(args, "ext", ["jpg", "png", "jpeg", "webp", "heic"])
    threshold = getattr(args, "threshold", 100.0)
    out_dir = Path(args.out)
    dry_run = getattr(args, "dry_run", False)

    print("=== 이미지 선명도 필터 ===")
    print(f"소스: {src}")
    print(f"임계값: {threshold}")
    print()

    files = find_media_files(src, exts=exts, date_str=date_str, recursive=False)
    if not files:
        print(f"오류: 지원 확장자({', '.join(exts)})에 해당하는 파일이 없습니다.", file=sys.stderr)
        sys.exit(1)

    passed = []
    for path in files:
        score = sharpness_score(path)
        if score is None:
            print(f"  {path.name}  score:   N/A  ✗ (읽기 실패)")
            continue
        if score >= threshold:
            marker = "✓"
            passed.append(path)
        else:
            marker = "✗ (흐림)"
        print(f"  {path.name}  score: {score:6.1f}  {marker}")

    filtered = len(files) - len(passed)
    print()
    print(f"결과: {len(files)}개 중 {len(passed)}개 통과, {filtered}개 제외")

    if dry_run:
        print("드라이런 모드 — 파일 복사 없음")
        return

    if passed:
        out_dir.mkdir(parents=True, exist_ok=True)
        for path in passed:
            shutil.copy2(path, out_dir / path.name)

    print(f"출력: {out_dir}")


def main() -> None:
    """CLI 진입점"""
    import argparse
    import argcomplete
    from datetime import datetime

    today = datetime.now().strftime("%Y%m%d")

    parser = argparse.ArgumentParser(
        prog="filter_sharp",
        description="Laplacian Variance로 흐린 이미지를 필터링합니다.",
    )
    parser.add_argument("--date", "-d", default=None,
                        help="파일명에서 매칭할 날짜 YYYYMMDD (미지정 시 전체)")
    parser.add_argument("--src", "-s", default=None,
                        help="소스 디렉토리 (필수)")
    parser.add_argument("--ext", "-e", nargs="+",
                        default=["jpg", "png", "jpeg", "webp", "heic"],
                        help="이미지 파일 확장자들 (기본: jpg png jpeg webp heic)")
    parser.add_argument("--out", "-o", default="./sharp",
                        help="출력 디렉토리 (기본: ./sharp)")
    parser.add_argument("--threshold", type=float, default=100.0,
                        help="선명도 임계값 (기본: 100.0, 낮을수록 관대)")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="복사 없이 결과만 표시")

    argcomplete.autocomplete(parser)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  작업이 중지되었습니다.\n")
        sys.exit(130)
