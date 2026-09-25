"""Read pattern pieces for one size from a single-page pattern PDF."""

import math
import re
from collections import Counter
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
GAP_CLOSE = 2  # mm; line ends this close to another line are joined to it
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
    page: int = 0  # sheet number (tiled pages joined count as one sheet)
    marks: MultiLineString = field(default_factory=MultiLineString)  # notches, same coordinates as outline
    half_marks: MultiLineString = field(default_factory=MultiLineString)  # notches of `half`

    @property
    def unfolded(self):
        return self.half is not None


def list_layers(path):
    return [ocg["name"] for ocg in pymupdf.open(path).get_ocgs().values()]


def _style(d):
    """Line colour and dash pattern, e.g. '#008000 16 6'."""
    r, g, b = (d.get("color") or (0, 0, 0))[:3]
    dashes = re.sub(r"[\[\]]|\s0$", "", d.get("dashes") or "").split()
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}" + "".join(" " + x for x in dashes)


def _black(d):
    return max((d.get("color") or (0, 0, 0))[:3]) < 0.15


def selector(size):
    """Which drawings belong to a size: a PDF layer name ('|' joins several), 'style:<style>'
    (lines of that colour and dash pattern, plus black lines shared by every size), or None (all)."""
    if size is None:
        return lambda d: True
    if size.startswith("style:"):
        key = size[6:]
        return lambda d: _style(d) == key or _black(d)
    names = set(size.split("|"))
    return lambda d: d.get("layer") in names


def list_sizes(path):
    """(value, label) for each size: the PDF layers, or else the coloured line styles.

    A line style is named after the legend entry above a short horizontal sample of it.
    """
    doc = pymupdf.open(path)
    layers = list_layers(path)
    if layers:
        return [(name, name) for name in layers]
    names = {}
    for page in doc:
        for d in page.get_drawings():
            if "s" not in d["type"] or _black(d):
                continue
            key = _style(d)
            r = d["rect"]
            if r.height < 2 and r.width < 150:  # a short horizontal legend sample, under its size name
                above = sorted((r.y0 - tr.y1, t) for t, tr in _text_lines(page)
                               if tr.y1 <= r.y0 + 2 and tr.x0 > r.x0 - 5 and r.y0 - tr.y1 < 40)
                named = [t for _, t in above if t[0].isupper()]
                if named:
                    names[key] = named[0].split(" ")[0].rstrip(",:")
            names.setdefault(key, None)
    return [("style:" + k, f"{v or 'lines'} ({k})") for k, v in names.items()]


def _text_lines(page):
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                yield text, pymupdf.Rect(line["bbox"])


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


def _signature(d):
    """A drawing's layer, item kinds and shape relative to its first point (for matching tiles)."""
    pts = []
    for item in d["items"]:
        for p in item[1:]:
            if isinstance(p, pymupdf.Point):
                pts.append(p)
            elif isinstance(p, pymupdf.Rect):
                pts += [p.tl, p.tr, p.br, p.bl]
            elif isinstance(p, pymupdf.Quad):
                pts += [p.ul, p.ur, p.lr, p.ll]
    if len(pts) < 4:
        return None, None
    x0, y0 = pts[0].x, pts[0].y
    shape = tuple((round(p.x - x0), round(p.y - y0)) for p in pts)
    return (d.get("layer"), "".join(i[0] for i in d["items"]), shape), (x0, y0)


def sheets(doc):
    """Groups of pages that are tiles of one sheet, with each page's offset on the sheet in mm.

    Tiled patterns often repeat whole paths on every page they cross, shifted
    by the page's position, and clipped to the page. Paths that run off the
    page and have the same shape on two pages vote for the shift between
    them; pages joined this way form one sheet.
    """
    anchors = []
    for page in doc:
        found = {}
        inside = page.rect + (-1, -1, 1, 1)
        for d in page.get_drawings():
            if d["rect"] in inside:
                continue  # only paths running off the page can continue on another tile
            sig, at = _signature(d)
            if sig is not None:
                found.setdefault(sig, at)
        anchors.append(found)
    offset = {}
    groups, todo = [], list(range(doc.page_count))
    while todo:
        root = todo.pop(0)
        group, queue, offset[root] = [root], [root], (0.0, 0.0)
        while queue:
            a = queue.pop()
            for b in list(todo):
                votes = Counter()
                for sig, (xa, ya) in anchors[a].items():
                    if sig in anchors[b]:
                        xb, yb = anchors[b][sig]
                        votes[(round(xa - xb, 1), round(ya - yb, 1))] += 1
                if votes:
                    (dx, dy), n = votes.most_common(1)[0]
                    if n >= 3:
                        offset[b] = (offset[a][0] + dx, offset[a][1] + dy)
                        todo.remove(b)
                        group.append(b)
                        queue.append(b)
        groups.append(sorted(group))
    groups = _fill_grid(groups, offset)
    return [[(n, offset[n][0] * MM_PER_PT, offset[n][1] * MM_PER_PT) for n in g] for g in groups]


def _fill_grid(groups, offset, tol=15):
    """Add lone pages to a sheet whose pages sit on a regular grid (pages in reading order).

    Blank or sparse tiles share no paths with their neighbours; their place
    follows from the grid the other tiles form.
    """
    lone = {g[0] for g in groups if len(g) == 1}
    for g in [g for g in groups if len(g) >= 3]:
        p0 = g[0]
        for cols in range(1, 13):
            cells = {p: ((p - p0) % cols, (p - p0) // cols) for p in g}
            sx = [offset[p][0] / c for p, (c, r) in cells.items() if c]
            sy = [offset[p][1] / r for p, (c, r) in cells.items() if r]
            sx = sorted(sx)[len(sx) // 2] if sx else 0.0
            sy = sorted(sy)[len(sy) // 2] if sy else 0.0
            if all(abs(offset[p][0] - c * sx) < tol and abs(offset[p][1] - r * sy) < tol for p, (c, r) in cells.items()):
                last = p0 + (max(r for _, r in cells.values()) + 1) * cols
                for p in sorted(lone):
                    if p0 < p < last:
                        offset[p] = (((p - p0) % cols) * sx, ((p - p0) // cols) * sy)
                        g.append(p)
                        lone.discard(p)
                g.sort()
                break
    return [g for g in groups if len(g) > 1 or g[0] in lone]


def _key(geom):
    return tuple(round(v * 2) for xy in geom.coords for v in xy)  # to 0.5 mm


def sheet_lines(doc, sheet, size):
    """Stroked lines of a sheet belonging to a size (see selector), mm, and the widest stroke."""
    wanted = selector(size)
    lines, stroke, seen = [], 0.0, set()
    for number, dx, dy in sheet:
        for d in doc[number].get_drawings():
            if "s" in d["type"] and wanted(d):
                for pl in _polylines(d["items"]):
                    line = affinity.translate(LineString(pl), dx, dy)
                    if _key(line) not in seen:  # tiles repeat paths that cross them
                        seen.add(_key(line))
                        lines.append(line)
                stroke = max(stroke, (d.get("width") or 0) * MM_PER_PT)
    return lines, stroke


def sheet_text(doc, sheet):
    """(text, centre, direction in degrees) of every text line on a sheet, mm."""
    found, seen = [], set()
    for number, dx, dy in sheet:
        for block in doc[number].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(s["text"] for s in line["spans"]).strip()
                x0, y0, x1, y1 = (v * MM_PER_PT for v in line["bbox"])
                centre = Point((x0 + x1) / 2 + dx, (y0 + y1) / 2 + dy)
                key = (text, round(centre.x), round(centre.y))
                if text and key not in seen:
                    seen.add(key)
                    found.append((text, centre, math.degrees(math.atan2(line["dir"][1], line["dir"][0]))))
    return found


def _regions(lines, gap=GAP_CLOSE):
    """Closed regions formed by the lines, after bridging line ends that stop short of another line."""
    from shapely.ops import nearest_points

    bridges = []
    for i, line in enumerate(lines):
        if line.is_closed:
            continue
        others = [o for j, o in enumerate(lines) if j != i]
        for end in (Point(line.coords[0]), Point(line.coords[-1])):
            near = [o for o in others if 1e-6 < o.distance(end) < gap]
            if near and not any(o.distance(end) <= 1e-6 for o in others):
                target = min(near, key=end.distance)
                bridges.append(LineString([end, nearest_points(target, end)[0]]))
    # grid snap joins near-coincident ends
    return polygonize(set_precision(unary_union(lines + bridges), 0.01))


def faces(lines):
    """Every closed region the lines form, as outlines without holes (for picking pieces by hand)."""
    found = _regions(lines)
    return [Polygon(f.exterior) for f in found if f.area > MIN_PIECE_AREA]


def _outlines(lines, stroke):
    """Piece outlines formed by the lines, inset to the inside of the stroke.

    Outlines may be drawn as several open paths, as rectangles, or as two
    mirrored halves that share an edge. All strokes are joined, split into
    closed faces, and faces that share an edge (or nearly do) are merged into
    one piece.
    """
    found = _regions(lines)
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

    The fold edge is the long straight edge closest to a fold label that runs
    along the grainline (vertical on the page when no grainline is marked),
    or failing that, along a fold label.
    """
    coords = list(outline.simplify(0.1).exterior.coords)
    grain = grain_deg if grain_deg is not None else 90.0
    for directions in ([grain], [d for _, d in fold_texts]):
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
        if best is not None:
            break
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


def pieces_from_outlines(texts, outlines, marks=None):
    """Attach names, cut counts, grainlines and fold edges from text lines (see sheet_text) to outlines."""
    marks = marks or [MultiLineString() for _ in outlines]
    labels = [[] for _ in outlines]
    names = [[] for _ in outlines]
    grain = [None] * len(outlines)
    folds = [[] for _ in outlines]
    for text, centre, direction in texts if outlines else []:
        dists = [o.distance(centre) for o in outlines]
        i = min(range(len(outlines)), key=dists.__getitem__)
        if dists[i] > LABEL_MAX_DIST_MM:
            continue
        if "grain" in text.lower():
            grain[i] = direction  # the word runs along the grainline arrow
        if "fold" in text.lower():
            folds[i].append((centre, direction))
        labels[i].append(text)
        size_list = len(re.findall(r"\b(?:NB|PM|\d+-\d+[mt]?)\b", text)) >= 3
        if dists[i] == 0 and not size_list and not re.search(r"^cut\b|grain|fold|seam\s+allowance|pattern|notch|prepared|copyright|order", text, re.I):
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


def extract_pieces(path, size_layer=None):
    """Pieces of one size from every sheet. size_layer None takes every stroke (a file per size)."""
    doc = pymupdf.open(path)
    pieces = []
    for number, sheet in enumerate(sheets(doc)):
        lines, stroke = sheet_lines(doc, sheet, size_layer)
        outlines = _outlines(lines, stroke)
        for piece in pieces_from_outlines(sheet_text(doc, sheet), outlines, _notches(lines, outlines)):
            piece.page = number
            pieces.append(piece)
    return pieces
