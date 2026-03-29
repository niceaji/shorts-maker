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
from shortmaker.files import find_media_files
from shortmaker.ffmpeg import (
    ENCODER_ARGS, ENCODER_VIDEO, ENCODER_AUDIO,
    build_enhance_chain, build_speed_filter,
    concat_segments, prepare_intro_outro,
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


def build_filter(zoom, enhance, fill=False, smart_crop=False, clip_path=None):
    """ffmpeg filter_complex 문자열을 생성한다.

    fill=False: 블러 배경(9:16 크롭 + 가우시안 블러) 위에 원본 영상을 중앙 배치
    fill=True: 전체 화면 꽉 채우기 (9:16 크롭만)

    zoom: 전경 영상 크기 배율 (fill=False일 때만 사용)
    smart_crop: 인물 위치를 감지하여 크롭 x 오프셋 자동 조정
    clip_path: smart_crop 활성화 시 첫 프레임 분석에 사용할 파일 경로
    """
    enhance_chain = build_enhance_chain(enhance)

    # 스마트 크롭: 인물 위치 감지로 크롭 오프셋 결정
    crop_position = "center"
    if smart_crop and clip_path is not None:
        # 첫 프레임을 임시 추출하여 인물 위치 감지
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

    # crop_position에 따라 x 오프셋 계산
    # "left": 왼쪽 기준, "right": 오른쪽 기준, "center": 중앙
    if crop_position == "left":
        crop_x = "0"
    elif crop_position == "right":
        crop_x = "iw-ih*9/16"
    else:
        crop_x = "(iw-ih*9/16)/2"

    if fill:
        # 전체 채우기: 9:16 크롭 → 1080x1920 스케일
        filt = (
            f"[0:v]crop=ih*9/16:ih:{crop_x}:0,"
            f"scale=1080:1920"
            f"{enhance_chain}[v]"
        )
        return filt

    # 배경: 중앙 9:16 크롭 → 1080x1920 스케일 → 강한 블러 (항상 중앙 크롭)
    bg = (
        "[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
        "scale=1080:1920,gblur=sigma=40"
        f"{enhance_chain}"
        "[bg]"
    )

    # 전경: zoom 배율로 스케일 (비율 유지)
    fg_w = f"trunc({zoom}*1080/2)*2"
    fg_h = f"trunc({zoom}*1080/iw*ih/2)*2"

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


def _concat_xfade(segment_files, output_path, tmp_dir, transition,
                  title_png, bgm, bgm_volume, bgm_fade, total_duration,
                  watermark_png=None):
    """xfade 필터로 세그먼트 간 크로스페이드 전환 효과를 적용한다."""
    n = len(segment_files)

    # 각 세그먼트 입력
    cmd = ["ffmpeg", "-y"]
    for seg in segment_files:
        cmd += ["-i", str(seg)]

    input_idx = n
    extra_inputs = []

    if title_png:
        extra_inputs.append(("-loop", "1", "-i", title_png))
        title_idx = input_idx
        input_idx += 1
    else:
        title_idx = None

    if watermark_png:
        extra_inputs.append(("-loop", "1", "-i", watermark_png))
        watermark_idx = input_idx
        input_idx += 1
    else:
        watermark_idx = None

    if bgm:
        extra_inputs.append(("-i", str(bgm)))
        bgm_idx = input_idx
        input_idx += 1
    else:
        bgm_idx = None

    for args_tuple in extra_inputs:
        cmd += list(args_tuple)

    # filter_complex 구성
    fc_parts = []

    # xfade 체인
    seg_dur = (total_duration / n) if total_duration else 3.0
    offset = seg_dur - transition

    if n == 2:
        fc_parts.append(
            f"[0:v][1:v]xfade=transition=fade:duration={transition}:offset={offset:.3f}[vx]"
        )
        vx_label = "[vx]"
        fc_parts.append(
            f"[0:a][1:a]acrossfade=d={transition}[ax]"
        )
        ax_label = "[ax]"
    else:
        prev_v = "[0:v]"
        for i in range(1, n):
            cur_off = seg_dur * i - transition * i
            if cur_off < 0:
                cur_off = 0
            out_label = f"[vx{i}]" if i < n - 1 else "[vx]"
            fc_parts.append(
                f"{prev_v}[{i}:v]xfade=transition=fade:duration={transition}:offset={cur_off:.3f}{out_label}"
            )
            prev_v = out_label
        vx_label = "[vx]"

        prev_a = "[0:a]"
        for i in range(1, n):
            out_label = f"[ax{i}]" if i < n - 1 else "[ax]"
            fc_parts.append(
                f"{prev_a}[{i}:a]acrossfade=d={transition}{out_label}"
            )
            prev_a = out_label
        ax_label = "[ax]"

    # 제목 오버레이 (페이드인)
    if title_idx is not None:
        fc_parts.append(
            f"[{title_idx}:v]format=rgba,fade=t=in:st=0:d=1:alpha=1[title];"
            f"{vx_label}[title]overlay=0:0:shortest=1[vafter_title]"
        )
        vx_label = "[vafter_title]"

    # 워터마크 오버레이 (페이드인 없음)
    if watermark_idx is not None:
        fc_parts.append(
            f"[{watermark_idx}:v]format=rgba[wm];"
            f"{vx_label}[wm]overlay=0:0:shortest=1[vfinal]"
        )
        vfinal_label = "[vfinal]"
    else:
        vfinal_label = vx_label

    # BGM 믹싱
    if bgm_idx is not None:
        fade_out_start = max(0, (total_duration or 30) - bgm_fade)
        fc_parts.append(
            f"[{bgm_idx}:a]volume={bgm_volume},"
            f"afade=t=in:st=0:d={bgm_fade},"
            f"afade=t=out:st={fade_out_start}:d={bgm_fade}[bgm];"
            f"{ax_label}[bgm]amix=inputs=2:duration=shortest:dropout_transition=0[aout]"
        )
        afinal_label = "[aout]"
    else:
        afinal_label = ax_label

    cmd += ["-filter_complex", ";".join(fc_parts)]
    cmd += ["-map", vfinal_label, "-map", afinal_label]
    cmd += ENCODER_VIDEO + ENCODER_AUDIO + ["-movflags", "+faststart", str(output_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print("  합치기 실패 (xfade). concat demuxer로 재시도합니다...")
        ok, stderr = concat_segments(
            segment_files, output_path, tmp_dir,
            title_png=title_png,
            bgm=bgm, bgm_volume=bgm_volume, bgm_fade=bgm_fade,
            total_duration=total_duration,
        )
        if not ok:
            print("  합치기 실패 (demuxer):")
            for line in stderr.strip().splitlines()[-5:]:
                print(f"    {line}")
            sys.exit(1)


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
        default=today,
        help="파일명에서 매칭할 날짜 YYYYMMDD (기본: 오늘)",
    )
    src_group.add_argument(
        "--src", "-s",
        default="/Volumes/SD_Card/DCIM",
        help="영상 클립 소스 디렉토리 (기본: /Volumes/SD_Card/DCIM)",
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
        help="각 클립에서 잘라낼 길이 (초, 기본: 2.5)",
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
    src_explicitly_set = args.src != "/Volumes/SD_Card/DCIM"
    date_str = None if src_explicitly_set else args.date
    output_path = args.out or f"./shorts_{args.date}.mp4"
    output_path = Path(output_path)

    print(f"날짜 필터: {date_str or '전체 (소스 폴더 직접 지정)'}")
    print(f"소스: {args.src}")
    print(f"출력: {output_path}")
    print(f"확장자: {' '.join(args.ext)}")
    print(f"클립당 길이: {args.duration}초")
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

            seg_duration = min(args.duration, duration)
            max_start = duration - seg_duration
            start = random.uniform(0, max_start) if max_start > 0 else 0
            print(f"  길이: {duration:.1f}초 | 컷: {seg_duration:.1f}초 | 시작: {start:.2f}초 | 오디오: {has_audio}")

            # 스마트 크롭이 켜져 있으면 클립별로 인물 위치를 감지하여 필터 생성
            vf = build_filter(
                args.zoom, args.enhance, fill=args.fill,
                smart_crop=args.smart_crop,
                clip_path=clip if args.smart_crop else None,
            )

            seg_path = Path(tmp_dir) / f"seg_{i:04d}.mp4"

            # 해당 클립에 자막이 있으면 자막 오버레이 생성
            sub_png = None
            if subtitles and processed < len(subtitles) and subtitles[processed]:
                font_path = resolve_font_path(args)
                sub_png = create_subtitle_overlay(
                    subtitles[processed], font_path,
                    zoom=args.zoom, fill=args.fill,
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
                    zoom=args.zoom, fill=args.fill,
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
                _concat_xfade(
                    segment_files, output_path, tmp_dir,
                    transition=args.transition,
                    title_png=title_png,
                    bgm=args.bgm, bgm_volume=args.bgm_volume, bgm_fade=args.bgm_fade,
                    total_duration=sum_duration,
                    watermark_png=watermark_png,
                )
            else:
                ok, err = concat_segments(
                    segment_files, output_path, tmp_dir,
                    title_png=title_png,
                    bgm=args.bgm, bgm_volume=args.bgm_volume,
                    bgm_fade=args.bgm_fade,
                    total_duration=sum_duration,
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
        actual_speed = args.speed if args.speed != 1.0 else 1.0
        total_duration = sum(
            min(args.duration, 9999) / actual_speed
            for _ in range(processed)
        )
        print()
        print(f"완료! 출력: {output_path} ({size_mb:.1f} MB)")
        print(f"총 영상 길이: ~{total_duration:.1f}초 ({processed}개 클립)")
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
    main()
