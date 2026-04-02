"""컨택트 시트(그리드 이미지) 생성 스크립트 — 이미지 또는 영상 클립 디렉토리에서 썸네일 그리드를 만든다."""

import argcomplete
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from shortmaker import DEFAULT_FONT
from shortmaker.color import parse_rgba
from shortmaker.files import find_media_files

# 영상 파일 확장자 목록
VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "mts", "m2ts"}


def extract_video_frame(video_path: Path, timestamp: float = 1.0) -> Image.Image | None:
    """ffmpeg으로 영상에서 지정된 시각의 프레임을 추출한다.

    Args:
        video_path: 영상 파일 경로
        timestamp: 추출할 시각 (초, 기본: 1.0)

    Returns:
        PIL Image 또는 None (실패 시)
    """
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name

    cmd = [
        "ffmpeg",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        "-y",
        tmp_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            return None
        img = Image.open(tmp_path).copy()
        Path(tmp_path).unlink(missing_ok=True)
        return img
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        return None


def load_image(path: Path) -> Image.Image | None:
    """이미지 또는 영상 파일을 PIL Image로 불러온다.

    영상 파일인 경우 ffmpeg으로 1초 지점 프레임을 추출한다.

    Args:
        path: 파일 경로

    Returns:
        PIL Image 또는 None (실패 시)
    """
    ext = path.suffix.lower().lstrip(".")
    if ext in VIDEO_EXTS:
        return extract_video_frame(path, timestamp=1.0)
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return None


def make_thumbnail(img: Image.Image, thumb_width: int, thumb_height: int) -> Image.Image:
    """이미지를 지정 크기로 크롭 리사이즈한다 (가운데 크롭, 비율 유지).

    Args:
        img: 원본 PIL Image
        thumb_width: 썸네일 가로 픽셀
        thumb_height: 썸네일 세로 픽셀

    Returns:
        리사이즈/크롭된 PIL Image
    """
    src_w, src_h = img.size
    target_ratio = thumb_width / thumb_height
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        # 원본이 더 넓음 → 좌우 크롭
        new_h = src_h
        new_w = int(src_h * target_ratio)
    else:
        # 원본이 더 높음 → 상하 크롭
        new_w = src_w
        new_h = int(src_w / target_ratio)

    left = (src_w - new_w) // 2
    top = (src_h - new_h) // 2
    img = img.crop((left, top, left + new_w, top + new_h))
    return img.resize((thumb_width, thumb_height), Image.LANCZOS)


def load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """폰트 파일을 불러온다. 실패 시 기본 폰트를 반환한다.

    Args:
        font_path: 폰트 파일 경로
        size: 폰트 크기 (픽셀)

    Returns:
        PIL 폰트 객체
    """
    try:
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()


def build_contact_sheet(args: argparse.Namespace) -> None:
    """컨택트 시트를 생성한다.

    Args:
        args: argparse 파싱 결과
    """
    src = Path(args.src)
    if not src.is_dir():
        print(f"오류: 소스 디렉토리를 찾을 수 없습니다 — {src}", file=sys.stderr)
        sys.exit(1)

    # 파일 검색
    print(f"파일 검색 중: {src}")
    src_explicitly_set = str(src) != "/Volumes/SD_Card/DCIM"
    date_str = None if src_explicitly_set else getattr(args, "date", None)
    files = find_media_files(src, exts=args.ext, date_str=date_str, recursive=False)
    if not files:
        print(f"오류: 지원 확장자({', '.join(args.ext)})에 해당하는 파일이 없습니다.", file=sys.stderr)
        sys.exit(1)
    print(f"  {len(files)}개 파일 발견")

    # 색상 파싱
    bg_rgba = parse_rgba(args.bg_color, default=(0, 0, 0, 255))
    bg_color = bg_rgba[:3]
    font_rgba = parse_rgba(args.font_color, default=(255, 255, 255, 255))
    font_color = font_rgba[:3]

    # 폰트 경로 결정
    font_path = args.font or str(Path(__file__).parent.parent / DEFAULT_FONT)

    # 썸네일 크기 (16:9 고정)
    thumb_w = args.thumb_width
    thumb_h = int(thumb_w * 9 / 16)

    cols = args.cols
    padding = args.padding

    # 썸네일 생성
    print("썸네일 생성 중...")
    thumbnails: list[tuple[Path, Image.Image]] = []
    for i, path in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {path.name}", end="\r")
        img = load_image(path)
        if img is None:
            print(f"\n  경고: {path.name} 불러오기 실패, 건너뜁니다.")
            continue
        thumb = make_thumbnail(img, thumb_w, thumb_h)
        thumbnails.append((path, thumb))
    print()  # 줄바꿈

    if not thumbnails:
        print("오류: 유효한 썸네일을 생성할 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    n = len(thumbnails)
    rows = (n + cols - 1) // cols

    # 레이블 높이 계산
    label_font_size = max(12, thumb_w // 28)
    label_font = load_font(font_path, label_font_size)
    label_height = (label_font_size + padding) if args.label else 0

    # 제목 영역 높이 계산
    title_height = 0
    title_font = None
    if args.title:
        title_font_size = max(24, thumb_w // 10)
        title_font = load_font(font_path, title_font_size)
        title_height = title_font_size + padding * 3

    # 캔버스 크기 계산
    canvas_w = cols * thumb_w + (cols + 1) * padding
    canvas_h = (title_height
                + rows * thumb_h
                + rows * label_height
                + (rows + 1) * padding)

    # 캔버스 생성
    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(canvas)

    # 제목 그리기
    y_offset = padding
    if args.title and title_font:
        bbox = draw.textbbox((0, 0), args.title, font=title_font)
        text_w = bbox[2] - bbox[0]
        text_x = (canvas_w - text_w) // 2
        draw.text((text_x, y_offset), args.title, font=title_font, fill=font_color)
        y_offset += title_height

    # 썸네일 배치
    for idx, (path, thumb) in enumerate(thumbnails):
        row = idx // cols
        col = idx % cols
        x = padding + col * (thumb_w + padding)
        y = y_offset + row * (thumb_h + label_height + padding)
        canvas.paste(thumb, (x, y))

        # 파일명 레이블
        if args.label:
            label_text = path.stem
            label_y = y + thumb_h + 2
            draw.text((x, label_y), label_text, font=label_font, fill=font_color)

    # 저장
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out_path), "JPEG", quality=92)

    print(f"완료: {out_path} ({canvas_w}×{canvas_h}px, 썸네일 {n}개)")


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자 파서를 생성한다."""
    parser = argparse.ArgumentParser(
        prog="make_contact_sheet",
        description="이미지/영상 파일에서 컨택트 시트(썸네일 그리드)를 생성합니다.",
        fromfile_prefix_chars="@",
    )

    # 기본 옵션
    basic = parser.add_argument_group("기본 옵션")
    basic.add_argument("--src", "-s", required=True, metavar="디렉토리",
                       help="소스 디렉토리 경로 (필수)")
    basic.add_argument("--out", "-o", default="./contact_sheet.jpg", metavar="파일",
                       help="출력 파일 경로 (기본: ./contact_sheet.jpg)")
    basic.add_argument("--ext", "-e", nargs="+",
                       default=["jpg", "png", "jpeg", "webp", "heic"],
                       metavar="확장자",
                       help="대상 파일 확장자 (기본: jpg png jpeg webp heic)")
    basic.add_argument("--cols", type=int, default=4, metavar="열수",
                       help="그리드 열 수 (기본: 4)")

    # 썸네일 설정
    thumb = parser.add_argument_group("썸네일 설정")
    thumb.add_argument("--thumb-width", type=int, default=480, metavar="픽셀",
                       help="썸네일 가로 픽셀 (기본: 480, 세로는 16:9 비율 자동 계산)")
    thumb.add_argument("--padding", type=int, default=8, metavar="픽셀",
                       help="썸네일 간격 픽셀 (기본: 8)")
    thumb.add_argument("--bg-color", default="black", metavar="색상",
                       help="배경 색상: 이름(black) 또는 hex(#1A1A1A) (기본: black)")
    thumb.add_argument("--label", action="store_true", default=False,
                       help="각 썸네일 아래 파일명 레이블 표시 (기본: 꺼짐)")

    # 텍스트
    text = parser.add_argument_group("텍스트")
    text.add_argument("--title", default=None, metavar="텍스트",
                      help="상단 제목 텍스트 (생략 시 제목 없음)")
    text.add_argument("--font", default=None, metavar="파일",
                      help=f"폰트 파일 경로 (기본: ./{DEFAULT_FONT})")
    text.add_argument("--font-color", default="white", metavar="색상",
                      help="텍스트 색상: 이름(white) 또는 hex(#FFFFFF) (기본: white)")

    return parser


def run(args) -> None:
    """실제 작업 수행 (서브커맨드에서도 호출됨)"""
    build_contact_sheet(args)


def main() -> None:
    """CLI 진입점 — 인자를 파싱하고 run()을 호출한다."""
    parser = build_parser()
    argcomplete.autocomplete(parser)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
