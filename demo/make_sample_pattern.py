"""Write demo/sample-pattern.pdf: a small made-up T-shirt pattern with two sizes on PDF layers.

It stands in for real patterns, which are copyright, in the demo and its video.
"""

from pathlib import Path

import pymupdf

PT = 72 / 25.4  # points per mm
OUT = Path(__file__).parent / "sample-pattern.pdf"
SIZES = {"Size S": 1.0, "Size M": 1.08}


def curve(a, b, bulge, n=12):
    """Points along a gentle arc from a to b, bowed sideways by `bulge` mm."""
    (ax, ay), (bx, by) = a, b
    nx, ny = -(by - ay), bx - ax
    length = (nx * nx + ny * ny) ** 0.5 or 1
    pts = []
    for i in range(1, n + 1):
        t = i / n
        k = 4 * t * (1 - t) * bulge / length
        pts.append((ax + (bx - ax) * t + nx * k, ay + (by - ay) * t + ny * k))
    return pts


def bodice(neck_depth):
    """Half a bodice, fold along x = 0."""
    pts = [(0, neck_depth)] + curve((0, neck_depth), (70, 0), 20)
    pts += [(170, 25)] + curve((170, 25), (195, 190), 30)
    pts += [(215, 560), (0, 560)]
    return pts


PIECES = [  # name, outline (mm), offset on the sheet (mm), notch points, label lines, grain line, option line
    ("FRONT", bodice(70), (40, 60), [(210, 470)],
     ["FRONT - cut 1 on fold", "Cut at the upper line for cropped version"], ((110, 150), (110, 450)), 440),
    ("BACK", bodice(25), (330, 60), [(210, 470)], ["BACK - cut 1 on fold"], ((110, 150), (110, 450)), None),
    ("SLEEVE", [(0, 100)] + curve((0, 100), (260, 100), -90, 20) + [(230, 330), (30, 330)], (620, 60), [(130, 30)],
     ["SLEEVE - cut 2"], ((130, 130), (130, 300)), None),
    ("CUFF", [(0, 0), (160, 0), (160, 70), (0, 70)], (640, 470), [], ["CUFF - cut 2"], ((20, 15), (20, 60)), None),
]


def main():
    doc = pymupdf.open()
    page = doc.new_page(width=950 * PT, height=700 * PT)
    layers = {name: doc.add_ocg(name) for name in SIZES}
    text = doc.add_ocg("text")
    for name, outline, (ox, oy), notches, labels, grain, option in PIECES:
        at = lambda x, y, k=1.0: pymupdf.Point((ox + x * k) * PT, (oy + y * k) * PT)
        for size, k in SIZES.items():
            pts = [at(x, y, k) for x, y in outline]
            page.draw_polyline(pts + [pts[0]], color=(0, 0, 0), width=1.2, oc=layers[size])
            for x, y in notches:  # a short tick across the cutting line
                page.draw_line(at(x - 3, y, k), at(x + 3, y, k), color=(0, 0, 0), width=1.2, oc=layers[size])
            if option is not None:
                page.draw_line(at(0, option, k), at(213, option, k), color=(0, 0, 0), width=1.2, oc=layers[size])
        (gx0, gy0), (gx1, gy1) = grain
        page.draw_line(at(gx0, gy0), at(gx1, gy1), color=(0, 0, 0), width=1, oc=text)
        page.insert_text(at(gx0 - 3, gy1 - 10), "GRAINLINE", fontsize=9, rotate=90, oc=text)
        for i, line in enumerate(labels):
            page.insert_text(at(gx0 + 10, gy0 + 60 + 14 * i), line, fontsize=11, oc=text)
        if "on fold" in labels[0]:
            page.insert_text(at(6, 400), "FOLD LINE", fontsize=9, rotate=90, oc=text)
    page.insert_text(pymupdf.Point(40 * PT, 670 * PT), "Sample pattern for the sewing-optimiser demo", fontsize=14, oc=text)
    doc.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
