# sewing-optimiser

Lays out sewing pattern pieces on fabric to use as little fabric as possible, and writes a 1:1 layout for projecting onto the fabric.

## Use

List the layers (sizes) in a pattern PDF:

    pixi run layout "examples/Cuff leggings/bt12-BW-projector-pattern.pdf"

Lay out one size on fabric 900 mm wide:

    pixi run layout "examples/Cuff leggings/bt12-BW-projector-pattern.pdf" --size 2-3t --width 900 --out output/cuff-2-3t

This writes `output/cuff-2-3t.svg` and `.pdf` at 1:1 scale, with a 10 cm check square. Options: `--gap` (mm between pieces, default 3), `--one-way` (no 180° turns, for napped or one-way fabric), `--skip TEXT` (leave out pieces whose name contains TEXT, e.g. `--skip "short sleeve"` for the long-sleeve view; repeatable).

## Current limits

- Single-page PDFs (projector or A0) with one layer per size, where each size layer holds complete outlines (Brindille & Twig works; Make by TFS, which draws shared edges on a base layer, does not yet).
- The grainline is taken from the direction of the word "grainline" inside each piece; pieces without it are assumed vertical.
- Pieces labelled "on fold" are mirrored across their fold edge for single-layer cutting. The fold edge is the long straight edge, parallel to the fold label or grainline, closest to the fold label.
- Nesting is greedy bottom-left placement.
