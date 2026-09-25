"""Turn reviewed pieces and fabric settings into nested layouts, one per fabric.

Cut-on-fold pieces are either unfolded and cut single layer, or cut on a
folded strip: the left selvedge is folded over by `fold_width`, giving a
double layer from the fold (x = 0) to x = fold_width. Half pieces sit with
their fold edge on the fold, and pieces cut in mirrored pairs also go in the
double layer, so one placement cuts both. Everything else is cut single
layer to the right of the strip. Several strip widths are tried, and the
unfolded layout is used instead when it needs less fabric.
"""

from dataclasses import dataclass, field, replace

from shapely import affinity
from shapely.geometry import JOIN_STYLE, LineString, MultiLineString, Point, box
from shapely.ops import unary_union

from .nest import Instance, Stripes, nest, rectangle, score

FOLD_STEP = 50  # mm between strip widths tried


@dataclass
class FabricSettings:
    width: float = 1500.0  # mm, selvedge to selvedge (ignored when `shape` is given)
    shape: object = None  # shapely Polygon of an irregular piece of fabric, mm
    gap: float = 3.0
    one_way: bool = False  # napped or one-way print: no 180° turns
    seam_allowance: float = 0.0  # mm added around every piece
    stripe_repeat: float | None = None
    stripe_phase: float = 0.0
    tries: int = 8
    aim: str = "length"  # "length", "width" or "compact": what the layout minimises (see nest.score)


@dataclass
class Layout:
    fabric: str
    placements: list
    length: float
    width_used: float
    fabric_shape: object  # as laid out (folded fabric is narrower)
    fold_width: float = 0.0  # > 0: fold this much of the left selvedge over; x = 0 is the fold
    notes: list = field(default_factory=list)

    @property
    def flat_width(self):
        """Width of flat fabric used: a folded strip takes twice its width."""
        return self.width_used + self.fold_width

    @property
    def compactness(self):
        """Area of the pieces cut over the rectangle around them (a double layer counts twice)."""
        cut = sum(p.outline.area * (2 if p.instance.double else 1) for p in self.placements)
        return cut / (self.length * self.flat_width)

    def score(self, aim):
        return score(aim, self.length, self.flat_width)

    def describe(self):
        return f"{self.length:.0f} × {self.flat_width:.0f} mm"

    @property
    def utilisation(self):
        """Area of all pieces cut over the area of fabric used (a double layer counts twice)."""
        cut = sum(p.outline.area * (2 if p.instance.double else 1) for p in self.placements)
        used = self.fabric_shape.intersection(box(-1e9, 0, 1e9, self.length)).area + self.fold_width * self.length
        return cut / used


def _align(geom, grain_deg):
    """Turn a page shape so its grainline runs along y (a grainline has no direction)."""
    grain = grain_deg % 180 if grain_deg is not None else 90.0
    return affinity.rotate(geom, 90.0 - grain, origin=(0, 0))


def _rotations(piece, one_way):
    rots = [0] if one_way else [0, 180]
    if piece.cross_grain:
        rots += [90] if one_way else [90, 270]
    return tuple(rots)


def _grow(poly, sa):
    return poly.buffer(sa, join_style=JOIN_STYLE.mitre) if sa else poly


def _fold_half(piece, sa):
    """(half piece, notches) aligned, with the fold edge on x = 0 and the piece to the right; None if not possible."""
    half = _align(piece.half, piece.grain_deg)
    marks = _align(piece.half_marks, piece.grain_deg)
    a, b = [_align(Point(p), piece.grain_deg) for p in piece.fold_edge]
    if abs(a.x - b.x) > 1.0:
        return None  # fold edge not parallel to the grainline
    if half.centroid.x < a.x:
        half = affinity.scale(half, -1, 1, origin=(a.x, 0))
        marks = affinity.scale(marks, -1, 1, origin=(a.x, 0))
    grown = _grow(half, sa).intersection(box(a.x, -1e9, 1e9, 1e9))  # no allowance on the fold
    return affinity.translate(grown, -a.x, 0), affinity.translate(marks, -a.x, 0)


def _fold_line(piece):
    """The fold line across the whole aligned piece, or None if it has no fold."""
    if piece.fold_edge is None:
        return None
    (ax, ay), (bx, by) = piece.fold_edge
    dx, dy = bx - ax, by - ay
    long = LineString([(ax - dx * 100, ay - dy * 100), (bx + dx * 100, by + dy * 100)])
    return _align(long.intersection(piece.outline), piece.grain_deg)


def _stretch(geom, y0, amount):
    """Lengthen (amount > 0) or shorten a shape along y at the line y = y0."""
    if geom is None or geom.is_empty or not amount:
        return geom
    if geom.geom_type in ("LineString", "MultiLineString"):
        parts = getattr(geom, "geoms", [geom])
        moved = [affinity.translate(g, 0, amount) if g.centroid.y > y0 else g for g in parts]
        return MultiLineString(moved) if geom.geom_type == "MultiLineString" else moved[0]
    big = 1e6
    top = geom.intersection(box(-big, -big, big, y0))
    cut = y0 if amount > 0 else y0 - amount
    bottom = affinity.translate(geom.intersection(box(-big, cut, big, big)), 0, amount)
    parts = [top, bottom]
    if amount > 0:  # fill the gap with the piece's cross-section at y0
        section = geom.intersection(LineString([(-big, y0), (big, y0)]))
        for seg in getattr(section, "geoms", [section]):
            if seg.geom_type == "LineString" and seg.length > 0:
                (x0, _), (x1, _) = seg.coords[0], seg.coords[-1]
                parts.append(box(min(x0, x1), y0, max(x0, x1), y0 + amount))
    joined = unary_union([p for p in parts if not p.is_empty]).buffer(0.01).buffer(-0.01)
    return max(getattr(joined, "geoms", [joined]), key=lambda g: g.area)


def _lengthened(piece):
    """A copy of the piece lengthened or shortened along the grain, in page coordinates."""
    if not piece.lengthen or piece.lengthen_at is None:
        return piece
    grain = piece.grain_deg % 180 if piece.grain_deg is not None else 90.0
    back = lambda g: affinity.rotate(g, grain - 90.0, origin=(0, 0)) if g is not None else None
    y0 = _align(piece.outline, piece.grain_deg).bounds[1] + piece.lengthen_at
    change = lambda g: back(_stretch(_align(g, piece.grain_deg), y0, piece.lengthen)) if g is not None else None
    edge = None
    if piece.fold_edge is not None:
        a, b = (_align(Point(p), piece.grain_deg) for p in piece.fold_edge)
        a, b = (back(Point(q.x, q.y + (piece.lengthen if q.y > y0 else 0))) for q in (a, b))
        edge = ((a.x, a.y), (b.x, b.y))
    return replace(piece, outline=change(piece.outline), half=change(piece.half), marks=change(piece.marks),
                   half_marks=change(piece.half_marks), fold_edge=edge)


def _instances(pieces, s, folding):
    """(double-layer instances, single-layer instances)."""
    double, single = [], []
    for piece in map(_lengthened, pieces):
        rots = _rotations(piece, s.one_way)
        full = _grow(_align(piece.outline, piece.grain_deg), s.seam_allowance)
        marks = _align(piece.marks, piece.grain_deg)
        fold_line = _fold_line(piece)
        match_y = piece.match_y + s.seam_allowance if s.stripe_repeat and piece.match_y is not None else None
        half = _fold_half(piece, s.seam_allowance) if folding and piece.half is not None and piece.cut_on_fold else None
        if half is not None:
            for copy in range(1, piece.copies + 1):
                double.append(Instance(f"{piece.name} {copy}/{piece.copies} (on fold)", half[0], rots, match_y,
                                       fold=True, double=True, marks=half[1]))
            continue
        copy = 1
        if folding and piece.mirror:
            while copy + 1 <= piece.copies:  # a mirrored pair cut through both layers
                double.append(Instance(f"{piece.name} {copy}+{copy + 1}/{piece.copies} (both layers)", full, rots,
                                       match_y, double=True, min_x=0.0, marks=marks))
                copy += 2
        for c in range(copy, piece.copies + 1):
            mirrored = piece.mirror and c % 2 == 0
            shape, m, f = full, marks, fold_line
            if mirrored:
                centre = full.centroid
                shape, m = (affinity.scale(g, -1, 1, origin=centre) for g in (full, marks))
                f = affinity.scale(f, -1, 1, origin=centre) if f is not None else None
            single.append(Instance(f"{piece.name} {c}/{piece.copies}" + (" (mirrored)" if mirrored else ""),
                                   shape, rots, match_y, marks=m, fold_line=f))
    return double, single


def _single_layout(name, single, s, stripes, notes):
    fabric = s.shape if s.shape is not None else rectangle(s.width, pieces=[i.shape for i in single])
    placements, length, used = nest(single, fabric, s.gap, stripes, s.tries, aim=s.aim)
    shape = s.shape if s.shape is not None else box(0, 0, s.width, length)
    return Layout(name, placements, length, used, shape, 0.0, notes)


def _fold_layout(name, double, single, s, stripes, fold_width, tries, aim=None):
    """Double layer in [0, fold_width], single layer in [fold_width, width - fold_width]."""
    folded_width = s.width - fold_width
    length_bound = rectangle(0, pieces=[i.shape for i in double + single]).bounds[3]
    # left of x = 0 is the mirror image, used only by the fold edge's margin
    zone = box(-(s.gap + 10), 0, fold_width, length_bound)
    placements, length, used = nest(double, zone, s.gap, stripes, tries, aim=aim or s.aim)
    if single:
        if folded_width - fold_width <= 0:
            raise ValueError("no single-layer fabric left")
        more, l2, u2 = nest(single, box(fold_width, 0, folded_width, length_bound), s.gap, stripes, tries,
                            aim=aim or s.aim)
        placements, length, used = placements + more, max(length, l2), max(fold_width, u2)
    return Layout(name, placements, length, used, box(0, 0, folded_width, length), fold_width)


def _layout_group(name, group, s, stripes):
    folding = s.shape is None and any(p.half is not None and p.cut_on_fold for p in group)
    notes = []
    if s.shape is not None and any(p.half is not None and p.cut_on_fold for p in group):
        notes.append("Cut-on-fold pieces are unfolded because this fabric has no straight selvedge to fold.")
    double, single = _instances(group, s, folding)
    if not any(i.fold for i in double):
        return _single_layout(name, _instances(group, s, False)[1], s, stripes, notes)

    narrowest = max(i.shape.bounds[2] - i.shape.bounds[0] for i in double) + s.gap + 10
    best = None
    width = narrowest
    while width <= s.width / 2:
        try:
            # quick trials packed for length; the chosen width is packed with the real aim below
            trial = _fold_layout(name, double, single, s, stripes, width, tries=1, aim="length")
        except ValueError:
            break
        if best is None or trial.score(s.aim) < best.score(s.aim):
            best = trial
        width += FOLD_STEP
    if best is None:
        notes.append("The fabric is too narrow to fold; cut-on-fold pieces are unfolded.")
        return _single_layout(name, _instances(group, s, False)[1], s, stripes, notes)
    folded = _fold_layout(name, double, single, s, stripes, best.fold_width, s.tries)
    unfolded = _single_layout(name, _instances(group, s, False)[1], s, stripes, notes)
    if unfolded.score(s.aim) < folded.score(s.aim):
        notes.append(f"Unfolded the cut-on-fold pieces: {unfolded.describe()} of fabric, "
                     f"against {folded.describe()} cutting them on a folded strip.")
        return unfolded
    folded.notes = notes + [f"Cutting on a folded strip: {folded.describe()} of fabric, "
                            f"against {unfolded.describe()} unfolded."]
    return folded


def make_layouts(pieces, s: FabricSettings):
    stripes = Stripes(s.stripe_repeat, s.stripe_phase) if s.stripe_repeat else None
    fabrics = sorted({p.fabric for p in pieces if p.include})
    return [_layout_group(f, [p for p in pieces if p.include and p.fabric == f], s, stripes) for f in fabrics]
