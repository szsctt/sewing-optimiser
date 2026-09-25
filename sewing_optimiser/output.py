"""Write a nested layout as 1:1 SVG and PDF (units: mm)."""

import pymupdf

from .pdf_import import MM_PER_PT

MARGIN = 20  # mm around the fabric
CAL = 100  # side of the calibration square, mm


def _label(p):
    text = f"{p.piece.name} {p.copy}/{p.piece.copies}"
    if p.mirrored:
        text += " (mirrored)"
    return text


def _grainline(p):
    """End points of a grainline arrow through the piece, parallel to the selvedge."""
    c = p.outline.representative_point()
    _, miny, _, maxy = p.outline.bounds
    half = (maxy - miny) * 0.3
    return (c.x, c.y - half), (c.x, c.y + half)


def _drawing(placements, width, length):
    """Shapes shared by both outputs: (kind, data) with coordinates in mm."""
    shapes = [("fabric", [(0, 0), (width, 0), (width, length), (0, length)])]
    for p in placements:
        shapes.append(("piece", list(p.outline.exterior.coords)))
        shapes.append(("grain", _grainline(p)))
        c = p.outline.representative_point()
        shapes.append(("text", ((c.x + 5, c.y), _label(p))))
    y = length + MARGIN
    shapes.append(("piece", [(0, y), (CAL, y), (CAL, y + CAL), (0, y + CAL)]))
    shapes.append(("text", ((5, y + CAL / 2), "10 cm check square")))
    shapes.append(("text", ((width / 2 - 40, -8), f"fabric width {width:.0f} mm, length used {length:.0f} mm")))
    return shapes, width + 2 * MARGIN, length + 3 * MARGIN + CAL


def write_svg(path, placements, width, length):
    shapes, w, h = _drawing(placements, width, length)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.1f}mm" height="{h:.1f}mm" '
        f'viewBox="{-MARGIN} {-MARGIN} {w:.1f} {h:.1f}">',
        '<g fill="none" stroke="black" stroke-width="0.5" font-family="sans-serif" font-size="8">',
    ]
    for kind, data in shapes:
        if kind in ("fabric", "piece"):
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in data)
            dash = ' stroke-dasharray="5 3"' if kind == "fabric" else ""
            out.append(f'<polygon points="{pts}"{dash}/>')
        elif kind == "grain":
            (x0, y0), (x1, y1) = data
            out.append(f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}"/>')
            for yy, d in ((y0, 1), (y1, -1)):
                out.append(f'<polyline points="{x0 - 3:.2f},{yy + 6 * d:.2f} {x0:.2f},{yy:.2f} {x0 + 3:.2f},{yy + 6 * d:.2f}"/>')
        elif kind == "text":
            (x, y), text = data
            out.append(f'<text x="{x:.2f}" y="{y:.2f}" fill="black" stroke="none">{text}</text>')
    out += ["</g>", "</svg>"]
    with open(path, "w") as f:
        f.write("\n".join(out))


def write_pdf(path, placements, width, length):
    shapes, w, h = _drawing(placements, width, length)
    doc = pymupdf.open()
    page = doc.new_page(width=w / MM_PER_PT, height=h / MM_PER_PT)

    def pt(x, y):
        return pymupdf.Point((x + MARGIN) / MM_PER_PT, (y + MARGIN) / MM_PER_PT)

    for kind, data in shapes:
        if kind in ("fabric", "piece"):
            pts = [pt(*xy) for xy in data]
            page.draw_polyline(pts + [pts[0]], width=1.4, dashes="[14 8] 0" if kind == "fabric" else None)
        elif kind == "grain":
            (x0, y0), (x1, y1) = data
            page.draw_line(pt(x0, y0), pt(x1, y1), width=1.4)
            for yy, d in ((y0, 1), (y1, -1)):
                page.draw_polyline([pt(x0 - 3, yy + 6 * d), pt(x0, yy), pt(x0 + 3, yy + 6 * d)], width=1.4)
        elif kind == "text":
            (x, y), text = data
            page.insert_text(pt(x, y), text, fontsize=8 / MM_PER_PT)
    doc.save(path)
