"""Tighter layouts with Sparrow (https://github.com/JeroenGar/sparrow), a nesting engine run as a separate program.

Sparrow packs pieces into a strip of fixed height and as short as possible, so the
fabric width is the strip's height and the fabric length runs along the strip:
pieces are turned a quarter turn on the way in (grainline along the strip) and
back on the way out. While it runs it writes a picture of every better layout it
finds, which the app shows as progress.
"""

import json
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from shapely import affinity
from shapely.geometry import Polygon, box

from .layout import FabricSettings, Layout, _instances
from .nest import Placement

BINARY = Path(__file__).parent.parent / "vendor" / "sparrow" / "target" / "release" / "sparrow"


def available():
    return BINARY.exists()


def _to_strip(geom):
    return affinity.rotate(geom, -90, origin=(0, 0))  # grainline along the strip's length


def _from_strip(geom, width):
    """Back from strip coordinates: the length runs down the page and x across the fabric."""
    return affinity.translate(affinity.rotate(geom, 90, origin=(0, 0)), width, 0)


class Run:
    """One Sparrow run for the pieces of one fabric."""

    def __init__(self, fabric, instances, settings: FabricSettings, seconds):
        self.fabric, self.instances, self.width, self.seconds = fabric, instances, settings.width, seconds
        self.folder = Path(tempfile.mkdtemp(prefix="sparrow-"))
        rots = lambda inst: [r for r in inst.rotations if r in (0, 180)] or [0]
        items = [{"id": i, "demand": 1,
                  "orientation": {"rotation": {"mode": "discrete", "angles": rots(inst)}},
                  "shape": {"type": "simple_polygon",
                            "data": [[round(x, 2), round(y, 2)]
                                     for x, y in list(_to_strip(inst.shape).simplify(0.2).exterior.coords)[:-1]]}}
                 for i, inst in enumerate(instances)]
        (self.folder / "pieces.json").write_text(json.dumps(
            {"name": "pieces", "items": items, "strip_height": settings.width, "min_item_separation": settings.gap}))
        self.started = time.time()
        self.process = subprocess.Popen([str(BINARY), "-i", "pieces.json", "-t", str(int(seconds))],
                                        cwd=self.folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def done(self):
        return self.process.poll() is not None

    def stop(self):
        """Finish early: Sparrow moves on to its last phase, then stops and writes its best layout."""
        for _ in range(2):
            if not self.done():
                self.process.send_signal(signal.SIGINT)
                time.sleep(0.5)

    def latest(self):
        """(length in mm, SVG) of the best layout found so far, or None before the first."""
        shots = [p for p in (self.folder / "output").glob("sols_*/*.svg") if "_nf" not in p.name]
        if not shots:
            return None
        shot = max(shots, key=lambda p: (p.stat().st_mtime, p.name))
        try:
            return float(shot.name.split("_")[1]), shot.read_text()
        except (ValueError, IndexError, OSError):
            return None

    def layout(self):
        """The finished layout, in the app's terms."""
        solution = json.loads((self.folder / "output" / "final_pieces.json").read_text())["solution"]
        placements = []
        for placed in solution["layout"]["placed_items"]:
            inst = self.instances[placed["item_id"]]
            turn, (dx, dy) = placed["transformation"]["rotation"], placed["transformation"]["translation"]
            move = lambda g: _from_strip(affinity.translate(affinity.rotate(_to_strip(g), turn, origin=(0, 0)), dx, dy),
                                         self.width)
            placements.append(Placement(inst, int(abs(turn)) % 360, move(inst.shape),
                                        move(inst.marks) if inst.marks is not None else None,
                                        move(inst.fold_line) if inst.fold_line is not None else None))
        length = solution["strip_width"]
        used = max(p.outline.bounds[2] for p in placements)
        return Layout(self.fabric, placements, length, used, box(0, 0, self.width, length),
                      notes=[f"Packed by Sparrow in {solution['run_time_sec']:.0f} s."])


def start(pieces, settings: FabricSettings, seconds=120):
    """A Sparrow run for each fabric. Cut-on-fold pieces are unfolded; stripes and photo fabric are not supported."""
    if settings.shape is not None or settings.stripe_repeat:
        raise ValueError("Sparrow lays out on a plain rectangle of fabric; use the quick layout for leftover "
                         "fabric from a photo or for stripe matching.")
    runs = []
    for fabric in sorted({p.fabric for p in pieces if p.include}):
        group = [p for p in pieces if p.include and p.fabric == fabric]
        _, single = _instances(group, settings, False)
        runs.append(Run(fabric, single, settings, seconds))
    return runs
