"""Read pattern pieces for one size from a single-page pattern PDF."""

import math
import re
from dataclasses import dataclass

import pymupdf
from shapely.geometry import Point, Polygon

MM_PER_PT = 25.4 / 72
LABEL_MAX_DIST_MM = 50  # text further than this from every piece is not a piece label


@dataclass
class Piece:
    name: str
    outline: Polygon  # page coordinates in mm, y pointing down
    grain_deg: float | None  # direction of the grainline; None if not found
    copies: int
    on_fold: bool


def list_layers(path):
    return [ocg["name"] for ocg in pymupdf.open(path).get_ocgs().values()]


def _flatten(items, samples=12):
    """Turn PyMuPDF path items (lines and cubic Béziers) into a list of points."""
    pts = [items[0][1]]
    for item in items:
        if item[0] == "l":
            pts.append(item[2])
        elif item[0] == "c":
            p0, c1, c2, p3 = item[1:]
            for i in range(1, samples + 1):
                t = i / samples
                u = 1 - t
                pts.append(p0 * u**3 + c1 * 3 * u * u * t + c2 * 3 * u * t * t + p3 * t**3)
        elif item[0] == "re":
            r = item[1]
            pts = [r.tl, r.tr, r.br, r.bl]
    return [(p.x * MM_PER_PT, p.y * MM_PER_PT) for p in pts]


def _outlines(page, layer):
    """Closed outlines drawn on the given layer, inset to the inside of the stroke."""
    polys = []
    for d in page.get_drawings():
        if d.get("layer") != layer or "s" not in d["type"]:
            continue
        poly = Polygon(_flatten(d["items"]))
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.area > 100:  # ignore marks smaller than 1 cm²
            half_stroke = (d.get("width") or 0) * MM_PER_PT / 2
            polys.append(poly.buffer(-half_stroke))
    return polys


def extract_pieces(path, size_layer):
    page = pymupdf.open(path)[0]
    outlines = _outlines(page, size_layer)
    labels = [[] for _ in outlines]
    grain = [None] * len(outlines)

    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            x0, y0, x1, y1 = (v * MM_PER_PT for v in line["bbox"])
            centre = Point((x0 + x1) / 2, (y0 + y1) / 2)
            dists = [o.distance(centre) for o in outlines]
            i = min(range(len(outlines)), key=dists.__getitem__)
            if dists[i] > LABEL_MAX_DIST_MM:
                continue
            if "grain" in text.lower():
                dx, dy = line["dir"]  # the word runs along the grainline arrow
                grain[i] = math.degrees(math.atan2(dy, dx))
            else:
                labels[i].append(text)

    pieces = []
    for outline, texts, g in zip(outlines, labels, grain):
        joined = " ".join(texts)
        cut = re.search(r"cut\s*(\d+)", joined, re.I)
        name = " ".join(t for t in texts if not re.match(r"cut\b", t, re.I)) or "piece"
        pieces.append(Piece(name, outline, g, int(cut.group(1)) if cut else 1, "fold" in joined.lower()))
    return pieces
