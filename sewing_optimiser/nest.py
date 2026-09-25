"""Nest pattern pieces on fabric using a raster (grid) search.

Fabric coordinates: x runs across the width (selvedge to selvedge), y runs
along the length, both in mm. The grainline of every piece is turned
parallel to y. Each piece is placed at the lowest free position, found for
every position at once by correlating the piece's grid mask with the grid of
occupied cells. Several piece orders are tried and the shortest layout kept.

Grid masks are built from shapes grown by half the gap plus a cell diagonal,
so two masks that do not overlap mean the real pieces are at least `gap`
apart and inside the fabric; `check` confirms this on the exact shapes.
"""

import random
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw
from scipy.signal import fftconvolve
from shapely import affinity
from shapely.geometry import box

RES = 2.0  # mm per grid cell
SLACK = RES * 0.71 + RES / 2  # half a cell diagonal plus rounding when drawing polygons


@dataclass
class Instance:
    """One piece to cut, already turned so its grainline runs along y."""

    label: str
    shape: object  # shapely Polygon, any position
    rotations: tuple  # allowed extra turns, from 0, 90, 180, 270
    match_y: float | None = None  # stripe match line, mm below the top of `shape`
    fold: bool = False  # half piece whose fold edge (the left side) sits on the fabric fold
    double: bool = False  # cut through two layers of folded fabric
    min_x: float | None = None  # leftmost allowed x (keeps double-layer pieces off the mirror side)
    marks: object = None  # notches, moved with the piece
    fold_line: object = None  # where to fold a rough-cut unfolded piece, moved with the piece
    data: dict = field(default_factory=dict)  # passed through to the placement


@dataclass
class Placement:
    instance: Instance
    rotation: int
    outline: object  # shapely Polygon in fabric coordinates (mm)
    marks: object = None  # notches in fabric coordinates
    fold_line: object = None


@dataclass
class Stripes:
    repeat: float  # mm between identical stripes along the length
    phase: float = 0.0  # y of a reference stripe, mm


def _turn(inst, rot):
    """[shape, marks, fold_line] turned by rot and moved so the shape's bounding box starts at (0, 0)."""
    geoms = [inst.shape, inst.marks, inst.fold_line]
    present = [g is not None for g in geoms]
    geoms = [g for g in geoms if g is not None]
    if inst.fold and rot == 180:
        geoms = [affinity.scale(g, 1, -1, origin=(0, 0)) for g in geoms]  # turned and mirrored: fold edge stays left
    else:
        centre = inst.shape.centroid
        geoms = [affinity.rotate(g, rot, origin=centre) for g in geoms]
    minx, miny, _, _ = geoms[0].bounds
    moved = iter(affinity.translate(g, -minx, -miny) for g in geoms)
    return [next(moved) if p else None for p in present]


def _raster(geom, rows, cols, dx=0.0, dy=0.0):
    """Cells whose centres fall inside geom (shifted by dx, dy mm)."""
    img = Image.new("1", (cols, rows), 0)
    draw = ImageDraw.Draw(img)
    for poly in getattr(geom, "geoms", [geom]):
        pts = [((x + dx) / RES - 0.5, (y + dy) / RES - 0.5) for x, y in poly.exterior.coords]
        draw.polygon(pts, fill=1)
        for hole in poly.interiors:
            draw.polygon([((x + dx) / RES - 0.5, (y + dy) / RES - 0.5) for x, y in hole.coords], fill=0)
    return np.array(img, dtype=bool)


def _variants(inst, gap, stripes):
    """(rotation, shape at origin, [marks, fold line], grid mask, margin, match_y) for each allowed turn."""
    out = []
    for rot in inst.rotations:
        if inst.fold and rot in (90, 270):
            continue  # the fold edge must stay along the length
        shape, *extras = _turn(inst, rot)
        _, _, w, h = shape.bounds
        match_y = None
        if inst.match_y is not None:
            if rot in (90, 270):
                continue  # a turned piece cannot match stripes along the length
            match_y = inst.match_y if rot == 0 else h - inst.match_y
        snapped = match_y is not None or inst.fold  # final position moves off the grid by up to a cell
        margin = gap / 2 + SLACK + (RES if snapped else 0)
        grown = shape.buffer(margin)
        rows, cols = int(np.ceil((h + 2 * margin) / RES)) + 1, int(np.ceil((w + 2 * margin) / RES)) + 1
        out.append((rot, shape, extras, _raster(grown, rows, cols, margin, margin), margin, match_y))
    return out


def _place_all(instances, fabric, gap, stripes, order):
    minx, miny, maxx, maxy = fabric.bounds
    cols, rows = int((maxx - minx) / RES) + 1, int((maxy - miny) / RES) + 1
    blocked = ~_raster(fabric.buffer(-SLACK), rows, cols, -minx, -miny)
    used_len = used_w = 0.0
    placements = []
    for i in order:
        inst = instances[i]
        best = None
        # positions lower than the current length plus this piece can only be worse
        tallest = max(v[3].shape[0] for v in inst.variants) if inst.variants else 0
        grid = blocked[:min(rows, int(used_len / RES) + 2 * tallest + 2)].astype(np.float32)
        for rot, shape, extras, mask, margin, match_y in inst.variants:
            mh, mw = mask.shape
            if mh > rows or mw > cols:
                continue
            # overlap[r, c] > 0 where the mask's top-left cell at (r, c) hits a blocked cell
            overlap = fftconvolve(grid, mask[::-1, ::-1].astype(np.float32), mode="valid")
            free = overlap < 0.5
            if inst.fold:
                keep = np.zeros_like(free)
                c0 = int(round((0 - minx - margin) / RES))  # column putting the fold edge on x = 0
                if 0 <= c0 < free.shape[1]:
                    keep[:, c0] = free[:, c0]
                free = keep
            r, c = np.nonzero(free)
            if not len(r):
                continue
            x = minx + c * RES + margin
            y = miny + r * RES + margin
            if inst.fold:
                x = np.zeros_like(x)
            if inst.min_x is not None:
                ok = x >= inst.min_x
                r, c, x, y = r[ok], c[ok], x[ok], y[ok]
            _, _, w, h = shape.bounds
            if match_y is not None:
                # move each candidate to the nearest y that puts the match line on a stripe
                target = stripes.phase - match_y + np.round((y + match_y - stripes.phase) / stripes.repeat) * stripes.repeat
                ok = np.abs(target - y) <= RES / 2
                r, c, x, y = r[ok], c[ok], x[ok], target[ok]
                if not len(r):
                    continue
            k1 = np.maximum(used_len, y + h - miny)
            k2 = np.maximum(used_w, x + w - minx)
            idx = np.lexsort((x, y, k2, k1))[0]
            key = (k1[idx], k2[idx], y[idx], x[idx])
            if best is None or key < best[0]:
                best = (key, rot, shape, extras, mask, r[idx], c[idx], x[idx], y[idx])
        if best is None:
            return None
        (used_len, used_w, _, _), rot, shape, extras, mask, r, c, x, y = best
        marks, fold_line = (affinity.translate(g, x, y) if g is not None else None for g in extras)
        placements.append(Placement(inst, rot, affinity.translate(shape, x, y), marks, fold_line))
        mh, mw = mask.shape
        blocked[r:r + mh, c:c + mw] |= mask
    return placements, used_len, used_w


def nest(instances, fabric, gap=3.0, stripes=None, tries=8, seed=0):
    """Place every instance on the fabric polygon; returns (placements, length, width used)."""
    for inst in instances:
        inst.variants = _variants(inst, gap, stripes)
    by_area = sorted(range(len(instances)), key=lambda i: -instances[i].shape.area)
    rng = random.Random(seed)
    best = None
    for t in range(tries):
        order = by_area[:]
        if t:  # later tries swap a few neighbours in the largest-first order
            for _ in range(max(1, len(order) // 3)):
                j = rng.randrange(len(order) - 1) if len(order) > 1 else 0
                order[j:j + 2] = order[j:j + 2][::-1]
        result = _place_all(instances, fabric, gap, stripes, order)
        if result and (best is None or result[1:] < best[1:]):
            best = result
    if best is None:
        raise ValueError("the pieces do not fit on this fabric")
    check(best[0], fabric, gap)
    return best


def check(placements, fabric, gap):
    """Raise if any two pieces are closer than gap or a piece leaves the fabric."""
    for i, a in enumerate(placements):
        if not fabric.buffer(1e-6).contains(a.outline):
            raise AssertionError(f"{a.instance.label} leaves the fabric")
        for b in placements[i + 1:]:
            if a.outline.distance(b.outline) < gap - 1e-6:
                raise AssertionError(f"{a.instance.label} and {b.instance.label} are closer than {gap} mm")


def rectangle(width, length=None, pieces=()):
    """Fabric rectangle; without a length, long enough for all pieces in a column."""
    if length is None:
        length = sum(max(p.bounds[2] - p.bounds[0], p.bounds[3] - p.bounds[1]) for p in pieces) + 100
    return box(0, 0, width, length)
