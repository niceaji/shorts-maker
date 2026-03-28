"""ffmpeg 관련 유틸리티 — 인코더 설정, 보정 필터, concat, BGM 믹싱"""

import subprocess
from pathlib import Path

# 이미지 확장자 목록 (인트로/아웃트로 파일 타입 판별에 사용)
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

ENHANCE_FILTER = "eq=contrast=1.15:brightness=0.03:saturation=1.25,unsharp=5:5:0.8:5:5:0.0"

ENCODER_VIDEO = [
    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
    "-r", "30", "-pix_fmt", "yuv420p",
]

ENCODER_AUDIO = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100"]

ENCODER_ARGS = ENCODER_VIDEO + ENCODER_AUDIO


def build_enhance_chain(enhance: bool) -> str:
    """보정 필터 체인 문자열을 반환한다. enhance=False이면 빈 문자열."""
    return f",{ENHANCE_FILTER}" if enhance else ""


def build_bgm_filter(bgm_idx: int, volume: float, fade: float, total_duration: float) -> str:
    """BGM 오디오 필터 체인 문자열을 반환한다.

    볼륨 조절 + 페이드인 + 페이드아웃 + 원본 오디오와 믹싱
    """
    fade_out_start = max(0, total_duration - fade)
    return (
        f"[{bgm_idx}:a]volume={volume},"
        f"afade=t=in:st=0:d={fade},"
        f"afade=t=out:st={fade_out_start}:d={fade}[bgm];"
        f"[0:a][bgm]amix=inputs=2:duration=shortest:dropout_transition=0[aout]"
    )


def build_speed_filter(speed: float) -> str:
    """속도 변경 필터를 반환한다. 1.0이면 빈 문자열.

    비디오: setpts=PTS/{speed}
    오디오: atempo={speed} (0.5~2.0 범위만 지원하므로 범위 초과 시 체인으로 연결)
    """
    if speed == 1.0:
        return ""

    # 비디오 필터: PTS 조정으로 재생 속도 변경
    video_filter = f"setpts=PTS/{speed}"

    # 오디오 필터: atempo는 0.5~2.0 범위만 지원하므로 극단값은 체인 연결
    if 0.5 <= speed <= 2.0:
        audio_filter = f"atempo={speed}"
    elif speed < 0.5:
        # 0.5 미만: 두 단계로 나눠서 체인 (예: 0.25 → atempo=0.5,atempo=0.5)
        audio_filter = f"atempo=0.5,atempo={speed / 0.5}"
    else:
        # 2.0 초과: 두 단계로 나눠서 체인 (예: 4.0 → atempo=2.0,atempo=2.0)
        audio_filter = f"atempo=2.0,atempo={speed / 2.0}"

    return f"{video_filter};{audio_filter}"


def prepare_intro_outro(intro_path, outro_path, width, height, tmp_dir):
    """인트로/아웃트로 파일을 세그먼트와 호환되도록 변환한다.

    이미지면 3초 영상으로, 영상이면 1080x1920로 리사이즈.
    반환값: (intro_segment_path, outro_segment_path) - 제공되지 않으면 None
    """
    intro_out = _convert_intro_outro(intro_path, "intro", width, height, tmp_dir)
    outro_out = _convert_intro_outro(outro_path, "outro", width, height, tmp_dir)
    return intro_out, outro_out


def _convert_intro_outro(file_path, label, width, height, tmp_dir):
    """단일 인트로/아웃트로 파일을 변환하는 내부 함수.

    이미지: Ken Burns 줌인 효과가 있는 3초 영상으로 변환
    영상: 지정된 해상도로 리인코딩
    오디오 트랙이 없으면 무음 오디오를 추가한다.
    """
    if file_path is None:
        return None

    src = Path(file_path)
    ext = src.suffix.lower()
    out_path = str(Path(tmp_dir) / f"{label}_segment.mp4")

    if ext in _IMAGE_EXTS:
        # 이미지: Ken Burns 줌인 효과를 적용하여 3초 영상 생성
        vf = (
            f"scale={width * 2}:{height * 2},"
            f"zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=90:s={width}x{height}:fps=30,"
            f"scale={width}:{height}"
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(src),
            "-vf", vf,
            "-t", "3",
            "-an",  # 일단 오디오 없이 생성 후 무음 오디오 추가
        ] + ENCODER_VIDEO + [str(out_path) + ".noaudio.mp4"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return None

        # 무음 오디오 트랙 추가
        tmp_noaudio = str(out_path) + ".noaudio.mp4"
        cmd_audio = [
            "ffmpeg", "-y",
            "-i", tmp_noaudio,
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-shortest",
        ] + ENCODER_ARGS + ["-movflags", "+faststart", out_path]
        result2 = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=120)
        return out_path if result2.returncode == 0 else None

    else:
        # 영상: 지정된 해상도로 리인코딩 (오디오 유무 확인 후 처리)
        # ffprobe로 오디오 트랙 존재 여부 확인
        probe_cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json", "-show_streams",
            str(src),
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        has_audio = False
        if probe_result.returncode == 0:
            import json
            try:
                data = json.loads(probe_result.stdout)
                has_audio = any(
                    s.get("codec_type") == "audio"
                    for s in data.get("streams", [])
                )
            except (json.JSONDecodeError, KeyError):
                pass

        vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"

        if has_audio:
            cmd = [
                "ffmpeg", "-y", "-i", str(src),
                "-vf", vf,
            ] + ENCODER_ARGS + ["-movflags", "+faststart", out_path]
        else:
            # 무음 오디오 트랙 추가
            cmd = [
                "ffmpeg", "-y",
                "-i", str(src),
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-vf", vf,
                "-shortest",
            ] + ENCODER_ARGS + ["-movflags", "+faststart", out_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return out_path if result.returncode == 0 else None


def concat_segments(segment_files, output_path, tmp_dir, *,
                    title_png=None, bgm=None, bgm_volume=0.3, bgm_fade=1.5,
                    total_duration=None, intro=None, outro=None,
                    watermark_png=None):
    """concat demuxer로 세그먼트를 합친다.

    제목 오버레이(페이드인)와 BGM 믹싱을 선택적으로 적용.
    intro/outro가 제공되면 세그먼트 앞뒤에 추가한다.
    watermark_png가 제공되면 제목 오버레이 이후에 워터마크를 합성한다.
    반환값: (성공 여부, stderr 문자열)
    """
    # 인트로/아웃트로를 세그먼트 목록에 포함
    all_segments = []
    if intro is not None:
        all_segments.append(intro)
    all_segments.extend(segment_files)
    if outro is not None:
        all_segments.append(outro)

    # BGM 페이드 계산용 total_duration 조정 (인트로/아웃트로 길이 추정)
    adjusted_duration = total_duration
    if adjusted_duration is not None:
        if intro is not None:
            adjusted_duration += 3.0  # 이미지 인트로는 3초, 영상은 대략 추정
        if outro is not None:
            adjusted_duration += 3.0

    list_file = Path(tmp_dir) / "segments.txt"
    with open(list_file, "w") as f:
        for seg in all_segments:
            f.write(f"file '{seg}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
    ]

    input_idx = 1
    vf_parts = []
    af_parts = []

    # 비디오 체인 레이블 추적 (제목 → 워터마크 순으로 오버레이)
    cur_v = "[0:v]"

    # 제목 오버레이 (페이드인)
    if title_png:
        cmd += ["-loop", "1", "-i", title_png]
        vf_parts.append(
            f"[{input_idx}:v]format=rgba,fade=t=in:st=0:d=1:alpha=1[title];"
            f"{cur_v}[title]overlay=0:0:shortest=1[vafter_title]"
        )
        cur_v = "[vafter_title]"
        input_idx += 1

    # 워터마크 오버레이 (페이드 없이 고정)
    if watermark_png:
        cmd += ["-loop", "1", "-i", watermark_png]
        vf_parts.append(
            f"[{input_idx}:v]format=rgba[wm];"
            f"{cur_v}[wm]overlay=0:0:shortest=1[vout]"
        )
        cur_v = "[vout]"
        input_idx += 1
    elif title_png:
        # 제목만 있을 때 최종 레이블을 vout으로 맞춤
        vf_parts[-1] = vf_parts[-1].replace("[vafter_title]", "[vout]")
        cur_v = "[vout]"

    # BGM
    if bgm:
        cmd += ["-i", str(bgm)]
        af_parts.append(build_bgm_filter(input_idx, bgm_volume, bgm_fade, adjusted_duration or 30))
        input_idx += 1

    # filter_complex 조합
    has_vf = bool(vf_parts)
    has_af = bool(af_parts)
    fc = vf_parts + af_parts
    if fc:
        cmd += ["-filter_complex", ";".join(fc)]

    # 매핑
    if has_vf and has_af:
        cmd += ["-map", "[vout]", "-map", "[aout]"]
    elif has_vf:
        cmd += ["-map", "[vout]", "-map", "0:a"]
    elif has_af:
        cmd += ["-map", "0:v", "-map", "[aout]"]

    cmd += ENCODER_ARGS + ["-movflags", "+faststart", str(output_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return result.returncode == 0, result.stderr
