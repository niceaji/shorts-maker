"""색상 문자열 파싱 유틸리티"""

from PIL import ImageColor


def parse_rgba(color: str, default: tuple = (255, 255, 255, 255)) -> tuple[int, ...]:
    """색상 문자열을 RGBA 튜플로 변환한다. 실패 시 default 반환.

    지원 형식: 색상 이름(white, red), hex(#FF5500), rgba
    """
    try:
        rgb = ImageColor.getrgb(color)
        return rgb + (255,) if len(rgb) == 3 else rgb
    except ValueError:
        return default
