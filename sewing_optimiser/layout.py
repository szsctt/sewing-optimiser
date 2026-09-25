"""Turn reviewed pieces and fabric settings into nested layouts, one per fabric.

Cut-on-fold pieces are either unfolded and cut single layer, or cut on a
folded strip: the left selvedge is folded over by `fold_width`, giving a
double layer from the fold (x = 0) to x = fold_width. Half pieces sit with
their fold edge on the fold, and pieces cut in mirrored pairs also go in the
double layer, so one placement cuts both. Everything else is cut single
layer to the right of the strip. Several strip widths are tried, and the
unfolded layout is used instead when it needs less fabric.
"""

from dataclasses import dataclass, field

from shapely import affinity
from shapely.geometry import JOIN_STYLE, Point, box

from .nest import Instance, Stripes, nest, rectangle

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
    """The half piece, aligned, with its fold edge on x = 0 and the piece to the right; None if not possible."""
    half = _align(piece.half, piece.grain_deg)
    a, b = [_align(Point(p), piece.grain_deg) for p in piece.fold_edge]
    if abs(a.x - b.x) > 1.0:
        return None  # fold edge not parallel to the grainline
    if half.centroid.x < a.x:
        half = affinity.scale(half, -1, 1, origin=(a.x, 0))
    grown = _grow(half, sa).intersection(box(a.x, -1e9, 1e9, 1e9))  # no allowance on the fold
    return affinity.translate(grown, -a.x, 0)


def _instances(pieces, s, folding):
    """(double-layer instances, single-layer instances)."""
    double, single = [], []
    for piece in pieces:
        rots = _rotations(piece, s.one_way)
        full = _grow(_align(piece.outline, piece.grain_deg), s.seam_allowance)
        match_y = piece.match_y + s.seam_allowance if s.stripe_repeat and piece.match_y is not None else None
        half = _fold_half(piece, s.seam_allowance) if folding and piece.half is not None and piece.cut_on_fold else None
        if half is not None:
            for copy in range(1, piece.copies + 1):
                double.append(Instance(f"{piece.name} {copy}/{piece.copies} (on fold)", half, rots, match_y,
                                       fold=True, double=True))
            continue
        copy = 1
        if folding and piece.mirror:
            while copy + 1 <= piece.copies:  # a mirrored pair cut through both layers
                double.append(Instance(f"{piece.name} {copy}+{copy + 1}/{piece.copies} (both layers)", full, rots,
                                       match_y, double=True, min_x=0.0))
                copy += 2
        for c in range(copy, piece.copies + 1):
            mirrored = piece.mirror and c % 2 == 0
            shape = affinity.scale(full, -1, 1, origin="centroid") if mirrored else full
            single.append(Instance(f"{piece.name} {c}/{piece.copies}" + (" (mirrored)" if mirrored else ""),
                                   shape, rots, match_y))
    return double, single


def _single_layout(name, single, s, stripes, notes):
    fabric = s.shape if s.shape is not None else rectangle(s.width, pieces=[i.shape for i in single])
    placements, length, used = nest(single, fabric, s.gap, stripes, s.tries)
    shape = s.shape if s.shape is not None else box(0, 0, s.width, length)
    return Layout(name, placements, length, used, shape, 0.0, notes)


def _fold_layout(name, double, single, s, stripes, fold_width, tries):
    """Double layer in [0, fold_width], single layer in [fold_width, width - fold_width]."""
    folded_width = s.width - fold_width
    length_bound = rectangle(0, pieces=[i.shape for i in double + single]).bounds[3]
    # left of x = 0 is the mirror image, used only by the fold edge's margin
    zone = box(-(s.gap + 10), 0, fold_width, length_bound)
    placements, length, used = nest(double, zone, s.gap, stripes, tries)
    if single:
        if folded_width - fold_width <= 0:
            raise ValueError("no single-layer fabric left")
        more, l2, u2 = nest(single, box(fold_width, 0, folded_width, length_bound), s.gap, stripes, tries)
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
            trial = _fold_layout(name, double, single, s, stripes, width, tries=1)
        except ValueError:
            break
        if best is None or (trial.length, trial.width_used) < (best.length, best.width_used):
            best = trial
        width += FOLD_STEP
    if best is None:
        notes.append("The fabric is too narrow to fold; cut-on-fold pieces are unfolded.")
        return _single_layout(name, _instances(group, s, False)[1], s, stripes, notes)
    folded = _fold_layout(name, double, single, s, stripes, best.fold_width, s.tries)
    unfolded = _single_layout(name, _instances(group, s, False)[1], s, stripes, notes)
    if unfolded.length < folded.length:
        notes.append(f"Unfolded the cut-on-fold pieces: {unfolded.length:.0f} mm of fabric, "
                     f"against {folded.length:.0f} mm cutting them on a folded strip.")
        return unfolded
    folded.notes = notes + [f"Cutting on a folded strip: {folded.length:.0f} mm of fabric, "
                            f"against {unfolded.length:.0f} mm unfolded."]
    return folded


def make_layouts(pieces, s: FabricSettings):
    stripes = Stripes(s.stripe_repeat, s.stripe_phase) if s.stripe_repeat else None
    fabrics = sorted({p.fabric for p in pieces if p.include})
    return [_layout_group(f, [p for p in pieces if p.include and p.fabric == f], s, stripes) for f in fabrics]
