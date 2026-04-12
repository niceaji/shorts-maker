#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
make_highlight.py - 단일 영상에서 균등 간격 하이라이트 클립 추출

긴 영상에서 N개의 구간을 균등한 간격으로 자르고, 하나로 합쳐
하이라이트 릴을 생성합니다.

사용법:
    python3 make_highlight.py -i input.mp4
    python3 make_highlight.py -i input.mp4 -t 2.0 -n 10
    python3 make_highlight.py -i input.mp4 --ratio 9:16 --bgm music.mp3
    python3 make_highlight.py -i input.mp4 --shuffle --transition 0.5
"""

import argparse
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from shortmaker.files import unique_path
from shortmaker.probe import get_video_info
from shortmaker.ffmpeg import (
    ENCODER_ARGS, ENCODER_VIDEO, ENCODER_AUDIO,
    ENHANCE_FILTER, build_speed_filter,
    concat_segments, concat_xfade,
)
from shortmaker.cli import (
    add_bgm_args,
    add_ratio_args,
    add_title_args,
    resolve_font_path,
)
from shortmaker.overlay import create_title_overlay
from shortmaker.score import score_segments, pick_top_segments


def _build_ratio_filter(ratio, src_w, src_h, enhance, bg="fill", zoom=1.0):
    """비율에 따른 비디오 필터를 생성한다.

    bg="fill": 전체 화면 꽉 채우기 (크롭)
    bg="blur": 블러 배경 위에 원본 영상 중앙 배치
    bg="letterbox": 검정 배경 위에 원본 영상 중앙 배치
    original: 항상 원본 비율 유지

    반환값: (filter_complex_string, target_w, target_h, is_complex)
    is_complex=True면 filter_complex에 여러 스트림이 포함됨
    """
    enhance_chain = f",{ENHANCE_FILTER}" if enhance else ""

    if ratio == "original":
        out_w = src_w if src_w % 2 == 0 else src_w - 1
        out_h = src_h if src_h % 2 == 0 else src_h - 1
        filt = f"scale={out_w}:{out_h}{enhance_chain}"
        return filt, out_w, out_h, False

    # 비율별 크롭/스케일 설정
    ratio_map = {
        "9:16": ("crop=ih*9/16:ih:(iw-ih*9/16)/2:0", 1080, 1920),
        "16:9": ("crop=iw:iw*9/16:0:(ih-iw*9/16)/2", 1920, 1080),
        "1:1":  ("crop=min(iw\\,ih):min(iw\\,ih):(iw-min(iw\\,ih))/2:(ih-min(iw\\,ih))/2", 1080, 1080),
    }
    crop_expr, out_w, out_h = ratio_map[ratio]

    if bg == "fill":
        filt = f"{crop_expr},scale={out_w}:{out_h}{enhance_chain}"
        return filt, out_w, out_h, False

    if bg == "letterbox":
        filt = (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:black"
            f"{enhance_chain}"
        )
        return filt, out_w, out_h, False

    # blur: 블러 배경 + 전경 중앙 배치
    fg_w = f"trunc({zoom}*{out_w}/2)*2"
    fg_h = f"trunc({zoom}*{out_w}/iw*ih/2)*2"

    bg_filter = (
        f"[0:v]{crop_expr},"
        f"scale={out_w}:{out_h},gblur=sigma=40"
        f"{enhance_chain}[bg]"
    )
    fg = (
        f"[0:v]scale={fg_w}:{fg_h}"
        f"{enhance_chain}[fg]"
    )
    overlay = "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
    filt = f"{bg_filter};{fg};{overlay}"
    return filt, out_w, out_h, True


def _extract_segment(input_path, start, duration, out_path, vf, has_audio,
                     speed=1.0, mute=False):
    """영상에서 구간을 추출하고 필터를 적용하여 세그먼트 파일로 저장한다."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(input_path),
    ]

    # 오디오가 없거나 음소거 모드이면 무음 오디오 생성
    if not has_audio or mute:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        audio_input = "1:a:0"
    else:
        audio_input = "0:a:0"

    map_v = "[v]"

    # 속도 필터 적용
    speed_filter = build_speed_filter(speed)
    cmd_extra = []
    if speed_filter:
        video_speed, audio_speed = speed_filter.split(";", 1)
        # [v] 레이블 앞에 속도 필터 삽입
        vf = vf.replace("[v]", "[v_pre]") + f";[v_pre]{video_speed}[v]"
        cmd_extra = ["-af", audio_speed]

    cmd += [
        "-t", str(duration),
        "-filter_complex", vf,
        "-map", map_v, "-map", audio_input,
    ] + cmd_extra + ENCODER_ARGS + [
        "-shortest",
        str(out_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.returncode == 0, result.stderr


def _calc_start_times(video_duration, seg_duration, count):
    """영상 전체에 걸쳐 균등 간격의 시작 시간 목록을 계산한다."""
    # 마지막 세그먼트가 영상 끝을 넘지 않도록 유효 구간 설정
    max_start = video_duration - seg_duration
    if max_start <= 0:
        return [0.0]

    if count == 1:
        return [max_start / 2]

    # 균등 간격: 0 ~ max_start 범위에서 count개 균등 배치
    step = max_start / (count - 1)
    return [step * i for i in range(count)]


def run(args):
    """실제 작업 수행 (서브커맨드에서도 호출됨)"""
    input_path = Path(args.input).expanduser()
    if not input_path.exists():
        print(f"오류: 입력 파일을 찾을 수 없습니다: {input_path}")
        sys.exit(1)

    output_path = unique_path(Path(args.out).expanduser())

    # 영상 정보 조회
    info = get_video_info(input_path)
    if info is None:
        print(f"오류: 영상 정보를 읽을 수 없습니다: {input_path}")
        sys.exit(1)

    video_duration = info["duration"]
    has_audio = info["has_audio"]
    src_w = info["width"] or 1920
    src_h = info["height"] or 1080

    # 세그먼트 수 계산
    seg_duration = args.duration
    if args.count is not None:
        count = max(1, args.count)
    else:
        # 자동: 대략 3초마다 1개 (최소 2개)
        count = max(2, int(video_duration / (seg_duration * 3)))

    # 세그먼트 수가 영상 길이를 초과하지 않도록 조정
    max_possible = max(1, int(video_duration / seg_duration))
    count = min(count, max_possible)

    # 비율 필터 생성
    bg = getattr(args, "bg", "fill")
    zoom = getattr(args, "zoom", 1.0)
    vf_base, target_w, target_h, is_complex = _build_ratio_filter(
        args.ratio, src_w, src_h, args.enhance, bg=bg, zoom=zoom)
    # is_complex=True면 이미 [v] 출력을 포함, 아니면 감싸줌
    vf = vf_base if is_complex else f"[0:v]{vf_base}[v]"

    # 시작 시간 계산
    smart = getattr(args, "smart", False)
    if smart:
        print("스마트 분석 모드:")
        analysis_interval = getattr(args, "interval", 0.5)
        no_audio = getattr(args, "no_audio_score", False)
        candidates = score_segments(input_path, seg_duration, video_duration,
                                    interval=analysis_interval, no_audio=no_audio)
        top = pick_top_segments(candidates, count, seg_duration)
        start_times = [t for t, _ in top]
        smart_scores = {t: s for t, s in top}
        print(f"  {len(candidates)}개 후보 중 상위 {len(start_times)}개 선택")
        print()
    else:
        start_times = _calc_start_times(video_duration, seg_duration, count)
        smart_scores = None

    # 비율 표시 문자열
    if args.ratio == "original":
        ratio_label = f"original ({src_w}x{src_h})"
    else:
        ratio_label = f"{args.ratio} ({target_w}x{target_h})"

    print("=== 하이라이트 영상 생성기 ===")
    print(f"소스: {input_path.name} (길이: {video_duration:.1f}초)")
    print(f"세그먼트: {seg_duration:.1f}초 × {len(start_times)}개")
    print(f"비율: {ratio_label}")
    if args.speed != 1.0:
        print(f"속도: {args.speed}x")
    if args.mute:
        print("음소거: 켜짐")
    if args.shuffle:
        print("셔플: 켜짐")
    if args.transition > 0:
        print(f"전환 효과: {args.transition}초")
    print()

    with tempfile.TemporaryDirectory(prefix="make_highlight_") as tmp_dir:
        segment_files = []
        segment_durations = []

        indices = list(range(len(start_times)))
        if args.shuffle:
            random.shuffle(indices)

        ordered_starts = [start_times[i] for i in indices]

        for idx, start in enumerate(ordered_starts):
            seg_num = idx + 1
            end = start + seg_duration
            time_label = f"{int(start // 60)}:{start % 60:05.2f} ~ {int(end // 60)}:{end % 60:05.2f}"

            seg_path = Path(tmp_dir) / f"seg_{idx:04d}.mp4"
            ok, err = _extract_segment(
                input_path, start, seg_duration, seg_path, vf, has_audio,
                speed=args.speed,
                mute=args.mute,
            )

            if ok:
                segment_files.append(seg_path)
                actual_duration = seg_duration / args.speed if args.speed != 1.0 else seg_duration
                segment_durations.append(actual_duration)
                score_str = f"  score: {smart_scores[start]:.2f}" if smart_scores and start in smart_scores else ""
                print(f"  #{seg_num:02d}  {time_label}{score_str}  ✓")
            else:
                print(f"  #{seg_num:02d}  {time_label}  ✗ 실패")
                err_lines = [l for l in err.strip().splitlines() if l.strip()]
                for line in err_lines[-2:]:
                    print(f"       {line}")

        print()

        if not segment_files:
            print("추출된 세그먼트가 없습니다. 종료합니다.")
            sys.exit(1)

        total_duration = sum(segment_durations)

        if len(segment_files) == 1:
            print(f"세그먼트 1개 — 바로 출력 파일로 복사합니다...")
            shutil.copy2(segment_files[0], output_path)
        else:
            print(f"{len(segment_files)}개 세그먼트 합치기 -> {output_path}")

            # 제목 오버레이 생성
            title_png = None
            if args.title:
                font_path = resolve_font_path(args)
                title_png = create_title_overlay(
                    args.title, font_path,
                    zoom=1.0, fill=True,
                    color=args.font_color,
                    tmp_dir=tmp_dir,
                )

            # 전환 효과 유무에 따라 합치기
            if args.transition > 0:
                concat_xfade(
                    segment_files, output_path, tmp_dir,
                    transition=args.transition,
                    title_png=title_png,
                    bgm=args.bgm, bgm_volume=args.bgm_volume,
                    bgm_fade=args.bgm_fade, bgm_loop=args.bgm_loop,
                    total_duration=total_duration,
                    bgm_start=args.bgm_start,
                )
            else:
                ok, err = concat_segments(
                    segment_files, output_path, tmp_dir,
                    title_png=title_png,
                    bgm=args.bgm, bgm_volume=args.bgm_volume,
                    bgm_fade=args.bgm_fade, bgm_start=args.bgm_start,
                    bgm_loop=args.bgm_loop, total_duration=total_duration,
                )
                if not ok:
                    print("합치기 실패:")
                    err_lines = [l for l in err.strip().splitlines() if l.strip()]
                    for line in err_lines[-5:]:
                        print(f"  {line}")
                    sys.exit(1)

    if output_path.exists():
        print(f"완료: {output_path} (~{total_duration:.1f}초)")
    else:
        print("출력 파일을 찾을 수 없습니다.")
        sys.exit(1)


def build_parser():
    """CLI 인자 파서를 생성하여 반환한다."""
    epilog = """예시:
  %(prog)s -i input.mp4                        # 기본 (자동 세그먼트 수)
  %(prog)s -i input.mp4 -t 2.0 -n 10          # 2초씩 10개
  %(prog)s -i input.mp4 --ratio 9:16           # 세로형 변환
  %(prog)s -i input.mp4 --shuffle              # 순서 랜덤
  %(prog)s -i input.mp4 --bgm music.mp3 --mute
  %(prog)s -i input.mp4 --transition 0.5 --title "하이라이트"
"""
    parser = argparse.ArgumentParser(
        description="단일 영상에서 균등 간격 하이라이트 클립을 추출하여 합칩니다.",
        fromfile_prefix_chars="@",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )

    # 소스 옵션
    src_group = parser.add_argument_group("소스 옵션")
    src_group.add_argument(
        "--input", "-i",
        default=None,
        help="소스 영상 파일 경로 (필수)",
    )

    # 출력 옵션
    out_group = parser.add_argument_group("출력 옵션")
    out_group.add_argument(
        "--out", "-o",
        default="./highlight.mp4",
        help="출력 파일 경로 (기본: ./highlight.mp4)",
    )
    out_group.add_argument(
        "--duration", "-t",
        type=float,
        default=1.0,
        help="각 세그먼트 길이 (초, 기본: 1.0)",
    )
    out_group.add_argument(
        "--count", "-n",
        type=int,
        default=None,
        help="추출할 세그먼트 수 (기본: 자동, 약 3초마다 1개)",
    )
    out_group.add_argument(
        "--shuffle",
        action="store_true",
        default=False,
        help="세그먼트 순서 랜덤 (기본: 꺼짐)",
    )
    out_group.add_argument(
        "--smart",
        action="store_true",
        default=False,
        help="움직임+소리 분석으로 하이라이트 구간 자동 선택 (기본: 균등 간격)",
    )
    out_group.add_argument(
        "--transition",
        type=float,
        default=0,
        help="세그먼트 간 크로스페이드 전환 길이 (초, 0=전환 없음, 기본: 0)",
    )

    # 비율 옵션
    add_ratio_args(parser, default="original")

    # 색보정
    enhance_group = parser.add_mutually_exclusive_group()
    enhance_group.add_argument("--enhance", dest="enhance", action="store_true",
                               default=True, help="색보정 적용 (기본: 켜짐)")
    enhance_group.add_argument("--no-enhance", dest="enhance", action="store_false",
                               help="색보정 끄기")

    # 속도/오디오 옵션
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="재생 속도 배율 (0.5=슬로모션, 2.0=2배속, 기본: 1.0)",
    )
    parser.add_argument(
        "--mute",
        action="store_true",
        default=False,
        help="원본 오디오 제거 (BGM만 사용, 기본: 꺼짐)",
    )

    # 제목 옵션
    add_title_args(parser)

    # 배경음악 옵션
    add_bgm_args(parser)

    return parser


def main():
    """CLI 진입점 — 인자를 파싱하고 run()을 호출한다."""
    parser = build_parser()
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  작업이 중지되었습니다.\n")
        sys.exit(130)
