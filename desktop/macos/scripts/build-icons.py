#!/usr/bin/env python3
"""Generate Mac app icons from the original transparent mascot."""

from pathlib import Path
import subprocess
import tempfile

from PIL import Image


def main():
    script = Path(__file__).resolve()
    project = script.parents[3]
    icons = script.parents[1] / "icons"
    source = Image.open(
        project / "static/qp-icons/official/mood_smile.png"
    ).convert("RGBA")
    mascot = source.crop(source.getchannel("A").getbbox())

    def render(size):
        canvas = Image.new("RGBA", (size, size))
        diameter = round(size * 0.78)
        face = mascot.resize((diameter, diameter), Image.Resampling.LANCZOS)
        offset = (size - diameter) // 2
        canvas.alpha_composite(face, (offset, offset))
        return canvas

    # Render each scale from the transparent original to avoid baked-in white edges.
    render(1024).save(icons / "icon.png")
    with tempfile.TemporaryDirectory(prefix="qqpet-icons-") as temporary:
        iconset = Path(temporary) / "QQPet.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            for scale, suffix in ((1, ""), (2, "@2x")):
                render(points * scale).save(
                    iconset / f"icon_{points}x{points}{suffix}.png"
                )
        subprocess.run(
            [
                "/usr/bin/iconutil",
                "--convert",
                "icns",
                "--output",
                str(icons / "icon.icns"),
                str(iconset),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
