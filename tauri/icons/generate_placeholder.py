"""Generate a 1024x1024 placeholder Kompany icon (green K on black).

Run once before the first `cargo tauri build` if no designer asset is
available. Output: `tauri/icons/icon.png`. After this, run:

    cd tauri && cargo tauri icon icons/icon.png

to materialise the per-platform multi-size set.

Depends on Pillow only. Not bundled in the Python package; the script
is a build-time convenience.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def render(out: Path, size: int = 1024) -> None:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)

    # Neon green border, mimicking the cyberpunk UI frame.
    border = int(size * 0.045)
    draw.rectangle(
        [(border, border), (size - border, size - border)],
        outline=(0, 255, 65, 255),
        width=border // 4 or 4,
    )

    # Big "K" centered. Try a couple of common font paths; fall back
    # to default bitmap font if none are available.
    font = None
    for path in (
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "C:/Windows/Fonts/consolab.ttf",
    ):
        try:
            font = ImageFont.truetype(path, int(size * 0.65))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    text = "K"
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - w) // 2 - bbox[0]
    y = (size - h) // 2 - bbox[1]
    draw.text((x, y), text, fill=(0, 255, 65, 255), font=font)

    img.save(out, "PNG")
    print(f"wrote {out}")


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    render(here / "icon.png")
