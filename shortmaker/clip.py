#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
make_shorts.py - DJI 영상 클립으로 세로형 숏폼 영상 생성

유튜브 쇼츠 / 인스타 릴스 규격 (9:16, 1080x1920) 영상을 자동 생성합니다.
각 클립에서 지정된 길이만큼 랜덤 구간을 잘라내고, 블러 배경 위에 원본을 합성합니다.

사용법:
    python3 make_shorts.py                          # 기본 (SD카드, 오늘 날짜)
    python3 make_shorts.py -d 20260318              # 특정 날짜
    python3 make_shorts.py -s ./clips -o out.mp4    # 로컬 폴더 (날짜 필터 무시)
    python3 make_shorts.py @preset.txt              # 프리셋 파일로 실행

프리셋 파일 예시 (preset.txt):
    --title
    제주 오픈워터 수영
    --subtitle
    준비중|입수!|달린다
    --zoom
    1.1
"""

import argcomplete
import argparse
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from shortmaker.probe import get_video_info
from shortmaker.files import find_media_files, unique_path
from shortmaker.ffmpeg import (
    ENCODER_ARGS, ENCODER_VIDEO, ENCODER_AUDIO,
    build_enhance_chain, build_speed_filter,
    concat_segments, concat_xfade, prepare_intro_outro,
)
from shortmaker.overlay import (
    create_title_overlay, create_subtitle_overlay, create_watermark_overlay,
)
from shortmaker.detect import detect_person_position
from shortmaker.cli import (
    add_title_args,
    add_subtitle_args,
    add_bgm_args,
    add_display_args,
    add_speed_args,
    add_audio_args,
    add_intro_outro_args,
    add_watermark_args,
    resolve_font_path,
)


def build_filter(zoom, enhance, bg="fill", smart_crop=False, clip_path=None,
                 ratio="9:16"):
    """ffmpeg filter_complex 문자열을 생성한다.

    bg="fill": 전체 화면 꽉 채우기 (크롭)
    bg="blur": 블러 배경 위에 원본 영상을 중앙 배치
    bg="letterbox": 검정 배경 위에 원본 영상을 중앙 배치
    ratio: 영상 비율 ("9:16", "16:9", "1:1", "original")

    zoom: 전경 영상 크기 배율 (bg="blur"일 때만 사용)
    smart_crop: 인물 위치를 감지하여 크롭 x 오프셋 자동 조정
    clip_path: smart_crop 활성화 시 첫 프레임 분석에 사용할 파일 경로
    """
    enhance_chain = build_enhance_chain(enhance)

    # original: 크롭 없이 원본 비율 유지
    if ratio == "original":
        filt = f"[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2{enhance_chain}[v]"
        return filt

    # 비율별 크롭/스케일 설정
    ratio_map = {
        "9:16": ("ih*9/16", "ih", 1080, 1920),
        "16:9": ("iw", "iw*9/16", 1920, 1080),
        "1:1":  ("min(iw\\,ih)", "min(iw\\,ih)", 1080, 1080),
    }
    crop_w, crop_h, out_w, out_h = ratio_map[ratio]

    # 스마트 크롭: 인물 위치 감지로 크롭 오프셋 결정
    crop_position = "center"
    if smart_crop and clip_path is not None:
        import tempfile as _tempfile
        import os
        tmp_frame = _tempfile.mktemp(suffix=".jpg")
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-ss", "0", "-i", str(clip_path),
                 "-frames:v", "1", "-q:v", "2", tmp_frame],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0 and os.path.exists(tmp_frame):
                crop_position = detect_person_position(tmp_frame)
        except Exception:
            crop_position = "center"
        finally:
            try:
                os.unlink(tmp_frame)
            except Exception:
                pass

    # crop_position에 따라 x/y 오프셋 계산
    if crop_position == "left":
        crop_x = "0"
    elif crop_position == "right":
        crop_x = f"iw-{crop_w}"
    else:
        crop_x = f"(iw-{crop_w})/2"
    crop_y = f"(ih-{crop_h})/2"

    if bg == "fill":
        filt = (
            f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
            f"scale={out_w}:{out_h}"
            f"{enhance_chain}[v]"
        )
        return filt

    if bg == "letterbox":
        # zoom 배율 적용: 확대 후 넘치는 부분 크롭, 부족하면 검정 패딩
        filt = (
            f"[0:v]scale=trunc({zoom}*{out_w}/2)*2:trunc({zoom}*{out_h}/2)*2"
            f":force_original_aspect_ratio=decrease,"
            f"crop=min(iw\\,{out_w}):min(ih\\,{out_h}):(iw-min(iw\\,{out_w}))/2:(ih-min(ih\\,{out_h}))/2,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:black"
            f"{enhance_chain}[v]"
        )
        return filt

    # blur: 블러 배경 (항상 중앙 크롭)
    bg = (
        f"[0:v]crop={crop_w}:{crop_h}:(iw-{crop_w})/2:(ih-{crop_h})/2,"
        f"scale={out_w}:{out_h},gblur=sigma=40"
        f"{enhance_chain}"
        "[bg]"
    )

    # 전경: zoom 배율로 스케일 (비율 유지)
    fg_w = f"trunc({zoom}*{out_w}/2)*2"
    fg_h = f"trunc({zoom}*{out_w}/iw*ih/2)*2"

    # 스마트 크롭: fill=False 모드에서 전경 오버레이 x 위치 조정
    if smart_crop and crop_position == "left":
        overlay_x = "0"
    elif smart_crop and crop_position == "right":
        overlay_x = "W-w"
    else:
        overlay_x = "(W-w)/2"

    fg = (
        f"[0:v]scale={fg_w}:{fg_h}"
        f"{enhance_chain}"
        "[fg]"
    )

    overlay = f"[bg][fg]overlay={overlay_x}:(H-h)/2[v]"

    return f"{bg};{fg};{overlay}"


def extract_segment(clip_path, start, duration, out_path, vf, has_audio,
                    subtitle_png=None, speed=1.0, mute=False):
    """클립에서 구간을 추출하고 필터를 적용하여 세그먼트 파일로 저장한다."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(clip_path),
    ]

    input_idx = 1
    # 오디오가 없거나 음소거 모드이면 무음 오디오 생성
    if not has_audio or mute:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        input_idx += 1

    if subtitle_png:
        cmd += ["-loop", "1", "-i", subtitle_png]
        sub_idx = input_idx
        vf = (vf + f";[v][{sub_idx}:v]overlay=0:0:shortest=1[vout]")
        map_v = "[vout]"
    else:
        map_v = "[v]"

    # 오디오가 없거나 음소거 모드이면 생성된 무음 오디오 사용
    audio_input = "0:a:0" if (has_audio and not mute) else "1:a:0"

    # 속도 필터 적용
    speed_filter = build_speed_filter(speed)
    if speed_filter:
        video_speed, audio_speed = speed_filter.split(";", 1)
        # 비디오 속도 필터를 [v] 레이블 앞에 삽입
        vf = vf.rstrip()
        # [v] 레이블이 map_v에 따라 달라지므로 현재 map_v 기준으로 처리
        if map_v == "[vout]":
            # 자막 있음: vout 앞에 속도 필터 삽입
            vf = vf.replace("[vout]", "[vspeed]") + f";[vspeed]{video_speed}[vout_s]"
            map_v = "[vout_s]"
        else:
            # 자막 없음: [v] 레이블에 속도 필터 삽입
            vf = vf.replace("[v]", "[v_pre]") + f";[v_pre]{video_speed}[v]"

        # 오디오 속도 필터
        audio_filter_str = audio_speed
        cmd_extra = ["-af", audio_filter_str]
    else:
        cmd_extra = []

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


def build_parser():
    """CLI 인자 파서를 생성하여 반환한다."""
    today = datetime.now().strftime("%Y%m%d")

    epilog = """예시:
  %(prog)s                              # SD카드, 오늘 날짜
  %(prog)s -d 20260318                  # 특정 날짜
  %(prog)s -s ./clips --fill            # 로컬 폴더, 전체 채우기
  %(prog)s @preset.txt                  # 프리셋 파일
  %(prog)s --bgm music.mp3 --mute      # BGM만 사용
  %(prog)s --title "제주" --watermark "2026.03.28"
"""

    parser = argparse.ArgumentParser(
        description="DJI 영상 클립으로 세로형 숏폼 영상(9:16)을 생성합니다.",
        fromfile_prefix_chars="@",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )

    # 소스 옵션
    src_group = parser.add_argument_group("소스 옵션")
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
    out_group = parser.add_argument_group("출력 옵션")
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
    add_display_args(parser)

    # 속도/오디오 옵션
    add_speed_args(parser)
    add_audio_args(parser)

    # 자막/제목/워터마크 옵션
    add_title_args(parser)
    add_subtitle_args(parser)
    add_watermark_args(parser)

    # 배경음악 옵션
    add_bgm_args(parser)

    # 인트로/아웃트로 옵션
    add_intro_outro_args(parser)

    return parser


def run(args):
    """실제 작업 수행 (서브커맨드에서도 호출됨)"""
    # --src를 직접 지정한 경우 날짜 필터 무시 (폴더 내 모든 영상 대상)
    date_str = args.date
    default_name = f"./shorts_{args.date}.mp4" if args.date else "./shorts.mp4"
    output_path = unique_path(Path(args.out or default_name))

    print(f"날짜 필터: {date_str or '전체'}")
    print(f"소스: {args.src}")
    print(f"출력: {output_path}")
    print(f"확장자: {' '.join(args.ext)}")
    print(f"클립당 길이: {'전체' if args.duration == 0 else f'{args.duration}초'}")
    print(f"줌: {args.zoom}")
    print(f"색보정: {'켜짐' if args.enhance else '꺼짐'}")
    print(f"셔플: {'켜짐' if args.shuffle else '꺼짐'}")
    print(f"속도: {args.speed}x")
    print(f"음소거: {'켜짐' if args.mute else '꺼짐'}")
    print(f"전환 효과: {args.transition}초" if args.transition > 0 else "전환 효과: 없음")
    print(f"스마트 크롭: {'켜짐' if args.smart_crop else '꺼짐'}")
    print()

    # 클립 검색
    clips = find_media_files(Path(args.src), args.ext, date_str=date_str)
    if not clips:
        ext_patterns = ", ".join(f"*.{ext}" for ext in args.ext)
        if date_str:
            label = ", ".join(f"*{date_str}*.{ext}" for ext in args.ext)
        else:
            label = ext_patterns
        print(f"{args.src}에서 {label} 패턴에 맞는 클립을 찾을 수 없습니다.")
        sys.exit(1)

    print(f"{len(clips)}개 클립 발견:")
    for c in clips:
        print(f"  {c}")
    print()

    if args.shuffle:
        random.shuffle(clips)

    subtitles = args.subtitle.split("|") if args.subtitle else []

    with tempfile.TemporaryDirectory(prefix="make_shorts_") as tmp_dir:
        segment_files = []
        segment_durations = []
        processed = 0
        skipped = 0

        for i, clip in enumerate(clips):
            print(f"[{i + 1}/{len(clips)}] 처리중: {clip.name}")

            info = get_video_info(clip)
            if info is None:
                print(f"  건너뜀: 영상 길이를 읽을 수 없음")
                skipped += 1
                continue

            duration = info["duration"]
            has_audio = info["has_audio"]

            seg_duration = duration if args.duration == 0 else min(args.duration, duration)
            max_start = duration - seg_duration
            start = random.uniform(0, max_start) if max_start > 0 else 0
            print(f"  길이: {duration:.1f}초 | 컷: {seg_duration:.1f}초 | 시작: {start:.2f}초 | 오디오: {has_audio}")

            # 스마트 크롭이 켜져 있으면 클립별로 인물 위치를 감지하여 필터 생성
            vf = build_filter(
                args.zoom, args.enhance, bg=args.bg,
                smart_crop=args.smart_crop,
                clip_path=clip if args.smart_crop else None,
                ratio=args.ratio,
            )

            seg_path = Path(tmp_dir) / f"seg_{i:04d}.mp4"

            # 해당 클립에 자막이 있으면 자막 오버레이 생성
            sub_png = None
            if subtitles and processed < len(subtitles) and subtitles[processed]:
                font_path = resolve_font_path(args)
                sub_png = create_subtitle_overlay(
                    subtitles[processed], font_path,
                    zoom=args.zoom, fill=(args.bg == "fill"),
                    color=args.subtitle_color,
                    tmp_dir=tmp_dir, index=processed,
                )

            ok, err = extract_segment(
                clip, start, seg_duration, seg_path, vf, has_audio,
                subtitle_png=sub_png,
                speed=args.speed,
                mute=args.mute,
            )

            if not ok:
                print(f"  세그먼트 추출 실패:")
                err_lines = [l for l in err.strip().splitlines() if l.strip()]
                for line in err_lines[-3:]:
                    print(f"    {line}")
                skipped += 1
                continue

            segment_files.append(seg_path)
            # 속도에 따라 실제 세그먼트 재생 길이 계산
            actual_duration = seg_duration / args.speed if args.speed != 1.0 else seg_duration
            segment_durations.append(actual_duration)
            processed += 1
            print(f"  완료 -> {seg_path.name}")

        print()
        print(f"처리: {processed} | 건너뜀: {skipped}")

        if not segment_files:
            print("합칠 세그먼트가 없습니다. 종료합니다.")
            sys.exit(1)

        if len(segment_files) == 1:
            print("세그먼트 1개 — 바로 출력 파일로 복사합니다...")
            shutil.copy2(segment_files[0], output_path)
        else:
            sum_duration = sum(segment_durations)
            print(f"{len(segment_files)}개 세그먼트 합치기 -> {output_path}")
            font_path = resolve_font_path(args)

            # 제목 오버레이 생성
            title_png = None
            if args.title:
                title_png = create_title_overlay(
                    args.title, font_path,
                    zoom=args.zoom, fill=(args.bg == "fill"),
                    color=args.font_color,
                    tmp_dir=tmp_dir,
                )

            # 워터마크 오버레이 생성
            watermark_png = None
            if args.watermark:
                watermark_png = create_watermark_overlay(
                    args.watermark, font_path,
                    position=args.watermark_position,
                    color=args.watermark_color,
                    opacity=args.watermark_opacity,
                    tmp_dir=tmp_dir,
                )

            # 인트로/아웃트로 변환
            intro_seg = None
            outro_seg = None
            if args.intro or args.outro:
                intro_seg, outro_seg = prepare_intro_outro(
                    args.intro, args.outro,
                    width=1080, height=1920,
                    tmp_dir=tmp_dir,
                )
                if args.intro and intro_seg is None:
                    print(f"  경고: 인트로 변환 실패 — 건너뜀")
                if args.outro and outro_seg is None:
                    print(f"  경고: 아웃트로 변환 실패 — 건너뜀")

            # 전환 효과 유무에 따라 합치기 방식 선택
            if args.transition > 0:
                concat_xfade(
                    segment_files, output_path, tmp_dir,
                    transition=args.transition,
                    title_png=title_png,
                    bgm=args.bgm, bgm_volume=args.bgm_volume, bgm_fade=args.bgm_fade,
                    bgm_loop=args.bgm_loop, total_duration=sum_duration,
                    watermark_png=watermark_png, bgm_start=args.bgm_start,
                    bgm_max=args.bgm_max,
                    intro=intro_seg, outro=outro_seg,
                )
            else:
                ok, err = concat_segments(
                    segment_files, output_path, tmp_dir,
                    title_png=title_png,
                    bgm=args.bgm, bgm_volume=args.bgm_volume,
                    bgm_fade=args.bgm_fade, bgm_start=args.bgm_start,
                    bgm_max=args.bgm_max,
                    bgm_loop=args.bgm_loop, total_duration=sum_duration,
                    intro=intro_seg,
                    outro=outro_seg,
                )
                if not ok:
                    print("합치기 실패:")
                    err_lines = [l for l in err.strip().splitlines() if l.strip()]
                    for line in err_lines[-5:]:
                        print(f"  {line}")
                    sys.exit(1)

                # xfade 미사용 시에도 워터마크를 별도로 오버레이
                if watermark_png and output_path.exists():
                    watermarked = output_path.with_stem(output_path.stem + "_wm")
                    wm_cmd = [
                        "ffmpeg", "-y",
                        "-i", str(output_path),
                        "-loop", "1", "-i", watermark_png,
                        "-filter_complex",
                        "[1:v]format=rgba[wm];[0:v][wm]overlay=0:0:shortest=1[v]",
                        "-map", "[v]", "-map", "0:a",
                    ] + ENCODER_ARGS + ["-movflags", "+faststart", str(watermarked)]
                    wm_result = subprocess.run(wm_cmd, capture_output=True, text=True, timeout=600)
                    if wm_result.returncode == 0:
                        import os
                        os.replace(str(watermarked), str(output_path))
                    else:
                        print("  경고: 워터마크 오버레이 실패 — 워터마크 없이 저장됨")

    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print()
        print(f"완료! 출력: {output_path} ({size_mb:.1f} MB)")
        print(f"클립 수: {processed}개")
    else:
        print("출력 파일을 찾을 수 없습니다.")
        sys.exit(1)


def main():
    """CLI 진입점 — 인자를 파싱하고 run()을 호출한다."""
    parser = build_parser()
    argcomplete.autocomplete(parser)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  작업이 중지되었습니다.\n")
        sys.exit(130)
