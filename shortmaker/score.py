"""영상 구간 스코어링 — 움직임 + 오디오 기반 하이라이트 감지"""

import subprocess
import time
import numpy as np
from pathlib import Path


def analyze_motion(video_path: Path, interval: float = 0.5) -> list[tuple[float, float]]:
    """프레임 간 차이로 움직임 점수를 계산한다.

    interval 간격으로 프레임을 추출하고, 인접 프레임 간 차이의 평균을 구한다.
    반환값: [(timestamp, motion_score), ...]
    """
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_interval = max(1, int(fps * interval))

    scores = []
    prev_gray = None
    frame_idx = 0
    t_start = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            # 진행률 표시
            if total_frames > 0:
                pct = frame_idx / total_frames * 100
                elapsed = time.time() - t_start
                m, s = divmod(int(elapsed), 60)
                print(f"\r  움직임 분석 중... {pct:.0f}% ({m}분 {s:02d}초)" if m
                      else f"\r  움직임 분석 중... {pct:.0f}% ({s}초)",
                      end="", flush=True)

            # 작은 크기로 리사이즈하여 성능 향상
            small = cv2.resize(frame, (320, 180))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            if prev_gray is not None:
                diff = cv2.absdiff(prev_gray, gray)
                motion = float(diff.mean())
                timestamp = frame_idx / fps
                scores.append((timestamp, motion))

            prev_gray = gray

        frame_idx += 1

    cap.release()
    print()  # 줄바꿈
    return scores


def analyze_audio(video_path: Path, window: float = 0.5, filter_wind: bool = True) -> list[tuple[float, float]]:
    """오디오 RMS 볼륨을 구간별로 계산한다.

    ffmpeg로 오디오를 raw PCM 추출 → numpy로 RMS 계산.
    filter_wind=True면 200Hz~3000Hz 대역만 분석 (바람소리 제거).
    반환값: [(timestamp, rms_score), ...]
    """
    af = "highpass=f=200,lowpass=f=3000" if filter_wind else None
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn",
    ]
    if af:
        cmd += ["-af", af]
    cmd += [
        "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        "-f", "s16le", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, Exception):
        return []

    samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)
    if len(samples) == 0:
        return []

    sample_rate = 16000
    window_samples = int(sample_rate * window)

    scores = []
    for i in range(0, len(samples) - window_samples, window_samples):
        chunk = samples[i:i + window_samples]
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        timestamp = i / sample_rate
        scores.append((timestamp, rms))

    return scores


def _normalize(scores: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """점수를 0~1 범위로 정규화한다."""
    if not scores:
        return []
    values = [s for _, s in scores]
    min_v, max_v = min(values), max(values)
    if max_v - min_v < 1e-6:
        return [(t, 0.5) for t, _ in scores]
    return [(t, (v - min_v) / (max_v - min_v)) for t, v in scores]


def score_segments(video_path: Path, seg_duration: float,
                   video_duration: float, interval: float = 0.5,
                   motion_weight: float = 0.6, audio_weight: float = 0.4,
                   no_audio: bool = False,
                   ) -> list[tuple[float, float]]:
    """영상의 각 가능한 세그먼트 위치에 대해 종합 점수를 매긴다.

    interval 간격으로 후보 위치를 평가.
    no_audio=True면 움직임만으로 스코어링 (바람 등 환경 소음이 심할 때).
    반환값: [(start_time, combined_score), ...] — 점수 내림차순 정렬
    """
    t0 = time.time()
    motion_raw = analyze_motion(video_path, interval)

    if no_audio:
        audio_raw = []
        motion_weight = 1.0
        audio_weight = 0.0
        print("  오디오 분석 건너뜀 (--no-audio-score)")
    else:
        t1 = time.time()
        print("  오디오 분석 중...", end="", flush=True)
        audio_raw = analyze_audio(video_path, interval)
        elapsed = time.time() - t1
        m_a, s_a = divmod(int(elapsed), 60)
        print(f"\r  오디오 분석 완료: {m_a}분 {s_a:02d}초" if m_a
              else f"\r  오디오 분석 완료: {s_a}초")

    total = time.time() - t0
    m, s = divmod(int(total), 60)
    print(f"  총 분석 시간: {m}분 {s:02d}초" if m else f"  총 분석 시간: {s}초")

    motion = _normalize(motion_raw)
    audio = _normalize(audio_raw)

    # 타임스탬프 → 점수 딕셔너리
    motion_dict = {round(t / interval) * interval: s for t, s in motion}
    audio_dict = {round(t / interval) * interval: s for t, s in audio}

    # 모든 가능한 세그먼트 시작 위치에 대해 윈도우 평균 점수 계산
    max_start = video_duration - seg_duration
    if max_start <= 0:
        return [(0.0, 1.0)]

    candidates = []
    step = interval
    t = 0.0
    while t <= max_start:
        # 이 세그먼트 윈도우에 해당하는 점수들을 평균
        window_motion = []
        window_audio = []
        wt = t
        while wt < t + seg_duration:
            key = round(wt / interval) * interval
            if key in motion_dict:
                window_motion.append(motion_dict[key])
            if key in audio_dict:
                window_audio.append(audio_dict[key])
            wt += interval

        m_score = sum(window_motion) / len(window_motion) if window_motion else 0
        a_score = sum(window_audio) / len(window_audio) if window_audio else 0
        combined = motion_weight * m_score + audio_weight * a_score
        candidates.append((t, combined))
        t += step

    # 점수 내림차순 정렬
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates


def pick_top_segments(candidates: list[tuple[float, float]],
                      count: int, seg_duration: float,
                      min_gap: float = None) -> list[tuple[float, float]]:
    """겹치지 않는 상위 N개 세그먼트를 선택한다.

    min_gap: 세그먼트 간 최소 간격 (기본: seg_duration, 겹침 방지)
    반환값: [(start_time, score), ...] — 시간순 정렬
    """
    if min_gap is None:
        min_gap = seg_duration

    selected = []
    for start, score in candidates:
        # 이미 선택된 세그먼트와 겹치지 않는지 확인
        overlap = False
        for sel_start, _ in selected:
            if abs(start - sel_start) < min_gap:
                overlap = True
                break
        if not overlap:
            selected.append((start, score))
        if len(selected) >= count:
            break

    # 시간순 정렬
    selected.sort(key=lambda x: x[0])
    return selected
