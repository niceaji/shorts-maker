"""인물 감지 유틸리티 — OpenCV HOG 기반 사람/얼굴 위치 탐지"""


def detect_person_position(image_path_or_frame) -> str:
    """이미지에서 사람 위치를 감지하여 크롭 방향을 반환한다.

    반환값: "left", "center", "right" (가로 영상에서 세로 크롭 시 어느 쪽을 기준으로 할지)

    OpenCV HOG person detector 사용. 감지 실패 시 "center" 반환.
    """
    try:
        import cv2  # 선택적 의존성 — 없으면 "center" 반환
        import numpy as np
    except ImportError:
        return "center"

    # 이미지 로드 (경로 문자열 또는 numpy 배열 모두 지원)
    if isinstance(image_path_or_frame, (str, bytes)):
        frame = cv2.imread(str(image_path_or_frame))
        if frame is None:
            return "center"
    else:
        frame = image_path_or_frame

    if frame is None or frame.size == 0:
        return "center"

    # 감지 성능을 위해 가로 640px 기준으로 리사이즈
    orig_w = frame.shape[1]
    if orig_w > 640:
        scale = 640.0 / orig_w
        new_w = 640
        new_h = int(frame.shape[0] * scale)
        frame_resized = cv2.resize(frame, (new_w, new_h))
    else:
        scale = 1.0
        frame_resized = frame

    # OpenCV 내장 HOG 보행자 감지기 초기화
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    # 다중 스케일 감지 수행
    detections, weights = hog.detectMultiScale(
        frame_resized,
        winStride=(8, 8),
        padding=(4, 4),
        scale=1.05,
    )

    if len(detections) == 0:
        # 감지 실패 시 기본값 반환
        return "center"

    # 감지된 사람들의 가중 중심 x 좌표 계산 (신뢰도 가중 평균)
    total_weight = 0.0
    weighted_cx = 0.0
    for (x, y, w, h), weight in zip(detections, weights):
        cx = x + w / 2.0
        weighted_cx += cx * weight
        total_weight += weight

    if total_weight == 0:
        return "center"

    center_x = weighted_cx / total_weight

    # 원본 이미지 기준 x 좌표로 환산
    img_width = frame_resized.shape[1]
    third = img_width / 3.0

    # 왼쪽 1/3, 가운데 1/3, 오른쪽 1/3으로 위치 분류
    if center_x < third:
        return "left"
    elif center_x < third * 2:
        return "center"
    else:
        return "right"
