"""Changes a person makes to pieces read from a PDF: marking a piece as cut on fold, joining two pieces."""

import math
from dataclasses import replace

from shapely import affinity
from shapely.geometry import LineString, MultiLineString
from shapely.ops import unary_union

from .pdf_import import MIN_FOLD_EDGE, _reflect


def _aligned(geom, grain_deg):
    grain = grain_deg % 180 if grain_deg is not None else 90.0
    return affinity.rotate(geom, 90.0 - grain, origin=(0, 0)) if geom is not None else None


def _edges(poly):
    coords = list(poly.simplify(0.3).exterior.coords)
    return [(a, b) for a, b in zip(coords, coords[1:]) if math.dist(a, b) >= MIN_FOLD_EDGE / 2]


def _unfold(half, marks, edge):
    a, b = edge
    full = unary_union([half, _reflect(half, a, b)]).buffer(0.01).buffer(-0.01)
    both = MultiLineString(list(marks.geoms) + list(_reflect(marks, a, b).geoms)) if not marks.is_empty else marks
    return full, both


def mark_on_fold(piece):
    """Treat the piece as a half cut on the fold: its longest straight edge along the grain is the fold."""
    if piece.half is not None:
        return piece
    grain = piece.grain_deg if piece.grain_deg is not None else 90.0
    along = [(a, b) for a, b in _edges(piece.outline)
             if abs((math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) - grain + 90) % 180 - 90) < 3]
    if not along:
        return piece
    edge = max(along, key=lambda e: math.dist(*e))
    full, marks = _unfold(piece.outline, piece.marks, edge)
    return replace(piece, outline=full, marks=marks, half=piece.outline, half_marks=piece.marks, fold_edge=edge)


def join(upper, lower):
    """One piece from two drawn in parts: the lower piece's top edge laid on the upper piece's bottom edge.

    Both are turned so their grainlines run down the page. The edges are
    the lowest straight edge across the upper piece and the highest across
    the lower one, matched at their ends on the fold side (or the left).
    """
    folded = upper.half is not None and lower.half is not None
    a = _aligned(upper.half if folded else upper.outline, upper.grain_deg)
    b = _aligned(lower.half if folded else lower.outline, lower.grain_deg)
    ma = _aligned(upper.half_marks if folded else upper.marks, upper.grain_deg)
    mb = _aligned(lower.half_marks if folded else lower.marks, lower.grain_deg)
    across = lambda poly: [e for e in _edges(poly) if abs(e[0][1] - e[1][1]) < 2]
    if not across(a) or not across(b):
        return None
    bottom = max(across(a), key=lambda e: e[0][1] + e[1][1])
    top = min(across(b), key=lambda e: e[0][1] + e[1][1])
    if folded:  # match the ends on the fold, which lies on the side away from the piece
        fa = _aligned(LineString(upper.fold_edge), upper.grain_deg).centroid.x
        fb = _aligned(LineString(lower.fold_edge), lower.grain_deg).centroid.x
        end_a = min(bottom, key=lambda p: abs(p[0] - fa))
        end_b = min(top, key=lambda p: abs(p[0] - fb))
        if (a.centroid.x - fa) * (b.centroid.x - fb) < 0:  # halves drawn on opposite sides of their folds
            b, mb = (affinity.scale(g, -1, 1, origin=(fb, 0)) for g in (b, mb))
            end_b = (2 * fb - end_b[0], end_b[1])
    else:
        end_a, end_b = min(bottom), min(top)
    dx, dy = end_a[0] - end_b[0], end_a[1] - end_b[1]
    b, mb = (affinity.translate(g, dx, dy) for g in (b, mb))
    shape = unary_union([a, b]).buffer(0.5).buffer(-0.5)  # close the seam where the edges differ slightly
    shape = max(getattr(shape, "geoms", [shape]), key=lambda g: g.area)
    marks = MultiLineString(list(ma.geoms) + list(mb.geoms))
    name = f"{upper.name} + {lower.name}"
    if not folded:
        return replace(upper, name=name, outline=shape, marks=marks, grain_deg=90.0, page=None)  # no longer on a sheet
    fold_x = end_a[0]
    _, miny, _, maxy = shape.bounds
    edge = ((fold_x, miny), (fold_x, maxy))
    full, both = _unfold(shape, marks, edge)
    return replace(upper, name=name, outline=full, marks=both, grain_deg=90.0, half=shape, half_marks=marks,
                   fold_edge=edge, page=None)


def trim(piece, indices):
    """The piece without the parts listed (by index into piece.regions): the cutting options not taken."""
    drop = [piece.regions[i] for i in indices if i < len(piece.regions)]
    if not drop:
        return piece
    gone = unary_union(drop).buffer(0.5)
    base = piece.half if piece.half is not None else piece.outline
    kept = base.difference(gone)
    kept = max(getattr(kept, "geoms", [kept]), key=lambda g: g.area)
    marks = lambda m: MultiLineString([l for l in m.geoms if not gone.contains(l.centroid)])
    if piece.half is None:
        return replace(piece, outline=kept, marks=marks(piece.marks))
    full, both = _unfold(kept, marks(piece.half_marks), piece.fold_edge)
    return replace(piece, outline=full, marks=both, half=kept, half_marks=marks(piece.half_marks))
