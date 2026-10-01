"""Texture placement: does the colour sit where it belongs?

The synthetic cases are the whole point of this file. A placement metric is
worthless unless it can be shown to distinguish a correctly textured mesh from a
scrambled one, and that has to be proven with known answers rather than
eyeballed on a render.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The CLI deliberately has no numpy, so these run in an engine venv where the
# geometry stack lives. Skipped, never faked: a reimplementation here would pass
# while the real path stayed broken.
np = pytest.importorskip("numpy", reason="numpy lives in an engine venv; run there")

from aurora_cli.core.texture_placement import (
    assess,
    colour_correlation,
    normalise_to_mask,
    placement_error,
)


def disc(size=96, radius=30, colour=(180, 90, 40)):
    """A round foreground on a flat background, with a vertical gradient inside."""
    yy, xx = np.mgrid[0:size, 0:size]
    mask = (yy - size / 2) ** 2 + (xx - size / 2) ** 2 <= radius ** 2
    image = np.zeros((size, size, 3), dtype=np.float32)
    image[mask] = np.array(colour, dtype=np.float32)
    shade = (yy[mask] / size * 90.0).astype(np.float32)
    image[mask, 0] += shade
    return image, mask


class TestNormalisation:
    def test_it_crops_to_the_foreground(self):
        image, mask = disc(size=96, radius=20)
        cropped, small_mask = normalise_to_mask(image, mask, size=64)
        assert cropped.shape == (64, 64, 3)
        assert small_mask.any()
        assert small_mask.mean() > 0.6, "a disc should dominate its own cropped frame"

    def test_an_empty_mask_yields_nothing_rather_than_raising(self):
        image = np.zeros((32, 32, 3), dtype=np.float32)
        empty = np.zeros((32, 32), dtype=bool)
        out, mask = normalise_to_mask(image, empty, size=32)
        assert not mask.any() and out.shape == (32, 32, 3)

    def test_translation_and_scale_do_not_change_the_result(self):
        image, mask = disc()
        small = normalise_to_mask(image, mask, size=64)
        shifted = np.roll(np.roll(image, 7, axis=0), 5, axis=1)
        shifted_mask = np.roll(np.roll(mask, 7, axis=0), 5, axis=1)
        assert colour_correlation(*small, *normalise_to_mask(shifted, shifted_mask, 64)) == 1.0


class TestCorrelation:
    def test_identical_images_correlate_at_one(self):
        image, mask = disc()
        assert colour_correlation(image, mask, image.copy(), mask.copy()) == 1.0

    def test_a_flat_texture_cannot_be_judged(self):
        """A constant render has no structure; that is missing, not disagreeing."""
        image, mask = disc()
        flat = np.zeros_like(image)
        assert colour_correlation(image, mask, flat, mask) is None

    def test_an_inverted_texture_scores_wrong(self):
        image, mask = disc()
        inverted = 255.0 - image
        score = colour_correlation(image, mask, inverted, mask)
        assert score is not None and score < 0.5

    def test_disjoint_coverage_is_not_measured(self):
        image, mask = disc()
        empty = np.zeros_like(mask)
        assert colour_correlation(image, mask, image, empty) is None


class TestPlacementError:
    def test_identical_images_have_no_error(self):
        image, mask = disc()
        assert placement_error(image, mask, image.copy(), mask.copy()) == 0.0

    def test_a_colour_absent_from_the_palette_costs_real_distance(self):
        image, mask = disc()
        alien = np.zeros_like(image)
        alien[mask] = (0, 255, 255)
        error = placement_error(image, mask, alien, mask)
        assert error is not None and error > 0.3


class TestVerdict:
    def test_intentionally_uniform_matching_colours_are_not_missing_texture(self):
        image, mask = disc()
        image[mask] = (180, 90, 40)
        result = assess(image, mask, image.copy(), mask.copy())
        assert result['verdict'] == 'placed'
        assert result['mode'] == 'uniform_colour'
        assert result['spatial_placement_observable'] is False
        assert result['colour_correlation'] is None

    def test_wrong_uniform_colour_is_rejected(self):
        image, mask = disc()
        image[mask] = (180, 90, 40)
        wrong = image.copy()
        wrong[mask] = (10, 10, 240)
        assert assess(image, mask, wrong, mask)['verdict'] == 'misplaced'

    def test_uniform_exception_does_not_hide_lost_patterned_parts(self):
        image, mask = disc()
        reference = image.copy()
        reference[mask] = (180, 90, 40)
        reference[mask & (np.indices(mask.shape)[1] < mask.shape[1] // 3)] = (0, 0, 255)
        render_mask = mask & (np.indices(mask.shape)[1] > mask.shape[1] // 3)
        image[mask] = (180, 90, 40)
        assert assess(reference, mask, image, render_mask)['verdict'] != 'placed'
    def test_a_correctly_placed_texture_is_accepted(self):
        image, mask = disc()
        result = assess(image, mask, image.copy(), mask.copy())
        assert result["verdict"] == "placed"
        assert result["colour_correlation"] == 1.0

    def test_a_scrambled_texture_is_reported_as_misplaced(self):
        """Left half and right half of the palette swapped: structure destroyed."""
        image, mask = disc()
        scrambled = image.copy()
        half = scrambled.shape[1] // 2
        scrambled[:, half:] = image[:, :half]
        scrambled[:, :half] = image[:, half:]
        result = assess(image, mask, scrambled, mask)
        assert result["verdict"] in {"misplaced", "partial"}
        assert (result["colour_correlation"] or 0) < 0.60

    def test_a_uniform_texture_is_not_accepted_as_placed(self):
        """The failure a file-size or structure check would pass."""
        image, mask = disc()
        uniform = np.zeros_like(image)
        uniform[mask] = (120, 120, 120)
        result = assess(image, mask, uniform, mask)
        assert result["verdict"] != "placed"

    def test_thresholds_are_reported_with_every_verdict(self):
        image, mask = disc()
        result = assess(image, mask, image.copy(), mask.copy())
        assert "min_correlation" in result["thresholds"]
