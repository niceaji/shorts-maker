"""공통 CLI 인자 그룹 — argparse 인자 중복 제거"""

import argparse
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
    parser.add_argument("--bgm-start", type=float, default=0.0,
                        help="BGM 시작 지점 (초, 기본: 0.0)")
    parser.add_argument("--no-bgm-loop", dest="bgm_loop", action="store_false",
                        default=True,
                        help="BGM 반복 끄기 (기본: 영상 길이만큼 반복)")


def add_display_args(parser):
    """화면 표시 관련 인자 추가: --bg, --zoom, --enhance/--no-enhance"""
    parser.add_argument("--bg", default="fill",
                        choices=["fill", "blur", "letterbox"],
                        help="배경 모드: fill=꽉채우기, blur=블러배경, letterbox=검정배경 (기본: fill)")
    parser.add_argument("--zoom", type=float, default=1.0,
                        help="전경 영상 확대 배율; 1.0=딱맞춤, 1.1=살짝 확대 (기본: 1.0)")
    enhance_group = parser.add_mutually_exclusive_group()
    enhance_group.add_argument("--enhance", dest="enhance", action="store_true",
                               default=True, help="아이폰 스타일 색보정 적용 (기본: 켜짐)")
    enhance_group.add_argument("--no-enhance", dest="enhance", action="store_false",
                               help="색보정 끄기")


def add_ratio_args(parser, default="original"):
    """영상 비율 인자: --ratio"""
    parser.add_argument("--ratio", default=default,
                        choices=["original", "9:16", "16:9", "1:1"],
                        help=f"영상 비율 (기본: {default})")


def add_speed_args(parser):
    """속도 관련 인자: --speed"""
    parser.add_argument("--speed", type=float, default=1.0,
                        help="재생 속도 배율 (0.5=슬로모션, 2.0=2배속, 기본: 1.0)")


def add_audio_args(parser):
    """오디오 관련 인자: --mute"""
    parser.add_argument("--mute", action="store_true", default=False,
                        help="원본 오디오 제거 (BGM만 사용, 기본: 꺼짐)")


def add_intro_outro_args(parser):
    """인트로/아웃트로 관련 인자"""
    parser.add_argument("--intro", default=None,
                        help="인트로 영상/이미지 파일 경로 (영상 앞에 추가)")
    parser.add_argument("--outro", default=None,
                        help="아웃트로 영상/이미지 파일 경로 (영상 뒤에 추가)")


def add_watermark_args(parser):
    """워터마크 관련 인자"""
    parser.add_argument("--watermark", default=None,
                        help="워터마크 텍스트 (예: 날짜, 위치 등)")
    parser.add_argument("--watermark-position", default="bottom_right",
                        choices=["top_left", "top_right", "bottom_left", "bottom_right"],
                        help="워터마크 위치 (기본: bottom_right)")
    parser.add_argument("--watermark-color", default="white",
                        help="워터마크 색상 (기본: white)")
    parser.add_argument("--watermark-opacity", type=float, default=0.7,
                        help="워터마크 투명도 (0.0~1.0, 기본: 0.7)")


def resolve_font_path(args) -> str:
    """--font 인자를 해석하여 폰트 파일 경로를 반환한다."""
    if args.font:
        return args.font
    return str(Path(__file__).parent.parent / DEFAULT_FONT)
