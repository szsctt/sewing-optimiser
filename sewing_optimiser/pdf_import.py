"""Read pattern pieces for one size from a single-page pattern PDF."""

import copy
import functools
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf
from shapely import affinity, set_precision
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.ops import polygonize, unary_union

MM_PER_PT = 25.4 / 72
LABEL_MAX_DIST_MM = 50  # text further than this from every piece is not a piece label
FOLD_TEXT_DIST = 10  # mm; a fold label must be inside the piece or this close to it
MIN_PIECE_AREA = 500  # mm²; smaller closed shapes are markings or size labels
MIN_FOLD_EDGE = 30  # mm; shortest straight edge accepted as a fold edge
JOIN_GAP = 1  # mm; faces closer than twice this are halves of one piece
NOTCH_MAX = 15  # mm; short strokes touching an outline are notches
NOTCH_TICK = 6  # mm; length of the tick drawn for a notch
GAP_CLOSE = 2  # mm; line ends this close to another line are joined to it
END_GAP = 5  # mm; loose line ends this close to each other are joined
# text inside a piece that is not its name
NOT_NAME = (r"^cut\b|grain|fold|seam\s+allowance|pattern|notch|prepared|copyright|order|square|reference|"
            r"indicates|length|\.com|^sizes?\b|do not cut|stitch|^[\d\s]+$|line for|version|"
            r"^(P|PM|NB|\d+-\d+[mt]?|\d+T)$|included in all|allowance|^1/4|\bseam\b|in all pieces|^\S$|shorten|lengthen")
NAMED_CUT = re.compile(r"^(.+?)\s*[-–—:]\s*cut\b", re.I)  # 'Right Front - cut 1
ON_FOLD = re.compile(r"on\s+(the\s+)?fold", re.I)


def per_file(fn):
    """Remember fn(path, ...) until the file changes; callers get their own copy of the result."""
    @functools.lru_cache(maxsize=64)
    def remembered(path, mtime, *args):
        return fn(path, *args)

    @functools.wraps(fn)
    def wrapper(path, *args):
        return copy.deepcopy(remembered(str(path), os.path.getmtime(path), *args))

    return wrapper


@per_file
def sheets_of(path):
    return sheets(pymupdf.open(path))


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
    lengthen: float = 0.0  # mm added (or, if negative, removed) along the grain ...
    lengthen_at: float | None = None  # ... at this many mm below the top of the grain-aligned piece
    page: int = 0  # sheet number (tiled pages joined count as one sheet)
    source: int | None = None  # index among the pieces read from the PDF
    cut_marked: bool = True  # the pattern says how many to cut
    marks: MultiLineString = field(default_factory=MultiLineString)  # notches, same coordinates as outline
    half_marks: MultiLineString = field(default_factory=MultiLineString)  # notches of `half`
    regions: list = field(default_factory=list)  # parts that option lines split the drawn piece into, page mm
    region_labels: list = field(default_factory=list)  # text inside each part, e.g. 'Cut at the longer line for footies'

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


@per_file
def list_sizes(path):
    """(value, label) for each size: the PDF layers, or else the coloured line styles.

    A line style is named after the legend entry above a short horizontal sample of it.
    """
    doc = pymupdf.open(path)
    layers = list_layers(path)
    if layers:
        return [(name, name) for name in layers]
    names, sized = {}, set()
    for page in doc:
        for d in page.get_drawings():
            if "s" not in d["type"]:
                continue
            key = _style(d)
            r = d["rect"]
            if r.height < 2 and r.width < 150:  # a short horizontal legend sample, under its size name
                above = sorted((r.y0 - tr.y1, t) for t, tr in _text_lines(page)
                               if tr.y1 <= r.y0 + 2 and tr.x0 > r.x0 - 10 and r.y0 - tr.y1 < 40)
                named = [t for _, t in above if t[0].isupper()]
                if named:
                    names[key] = named[0].split(" ")[0].rstrip(",:")
                    if _black(d):
                        sized.add(key)  # black lines are shared by every size, unless the legend names them
            long = any(LineString(pl).length > 60 for pl in _polylines(d["items"]))  # not lettering drawn as lines
            if not _black(d) and long and (len(d["items"]) > 1 or d["items"][0][0] not in "lre"):  # lines, boxes are not sizes
                sized.add(key)
    for page in doc:  # legends written in the line colour
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"][:1]:
                    colour = f"#{span['color']:06x}"
                    words = span["text"].split()
                    for key in sized:
                        legend = words and words[0][0].isupper() and any(c.isdigit() for c in span["text"])
                        if key.split(" ")[0] == colour and legend and key not in names:
                            names[key] = words[0].rstrip(",:")
    sizes = [("style:" + k, f"{names.get(k) or 'lines'} ({k})") for k in sized]
    if len(sizes) > 1:
        return sizes
    labelled = sorted({t for page in doc for t in _size_labels(page).values() if "-" not in t}, key=_size_order)
    return [("label:" + t, f"Size {t}") for t in labelled] if len(labelled) > 1 else []


SIZE_LABEL = re.compile(r"^size\s+(\S+(?:\s*-\s*\S+)?)$", re.I)


def _size_order(token):
    return (0, float(token)) if re.fullmatch(r"[\d.]+", token) else (1, token)


def _size_labels(page):
    """{index of a closed stroked path: size written along it}, for patterns that label each size's line."""
    labels = []
    for text, rect in _text_lines(page):
        m = SIZE_LABEL.match(text.strip())
        if m:
            c = ((rect.x0 + rect.x1) / 2 * MM_PER_PT, (rect.y0 + rect.y1) / 2 * MM_PER_PT)
            labels.append((Point(c), m.group(1).replace(" ", "")))
    found = {}
    for k, d in enumerate(page.get_drawings()):
        if "s" not in d["type"] or not labels:
            continue
        for pl in _polylines(d["items"]):
            line = LineString(pl)
            if line.length < 100:
                continue
            dist, token = min((line.distance(c), t) for c, t in labels)
            if dist < 3:
                found[k] = token
    return found


def _in_size(token, size):
    """Whether a label ('10', or a range '6-16') covers the size."""
    if token == size:
        return True
    lo, _, hi = token.partition("-")
    try:
        return bool(hi) and float(lo) <= float(size) <= float(hi)
    except ValueError:
        return False


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
    groups = _tape_pages(doc, groups, offset)
    return [[(n, offset[n][0] * MM_PER_PT, offset[n][1] * MM_PER_PT) for n in g] for g in groups]


def _tape_lines(page):
    """Straight coloured dotted lines at least 80 mm long, by style: the 'tape here' lines of home-made patterns."""
    found = {}
    for d in page.get_drawings():
        dashes = (d.get("dashes") or "[]").strip("[] 0").split()
        if ("s" in d["type"] and not _black(d) and dashes and float(dashes[0]) < 0.1
                and len(d["items"]) == 1 and d["items"][0][0] == "l"):
            a, b = d["items"][0][1:]
            if abs(b - a) * MM_PER_PT >= 80:
                found[_style(d)] = sorted([(a.x, a.y), (b.x, b.y)])
    return found


def _tape_pages(doc, groups, offset):
    """Join consecutive lone pages that carry the same tape line, laying one line on the other.

    The lines may differ in length, so both ends are tried and the one that
    lands more of the second page's line ends on the first page's lines wins.
    """
    lone = [g[0] for g in groups if len(g) == 1]
    joined = {}
    for a, b in zip(lone, lone[1:]):
        if b != a + 1:
            continue
        ta, tb = _tape_lines(doc[a]), _tape_lines(doc[b])
        common = set(ta) & set(tb)
        if not common:
            continue
        key = common.pop()
        lines_a = [LineString(pl) for d in doc[a].get_drawings() if "s" in d["type"] for pl in _polylines(d["items"])]
        ends_b = [pt for d in doc[b].get_drawings() if "s" in d["type"]
                  for pl in _polylines(d["items"]) for pt in (pl[0], pl[-1])]
        best = None
        for end in (0, 1):
            dx, dy = ta[key][end][0] - tb[key][end][0], ta[key][end][1] - tb[key][end][1]
            hits = sum(1 for x, y in ends_b
                       if any(l.distance(Point(x + dx * MM_PER_PT, y + dy * MM_PER_PT)) < 1.5 for l in lines_a))
            if best is None or hits > best[0]:
                best = (hits, dx, dy)
        root = joined.get(a, a)
        offset[b] = (offset[root][0] + best[1], offset[root][1] + best[2]) if root != a else (best[1], best[2])
        if root != a:
            offset[b] = (offset[a][0] + best[1], offset[a][1] + best[2])
        joined[b] = root
    merged = {}
    for g in groups:
        root = joined.get(g[0], g[0]) if len(g) == 1 else g[0]
        merged.setdefault(root, []).extend(g)
    return [sorted(g) for g in merged.values()]


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
    """Stroked lines of a sheet belonging to a size (see selector), mm, and the widest stroke.

    A size 'label:<size>' keeps the lines labelled with that size (or a range holding it),
    and every line with no size label."""
    wanted = selector(None if size and size.startswith("label:") else size)
    lines, stroke, seen = [], 0.0, set()
    for number, dx, dy in sheet:
        page_w, page_h = doc[number].rect.width * MM_PER_PT, doc[number].rect.height * MM_PER_PT
        labels = _size_labels(doc[number]) if size and size.startswith("label:") else {}
        for k, d in enumerate(doc[number].get_drawings()):
            if k in labels and not _in_size(labels[k], size[6:]):
                continue  # another size's line
            if "s" in d["type"] and wanted(d):
                for pl in _polylines(d["items"]):
                    if len(sheet) == 1 and _page_frame(pl, page_w, page_h):  # tiles do run to their page edges
                        continue
                    line = affinity.translate(LineString(pl), dx, dy)
                    if _key(line) not in seen:  # tiles repeat paths that cross them
                        seen.add(_key(line))
                        lines.append(line)
                stroke = max(stroke, (d.get("width") or 0) * MM_PER_PT)
    return lines, stroke


def _page_frame(points, page_w, page_h, edge=10):
    """A border drawn round the page (whole, or one side at a time), not part of any piece."""
    xs, ys = [x for x, _ in points], [y for _, y in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    wide = x1 - x0 > 0.95 * page_w - 2 * edge and (y0 < edge or y1 > page_h - edge)
    tall = y1 - y0 > 0.95 * page_h - 2 * edge and (x0 < edge or x1 > page_w - edge)
    straight = len(points) == 2 or (x1 - x0 < 1 or y1 - y0 < 1)
    return (wide or tall) and (straight or (wide and tall))


def _grain_from_arrow(shape, lines):
    """Direction of the grainline drawn inside a piece: its longest straight line clear of the edge."""
    inner = shape.buffer(-5)
    best = None
    for line in lines:
        if len(line.coords) != 2 or line.length < 0.25 * max(shape.bounds[2] - shape.bounds[0],
                                                          shape.bounds[3] - shape.bounds[1]):
            continue
        if inner.contains(line) and (best is None or line.length > best.length):
            best = line
    if best is None:
        return None
    (ax, ay), (bx, by) = best.coords
    return math.degrees(math.atan2(by - ay, bx - ax))


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
    """Closed regions formed by the lines, after bridging gaps.

    A line end that touches nothing is joined to the nearest other loose end
    within END_GAP (lines drawn as separate dashes), or else to the nearest
    line within `gap`.
    """
    from shapely import STRtree
    from shapely.ops import nearest_points

    tree = STRtree(lines)
    loose = []
    for i, line in enumerate(lines):
        if line.is_closed:
            continue
        for end in (Point(line.coords[0]), Point(line.coords[-1])):
            if not any(j != i and lines[j].distance(end) <= 1e-6 for j in tree.query(end.buffer(1e-5))):
                loose.append((i, end))
    ends = STRtree([e for _, e in loose])
    bridges, used = [], set()
    for k, (i, end) in enumerate(loose):
        partners = [(end.distance(loose[m][1]), m) for m in ends.query(end.buffer(END_GAP))
                    if loose[m][0] != i and m != k]
        if partners and min(partners)[0] < END_GAP:
            m = min(partners)[1]
            if (m, k) not in used:
                used.add((k, m))
                bridges.append(LineString([end, loose[m][1]]))
            continue
        near = [lines[j] for j in tree.query(end.buffer(gap)) if j != i and lines[j].distance(end) < gap]
        if near:
            hit = nearest_points(min(near, key=end.distance), end)[0]
            # overshoot slightly so the bridge crosses the line and is split there
            dx, dy = hit.x - end.x, hit.y - end.y
            f = 1 + 0.05 / max(end.distance(hit), 1e-9)
            bridges.append(LineString([end, (end.x + dx * f, end.y + dy * f)]))
    # grid snap joins near-coincident ends; bridges can also split a region oddly, so keep both results
    plain = list(polygonize(set_precision(unary_union(lines), 0.01)))
    bridged = list(polygonize(unary_union([set_precision(g, 0.01) for g in lines + bridges]))) if bridges else []
    return plain + bridged


PICK_MIN_AREA = 20  # mm²; regions this small can still be picked by hand (thin strips between sizes)


def faces(lines, min_area=MIN_PIECE_AREA):
    """Every closed region the lines form, with its holes."""
    found, seen = [], set()
    for f in _regions(lines):
        key = tuple(round(v) for v in f.bounds) + (round(f.area),)
        if f.area > min_area and key not in seen:
            seen.add(key)
            found.append(f)
    return found


def picked_outlines(lines, stroke, points):
    """Outlines of the pieces a person picked by clicking regions (points, mm).

    Clicked regions that touch form one piece, taking in any regions they
    enclose. Where nested sizes share edges, the regions between size lines
    are strips that enclose nothing, so each strip from the smallest size
    out to the chosen one has to be clicked.
    """
    regions = faces(lines, PICK_MIN_AREA)
    chosen = [r for r in regions if any(r.contains(Point(p)) for p in points)]
    merged = unary_union([r.buffer(JOIN_GAP) for r in chosen]).buffer(-JOIN_GAP - stroke / 2)
    return [Polygon(g.exterior) for g in getattr(merged, "geoms", [merged]) if not g.is_empty]


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
    """Notches on each outline, as ticks NOTCH_TICK mm long running into the piece from its edge.

    A notch is drawn as one or a few short strokes that touch the outline and
    stand off it (strokes lying along it are pieces of the outline itself).
    """
    found = [[] for _ in outlines]
    for line in lines:
        if line.length > NOTCH_MAX:
            continue
        dists = [o.exterior.distance(line) for o in outlines]
        if not dists or min(dists) >= 2:
            continue
        i = dists.index(min(dists))
        edge = outlines[i].exterior
        if max(edge.distance(Point(c)) for c in line.coords) > 0.8:
            found[i].append(line)
    marks = []
    for outline, strokes in zip(outlines, found):
        ticks = []
        clusters = unary_union([l.buffer(2) for l in strokes])
        for cluster in getattr(clusters, "geoms", [clusters]) if strokes else []:
            edge = outline.exterior
            at = edge.project(cluster.centroid)
            p, a, b = edge.interpolate(at), edge.interpolate(at - 1), edge.interpolate(at + 1)
            nx, ny = -(b.y - a.y), b.x - a.x
            n = math.hypot(nx, ny) or 1
            nx, ny = nx / n, ny / n
            if not outline.contains(Point(p.x + nx, p.y + ny)):
                nx, ny = -nx, -ny
            ticks.append(LineString([(p.x, p.y), (p.x + nx * NOTCH_TICK, p.y + ny * NOTCH_TICK)]))
        marks.append(MultiLineString(ticks))
    return marks


def _owners(texts, outlines):
    """The outline each text line belongs to (None if none is near).

    Text inside a piece belongs to it. A label block outside every piece
    (consecutive lines less than 10 mm apart) goes as a whole to the piece
    nearest on average, so its name and cut count stay together.
    """
    if not outlines:
        return [None] * len(texts)
    dists = [[o.distance(c) for o in outlines] for _, c, _ in texts]
    blocks, start = [], 0
    for k in range(1, len(texts) + 1):
        if k == len(texts) or texts[k][1].distance(texts[k - 1][1]) > 10 or min(dists[k]) == 0:
            blocks.append(range(start, k))
            start = k
    owner = [None] * len(texts)
    for block in blocks:
        mean = [sum(dists[k][j] for k in block) / len(block) for j in range(len(outlines))]
        for k in block:
            inside = [j for j in range(len(outlines)) if dists[k][j] == 0]
            j = inside[0] if inside else min(range(len(outlines)), key=mean.__getitem__)
            owner[k] = j if dists[k][j] <= LABEL_MAX_DIST_MM else None
    return owner


def pieces_from_outlines(texts, outlines, marks=None):
    """Attach names, cut counts, grainlines and fold edges from text lines (see sheet_text) to outlines."""
    marks = marks or [MultiLineString() for _ in outlines]
    labels = [[] for _ in outlines]
    names = [[] for _ in outlines]
    grain = [None] * len(outlines)
    folds = [[] for _ in outlines]
    owner = _owners(texts, outlines)
    for (text, centre, direction), i in zip(texts, owner):
        if i is None:
            continue
        dists = [o.distance(centre) for o in outlines]
        if "grain" in text.lower():
            grain[i] = direction  # the word runs along the grainline arrow
        if "fold" in text.lower() and dists[i] <= FOLD_TEXT_DIST:
            folds[i].append((centre, direction, text))
        labels[i].append(text)
        size_list = len(re.findall(r"\b(?:NB|PM|\d+-\d+[mt]?)\b", text)) >= 3
        if dists[i] == 0 and not size_list and not re.search(NOT_NAME, text, re.I):
            names[i].append(text)

    # a line printed in most pieces, such as the maker's logo, is not part of their names
    if len(names) > 2:
        common = {t for n in names for t in set(n) if sum(t in m for m in names) > 0.6 * len(names)}
        names = [[t for t in n if t not in common] for n in names]
    pieces = []
    for outline, notches, texts, name_parts, g, fold_texts in zip(outlines, marks, labels, names, grain, folds):
        joined = " ".join(texts)
        cut = re.search(r"cut\s*(\d+|one|two|three|four)(\s*pairs?)?", joined, re.I)
        near = [t for t in texts if not re.search(r"^cut\b|grain|fold|square|prepared|copyright", t, re.I)]
        named = [m.group(1) for t in texts if (m := NAMED_CUT.match(t)) and not re.search(NOT_NAME, m.group(1), re.I)]
        named += [before for before, t in zip(texts, texts[1:])  # a name on the line above 'Cut 1 Pair Self'
                  if re.match(r"cut\s*(\d|one|two|three|four)", t, re.I) and not re.search(r"^cut\b|grain|\bfold\b|^sizes?\b|^[\d\s.-]+$", before, re.I)]
        name = named[0] if named else " ".join(name_parts or near[:1])
        name = re.split(r"\s(?:Place|tape)\b|\s\(", name, flags=re.I)[0].strip() or "piece"  # drop instructions
        name = re.sub(r"^\d+\.\s*", "", name) or name  # piece numbers such as '1.'
        count = cut and (int(cut.group(1)) if cut.group(1).isdigit() else NUMBERS[cut.group(1).lower()])
        copies = count * (2 if cut.group(2) else 1) if cut else 1
        if g is None and re.search(r"\bbias\b", joined, re.I):
            g = _long_axis(outline) + 45  # cut on the bias: grain at 45° to the piece's length
        on_fold = any(ON_FOLD.search(t) for _, _, t in fold_texts)
        unfolded = _unfold(outline, notches, [(c, d) for c, d, _ in fold_texts], g) if on_fold else None
        fabric = "interfacing" if re.search(r"interfacing", name, re.I) else "main"
        if unfolded:
            full, both, edge = unfolded
            pieces.append(Piece(name, full, g, copies, half=outline, fold_edge=edge, marks=both, half_marks=notches,
                                fabric=fabric))
        else:
            pieces.append(Piece(name, outline, g, copies, marks=notches, fabric=fabric))
        pieces[-1].cut_marked = bool(cut)
    return pieces


@per_file
def extract_pieces(path, size_layer=None):
    """Pieces of one size from every sheet. size_layer None takes every stroke (a file per size)."""
    doc = pymupdf.open(path)
    pieces = []
    for number, sheet in enumerate(sheets(doc)):
        lines, stroke = sheet_lines(doc, sheet, size_layer)
        outlines = _outlines(lines, stroke)
        parts = faces(lines)
        texts = sheet_text(doc, sheet)
        boxes = _shaded_boxes(doc, sheet)
        dashed = _dashed_lines(doc, sheet)
        every_line = None
        for piece in pieces_from_outlines(texts, outlines, _notches(lines, outlines)):
            if _check_square(piece) or any(b.contains(piece.outline) for b in boxes):
                continue
            if piece.half is None and not list_layers(path):  # layered patterns draw sizes, not folds, dashed
                piece = _fold_on_dashed_edge(piece, dashed)
            if piece.grain_deg is None:  # no 'grainline' label: use the arrow
                if every_line is None:
                    every_line = sheet_lines(doc, sheet, None)[0]
                piece.grain_deg = _grain_from_arrow(piece.half if piece.half is not None else piece.outline, every_line)
            piece.page = number
            _option_regions(piece, parts, texts)
            pieces.append(piece)
    return _drop_common_title(_drop_unmarked(pieces))


def _option_regions(piece, parts, texts):
    """Record the parts a piece is split into by lines inside it (cutting options), with their labels."""
    drawn = (piece.half if piece.half is not None else piece.outline).buffer(1)
    inside = [r for r in parts if drawn.contains(r.representative_point()) and r.area < 0.98 * drawn.area]
    if len(inside) < 2:
        return
    piece.regions = inside
    for r in inside:
        words = [t for t, c, _ in texts if r.contains(c) and not re.match(r"^(P|PM|NB|\d+-\d+[mt]?|\d+T)$", t)]
        piece.region_labels.append(" ".join(words)[:60])


NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4}


def _long_axis(poly):
    """Direction of a piece's longest side of its smallest enclosing rectangle, degrees."""
    ring = list(poly.minimum_rotated_rectangle.exterior.coords)
    (ax, ay), (bx, by) = max(zip(ring, ring[1:]), key=lambda e: math.dist(*e))
    return math.degrees(math.atan2(by - ay, bx - ax))


def _drop_unmarked(pieces):
    """Where most pieces say how many to cut, a region that says nothing (a logo, a notes box) is not a piece."""
    marked = [p for p in pieces if p.cut_marked]
    return marked if len(marked) * 2 > len(pieces) else pieces


def _drop_common_title(pieces):
    """Remove the pattern title that starts most piece names ('Fog Tee Sleeve' -> 'Sleeve'), and shorten names."""
    names = [p.name for p in pieces if p.name != "piece"]
    title = ""
    for name in names:
        words = name.split()
        for j in range(2, len(words) + 1):  # a title starts the name
            phrase = " ".join(words[:j])
            if len(phrase) > len(title) and sum(n.startswith(phrase) for n in names) * 2 > len(names) > 1:
                title = phrase
    for p in pieces:
        shorter = " ".join(p.name.replace(title, " ").split()).strip(" .,:") if title else p.name
        p.name = shorter or p.name
    for p in pieces:
        p.name = p.name[:50]
    return pieces


def _dashed_lines(doc, sheet):
    """Straight black dashed lines, mm: home-made patterns mark the fold this way."""
    found = []
    for number, dx, dy in sheet:
        for d in doc[number].get_drawings():
            dashes = (d.get("dashes") or "[]").strip("[] 0").split()
            if "s" in d["type"] and _black(d) and dashes and len(d["items"]) == 1 and d["items"][0][0] == "l":
                a, b = d["items"][0][1:]
                found.append(LineString([(a.x * MM_PER_PT + dx, a.y * MM_PER_PT + dy),
                                         (b.x * MM_PER_PT + dx, b.y * MM_PER_PT + dy)]))
    return found


def _fold_on_dashed_edge(piece, dashed):
    """Unfold a piece whose long straight edge along the grain is drawn dashed (a fold line)."""
    grain = piece.grain_deg if piece.grain_deg is not None else 90.0
    coords = list(piece.outline.simplify(0.3).exterior.coords)
    for a, b in sorted(zip(coords, coords[1:]), key=lambda e: -math.dist(*e)):
        edge = LineString([a, b])
        angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        if edge.length < 50 or abs((angle - grain + 90) % 180 - 90) > 3:
            continue
        covered = sum(edge.intersection(l.buffer(2)).length for l in dashed)
        if covered > 0.8 * edge.length:
            full = unary_union([piece.outline, _reflect(piece.outline, a, b)]).buffer(0.01).buffer(-0.01)
            if full.geom_type == "Polygon":
                marks = piece.marks
                both = MultiLineString(list(marks.geoms) + list(_reflect(marks, a, b).geoms)) if not marks.is_empty else marks
                return Piece(piece.name, full, piece.grain_deg, piece.copies, half=piece.outline, fold_edge=(a, b),
                             marks=both, half_marks=marks)
    return piece


def _check_square(piece):
    """A printing check square (1 inch, 4 cm, 5 cm) rather than a piece."""
    minx, miny, maxx, maxy = piece.outline.bounds
    w, h = maxx - minx, maxy - miny
    return abs(w - h) < 2 and 20 < w < 130 and piece.outline.area > 0.95 * w * h  # up to a 5 inch grid


def _shaded_boxes(doc, sheet):
    """Filled coloured rectangles, mm: illustrations such as a sample cutting layout."""
    boxes = []
    for number, dx, dy in sheet:
        for d in doc[number].get_drawings():
            fill = d.get("fill")
            coloured = fill and max(fill[:3]) - min(fill[:3]) > 0.3  # not white, grey or black
            if coloured and len(d["items"]) == 1 and d["items"][0][0] == "re":
                r = d["rect"]
                boxes.append(box(r.x0 * MM_PER_PT + dx, r.y0 * MM_PER_PT + dy, r.x1 * MM_PER_PT + dx, r.y1 * MM_PER_PT + dy).buffer(1))
    return boxes


INCH = 25.4
SIZE_WORDS = [("xxxlarge", "xxxl"), ("xxlarge", "xxl"), ("xlarge", "xl"), ("large", "l"), ("medium", "m"),
              ("small", "s"), ("newborn", "nb")]


def size_key(name):
    """A size name reduced for comparison: 'XLarge' and 'XL' both give 'xl'."""
    name = name.lower().strip()
    return dict(SIZE_WORDS).get(name, name)


@per_file
def text_rectangles(path):
    """Pieces the instructions give only by size, e.g. 'Waistband ... 12” by 4” for the newborn soaker'.

    Returns {"name", "copies", "sizes": {size key: (a, b) in mm}} for each.
    """
    found = []
    for page in pymupdf.open(path):
        for block in page.get_text("blocks"):
            # a block may hold several, each headed 'Name:'
            for text in re.split(r"(?<=[.”\"])\s(?=[A-Z][a-z]+(?: [A-Z][a-z]+)?:)", " ".join(block[4].split())):
                sizes = re.findall(r"([\d.]+)[”\"]\s*(?:by|x)\s*([\d.]+)[”\"]\s*for the (\w+)", text)
                if sizes:
                    name = re.split(r"[:(]| Place | Cut ", text)[0].strip()
                    copies = re.search(r"\(Cut (\d+)", text, re.I)
                    found.append({"name": name, "copies": int(copies.group(1)) if copies else 1,
                                  "sizes": {size_key(k): (float(a) * INCH, float(b) * INCH) for a, b, k in sizes}})
    for rect in found:  # a count given elsewhere, e.g. a label 'Leg Cuffs (Cut 2)'
        for page in pymupdf.open(path):
            m = re.search(re.escape(rect["name"]) + r"\s*\(Cut (\d+)", page.get_text(), re.I)
            if m:
                rect["copies"] = int(m.group(1))
    return found
