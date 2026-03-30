#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
make_image_shorts.py - 이미지 디렉토리로 세로형 숏폼 영상 생성

유튜브 쇼츠 / 인스타 릴스 규격 (9:16, 1080x1920) 영상을 자동 생성합니다.
각 이미지에 켄번즈(Ken Burns) 애니메이션 효과를 적용하고 선택적으로 자막을 추가합니다.

사용법:
    python3 make_image_shorts.py -s ./photos -o output.mp4
    python3 make_image_shorts.py -s ./photos --title "제주 오픈워터" --bgm music.mp3
    python3 make_image_shorts.py @preset.txt

프리셋 파일 예시 (preset.txt):
    --src
    ./photos
    --title
    제주 오픈워터 수영
    --subtitle
    아침|입수|수영중
    --bgm
    music.mp3
"""

import argcomplete
import argparse
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from shortmaker import OUT_W, OUT_H, FPS
from shortmaker.cli import (
    add_audio_args,
    add_bgm_args,
    add_display_args,
    add_intro_outro_args,
    add_speed_args,
    add_subtitle_args,
    add_title_args,
    add_watermark_args,
    resolve_font_path,
)
from shortmaker.ffmpeg import (
    ENCODER_AUDIO,
    ENCODER_VIDEO,
    build_bgm_filter,
    concat_segments,
    prepare_intro_outro,
)
from shortmaker.files import find_media_files
from shortmaker.overlay import (
    create_subtitle_overlay,
    create_title_overlay,
    create_watermark_overlay,
)


EFFECTS = [
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
]


def get_effect(effect_type):
    """효과 타입에 따라 실제 효과 이름을 반환한다. random이면 무작위 선택."""
    if effect_type == "random":
        return random.choice(EFFECTS)
    return effect_type


def build_zoompan_filter(effect, duration, zoom_range=1.15):
    """켄번즈 효과에 맞는 ffmpeg zoompan 필터 문자열을 반환한다.

    6가지 단순 효과: zoom_in, zoom_out, pan_left/right/up/down
    원본 해상도에서 직접 처리하여 부드러운 30fps 출력.
    """
    frames = int(duration * FPS)
    base = f"s={OUT_W}x{OUT_H}:fps={FPS}:d={frames}"
    z = zoom_range
    step = (z - 1.0) / frames

    if effect == "zoom_in":
        filt = (
            f"zoompan=z='1+{step:.8f}*on':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':{base}"
        )
    elif effect == "zoom_out":
        filt = (
            f"zoompan=z='{z}-{step:.8f}*on':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':{base}"
        )
    elif effect == "pan_left":
        filt = (
            f"zoompan=z={z}:"
            f"x='(iw-iw/{z})*(1-on/{frames})':y='ih/2-ih/{z}/2':{base}"
        )
    elif effect == "pan_right":
        filt = (
            f"zoompan=z={z}:"
            f"x='(iw-iw/{z})*(on/{frames})':y='ih/2-ih/{z}/2':{base}"
        )
    elif effect == "pan_up":
        filt = (
            f"zoompan=z={z}:"
            f"x='iw/2-iw/{z}/2':y='(ih-ih/{z})*(1-on/{frames})':{base}"
        )
    elif effect == "pan_down":
        filt = (
            f"zoompan=z={z}:"
            f"x='iw/2-iw/{z}/2':y='(ih-ih/{z})*(on/{frames})':{base}"
        )
    else:
        filt = (
            f"zoompan=z='1+{step:.8f}*on':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':{base}"
        )

    return filt


_HEIC_EXTS = {".heic", ".heif"}


def _prepare_image(img_path, tmp_dir):
    """ffmpeg이 읽을 수 있는 이미지 경로를 반환한다. HEIC는 JPG로 변환."""
    if Path(img_path).suffix.lower() in _HEIC_EXTS:
        im = Image.open(img_path)
        converted = Path(tmp_dir) / (Path(img_path).stem + ".jpg")
        im.save(str(converted), "JPEG", quality=95)
        return converted
    return img_path


def _get_image_size(img_path):
    """이미지의 가로, 세로 픽셀 크기를 반환한다."""
    im = Image.open(img_path)
    return im.size  # (width, height)


def process_image_segment(img_path, effect, duration, zoom_range, out_path,
                          subtitle_png=None, fill=False, zoom=1.1, speed=1.0, tmp_dir=None):
    """이미지를 켄번즈 효과와 함께 세그먼트 mp4로 변환한다.

    fill=True: 전체 채우기 (scale+crop으로 1080x1920 커버)
    fill=False: 블러 배경(정지) + 전경(켄번즈 모션) 분리 합성
    speed != 1.0이면 zoompan 프레임 수를 조정하고 setpts 필터를 추가한다.
    """
    # HEIC 등 ffmpeg이 못 읽는 포맷은 JPG로 변환
    if tmp_dir:
        img_path = _prepare_image(img_path, tmp_dir)

    # 속도 적용: duration을 speed로 나눠 zoompan 프레임 수 조정
    adjusted_duration = duration / speed if speed != 1.0 else duration
    zoompan = build_zoompan_filter(effect, adjusted_duration, zoom_range)

    # speed != 1.0이면 setpts 필터 추가
    speed_filter = f",setpts=PTS/{speed}" if speed != 1.0 else ""

    if fill:
        # 전체 채우기: scale+crop → zoompan
        pre_filter = (
            f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H}"
        )
        main_filter = f"[0:v]{pre_filter},{zoompan}{speed_filter}"
    else:
        # 블러 배경(정지) + 전경(켄번즈 모션) 분리
        # 전경 크기 계산
        src_w, src_h = _get_image_size(img_path)
        fg_w = int(zoom * OUT_W)
        fg_h = int(fg_w * src_h / src_w)
        fg_w = fg_w // 2 * 2  # 짝수로
        fg_h = fg_h // 2 * 2

        # zoompan 필터를 전경 크기에 맞게 재생성
        frames = int(adjusted_duration * FPS)
        zp_base = f"s={fg_w}x{fg_h}:fps={FPS}:d={frames}"
        z = zoom_range
        step = (z - 1.0) / frames

        # 효과별 zoompan (전경 전용)
        if "zoom_in" in effect:
            z_expr = f"1+{step:.8f}*on"
        elif "zoom_out" in effect:
            z_expr = f"{z}-{step:.8f}*on"
        elif "pan" in effect:
            z_expr = f"{z}"
        else:
            z_expr = f"1+{step:.8f}*on"

        if "pan_left" in effect:
            x_expr = f"(iw-iw/zoom)*(1-on/{frames})"
            y_expr = "ih/2-ih/zoom/2"
        elif "pan_right" in effect:
            x_expr = f"(iw-iw/zoom)*(on/{frames})"
            y_expr = "ih/2-ih/zoom/2"
        elif "pan_up" in effect:
            x_expr = "iw/2-iw/zoom/2"
            y_expr = f"(ih-ih/zoom)*(1-on/{frames})"
        elif "pan_down" in effect:
            x_expr = "iw/2-iw/zoom/2"
            y_expr = f"(ih-ih/zoom)*(on/{frames})"
        else:
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"

        fg_zoompan = f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':{zp_base}{speed_filter}"

        main_filter = (
            f"[0:v]split=2[bg_in][fg_in];"
            f"[bg_in]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},gblur=sigma=40[bg];"
            f"[fg_in]{fg_zoompan}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1"
        )

    if subtitle_png:
        filter_complex = (
            f"{main_filter}[kb];"
            f"[1:v]format=rgba[sub];"
            f"[kb][sub]overlay=0:0:shortest=1[vout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(img_path),
            "-loop", "1", "-i", str(subtitle_png),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "2:a",
            "-t", str(duration),
        ] + ENCODER_VIDEO + ENCODER_AUDIO + ["-shortest", str(out_path)]
    else:
        filter_complex = f"{main_filter}[vout]"
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(img_path),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "1:a",
            "-t", str(duration),
        ] + ENCODER_VIDEO + ENCODER_AUDIO + ["-shortest", str(out_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return result.returncode == 0, result.stderr


def concat_with_transition(segment_files, output_path, tmp_dir, transition,
                           title=None, font_path=None, font_color="white",
                           bgm=None, bgm_volume=0.3, bgm_fade=1.5,
                           total_duration=None, fill=False, zoom=1.1,
                           watermark=None, watermark_position="bottom_right",
                           watermark_color="white", watermark_opacity=0.7,
                           intro=None, outro=None):
    """세그먼트들을 하나의 영상으로 합친다.

    transition > 0이면 xfade 필터로 전환 효과 적용.
    transition == 0이면 concat demuxer 사용 (빠르고 단순).
    제목/워터마크 오버레이와 BGM 믹싱도 처리한다.
    """
    title_png = None
    if title and font_path:
        title_png = create_title_overlay(title, font_path, color=font_color,
                                        fill=fill, zoom=zoom, tmp_dir=tmp_dir)

    watermark_png = None
    if watermark and font_path:
        watermark_png = create_watermark_overlay(
            watermark, font_path,
            position=watermark_position,
            color=watermark_color,
            opacity=watermark_opacity,
            tmp_dir=tmp_dir,
        )

    if transition > 0 and len(segment_files) > 1:
        _concat_xfade(segment_files, output_path, tmp_dir, transition,
                      title_png, bgm, bgm_volume, bgm_fade, total_duration,
                      watermark_png=watermark_png,
                      intro=intro, outro=outro)
    else:
        ok, stderr = concat_segments(
            segment_files, output_path, tmp_dir,
            title_png=title_png,
            bgm=bgm, bgm_volume=bgm_volume, bgm_fade=bgm_fade,
            total_duration=total_duration,
            intro=intro, outro=outro,
            watermark_png=watermark_png,
        )
        if not ok:
            print("  합치기 실패 (demuxer):")
            for line in stderr.strip().splitlines()[-5:]:
                print(f"    {line}")
            sys.exit(1)


def _concat_xfade(segment_files, output_path, tmp_dir, transition,
                  title_png, bgm, bgm_volume, bgm_fade, total_duration,
                  watermark_png=None, intro=None, outro=None):
    """xfade 필터로 세그먼트 간 크로스페이드 전환 효과를 적용한다."""
    # 인트로/아웃트로를 세그먼트 목록에 포함
    all_segments = []
    if intro is not None:
        all_segments.append(intro)
    all_segments.extend(segment_files)
    if outro is not None:
        all_segments.append(outro)

    n = len(all_segments)

    # 각 세그먼트 입력
    cmd = ["ffmpeg", "-y"]
    for seg in all_segments:
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

    # xfade 체인: [0][1]xfade...[v01]; [v01][2]xfade...[v012]; ...
    # 각 세그먼트의 길이를 기반으로 offset 계산
    # 모든 세그먼트가 동일한 duration이라고 가정 (total_duration / n)
    seg_dur = (total_duration / n) if total_duration else 3.0
    offset = seg_dur - transition  # 첫 번째 전환 시작 시간

    if n == 2:
        fc_parts.append(
            f"[0:v][1:v]xfade=transition=fade:duration={transition}:offset={offset:.3f}[vx]"
        )
        vx_label = "[vx]"
        # 오디오 concat
        fc_parts.append(
            f"[0:a][1:a]acrossfade=d={transition}[ax]"
        )
        ax_label = "[ax]"
    else:
        # 비디오 xfade 체인
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

        # 오디오 acrossfade 체인
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
        vafter_title_label = "[vafter_title]"
    else:
        vafter_title_label = vx_label

    # 워터마크 오버레이 (페이드 없이 고정)
    if watermark_idx is not None:
        fc_parts.append(
            f"[{watermark_idx}:v]format=rgba[wm];"
            f"{vafter_title_label}[wm]overlay=0:0:shortest=1[vfinal]"
        )
        vfinal_label = "[vfinal]"
    else:
        vfinal_label = vafter_title_label

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
        # xfade 실패 시 demuxer로 폴백
        ok, stderr = concat_segments(
            segment_files, output_path, tmp_dir,
            title_png=title_png,
            bgm=bgm, bgm_volume=bgm_volume, bgm_fade=bgm_fade,
            total_duration=total_duration,
            intro=intro, outro=outro,
            watermark_png=watermark_png,
        )
        if not ok:
            print("  합치기 실패 (demuxer):")
            for line in stderr.strip().splitlines()[-5:]:
                print(f"    {line}")
            sys.exit(1)


def build_parser():
    """CLI 인자 파서를 생성하여 반환한다."""
    parser = argparse.ArgumentParser(
        description="이미지 디렉토리로 세로형 숏폼 영상(9:16, 1080x1920)을 생성합니다.",
        fromfile_prefix_chars="@",
        epilog=(
            "예시:\n"
            "  %(prog)s -s ./img                     # 기본 (랜덤 효과)\n"
            "  %(prog)s -s ./img --fill              # 전체 채우기\n"
            "  %(prog)s -s ./img --effect zoom_in    # 효과 지정\n"
            "  %(prog)s -s ./img --bgm music.mp3     # BGM 추가\n"
            "  %(prog)s @preset.txt                  # 프리셋 파일"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 소스 옵션
    src_group = parser.add_argument_group("소스 옵션")
    src_group.add_argument(
        "--src", "-s",
        required=True,
        help="이미지 소스 디렉토리 (필수)",
    )
    src_group.add_argument(
        "--ext", "-e",
        nargs="+",
        default=["jpg", "png", "jpeg", "webp", "heic"],
        help="이미지 파일 확장자들 (기본: jpg png jpeg webp heic)",
    )

    # 출력 옵션
    out_group = parser.add_argument_group("출력 옵션")
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
    fx_group = parser.add_argument_group("효과 옵션")
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

    add_title_args(parser)
    add_subtitle_args(parser)
    add_bgm_args(parser)
    add_display_args(parser)
    add_speed_args(parser)
    add_audio_args(parser)
    add_watermark_args(parser)
    add_intro_outro_args(parser)

    return parser


def run(args):
    """실제 작업 수행 (서브커맨드에서도 호출됨)"""
    output_path = Path(args.out).expanduser()
    font_path = resolve_font_path(args)

    print("=== 이미지 숏폼 영상 생성기 ===")
    print(f"소스 디렉토리: {args.src}")
    print(f"출력 파일: {output_path}")
    print(f"확장자: {' '.join(args.ext)}")
    print(f"이미지당 길이: {args.duration}초")
    print(f"효과: {args.effect}")
    print(f"줌 범위: 1.0 ~ {args.zoom_range}")
    print(f"전환 효과: {args.transition}초" if args.transition > 0 else "전환 효과: 없음")
    print(f"셔플: {'켜짐' if args.shuffle else '꺼짐'}")
    if args.speed != 1.0:
        print(f"재생 속도: {args.speed}x")
    if args.mute:
        print("음소거: 켜짐 (무음 트랙 유지)")
    if args.title:
        print(f"제목: {args.title}")
    if args.subtitle:
        print(f"자막: {args.subtitle}")
    if args.bgm:
        print(f"BGM: {args.bgm} (볼륨: {args.bgm_volume})")
    if args.watermark:
        print(f"워터마크: {args.watermark} ({args.watermark_position})")
    if args.intro:
        print(f"인트로: {args.intro}")
    if args.outro:
        print(f"아웃트로: {args.outro}")
    print()

    # 이미지 검색
    images = find_media_files(Path(args.src).expanduser(), args.ext, recursive=False)
    if not images:
        ext_list = ", ".join(f"*.{e}" for e in args.ext)
        print(f"오류: {args.src} 에서 이미지 파일을 찾을 수 없습니다. ({ext_list})")
        sys.exit(1)

    print(f"{len(images)}개 이미지 발견:")
    for img in images:
        print(f"  {img.name}")
    print()

    if args.shuffle:
        random.shuffle(images)
        print("이미지 순서를 무작위로 섞었습니다.")
        print()

    subtitles = args.subtitle.split("|") if args.subtitle else []

    with tempfile.TemporaryDirectory(prefix="make_image_shorts_") as tmp_dir:
        segment_files = []
        processed = 0
        skipped = 0

        # 인트로/아웃트로 준비
        intro_seg, outro_seg = prepare_intro_outro(
            args.intro, args.outro, OUT_W, OUT_H, tmp_dir
        )
        if args.intro and intro_seg is None:
            print(f"경고: 인트로 파일 변환 실패 ({args.intro}). 건너뜁니다.")
        if args.outro and outro_seg is None:
            print(f"경고: 아웃트로 파일 변환 실패 ({args.outro}). 건너뜁니다.")

        for i, img_path in enumerate(images):
            print(f"[{i + 1}/{len(images)}] 처리중: {img_path.name}")

            # 효과 결정
            effect = get_effect(args.effect)
            print(f"  효과: {effect}")

            seg_path = Path(tmp_dir) / f"seg_{i:04d}.mp4"

            # 자막 PNG 생성 (해당 이미지에 자막이 있는 경우)
            sub_png = None
            if subtitles and i < len(subtitles) and subtitles[i].strip():
                sub_png = create_subtitle_overlay(
                    subtitles[i].strip(), font_path,
                    zoom=args.zoom, fill=args.fill,
                    color=args.subtitle_color,
                    tmp_dir=tmp_dir, index=i,
                )

            ok, err = process_image_segment(
                img_path, effect, args.duration, args.zoom_range,
                seg_path, subtitle_png=sub_png,
                fill=args.fill, zoom=args.zoom,
                speed=args.speed, tmp_dir=tmp_dir,
            )

            if not ok:
                print(f"  세그먼트 변환 실패:")
                err_lines = [l for l in err.strip().splitlines() if l.strip()]
                for line in err_lines[-3:]:
                    print(f"    {line}")
                skipped += 1
                continue

            segment_files.append(seg_path)
            processed += 1
            print(f"  완료 -> {seg_path.name}")

        print()
        print(f"처리 완료: {processed}개 | 건너뜀: {skipped}개")

        if not segment_files:
            print("오류: 변환된 세그먼트가 없습니다. 종료합니다.")
            sys.exit(1)

        total_duration = processed * args.duration

        if len(segment_files) == 1:
            print("세그먼트 1개 — 후처리(제목/BGM)를 적용합니다...")
            # 단일 세그먼트도 제목/BGM 처리를 위해 concat 경로 사용
            concat_with_transition(
                segment_files, output_path, tmp_dir,
                transition=0,
                title=args.title, font_path=font_path, font_color=args.font_color,
                bgm=args.bgm, bgm_volume=args.bgm_volume, bgm_fade=args.bgm_fade,
                total_duration=total_duration, fill=args.fill, zoom=args.zoom,
                watermark=args.watermark,
                watermark_position=args.watermark_position,
                watermark_color=args.watermark_color,
                watermark_opacity=args.watermark_opacity,
                intro=intro_seg, outro=outro_seg,
            )
        else:
            print(f"{len(segment_files)}개 세그먼트 합치기 -> {output_path}")
            concat_with_transition(
                segment_files, output_path, tmp_dir,
                transition=args.transition,
                title=args.title, font_path=font_path, font_color=args.font_color,
                bgm=args.bgm, bgm_volume=args.bgm_volume, bgm_fade=args.bgm_fade,
                total_duration=total_duration, fill=args.fill, zoom=args.zoom,
                watermark=args.watermark,
                watermark_position=args.watermark_position,
                watermark_color=args.watermark_color,
                watermark_opacity=args.watermark_opacity,
                intro=intro_seg, outro=outro_seg,
            )

    # 결과 확인
    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print()
        print(f"완료! 출력 파일: {output_path} ({size_mb:.1f} MB)")
        print(f"총 영상 길이: ~{total_duration:.1f}초 ({processed}개 이미지)")
        if args.transition > 0 and processed > 1:
            net_duration = total_duration - args.transition * (processed - 1)
            print(f"전환 효과 적용 후 실제 길이: ~{net_duration:.1f}초")
    else:
        print("오류: 출력 파일을 찾을 수 없습니다.")
        sys.exit(1)


def main():
    """CLI 진입점 — 인자를 파싱하고 run()을 호출한다."""
    parser = build_parser()
    argcomplete.autocomplete(parser)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
