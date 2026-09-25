"""Read pattern pieces for one size from a single-page pattern PDF."""

import math
import re
from dataclasses import dataclass

import pymupdf
from shapely import affinity, set_precision
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

MM_PER_PT = 25.4 / 72
LABEL_MAX_DIST_MM = 50  # text further than this from every piece is not a piece label
MIN_PIECE_AREA = 100  # mm²; smaller closed shapes are markings
MIN_FOLD_EDGE = 30  # mm; shortest straight edge accepted as a fold edge
JOIN_GAP = 1  # mm; faces closer than twice this are halves of one piece
ON_FOLD = re.compile(r"on\s+(the\s+)?fold", re.I)


@dataclass
class Piece:
    name: str
    outline: Polygon  # page coordinates in mm, y pointing down
    grain_deg: float | None  # direction of the grainline; None if not found
    copies: int
    unfolded: bool  # drawn as a half on the fold and mirrored to a full piece


def list_layers(path):
    return [ocg["name"] for ocg in pymupdf.open(path).get_ocgs().values()]


def _mm(p):
    return (p.x * MM_PER_PT, p.y * MM_PER_PT)


def _bezier(p0, c1, c2, p3, samples=12):
    for i in range(1, samples + 1):
        t = i / samples
        u = 1 - t
        yield p0 * u**3 + c1 * 3 * u * u * t + c2 * 3 * u * t * t + p3 * t**3


def _polylines(items):
    """Split PyMuPDF path items into polylines (lists of mm points)."""
    lines, current = [], []
    for item in items:
        kind = item[0]
        if kind == "re":
            r = item[1]
            lines.append([_mm(p) for p in (r.tl, r.tr, r.br, r.bl, r.tl)])
        elif kind == "qu":
            q = item[1]
            lines.append([_mm(p) for p in (q.ul, q.ur, q.lr, q.ll, q.ul)])
        else:
            start = item[1]
            if not current or _mm(start) != current[-1]:
                current = [_mm(start)]
                lines.append(current)
            if kind == "l":
                current.append(_mm(item[2]))
            elif kind == "c":
                current.extend(_mm(p) for p in _bezier(*item[1:]))
    return [line for line in lines if len(line) > 1]


def _outlines(page, layer):
    """Piece outlines on the given layer, inset to the inside of the stroke.

    Outlines may be drawn as several open paths, as rectangles, or as two
    mirrored halves that share an edge. All strokes are joined, split into
    closed faces, and faces that share an edge (or nearly do) are merged into
    one piece.
    """
    lines, stroke = [], 0.0
    for d in page.get_drawings():
        if d.get("layer") == layer and "s" in d["type"]:
            lines += [LineString(pl) for pl in _polylines(d["items"])]
            stroke = max(stroke, (d.get("width") or 0) * MM_PER_PT)
    faces = polygonize(set_precision(unary_union(lines), 0.01))  # grid snap joins near-coincident ends
    merged = unary_union([f.buffer(JOIN_GAP) for f in faces]).buffer(-JOIN_GAP)
    parts = getattr(merged, "geoms", [merged])
    return [Polygon(p.exterior).buffer(-stroke / 2) for p in parts if p.area > MIN_PIECE_AREA]


def _reflect(geom, a, b):
    """Mirror geom across the line through points a and b."""
    ux, uy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(ux, uy)
    ux, uy = ux / n, uy / n
    m = [2 * ux * ux - 1, 2 * ux * uy, 2 * ux * uy, 2 * uy * uy - 1]
    xoff = a[0] - (m[0] * a[0] + m[1] * a[1])
    yoff = a[1] - (m[2] * a[0] + m[3] * a[1])
    return affinity.affine_transform(geom, m + [xoff, yoff])


def _unfold(outline, fold_texts, grain_deg):
    """Mirror a half piece across its fold edge.

    The fold edge is the long straight edge, parallel to a fold label or to
    the grainline, that lies closest to a fold label.
    """
    directions = [d for _, d in fold_texts] + ([grain_deg] if grain_deg is not None else [])
    coords = list(outline.simplify(0.1).exterior.coords)
    best = None
    for a, b in zip(coords, coords[1:]):
        edge = LineString([a, b])
        if edge.length < MIN_FOLD_EDGE:
            continue
        angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        if not any(abs((angle - d + 90) % 180 - 90) < 3 for d in directions):
            continue
        dist = min(edge.distance(c) for c, _ in fold_texts)
        if best is None or dist < best[0]:
            best = (dist, a, b)
    if best is None:
        return None
    _, a, b = best
    full = unary_union([outline, _reflect(outline, a, b)]).buffer(0.01).buffer(-0.01)
    return full if full.geom_type == "Polygon" else None


def extract_pieces(path, size_layer):
    page = pymupdf.open(path)[0]
    outlines = _outlines(page, size_layer)
    labels = [[] for _ in outlines]
    grain = [None] * len(outlines)
    folds = [[] for _ in outlines]

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
            direction = math.degrees(math.atan2(line["dir"][1], line["dir"][0]))
            if "grain" in text.lower():
                grain[i] = direction  # the word runs along the grainline arrow
            if "fold" in text.lower():
                folds[i].append((centre, direction))
            labels[i].append(text)

    pieces = []
    for outline, texts, g, fold_texts in zip(outlines, labels, grain, folds):
        joined = " ".join(texts)
        cut = re.search(r"cut\s*(\d+)", joined, re.I)
        name = " ".join(t for t in texts if not re.search(r"^cut\b|grain|fold", t, re.I)) or "piece"
        full = _unfold(outline, fold_texts, g) if ON_FOLD.search(joined) else None
        pieces.append(Piece(name, full or outline, g, int(cut.group(1)) if cut else 1, full is not None))
    return pieces
