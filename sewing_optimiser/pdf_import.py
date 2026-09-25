"""Read pattern pieces for one size from a single-page pattern PDF."""

import math
import re
from dataclasses import dataclass, field

import pymupdf
from shapely import affinity, set_precision
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import polygonize, unary_union

MM_PER_PT = 25.4 / 72
LABEL_MAX_DIST_MM = 50  # text further than this from every piece is not a piece label
MIN_PIECE_AREA = 100  # mm²; smaller closed shapes are markings
MIN_FOLD_EDGE = 30  # mm; shortest straight edge accepted as a fold edge
JOIN_GAP = 1  # mm; faces closer than twice this are halves of one piece
NOTCH_MAX = 15  # mm; short strokes touching an outline are notches
ON_FOLD = re.compile(r"on\s+(the\s+)?fold", re.I)


@dataclass
class Piece:
    name: str
    outline: Polygon  # whole piece, page coordinates in mm, y pointing down
    grain_deg: float | None  # direction of the grainline; None if not found
    copies: int
    half: Polygon | None = None  # the half drawn on the fold, if the piece is cut on fold
    fold_edge: tuple | None = None  # ((x, y), (x, y)) ends of the fold edge of `half`
    # choices confirmed in the review step
    include: bool = True
    fabric: str = "main"
    cross_grain: bool = False  # may also be turned 90°
    mirror: bool = True  # every second copy is mirrored (left/right pairs)
    cut_on_fold: bool = True  # for pieces with a half: cut on a folded strip, else unfolded
    match_y: float | None = None  # stripe match line, mm below the top of the grain-aligned piece
    page: int = 0
    marks: MultiLineString = field(default_factory=MultiLineString)  # notches, same coordinates as outline
    half_marks: MultiLineString = field(default_factory=MultiLineString)  # notches of `half`

    @property
    def unfolded(self):
        return self.half is not None


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


def _linework(page, layers):
    """Stroked lines on the given layers (None: every stroke) and the widest stroke, mm."""
    lines, stroke = [], 0.0
    for d in page.get_drawings():
        if "s" in d["type"] and (layers is None or d.get("layer") in layers):
            lines += [LineString(pl) for pl in _polylines(d["items"])]
            stroke = max(stroke, (d.get("width") or 0) * MM_PER_PT)
    return lines, stroke


def faces(page, layers=None):
    """Every closed region the strokes form, as outlines without holes (for picking pieces by hand)."""
    lines, _ = _linework(page, layers)
    found = polygonize(set_precision(unary_union(lines), 0.01))
    return [Polygon(f.exterior) for f in found if f.area > MIN_PIECE_AREA]


def _outlines(page, layers):
    """Piece outlines on the given layers, inset to the inside of the stroke.

    Outlines may be drawn as several open paths, as rectangles, or as two
    mirrored halves that share an edge. All strokes are joined, split into
    closed faces, and faces that share an edge (or nearly do) are merged into
    one piece.
    """
    lines, stroke = _linework(page, layers)
    found = polygonize(set_precision(unary_union(lines), 0.01))  # grid snap joins near-coincident ends
    merged = unary_union([f.buffer(JOIN_GAP) for f in found]).buffer(-JOIN_GAP)
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


def _unfold(outline, marks, fold_texts, grain_deg):
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
    if full.geom_type != "Polygon":
        return None
    both = MultiLineString(list(marks.geoms) + list(_reflect(marks, a, b).geoms)) if not marks.is_empty else marks
    return full, both, (a, b)


def _notches(lines, outlines):
    """Short strokes touching each outline."""
    marks = [[] for _ in outlines]
    for line in lines:
        if line.length > NOTCH_MAX:
            continue
        dists = [o.exterior.distance(line) for o in outlines]
        if dists and min(dists) < 2:
            marks[dists.index(min(dists))].append(line)
    return [MultiLineString(m) for m in marks]


def pieces_from_outlines(page, outlines, marks=None):
    """Attach names, cut counts, grainlines and fold edges from the page text to outlines (mm)."""
    marks = marks or [MultiLineString() for _ in outlines]
    labels = [[] for _ in outlines]
    names = [[] for _ in outlines]
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
            if dists[i] == 0 and not re.search(r"^cut\b|grain|fold|seam\s+allowance|pattern|notch", text, re.I):
                names[i].append(text)

    pieces = []
    for outline, notches, texts, name_parts, g, fold_texts in zip(outlines, marks, labels, names, grain, folds):
        joined = " ".join(texts)
        cut = re.search(r"cut\s*(\d+)", joined, re.I)
        name = " ".join(name_parts)[:40].strip() or "piece"
        copies = int(cut.group(1)) if cut else 1
        unfolded = _unfold(outline, notches, fold_texts, g) if ON_FOLD.search(joined) else None
        if unfolded:
            full, both, edge = unfolded
            pieces.append(Piece(name, full, g, copies, half=outline, fold_edge=edge, marks=both, half_marks=notches))
        else:
            pieces.append(Piece(name, outline, g, copies, marks=notches))
    return pieces


def extract_pieces(path, size_layer=None, pages=None):
    """Pieces of one size. size_layer None takes every stroke (a file per size)."""
    doc = pymupdf.open(path)
    pieces = []
    for number in pages if pages is not None else range(doc.page_count):
        page = doc[number]
        layers = None if size_layer is None else {size_layer}
        outlines = _outlines(page, layers)
        marks = _notches(_linework(page, layers)[0], outlines)
        for piece in pieces_from_outlines(page, outlines, marks):
            piece.page = number
            pieces.append(piece)
    return pieces
