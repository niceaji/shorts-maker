"""ffmpeg 관련 유틸리티 — 인코더 설정, 보정 필터, concat, BGM 믹싱"""

import subprocess
from pathlib import Path

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


def concat_segments(segment_files, output_path, tmp_dir, *,
                    title_png=None, bgm=None, bgm_volume=0.3, bgm_fade=1.5,
                    total_duration=None):
    """concat demuxer로 세그먼트를 합친다.

    제목 오버레이(페이드인)와 BGM 믹싱을 선택적으로 적용.
    반환값: (성공 여부, stderr 문자열)
    """
    list_file = Path(tmp_dir) / "segments.txt"
    with open(list_file, "w") as f:
        for seg in segment_files:
            f.write(f"file '{seg}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
    ]

    input_idx = 1
    vf_parts = []
    af_parts = []

    # 제목 오버레이
    if title_png:
        cmd += ["-loop", "1", "-i", title_png]
        vf_parts.append(
            f"[{input_idx}:v]format=rgba,fade=t=in:st=0:d=1:alpha=1[title];"
            f"[0:v][title]overlay=0:0:shortest=1[vout]"
        )
        input_idx += 1

    # BGM
    if bgm:
        cmd += ["-i", str(bgm)]
        af_parts.append(build_bgm_filter(input_idx, bgm_volume, bgm_fade, total_duration or 30))
        input_idx += 1

    # filter_complex 조합
    fc = vf_parts + af_parts
    if fc:
        cmd += ["-filter_complex", ";".join(fc)]

    # 매핑
    if vf_parts and af_parts:
        cmd += ["-map", "[vout]", "-map", "[aout]"]
    elif vf_parts:
        cmd += ["-map", "[vout]", "-map", "0:a"]
    elif af_parts:
        cmd += ["-map", "0:v", "-map", "[aout]"]

    cmd += ENCODER_ARGS + ["-movflags", "+faststart", str(output_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return result.returncode == 0, result.stderr
