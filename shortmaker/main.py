#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""shorts - DJI 영상/이미지에서 숏폼 영상을 생성하는 통합 CLI"""

import argparse
import sys
from pathlib import Path

import argcomplete

from shortmaker import DEFAULT_FONT
from shortmaker.cli import (
    add_audio_args,
    add_bgm_args,
    add_display_args,
    add_intro_outro_args,
    add_ratio_args,
    add_speed_args,
    add_subtitle_args,
    add_title_args,
    add_watermark_args,
)


def _build_clip_parser(subparsers):
    """영상 클립 숏폼 서브커맨드 파서를 생성한다."""
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")

    p = subparsers.add_parser(
        "clip",
        help="영상 클립에서 숏폼 생성",
        fromfile_prefix_chars="@",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  shorts clip                              # SD카드, 오늘 날짜
  shorts clip -d 20260318                  # 특정 날짜
  shorts clip -s ./clips --blur-bg         # 블러 배경 모드
  shorts clip @preset.txt                  # 프리셋 파일
  shorts clip --bgm music.mp3 --mute      # BGM만 사용
  shorts clip --title "제주" --watermark "2026.03.28" """,
    )

    # 소스 옵션
    src_group = p.add_argument_group("소스 옵션")
    src_group.add_argument(
        "--date", "-d",
        default=None,
        help="파일명에서 매칭할 날짜 YYYYMMDD (미지정 시 전체)",
    )
    src_group.add_argument(
        "--src", "-s",
        default=None,
        help="영상 클립 소스 디렉토리 (필수)",
    )
    src_group.add_argument(
        "--ext", "-e",
        nargs="+",
        default=["MP4", "MOV"],
        help="영상 파일 확장자들 (기본: MP4 MOV)",
    )

    # 출력 옵션
    out_group = p.add_argument_group("출력 옵션")
    out_group.add_argument(
        "--out", "-o",
        default=None,
        help="출력 파일 경로 (기본: ./shorts_YYYYMMDD.mp4)",
    )
    out_group.add_argument(
        "--duration", "-t",
        type=float,
        default=2.5,
        help="각 클립에서 잘라낼 길이 (초, 기본: 2.5, 0=전체)",
    )
    out_group.add_argument(
        "--shuffle",
        action="store_true",
        default=False,
        help="클립 순서 랜덤 (기본: 꺼짐)",
    )
    out_group.add_argument(
        "--transition",
        type=float,
        default=0,
        help="클립 간 크로스페이드 전환 길이 (초, 0=전환 없음, 기본: 0)",
    )
    out_group.add_argument(
        "--smart-crop",
        action="store_true",
        default=False,
        help="인물 위치 자동 감지로 크롭 오프셋 조정 (기본: 꺼짐)",
    )

    # 화면/영상 옵션
    add_display_args(p)
    add_ratio_args(p)

    # 속도/오디오 옵션
    add_speed_args(p)
    add_audio_args(p)

    # 자막/제목/워터마크 옵션
    add_title_args(p)
    add_subtitle_args(p)
    add_watermark_args(p)

    # 배경음악 옵션
    add_bgm_args(p)

    # 인트로/아웃트로 옵션
    add_intro_outro_args(p)

    return p


def _build_image_parser(subparsers):
    """이미지 숏폼 서브커맨드 파서를 생성한다."""
    from datetime import datetime

    p = subparsers.add_parser(
        "image",
        help="이미지에서 숏폼 생성",
        fromfile_prefix_chars="@",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  shorts image -s ./img                     # 기본 (랜덤 효과)
  shorts image -s ./img --fill              # 전체 채우기
  shorts image -s ./img --effect zoom_in    # 효과 지정
  shorts image -s ./img --bgm music.mp3     # BGM 추가
  shorts image @preset.txt                  # 프리셋 파일""",
    )

    # 소스 옵션
    src_group = p.add_argument_group("소스 옵션")
    src_group.add_argument(
        "--date", "-d",
        default=None,
        help="파일명에서 매칭할 날짜 YYYYMMDD (미지정 시 전체)",
    )
    src_group.add_argument(
        "--src", "-s",
        default=None,
        help="이미지 소스 디렉토리 (필수)",
    )
    src_group.add_argument(
        "--ext", "-e",
        nargs="+",
        default=["jpg", "png", "jpeg", "webp", "heic"],
        help="이미지 파일 확장자들 (기본: jpg png jpeg webp heic)",
    )

    # 출력 옵션
    out_group = p.add_argument_group("출력 옵션")
    out_group.add_argument(
        "--out", "-o",
        default="./image_shorts.mp4",
        help="출력 파일 경로 (기본: ./image_shorts.mp4)",
    )
    out_group.add_argument(
        "--duration", "-t",
        type=float,
        default=3.0,
        help="이미지당 재생 시간 (초, 기본: 3.0)",
    )
    out_group.add_argument(
        "--shuffle",
        action="store_true",
        default=False,
        help="이미지 순서 무작위 (기본: 꺼짐)",
    )

    # 효과 옵션
    fx_group = p.add_argument_group("효과 옵션")
    fx_group.add_argument(
        "--effect",
        choices=["zoom_in", "zoom_out", "pan_left", "pan_right", "random"],
        default="random",
        help="켄번즈 애니메이션 효과 종류 (기본: random)",
    )
    fx_group.add_argument(
        "--zoom-range",
        type=float,
        default=1.15,
        dest="zoom_range",
        help="켄번즈 최대 줌 배율 (기본: 1.15)",
    )
    fx_group.add_argument(
        "--transition",
        type=float,
        default=0.5,
        help="이미지 간 전환 효과 길이 (초, 0이면 전환 없음, 기본: 0.5)",
    )

    add_title_args(p)
    add_subtitle_args(p)
    add_bgm_args(p)
    add_display_args(p)
    add_ratio_args(p)
    add_speed_args(p)
    add_audio_args(p)
    add_watermark_args(p)
    add_intro_outro_args(p)

    return p


def _build_frames_parser(subparsers):
    """프레임 추출 서브커맨드 파서를 생성한다."""
    from datetime import date
    today = date.today().strftime("%Y%m%d")

    p = subparsers.add_parser(
        "frames",
        help="영상에서 랜덤 프레임 추출",
        fromfile_prefix_chars="@",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  shorts frames                          # SD카드, 오늘 날짜
  shorts frames -d 20260318              # 특정 날짜
  shorts frames -s ./clips -o ./out      # 로컬 폴더 (날짜 필터 무시)
  shorts frames --no-enhance             # 색보정 없이
  shorts frames --level                  # 수평 보정 (실험적)""",
    )

    p.add_argument(
        "--date", "-d",
        default=None,
        metavar="YYYYMMDD",
        help=f"파일명에서 매칭할 날짜 (기본: {today})",
    )
    p.add_argument(
        "--src", "-s",
        default=None,
        metavar="DIR",
        help="영상 소스 디렉토리 (필수)",
    )
    p.add_argument(
        "--out", "-o",
        default="./img",
        metavar="DIR",
        help="이미지 출력 디렉토리 (기본: ./img)",
    )
    p.add_argument(
        "--ext", "-e",
        nargs="+",
        default=["MP4", "MOV"],
        metavar="EXT",
        help="영상 파일 확장자 (기본: MP4 MOV)",
    )
    p.add_argument(
        "--enhance", action="store_true", default=True,
        help="아이폰 스타일 색보정 적용 (기본: 켜짐)",
    )
    p.add_argument(
        "--no-enhance", dest="enhance", action="store_false",
        help="색보정 끄기",
    )
    p.add_argument(
        "--level", action="store_true", default=False,
        help="수평 자동 보정 (실험적, 기본: 꺼짐)",
    )

    return p


def _build_contact_parser(subparsers):
    """컨택트 시트 서브커맨드 파서를 생성한다."""
    from datetime import datetime

    p = subparsers.add_parser(
        "contact",
        help="컨택트 시트(썸네일 그리드) 생성",
        fromfile_prefix_chars="@",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  shorts contact -s ./img                        # 기본 (4열 그리드)
  shorts contact -s ./img --cols 3               # 3열 그리드
  shorts contact -s ./img --title "제주 수영"    # 제목 추가
  shorts contact -s ./img --label                # 파일명 레이블 표시
  shorts contact @preset.txt                     # 프리셋 파일""",
    )

    # 기본 옵션
    basic = p.add_argument_group("기본 옵션")
    basic.add_argument("--date", "-d",
                       default=None,
                       help="파일명에서 매칭할 날짜 YYYYMMDD (미지정 시 전체)")
    basic.add_argument("--src", "-s", default=None, metavar="디렉토리",
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
    thumb = p.add_argument_group("썸네일 설정")
    thumb.add_argument("--thumb-width", type=int, default=480, metavar="픽셀",
                       help="썸네일 가로 픽셀 (기본: 480, 세로는 16:9 비율 자동 계산)")
    thumb.add_argument("--padding", type=int, default=8, metavar="픽셀",
                       help="썸네일 간격 픽셀 (기본: 8)")
    thumb.add_argument("--bg-color", default="black", metavar="색상",
                       help="배경 색상: 이름(black) 또는 hex(#1A1A1A) (기본: black)")
    thumb.add_argument("--label", action="store_true", default=False,
                       help="각 썸네일 아래 파일명 레이블 표시 (기본: 꺼짐)")

    # 텍스트
    text = p.add_argument_group("텍스트")
    text.add_argument("--title", default=None, metavar="텍스트",
                      help="상단 제목 텍스트 (생략 시 제목 없음)")
    text.add_argument("--font", default=None, metavar="파일",
                      help=f"폰트 파일 경로 (기본: ./{DEFAULT_FONT})")
    text.add_argument("--font-color", default="white", metavar="색상",
                      help="텍스트 색상: 이름(white) 또는 hex(#FFFFFF) (기본: white)")

    return p


def _build_sharp_parser(subparsers):
    """흐린 이미지 필터 서브커맨드 파서를 생성한다."""
    from datetime import datetime

    p = subparsers.add_parser(
        "sharp",
        help="흐린 이미지 필터링 (선명한 것만 남기기)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  shorts sharp -s ./img                    # 기본 (임계값 100)
  shorts sharp -s ./img --threshold 50     # 관대한 필터
  shorts sharp -s ./img --dry-run          # 복사 없이 결과만 확인
  shorts sharp -d 20260401                 # 특정 날짜""",
    )

    p.add_argument("--date", "-d",
                   default=None,
                   help="파일명에서 매칭할 날짜 YYYYMMDD (미지정 시 전체)")
    p.add_argument("--src", "-s",
                   default=None,
                   help="소스 디렉토리 (필수)")
    p.add_argument("--ext", "-e",
                   nargs="+",
                   default=["jpg", "png", "jpeg", "webp", "heic"],
                   help="이미지 파일 확장자들 (기본: jpg png jpeg webp heic)")
    p.add_argument("--out", "-o",
                   default="./sharp",
                   help="출력 디렉토리 (기본: ./sharp)")
    p.add_argument("--threshold",
                   type=float,
                   default=100.0,
                   help="선명도 임계값 (기본: 100.0, 낮을수록 관대)")
    p.add_argument("--dry-run",
                   action="store_true",
                   default=False,
                   help="복사 없이 결과만 표시")

    return p


def _build_highlight_parser(subparsers):
    """하이라이트 릴 서브커맨드 파서를 생성한다."""
    p = subparsers.add_parser(
        "highlight",
        help="단일 영상에서 하이라이트 릴 생성",
        fromfile_prefix_chars="@",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  shorts highlight -i video.mp4                    # 기본 (1초씩, 원본 비율)
  shorts highlight -i video.mp4 -t 2.0 -n 10      # 2초씩 10개
  shorts highlight -i video.mp4 --ratio 9:16       # 쇼츠 비율
  shorts highlight -i video.mp4 --bgm music.mp3    # BGM 추가
  shorts highlight -i video.mp4 --shuffle          # 랜덤 순서""",
    )

    # 소스/출력
    src = p.add_argument_group("소스/출력")
    src.add_argument("--input", "-i", default=None,
                     help="소스 영상 파일 경로 (필수)")
    src.add_argument("--out", "-o", default="./highlight.mp4",
                     help="출력 파일 경로 (기본: ./highlight.mp4)")

    # 세그먼트 설정
    seg = p.add_argument_group("세그먼트 설정")
    seg.add_argument("--duration", "-t", type=float, default=1.0,
                     help="각 세그먼트 길이 (초, 기본: 1.0)")
    seg.add_argument("--count", "-n", type=int, default=None,
                     help="세그먼트 개수 (기본: 자동)")
    seg.add_argument("--shuffle", action="store_true", default=False,
                     help="세그먼트 순서 랜덤 (기본: 꺼짐)")
    seg.add_argument("--transition", type=float, default=0,
                     help="세그먼트 간 크로스페이드 길이 (초, 기본: 0)")

    # 스마트 분석
    smart = p.add_argument_group("스마트 분석")
    smart.add_argument("--smart", action="store_true", default=False,
                       help="움직임+소리 분석으로 하이라이트 자동 선택 (기본: 균등 간격)")
    smart.add_argument("--interval", type=float, default=0.5,
                       help="분석 샘플링 간격 (초, 클수록 빠름, 기본: 0.5)")
    smart.add_argument("--no-audio-score", dest="no_audio_score", action="store_true",
                       default=False,
                       help="오디오 무시, 움직임만으로 분석 (바람소리 등 환경 소음이 심할 때)")

    # 화면/영상 옵션
    add_ratio_args(p, default="original")
    add_display_args(p)
    add_speed_args(p)
    add_audio_args(p)
    add_title_args(p)
    add_bgm_args(p)

    return p


class _FriendlyParser(argparse.ArgumentParser):
    """인자 오류 시 친절한 메시지를 출력하는 파서"""

    def error(self, message):
        print(f"\n  오류: {message}\n", file=sys.stderr)
        self.print_usage(sys.stderr)
        sys.exit(2)


def main():
    """통합 CLI 진입점"""
    try:
        _run()
    except KeyboardInterrupt:
        print("\n\n  작업이 중지되었습니다.\n")
        sys.exit(130)


def _run():
    parser = _FriendlyParser(
        prog="shorts",
        description="DJI 영상/이미지에서 숏폼 영상을 생성합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""서브커맨드:
  clip     영상 클립에서 숏폼 생성
  image    이미지에서 숏폼 생성
  frames   영상에서 랜덤 프레임 추출
  contact  컨택트 시트(썸네일 그리드) 생성
  sharp    흐린 이미지 필터링 (선명한 것만 남기기)

예시:
  shorts clip                    # 영상 숏폼 (기본)
  shorts image -s ./img          # 이미지 숏폼
  shorts frames -d 20260318      # 프레임 추출
  shorts contact -s ./img        # 컨택시트
  shorts sharp -s ./img          # 선명도 필터
  shorts highlight -i video.mp4  # 하이라이트 릴
  shorts clip @preset.txt        # 프리셋 파일""",
    )
    subparsers = parser.add_subparsers(dest="command")

    sub = {
        "clip": _build_clip_parser(subparsers),
        "image": _build_image_parser(subparsers),
        "frames": _build_frames_parser(subparsers),
        "contact": _build_contact_parser(subparsers),
        "sharp": _build_sharp_parser(subparsers),
        "highlight": _build_highlight_parser(subparsers),
    }

    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    # 경로 인자 틸드(~) 확장
    for attr in ("src", "out", "bgm", "intro", "outro", "watermark", "font", "input"):
        val = getattr(args, attr, None)
        if val:
            setattr(args, attr, str(Path(val).expanduser()))

    if args.command is None:
        parser.print_help()
        return

    # 소스 미지정 시 해당 서브커맨드 help 출력
    src_attr = "input" if args.command == "highlight" else "src"
    if getattr(args, src_attr, None) is None:
        sub[args.command].print_help()
        return

    # 입력 파일/디렉토리 존재 여부 조기 검증
    _FILE_ARGS = ("bgm", "intro", "outro", "font", "input")
    _DIR_ARGS = ("src",)
    errors = []
    for attr in _FILE_ARGS:
        val = getattr(args, attr, None)
        if val and not Path(val).exists():
            errors.append(f"파일을 찾을 수 없습니다: {val}")
    for attr in _DIR_ARGS:
        val = getattr(args, attr, None)
        if val and not Path(val).is_dir():
            errors.append(f"디렉토리를 찾을 수 없습니다: {val}")
    if errors:
        for e in errors:
            print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)

    if args.command == "clip":
        from shortmaker.clip import run
        run(args)
    elif args.command == "image":
        from shortmaker.image import run
        run(args)
    elif args.command == "frames":
        from shortmaker.frames import run
        run(args)
    elif args.command == "contact":
        from shortmaker.contact import run
        run(args)
    elif args.command == "sharp":
        from shortmaker.sharp import run
        run(args)
    elif args.command == "highlight":
        from shortmaker.highlight import run
        run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
