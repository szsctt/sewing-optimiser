"""Draw layouts as 1:1 SVG (mm units) and PDF."""

from html import escape

import pymupdf
from shapely.geometry import box

MARGIN = 20  # mm around the fabric
CAL = 100  # side of the calibration square, mm


def _points(coords):
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)


def _polygon(poly, attrs=""):
    d = "".join(f"M{_points(ring.coords)}Z" for ring in [poly.exterior, *poly.interiors])
    return f'<path d="{d.replace(" ", "L")}" {attrs}/>'


def _grainline(p):
    """Arrow through the piece along the grain: vertical, or horizontal if turned 90°."""
    c = p.outline.representative_point()
    minx, miny, maxx, maxy = p.outline.bounds
    if p.rotation in (90, 270):
        half = (maxx - minx) * 0.3
        a, b = (c.x - half, c.y), (c.x + half, c.y)
        heads = [(a, (4, -2), (4, 2)), (b, (-4, -2), (-4, 2))]
    else:
        half = (maxy - miny) * 0.3
        a, b = (c.x, c.y - half), (c.x, c.y + half)
        heads = [(a, (-2, 4), (2, 4)), (b, (-2, -4), (2, -4))]
    out = [f'<line x1="{a[0]:.2f}" y1="{a[1]:.2f}" x2="{b[0]:.2f}" y2="{b[1]:.2f}"/>']
    for (x, y), (dx1, dy1), (dx2, dy2) in heads:
        out.append(f'<polyline points="{x + dx1:.2f},{y + dy1:.2f} {x:.2f},{y:.2f} {x + dx2:.2f},{y + dy2:.2f}"/>')
    return out


def layout_svg(layout, stripes=None, inverted=False, calibration=True):
    """SVG of one layout at 1:1 scale (1 user unit = 1 mm)."""
    fg, bg = ("white", "black") if inverted else ("black", "white")
    minx, _, maxx, _ = layout.fabric_shape.bounds
    minx = min(minx, 0)
    length = layout.length
    w = maxx - minx + 2 * MARGIN
    h = length + 2 * MARGIN + (CAL + MARGIN if calibration else 0)
    x0, y0 = minx - MARGIN, -MARGIN
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.1f}mm" height="{h:.1f}mm" viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}">',
        f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{bg}"/>',
        f'<g fill="none" stroke="{fg}" stroke-width="0.6" font-family="Helvetica, sans-serif" font-size="8">',
    ]
    shown = layout.fabric_shape.intersection(box(minx, 0, maxx, length))
    for part in getattr(shown, "geoms", [shown]):
        if part.geom_type == "Polygon":
            out.append(_polygon(part, 'stroke-dasharray="6 3"'))
    if stripes:
        y = stripes.phase % stripes.repeat
        while y < length:
            out.append(f'<line x1="{minx:.1f}" y1="{y:.2f}" x2="{maxx:.1f}" y2="{y:.2f}" stroke-opacity="0.3" stroke-width="0.4"/>')
            y += stripes.repeat
    if layout.fold_width:
        fw = layout.fold_width
        out.append(f'<rect x="0" y="0" width="{fw:.1f}" height="{length:.1f}" fill="{fg}" fill-opacity="0.08" stroke="none"/>')
        out.append(f'<line x1="0" y1="0" x2="0" y2="{length:.1f}" stroke-width="1.5" stroke-dasharray="12 4"/>')
        out.append(f'<text x="3" y="{length - 4:.1f}" fill="{fg}" stroke="none">FOLD — double layer to {fw:.0f} mm</text>')
    for p in layout.placements:
        out.append(_polygon(p.outline, 'stroke-width="0.8"'))
        out += _grainline(p)
        c = p.outline.representative_point()
        out.append(f'<text x="{c.x + 4:.1f}" y="{c.y:.1f}" fill="{fg}" stroke="none">{escape(p.instance.label)}</text>')
    title = f"{layout.fabric}: length used {length:.0f} mm, width used {layout.width_used:.0f} mm"
    if layout.fold_width:
        title += f", fold {layout.fold_width:.0f} mm of the left selvedge over first"
    out.append(f'<text x="{minx:.1f}" y="-8" fill="{fg}" stroke="none">{escape(title)}</text>')
    if calibration:
        y = length + MARGIN
        out.append(f'<rect x="{minx:.1f}" y="{y:.1f}" width="{CAL}" height="{CAL}" stroke-width="0.8"/>')
        out.append(f'<text x="{minx + 5:.1f}" y="{y + CAL / 2:.1f}" fill="{fg}" stroke="none">10 cm check square</text>')
    out += ["</g>", "</svg>"]
    return "\n".join(out)


def write_svg(path, layout, stripes=None):
    with open(path, "w") as f:
        f.write(layout_svg(layout, stripes))


def write_pdf(path, layouts, stripes=None):
    """One page per fabric, each at 1:1 scale."""
    doc = pymupdf.open()
    for layout in layouts:
        svg = pymupdf.open(stream=layout_svg(layout, stripes).encode(), filetype="svg")
        doc.insert_pdf(pymupdf.open("pdf", svg.convert_to_pdf()))
    doc.save(path)
