"""Greedy bottom-left nesting of pieces on a rectangle of fixed width.

Fabric coordinates: x runs across the width (selvedge to selvedge), y runs
along the length. The grainline of every piece is turned parallel to y.
"""

from dataclasses import dataclass

from shapely import affinity, prepared
from shapely.ops import unary_union

from .pdf_import import Piece


@dataclass
class Placement:
    piece: Piece
    copy: int  # 1-based
    mirrored: bool
    rotation: int  # 0 or 180, after aligning the grainline
    outline: object  # shapely Polygon in fabric coordinates (mm)


def _grain_aligned(piece):
    grain = piece.grain_deg % 180 if piece.grain_deg is not None else 90.0  # a grainline has no direction
    return affinity.rotate(piece.outline, 90.0 - grain, origin="centroid")


def _to_origin(poly):
    minx, miny, _, _ = poly.bounds
    return affinity.translate(poly, -minx, -miny)


def nest(pieces, width, gap=3.0, step=5.0, allow_180=True):
    instances = []
    for piece in pieces:
        base = _grain_aligned(piece)
        for copy in range(1, piece.copies + 1):
            mirrored = copy % 2 == 0  # left/right pairs on single-layer fabric
            shape = affinity.scale(base, -1, 1, origin="centroid") if mirrored else base
            instances.append((piece, copy, mirrored, shape))
    instances.sort(key=lambda inst: -inst[3].area)

    placements, obstacles = [], []
    used_length = used_width = 0.0
    for piece, copy, mirrored, shape in instances:
        blocked = prepared.prep(unary_union(obstacles)) if obstacles else None
        best = None
        for rotation in (0, 180) if allow_180 else (0,):
            candidate = _to_origin(affinity.rotate(shape, rotation, origin="centroid"))
            _, _, w, h = candidate.bounds
            if w > width:
                raise ValueError(f"{piece.name} ({w:.0f} mm) is wider than the fabric")
            x = 0.0
            while x + w <= width:
                y = 0.0
                moved = affinity.translate(candidate, x, y)
                while blocked and blocked.intersects(moved):
                    y += step
                    moved = affinity.translate(candidate, x, y)
                # Shortest fabric first, then narrowest, so offcuts stay in one piece.
                key = (max(used_length, y + h), max(used_width, x + w), y, x)
                if best is None or key < best[0]:
                    best = (key, rotation, moved)
                x += step
        (used_length, used_width, _, _), rotation, outline = best
        placements.append(Placement(piece, copy, mirrored, rotation, outline))
        obstacles.append(outline.buffer(gap))

    used = sum(p.outline.area for p in placements)
    return placements, used_length, used_width, used / (width * used_length)
