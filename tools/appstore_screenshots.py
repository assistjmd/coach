"""Resize iPhone screenshots to a size App Store Connect will accept.

A screenshot taken on an iPhone 16 Pro is 1206x2622. App Store Connect does not
accept that size — it wants 6.9 inch (1320x2868) or 6.5 inch (1242x2688) — and
rejects the upload without saying which sizes it would take.

Scaling to fit and padding the remainder, rather than stretching: the aspect
ratios are close but not identical, and a stretched screenshot of a fitness app
makes the ring look like an oval. Padding is in the app's own background colour,
so on a dark screenshot the seam is invisible.

    python3 tools/appstore_screenshots.py ~/Downloads/IMG_1893.PNG ...
"""

import pathlib
import sys

from PIL import Image

# 6.5 inch. App Store Connect validates against the slot you drop into, not
# against any accepted size, so this must match the box on screen.
TARGET = (1284, 2778)
# Theme.Palette.bg. Padding in anything else would show as a band.
BG = (0x0B, 0x0B, 0x0D)
OUT = pathlib.Path.home() / "Downloads" / "appstore-screenshots"


def convert(path: pathlib.Path) -> pathlib.Path:
    img = Image.open(path).convert("RGB")
    scale = min(TARGET[0] / img.width, TARGET[1] / img.height)
    resized = img.resize(
        (round(img.width * scale), round(img.height * scale)), Image.LANCZOS
    )
    canvas = Image.new("RGB", TARGET, BG)
    canvas.paste(
        resized,
        ((TARGET[0] - resized.width) // 2, (TARGET[1] - resized.height) // 2),
    )
    OUT.mkdir(parents=True, exist_ok=True)
    # Numbered, because App Store Connect orders screenshots by upload and
    # dragging five files in at once does not preserve the order you picked.
    dest = OUT / f"{path.stem}.png"
    canvas.save(dest, "PNG")
    return dest


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for arg in sys.argv[1:]:
        src = pathlib.Path(arg).expanduser()
        if not src.exists():
            print(f"missing: {src}")
            continue
        out = convert(src)
        print(f"{src.name}  ->  {out}  {TARGET[0]}x{TARGET[1]}")
