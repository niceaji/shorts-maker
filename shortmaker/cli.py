"""공통 CLI 인자 그룹 — argparse 인자 중복 제거"""

from pathlib import Path
from . import DEFAULT_FONT


def add_title_args(parser):
    """제목 관련 인자 추가: --title, --font, --font-color"""
    parser.add_argument("--title", default=None,
                        help="상단 제목 텍스트 (Pretendard Bold, 페이드인)")
    parser.add_argument("--font", default=None,
                        help=f"폰트 파일 경로 (기본: ./{DEFAULT_FONT})")
    parser.add_argument("--font-color", default="white",
                        help="제목 색상: 이름(white) 또는 hex(#FF5500) (기본: white)")


def add_subtitle_args(parser):
    """자막 관련 인자 추가: --subtitle, --subtitle-color"""
    parser.add_argument("--subtitle", default=None,
                        help='클립별 자막, 파이프로 구분 (예: "준비|입수!|달린다")')
    parser.add_argument("--subtitle-color", default="black",
                        help="자막 색상 (기본: black)")


def add_bgm_args(parser):
    """배경음악 관련 인자 추가: --bgm, --bgm-volume, --bgm-fade"""
    parser.add_argument("--bgm", default=None,
                        help="배경음악 파일 경로 (mp3, wav 등)")
    parser.add_argument("--bgm-volume", type=float, default=0.3,
                        help="배경음악 볼륨 (0.0~1.0, 기본: 0.3)")
    parser.add_argument("--bgm-fade", type=float, default=1.5,
                        help="배경음악 페이드인/아웃 길이 (초, 기본: 1.5)")


def add_display_args(parser):
    """화면 표시 관련 인자 추가: --fill, --zoom, --enhance/--no-enhance"""
    parser.add_argument("--fill", action="store_true", default=False,
                        help="영상을 전체 화면에 꽉 채우기 (기본: 블러 배경 + 전경 중앙)")
    parser.add_argument("--zoom", type=float, default=1.1,
                        help="전경 영상 확대 배율; 1.0=딱맞춤, 1.1=살짝 확대 (기본: 1.1)")
    enhance_group = parser.add_mutually_exclusive_group()
    enhance_group.add_argument("--enhance", dest="enhance", action="store_true",
                               default=True, help="아이폰 스타일 색보정 적용 (기본: 켜짐)")
    enhance_group.add_argument("--no-enhance", dest="enhance", action="store_false",
                               help="색보정 끄기")


def resolve_font_path(args) -> str:
    """--font 인자를 해석하여 폰트 파일 경로를 반환한다."""
    if args.font:
        return args.font
    return str(Path(__file__).parent.parent / DEFAULT_FONT)
