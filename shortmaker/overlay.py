"""제목/자막 오버레이 PNG 생성 — Pillow 기반"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import OUT_W, OUT_H
from .color import parse_rgba


def create_title_overlay(title, font_path, width=OUT_W, height=OUT_H,
                         zoom=1.1, fill=False, color="white", tmp_dir=None):
    """제목 텍스트 PNG를 생성한다.

    fill=True: 전체 채우기 모드 → 상단 120px 고정
    fill=False: 블러 배경 모드 → 상단 여백과 전경 이미지 사이 세로 중앙
    """
    try:
        font = ImageFont.truetype(font_path, 80)
    except Exception:
        return None

    # 텍스트 크기 측정
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), title, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    x = (width - tw) // 2
    if fill:
        y = 120
    else:
        fg_h = zoom * width * 9 / 16
        gap_top = (height - fg_h) / 2
        y = int((gap_top - th) / 2)

    rgba = parse_rgba(color, default=(255, 255, 255, 255))
    draw.text((x, y), title, font=font, fill=rgba)

    out_path = Path(tmp_dir) / "title_overlay.png"
    img.save(str(out_path))
    return str(out_path)


def create_subtitle_overlay(text, font_path, width=OUT_W, height=OUT_H,
                            zoom=1.1, fill=False, color="black",
                            tmp_dir=None, index=0):
    """자막 텍스트 PNG를 생성한다.

    fill=True: 영상 하단에서 20px 아래
    fill=False: 전경 영상 하단에서 20px 아래
    """
    try:
        font = ImageFont.truetype(font_path, 56)
    except Exception:
        return None

    rgba = parse_rgba(color, default=(0, 0, 0, 255))

    # 텍스트 크기 측정
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    if fill:
        y = height - 140
    else:
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
