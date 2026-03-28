#!/usr/bin/env python3
"""
extract_frames.py - DJI 영상 클립에서 랜덤 프레임 추출

각 영상 클립에서 랜덤 타임스탬프의 프레임 한 장을 JPG로 추출합니다.

사용법:
    python3 extract_frames.py                          # 기본 (SD카드, 오늘 날짜)
    python3 extract_frames.py -d 20260318              # 특정 날짜
    python3 extract_frames.py -s ./clips -o ./out      # 로컬 폴더 (날짜 필터 무시)
    python3 extract_frames.py --no-enhance             # 색보정 없이
    python3 extract_frames.py --level                  # 수평 보정 (실험적)
"""

import argparse
import json
import math
import random
import subprocess
import sys
from datetime import date
from pathlib import Path

import cv2
import numpy as np


def get_video_duration(filepath: Path) -> float | None:
    """ffprobe로 영상 길이(초)를 반환한다. 실패 시 None."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(filepath),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        duration = float(info["format"]["duration"])
        return duration if duration > 0 else None
    except (subprocess.CalledProcessError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"  경고: {filepath.name} 길이 읽기 실패: {exc}", file=sys.stderr)
        return None


def extract_frame(filepath: Path, timestamp: float, out_path: Path, enhance: bool = False) -> bool:
    """지정된 타임스탬프에서 프레임 한 장을 추출하여 JPG로 저장한다."""
    vf_filters = []
    if enhance:
        # 아이폰 스타일 자동 보정: 대비, 채도, 선명도, 밝기
        vf_filters = [
            "eq=contrast=1.15:brightness=0.03:saturation=1.25",
            "unsharp=5:5:0.8:5:5:0.0",
        ]
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", f"{timestamp:.3f}",
        "-i", str(filepath),
        "-frames:v", "1",
        "-q:v", "2",
    ]
    if vf_filters:
        cmd += ["-vf", ",".join(vf_filters)]
    cmd.append(str(out_path))
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"  경고: {filepath.name} ffmpeg 실패: {exc.stderr.decode().strip()}", file=sys.stderr)
        return False


def level_horizon(path: Path) -> float | None:
    """수평선을 감지하여 이미지를 회전 보정한다. 보정 각도 또는 None 반환.

    상단 영역(10~45%)에서 Canny + HoughLinesP로 수평선을 감지한다.
    각도 분산이 크면(건물 등 복잡한 씬) 건너뛰고,
    보정 각도가 0.3° 미만이면 이미 수평으로 판단한다.
    """
    img = cv2.imread(str(path))
    if img is None:
        return None
    h, w = img.shape[:2]

    # 수평선이 있을 가능성이 높은 상단 영역
    y_start, y_end = int(h * 0.1), int(h * 0.45)
    roi = img[y_start:y_end, :]

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 30, 100)

    lines = cv2.HoughLinesP(edges, 1, math.pi / 180, threshold=50,
                            minLineLength=w // 8, maxLineGap=30)
    if lines is None:
        return None

    # 수평에 가까운 선(±20°)과 길이 수집
    candidates = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        length = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        if abs(angle) < 20:
            candidates.append((angle, length))

    if not candidates:
        return None

    # 각도 분산 체크 — 분산이 크면 건물 등 복잡한 씬으로 판단
    angles_only = [a for a, _ in candidates]
    angle_std = float(np.std(angles_only))
    if angle_std > 6.0:
        return None

    # 길이 가중 평균으로 보정 각도 계산
    correction = sum(a * l for a, l in candidates) / sum(l for _, l in candidates)
    if abs(correction) < 0.3:
        return None  # 이미 수평
    if abs(correction) > 15.0:
        return None  # 너무 큰 값은 오감지

    # 회전 후 중앙 크롭
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, correction, 1.0)
    cos_a = abs(math.cos(math.radians(correction)))
    sin_a = abs(math.sin(math.radians(correction)))
    new_w = int(h * sin_a + w * cos_a)
    new_h = int(h * cos_a + w * sin_a)
    matrix[0, 2] += (new_w - w) / 2
    matrix[1, 2] += (new_h - h) / 2
    rotated = cv2.warpAffine(img, matrix, (new_w, new_h), borderMode=cv2.BORDER_REPLICATE)

    cx, cy = new_w // 2, new_h // 2
    crop = rotated[cy - h // 2:cy + h // 2, cx - w // 2:cx + w // 2]
    cv2.imwrite(str(path), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return correction


def find_video_files(src: Path, date_str: str | None, ext: str) -> list[Path]:
    """날짜 문자열로 필터링하여 영상 파일 목록을 반환한다. date_str이 None이면 전체."""
    ext_lower = ext.lower().lstrip(".")
    matches = []
    for p in src.rglob(f"*.{ext_lower}"):
        if date_str is None or date_str in p.name:
            matches.append(p)
    # 대소문자 구분 파일시스템 대응
    if not matches:
        for p in src.rglob(f"*.{ext.upper()}"):
            if date_str is None or date_str in p.name:
                matches.append(p)
    # 중복 제거
    seen = set()
    unique = []
    for p in matches:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return sorted(unique)


def main() -> None:
    today = date.today().strftime("%Y%m%d")

    parser = argparse.ArgumentParser(
        description="DJI 영상 클립에서 랜덤 프레임을 추출합니다.",
    )
    parser.add_argument(
        "--date", "-d",
        default=today,
        metavar="YYYYMMDD",
        help=f"파일명에서 매칭할 날짜 (기본: {today})",
    )
    parser.add_argument(
        "--src", "-s",
        default="/Volumes/SD_Card/DCIM",
        metavar="DIR",
        help="영상 소스 디렉토리 (기본: /Volumes/SD_Card/DCIM)",
    )
    parser.add_argument(
        "--out", "-o",
        default="./img",
        metavar="DIR",
        help="이미지 출력 디렉토리 (기본: ./img)",
    )
    parser.add_argument(
        "--ext", "-e",
        default="MP4",
        metavar="EXT",
        help="영상 파일 확장자 (기본: MP4)",
    )
    parser.add_argument(
        "--enhance", action="store_true", default=True,
        help="아이폰 스타일 색보정 적용 (기본: 켜짐)",
    )
    parser.add_argument(
        "--no-enhance", dest="enhance", action="store_false",
        help="색보정 끄기",
    )
    parser.add_argument(
        "--level", action="store_true", default=False,
        help="수평 자동 보정 (실험적, 기본: 꺼짐)",
    )
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)

    # --src를 직접 지정한 경우 날짜 필터 무시
    src_explicitly_set = args.src != "/Volumes/SD_Card/DCIM"
    date_str = None if src_explicitly_set else args.date

    if not src.exists():
        print(f"오류: 소스 디렉토리가 존재하지 않습니다: {src}", file=sys.stderr)
        sys.exit(1)

    out.mkdir(parents=True, exist_ok=True)

    label = f"*{date_str}*.{args.ext}" if date_str else f"*.{args.ext}"
    print(f"{src}에서 {label} 검색중 ...")
    video_files = find_video_files(src, date_str, args.ext)

    total = len(video_files)
    print(f"{total}개 파일 발견.\n")

    if total == 0:
        print("처리할 파일이 없습니다.")
        return

    extracted = 0
    skipped = 0

    for i, filepath in enumerate(video_files, start=1):
        print(f"[{i}/{total}] {filepath.name}")

        duration = get_video_duration(filepath)
        if duration is None:
            print(f"  건너뜀: 유효하지 않은 영상")
            skipped += 1
            continue

        timestamp = random.uniform(0, duration)
        print(f"  길이: {duration:.2f}초  |  선택 지점: {timestamp:.3f}초")

        out_path = out / (filepath.stem + ".jpg")
        success = extract_frame(filepath, timestamp, out_path, enhance=args.enhance)
        if success:
            if args.level:
                angle = level_horizon(out_path)
                if angle is not None:
                    print(f"  수평 보정: {angle:+.1f}°")
            print(f"  저장: {out_path}")
            extracted += 1
        else:
            skipped += 1

    print(f"\n완료. 전체: {total}  |  추출: {extracted}  |  건너뜀: {skipped}")


if __name__ == "__main__":
    main()
