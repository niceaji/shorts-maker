#!/usr/bin/env python3
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
from shortmaker.ffmpeg import ENCODER_ARGS, build_enhance_chain, concat_segments
from shortmaker.overlay import create_title_overlay, create_subtitle_overlay
from shortmaker.cli import (
    add_title_args,
    add_subtitle_args,
    add_bgm_args,
    add_display_args,
    resolve_font_path,
)


def build_filter(zoom, enhance, fill=False):
    """ffmpeg filter_complex 문자열을 생성한다.

    fill=False: 블러 배경(9:16 크롭 + 가우시안 블러) 위에 원본 영상을 중앙 배치
    fill=True: 전체 화면 꽉 채우기 (9:16 크롭만)

    zoom: 전경 영상 크기 배율 (fill=False일 때만 사용)
    """
    enhance_chain = build_enhance_chain(enhance)

    if fill:
        # 전체 채우기: 9:16 크롭 → 1080x1920 스케일
        filt = (
            f"[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale=1080:1920"
            f"{enhance_chain}[v]"
        )
        return filt

    # 배경: 중앙 9:16 크롭 → 1080x1920 스케일 → 강한 블러
    bg = (
        "[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
        "scale=1080:1920,gblur=sigma=40"
        f"{enhance_chain}"
        "[bg]"
    )

    # 전경: zoom 배율로 스케일 (비율 유지)
    fg_w = f"trunc({zoom}*1080/2)*2"
    fg_h = f"trunc({zoom}*1080/iw*ih/2)*2"
    fg = (
        f"[0:v]scale={fg_w}:{fg_h}"
        f"{enhance_chain}"
        "[fg]"
    )

    # 전경을 배경 위에 중앙 배치
    overlay = "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"

    return f"{bg};{fg};{overlay}"


def extract_segment(clip_path, start, duration, out_path, vf, has_audio, subtitle_png=None):
    """클립에서 구간을 추출하고 필터를 적용하여 세그먼트 파일로 저장한다."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(clip_path),
    ]

    input_idx = 1
    if not has_audio:
        # 오디오 없는 클립용 무음 오디오 생성
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        input_idx += 1

    if subtitle_png:
        cmd += ["-loop", "1", "-i", subtitle_png]
        # 자막 오버레이를 필터 체인에 추가
        sub_idx = input_idx
        vf = (vf + f";[v][{sub_idx}:v]overlay=0:0:shortest=1[vout]")
        map_v = "[vout]"
    else:
        map_v = "[v]"

    audio_input = "0:a:0" if has_audio else "1:a:0"

    cmd += [
        "-t", str(duration),
        "-filter_complex", vf,
        "-map", map_v, "-map", audio_input,
    ] + ENCODER_ARGS + [
        "-shortest",
        str(out_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.returncode == 0, result.stderr


def main():
    today = datetime.now().strftime("%Y%m%d")

    parser = argparse.ArgumentParser(
        description="DJI 영상 클립으로 세로형 숏폼 영상(9:16)을 생성합니다.",
        fromfile_prefix_chars="@",
    )
    parser.add_argument(
        "--date", "-d",
        default=today,
        help="파일명에서 매칭할 날짜 YYYYMMDD (기본: 오늘)",
    )
    parser.add_argument(
        "--src", "-s",
        default="/Volumes/SD_Card/DCIM",
        help="영상 클립 소스 디렉토리 (기본: /Volumes/SD_Card/DCIM)",
    )
    parser.add_argument(
        "--out", "-o",
        default=None,
        help="출력 파일 경로 (기본: ./shorts_YYYYMMDD.mp4)",
    )
    parser.add_argument(
        "--ext", "-e",
        nargs="+",
        default=["MP4", "MOV"],
        help="영상 파일 확장자들 (기본: MP4 MOV)",
    )
    parser.add_argument(
        "--duration", "-t",
        type=float,
        default=2.5,
        help="각 클립에서 잘라낼 길이 (초, 기본: 2.5)",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        default=False,
        help="클립 순서 랜덤 (기본: 꺼짐)",
    )
    add_display_args(parser)
    add_title_args(parser)
    add_subtitle_args(parser)
    add_bgm_args(parser)

    args = parser.parse_args()

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

    vf = build_filter(args.zoom, args.enhance, fill=args.fill)
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

            ok, err = extract_segment(clip, start, seg_duration, seg_path, vf, has_audio, subtitle_png=sub_png)

            if not ok:
                print(f"  세그먼트 추출 실패:")
                err_lines = [l for l in err.strip().splitlines() if l.strip()]
                for line in err_lines[-3:]:
                    print(f"    {line}")
                skipped += 1
                continue

            segment_files.append(seg_path)
            segment_durations.append(seg_duration)
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
            title_png = None
            if args.title:
                title_png = create_title_overlay(
                    args.title, font_path,
                    zoom=args.zoom, fill=args.fill,
                    color=args.font_color,
                    tmp_dir=tmp_dir,
                )
            ok, err = concat_segments(
                segment_files, output_path, tmp_dir,
                title_png=title_png,
                bgm=args.bgm, bgm_volume=args.bgm_volume,
                bgm_fade=args.bgm_fade,
                total_duration=sum_duration,
            )
            if not ok:
                print("합치기 실패:")
                err_lines = [l for l in err.strip().splitlines() if l.strip()]
                for line in err_lines[-5:]:
                    print(f"  {line}")
                sys.exit(1)

    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        total_duration = processed * args.duration
        print()
        print(f"완료! 출력: {output_path} ({size_mb:.1f} MB)")
        print(f"총 영상 길이: ~{total_duration:.1f}초 ({processed}개 클립)")
    else:
        print("출력 파일을 찾을 수 없습니다.")
        sys.exit(1)


if __name__ == "__main__":
    main()
