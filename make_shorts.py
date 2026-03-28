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
import json
import random
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def get_video_info(filepath):
    """ffprobe로 영상 길이(초)와 오디오 유무를 반환한다."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(filepath),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None, False
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None, False

    duration = None
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, ValueError):
        for stream in data.get("streams", []):
            if "duration" in stream:
                try:
                    duration = float(stream["duration"])
                    break
                except ValueError:
                    pass

    has_audio = any(
        s.get("codec_type") == "audio" for s in data.get("streams", [])
    )

    return duration, has_audio


def find_clips(src_dir, date_str, exts):
    """날짜 문자열로 필터링하여 영상 파일 목록을 반환한다. date_str이 None이면 전체."""
    src_path = Path(src_dir)
    ext_set = {e.lower() for e in exts}
    clips = []
    for p in src_path.rglob("*"):
        if p.suffix.lower().lstrip(".") in ext_set:
            if date_str is None or date_str in p.name:
                clips.append(p)
    return sorted(clips)


def build_filter(zoom, enhance):
    """ffmpeg filter_complex 문자열을 생성한다.

    블러 배경(9:16 크롭 + 가우시안 블러) 위에 원본 영상을 중앙 배치한다.

    zoom: 전경 영상 크기 배율
      1.0 = 프레임 가로폭에 딱 맞춤
      1.1 = 10% 확대 (양쪽 살짝 잘림, 세로 공간 더 채움)
    """
    enhance_chain = ""
    if enhance:
        enhance_chain = ",eq=contrast=1.15:brightness=0.03:saturation=1.25,unsharp=5:5:0.8:5:5:0.0"

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
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-r", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-shortest",
        str(out_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.returncode == 0, result.stderr


def create_title_overlay(title, font_path, width=1080, height=1920, zoom=1.1, color="white", tmp_dir=None):
    """Pillow로 제목 텍스트 PNG를 생성한다.

    가로 중앙 정렬, 프레임 상단과 영상 상단 사이 세로 중앙에 배치.
    """
    try:
        font = ImageFont.truetype(font_path, 80)
    except Exception:
        return None

    # 전경 영상 위쪽 여백 계산
    fg_h = zoom * width * 9 / 16
    gap_top = (height - fg_h) / 2

    # 텍스트 크기 측정
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), title, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    x = (width - tw) // 2
    y = int((gap_top - th) / 2)

    from PIL import ImageColor
    try:
        rgba = ImageColor.getrgb(color) + (255,) if len(ImageColor.getrgb(color)) == 3 else ImageColor.getrgb(color)
    except ValueError:
        rgba = (255, 255, 255, 255)
    draw.text((x, y), title, font=font, fill=rgba)

    out_path = Path(tmp_dir) / "title_overlay.png"
    img.save(str(out_path))
    return str(out_path)


def create_subtitle_overlay(text, font_path, width=1080, height=1920, zoom=1.1, color="black", tmp_dir=None, index=0):
    """Pillow로 자막 텍스트 PNG를 생성한다. 영상 하단 바로 아래에 배치."""
    try:
        font = ImageFont.truetype(font_path, 56)
    except Exception:
        return None

    from PIL import ImageColor
    try:
        rgba = ImageColor.getrgb(color)
        if len(rgba) == 3:
            rgba = rgba + (255,)
    except ValueError:
        rgba = (0, 0, 0, 255)

    # 텍스트 크기 측정
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    # 전경 영상 아래쪽 바로 밑에 배치 (20px 마진)
    fg_h = zoom * width * 9 / 16
    gap_bottom = (height - fg_h) / 2
    fg_bottom = height - gap_bottom
    y = int(fg_bottom + 20)
    x = (width - tw) // 2

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((x, y), text, font=font, fill=rgba)

    out_path = Path(tmp_dir) / f"subtitle_{index:04d}.png"
    img.save(str(out_path))
    return str(out_path)


def concat_segments(segment_files, output_path, tmp_dir, title=None, font_path=None,
                    zoom=1.1, color="white", bgm=None, bgm_volume=0.3, bgm_fade=1.5,
                    total_duration=None):
    """세그먼트 파일들을 하나로 합친다.

    제목이 있으면 페이드인 오버레이 적용.
    BGM이 있으면 원본 오디오와 믹싱 (볼륨 조절, 페이드인/아웃).
    """
    list_file = Path(tmp_dir) / "segments.txt"
    with open(list_file, "w") as f:
        for seg in segment_files:
            f.write(f"file '{seg}'\n")

    title_png = None
    if title and font_path:
        title_png = create_title_overlay(title, font_path, zoom=zoom, color=color, tmp_dir=tmp_dir)

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
    ]

    input_idx = 1  # 다음 입력 인덱스 추적

    # 제목 오버레이
    vf_parts = []
    if title_png:
        cmd += ["-loop", "1", "-i", title_png]
        vf_parts.append(
            f"[{input_idx}:v]format=rgba,fade=t=in:st=0:d=1:alpha=1[title];"
            f"[0:v][title]overlay=0:0:shortest=1[vout]"
        )
        input_idx += 1

    # BGM
    af_parts = []
    if bgm:
        cmd += ["-i", str(bgm)]
        bgm_idx = input_idx
        input_idx += 1
        # BGM: 볼륨 조절 + 페이드인 + 페이드아웃 (영상 끝 기준)
        fade_out_start = max(0, (total_duration or 30) - bgm_fade)
        af_parts.append(
            f"[{bgm_idx}:a]volume={bgm_volume},"
            f"afade=t=in:st=0:d={bgm_fade},"
            f"afade=t=out:st={fade_out_start}:d={bgm_fade}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=shortest:dropout_transition=0[aout]"
        )

    # filter_complex 조합
    fc = []
    if vf_parts:
        fc.extend(vf_parts)
    if af_parts:
        fc.extend(af_parts)

    if fc:
        cmd += ["-filter_complex", ";".join(fc)]

    # 매핑
    if vf_parts and af_parts:
        cmd += ["-map", "[vout]", "-map", "[aout]"]
    elif vf_parts:
        cmd += ["-map", "[vout]", "-map", "0:a"]
    elif af_parts:
        cmd += ["-map", "0:v", "-map", "[aout]"]

    cmd += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-r", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-movflags", "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
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
        "--zoom",
        type=float,
        default=1.1,
        help="전경 영상 확대 배율; 1.0=가로 딱맞춤, 1.1=살짝 확대 (기본: 1.1)",
    )
    enhance_group = parser.add_mutually_exclusive_group()
    enhance_group.add_argument(
        "--enhance",
        dest="enhance",
        action="store_true",
        default=True,
        help="아이폰 스타일 색보정 적용 (기본: 켜짐)",
    )
    enhance_group.add_argument(
        "--no-enhance",
        dest="enhance",
        action="store_false",
        help="색보정 끄기",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        default=False,
        help="클립 순서 랜덤 (기본: 꺼짐)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="상단 제목 텍스트 (Pretendard Bold, 페이드인)",
    )
    parser.add_argument(
        "--font",
        default=None,
        help="폰트 파일 경로 (기본: ./fonts/Pretendard-Bold.otf)",
    )
    parser.add_argument(
        "--font-color",
        default="white",
        help="제목 색상: 이름(white) 또는 hex(#FF5500) (기본: white)",
    )
    parser.add_argument(
        "--subtitle",
        default=None,
        help="클립별 자막, 파이프로 구분 (예: \"준비|입수!|달린다\")",
    )
    parser.add_argument(
        "--subtitle-color",
        default="black",
        help="자막 색상 (기본: black)",
    )
    parser.add_argument(
        "--bgm",
        default=None,
        help="배경음악 파일 경로 (mp3, wav 등)",
    )
    parser.add_argument(
        "--bgm-volume",
        type=float,
        default=0.3,
        help="배경음악 볼륨 (0.0~1.0, 기본: 0.3)",
    )
    parser.add_argument(
        "--bgm-fade",
        type=float,
        default=1.5,
        help="배경음악 페이드인/아웃 길이 (초, 기본: 1.5)",
    )

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
    clips = find_clips(args.src, date_str, args.ext)
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

    vf = build_filter(args.zoom, args.enhance)
    subtitles = args.subtitle.split("|") if args.subtitle else []

    with tempfile.TemporaryDirectory(prefix="make_shorts_") as tmp_dir:
        segment_files = []
        segment_durations = []
        processed = 0
        skipped = 0

        for i, clip in enumerate(clips):
            print(f"[{i + 1}/{len(clips)}] 처리중: {clip.name}")

            duration, has_audio = get_video_info(clip)
            if duration is None:
                print(f"  건너뜀: 영상 길이를 읽을 수 없음")
                skipped += 1
                continue

            seg_duration = min(args.duration, duration)
            max_start = duration - seg_duration
            start = random.uniform(0, max_start) if max_start > 0 else 0
            print(f"  길이: {duration:.1f}초 | 컷: {seg_duration:.1f}초 | 시작: {start:.2f}초 | 오디오: {has_audio}")

            seg_path = Path(tmp_dir) / f"seg_{i:04d}.mp4"

            # 해당 클립에 자막이 있으면 자막 오버레이 생성
            sub_png = None
            if subtitles and processed < len(subtitles) and subtitles[processed]:
                font_path = args.font or str(Path(__file__).parent / "fonts" / "Pretendard-Bold.otf")
                sub_png = create_subtitle_overlay(
                    subtitles[processed], font_path,
                    zoom=args.zoom, color=args.subtitle_color,
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
            import shutil
            shutil.copy2(segment_files[0], output_path)
        else:
            sum_duration = sum(segment_durations)
            print(f"{len(segment_files)}개 세그먼트 합치기 -> {output_path}")
            font_path = args.font or str(Path(__file__).parent / "fonts" / "Pretendard-Bold.otf")
            ok, err = concat_segments(segment_files, output_path, tmp_dir,
                                      title=args.title, font_path=font_path,
                                      zoom=args.zoom, color=args.font_color,
                                      bgm=args.bgm, bgm_volume=args.bgm_volume,
                                      bgm_fade=args.bgm_fade,
                                      total_duration=sum_duration)
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
