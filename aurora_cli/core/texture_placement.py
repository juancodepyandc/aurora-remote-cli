"""Is the texture on the right part of the mesh?

The existing checks cannot answer this. `validate_glb` sees structure. Aurora's
`color_richness` only looks at vertex colours, as its own comment admits. And a
diffusion bake at 256 px per view produces a plausible-looking atlas whether or
not the colours landed in the right place — which is exactly the failure reported.

The signal here is deliberately geometric rather than semantic. Both the
reference and a render of the mesh are reduced to their foreground, normalised
into a common frame by their bounding boxes, and compared where they overlap:

* `colour_correlation` — how well the three channels co-vary between the
  reference and the render over the shared area. High means the palette varies
  in the same places in both, which is what "correctly placed" means.
* `placement_error` — mean distance, in units of the shared frame, from each
  rendered pixel's colour to the nearest reference colour. High means the render
  is painting a colour that does not belong there.

Neither needs a model, so neither can fail because an encoder is misconfigured,
and both are testable against synthetic cases with known answers.
"""
from __future__ import annotations

import numpy as np

# Colour channels compared, in RGB order.
CHANNELS = 3


def normalise_to_mask(rgb: np.ndarray, mask: np.ndarray, size: int = 128):
    """Crop to the foreground bounding box and resample to a square grid.

    Normalising both images by their own foreground is what makes the comparison
    independent of translation and overall scale. It does not establish
    invariance to pose, camera rotation or changes in lighting.
    """
    from PIL import Image

    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return np.zeros((size, size, CHANNELS), dtype=np.float32), np.zeros((size, size), dtype=bool)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    cropped = rgb[y0:y1, x0:x1]
    cropped_mask = mask[y0:y1, x0:x1]
    resized = Image.fromarray(cropped.astype(np.uint8)).resize((size, size), Image.NEAREST)
    resized_mask = Image.fromarray((cropped_mask * 255).astype(np.uint8)).resize((size, size), Image.NEAREST)
    return (np.asarray(resized, dtype=np.float32),
            np.asarray(resized_mask) > 127)


def colour_correlation(reference: np.ndarray, ref_mask: np.ndarray,
                       render: np.ndarray, render_mask: np.ndarray) -> float | None:
    """Mean per-channel correlation between the two images over shared pixels.

    Flat channels are skipped rather than fatal: a subject can easily be uniform
    in one channel, and a single flat channel says nothing about whether the
    texture is placed correctly. Returns None only when no channel on either side
    carries any structure, because then there is nothing to correlate, and
    reporting 0 would read as a measured disagreement instead of a missing
    measurement.
    """
    shared = ref_mask & render_mask
    if shared.sum() < 64:
        return None
    left = reference[shared].reshape(-1, CHANNELS)
    right = render[shared].reshape(-1, CHANNELS)
    scores = []
    for channel in range(CHANNELS):
        a, b = left[:, channel].astype(np.float64), right[:, channel].astype(np.float64)
        if a.std() < 1e-6 or b.std() < 1e-6:
            continue
        scores.append(float(np.corrcoef(a, b)[0, 1]))
    if not scores:
        return None
    return round(float(np.mean(scores)), 4)


def placement_error(reference: np.ndarray, ref_mask: np.ndarray,
                    render: np.ndarray, render_mask: np.ndarray,
                    sample: int = 4000) -> float | None:
    """Mean distance from each sampled rendered colour to the nearest reference colour.

    Normalised to the reference's own colour spread, so the number means
    "fraction of the palette that had to be invented here". Returns None when
    either side is flat or has too little area to judge.
    """
    shared = ref_mask & render_mask
    if shared.sum() < 64:
        return None
    ref_palette = reference[ref_mask]
    if len(ref_palette) < 16:
        return None
    spread = float(np.linalg.norm(ref_palette.max(axis=0) - ref_palette.min(axis=0)))
    if spread < 1e-6:
        return None
    rendered = render[shared].reshape(-1, CHANNELS)
    if len(rendered) > sample:
        step = len(rendered) // sample + 1
        rendered = rendered[::step]
    # Nearest-neighbour distance to the reference palette, in chunks so a large
    # render does not need a full pairwise matrix.
    best = np.full(len(rendered), np.inf, dtype=np.float32)
    chunk = max(1, 2_000_000 // max(1, len(ref_palette)))
    for start in range(0, len(ref_palette), chunk):
        block = ref_palette[start:start + chunk]
        distances = np.linalg.norm(
            rendered[:, None, :] - block[None, :, :], axis=2)
        best = np.minimum(best, distances.min(axis=1))
    return round(float(best.mean()) / spread, 4)


def assess(reference: np.ndarray, ref_mask: np.ndarray,
           render: np.ndarray, render_mask: np.ndarray, size: int = 128, *,
           min_correlation: float = 0.60, max_placement_error: float = 0.35,
           misplaced_correlation: float = 0.20,
           max_uniform_channel_std: float = 1.0,
           max_uniform_colour_error: float = 0.05) -> dict:
    """Both placement signals plus a thresholded verdict.

    Thresholds are arguments rather than module constants so a caller can state
    what counts as acceptable instead of inheriting it.
    """
    ref_img, ref_small = normalise_to_mask(reference, ref_mask, size)
    ren_img, ren_small = normalise_to_mask(render, render_mask, size)
    correlation = colour_correlation(ref_img, ref_small, ren_img, ren_small)
    error = placement_error(ref_img, ref_small, ren_img, ren_small)
    verdict = "unknown"
    mode, uniform_error = 'spatial_correlation', None
    if correlation is not None and error is not None:
        if correlation >= min_correlation and error <= max_placement_error:
            verdict = "placed"
        elif correlation < misplaced_correlation:
            verdict = "misplaced"
        else:
            verdict = "partial"
    elif correlation is None:
        shared = ref_small & ren_small
        left, right = ref_img[ref_small], ren_img[ren_small]
        # Uniform colours have no observable placement to correlate. Accept
        # only when BOTH complete foregrounds are uniform and colours agree;
        # a patterned reference reduced to grey still fails closed.
        if (shared.sum() >= 64 and len(left) >= 64 and len(right) >= 64
                and np.max(left.std(axis=0)) <= max_uniform_channel_std
                and np.max(right.std(axis=0)) <= max_uniform_channel_std):
            mode = 'uniform_colour'
            uniform_error = round(float(np.max(np.abs(left.mean(axis=0) - right.mean(axis=0)))) / 255, 4)
            verdict = 'placed' if uniform_error <= max_uniform_colour_error else 'misplaced'
    return {
        "verdict": verdict,
        "mode": mode,
        "uniform_colour_error": uniform_error,
        "spatial_placement_observable": correlation is not None,
        "colour_correlation": correlation,
        "placement_error": error,
        "thresholds": {"min_correlation": min_correlation,
                       "max_placement_error": max_placement_error,
                       "misplaced_correlation": misplaced_correlation,
                       "max_uniform_channel_std": max_uniform_channel_std,
                       "max_uniform_colour_error": max_uniform_colour_error},
    }
