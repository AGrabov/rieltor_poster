"""Згенерувати іконку застосунку (assets/icon.ico + icon.png).

Проста пласка «хатинка» у фірмовому синьому — тема нерухомості (🏠), яку
використовує дашборд. Малюємо у високій роздільності й зменшуємо для гладких країв.

Запуск:
    uv run python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # supersample, потім зменшуємо
BLUE = (30, 91, 214, 255)  # #1E5BD6
WHITE = (255, 255, 255, 255)


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Округлений квадрат-підкладка
    pad = 56
    d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=210, fill=BLUE)

    # Дах (з виступом ширше за корпус)
    d.polygon([(512, 268), (236, 540), (788, 540)], fill=WHITE)
    # Корпус
    d.rectangle([320, 520, 704, 800], fill=WHITE)
    # Двері (виріз кольором підкладки)
    d.rounded_rectangle([462, 624, 566, 800], radius=14, fill=BLUE)
    # Вікно
    d.rounded_rectangle([360, 576, 448, 664], radius=10, fill=BLUE)

    return img.resize((256, 256), Image.LANCZOS)


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "assets"
    out_dir.mkdir(exist_ok=True)
    icon = build()
    icon.save(out_dir / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.save(out_dir / "icon.png")
    print("wrote", out_dir / "icon.ico", "and icon.png")


if __name__ == "__main__":
    main()
