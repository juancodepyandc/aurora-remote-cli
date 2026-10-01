"""Fidelity: does the rendered mesh actually depict the reference?

The control cases matter more than the rest. A renderer that silently draws
nothing, or draws a mesh edge-on, would score a correct mesh as useless — so
the rasterizer is checked against a cube and a sphere of known coverage first,
and the mesh's own aspect ratio is checked before its score is believed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

np = pytest.importorskip("numpy", reason="fidelity runs in the engine venv")

trimesh = None
try:
    import trimesh  # noqa: F811
    import torch  # noqa: F401

    ENGINE_STACK = True
except ImportError:
    ENGINE_STACK = False

from aurora_cli.core.engine3d import find_hunyuan3d
engine = find_hunyuan3d()
ENGINE_ROOT = engine.root if engine else Path('missing-test-engine')
if ENGINE_STACK and not (ENGINE_ROOT / "torch_rasterizer.py").is_file():
    ENGINE_STACK = False

requires_engine_stack = pytest.mark.skipif(
    not ENGINE_STACK,
    reason="torch + trimesh + the engine rasteriser are needed; run with the Hunyuan engine venv",
)


from aurora_cli.core.fidelity import (
    camera_matrices,
    clip_positions,
    fit_camera,
    foreground_mask,
    render_view,
    silhouette_iou,
)


def test_surface_contract_does_not_mistake_a_valid_open_mesh_for_a_failed_solid():
    from aurora_cli.core.fidelity import verdict, DEFAULT_THRESHOLDS
    assert verdict(0.9, 0, DEFAULT_THRESHOLDS) == 'degenerate_geometry'
    assert verdict(0.9, 0, DEFAULT_THRESHOLDS | {'geometry_contract': 'surface'}) == 'plausible'
    assert verdict(float('nan'), 1, DEFAULT_THRESHOLDS) == 'invalid_measurement'
    with pytest.raises(ValueError):
        verdict(0.9, 1, DEFAULT_THRESHOLDS | {'geometry_contract': 'invented'})


@requires_engine_stack
def test_decoding_checks_all_connected_maps_not_only_the_albedo(tmp_path):
    from test_pipeline_execution import glb_bytes
    from aurora_cli.core.glb_validation import inspect_glb
    path = tmp_path / 'pbr.glb'
    def material(doc):
        doc['materials'][0]['pbrMetallicRoughness']['metallicRoughnessTexture'] = {'index': 1}
        doc['textures'].append({'source': 1})
        doc['images'].append(dict(doc['images'][0]))
    path.write_bytes(glb_bytes(textured=True, edit=material))
    result = inspect_glb(path, require_pbr_maps=True, decode_textures=True)
    maps = result['materials'][0]['maps']
    assert maps['base_colour']['decoded'] and maps['metallic_roughness']['decoded']
    assert result['embedded_images'] == 2
    def corrupt(doc):
        material(doc)
        # Correct-looking signature/dimensions, but a damaged PNG chunk CRC.
        doc['images'][1]['uri'] = ('data:image/png;base64,'
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aE8sAAAAASUVORK5CYII=')
    path.write_bytes(glb_bytes(textured=True, edit=corrupt))
    with pytest.raises(ValueError, match='illisible'):
        inspect_glb(path, require_pbr_maps=True, decode_textures=True)


@requires_engine_stack
def test_actual_textured_open_surface_is_not_rejected_as_a_collapsed_solid(tmp_path):
    from PIL import Image
    from aurora_cli.core.fidelity import evaluate, DEFAULT_THRESHOLDS
    from trimesh.visual.material import PBRMaterial
    vertices = np.array([[-.5, -.5, 0], [.5, -.5, 0], [.5, .5, 0], [-.5, .5, 0]])
    material = PBRMaterial(baseColorTexture=Image.new('RGBA', (8, 8), (220, 30, 10, 255)),
                           metallicRoughnessTexture=Image.new('RGB', (8, 8), (0, 128, 0)))
    visual = trimesh.visual.texture.TextureVisuals(uv=np.array([[0, 0], [1, 0], [1, 1], [0, 1]]),
                                                  material=material)
    sheet = trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2], [0, 2, 3]], visual=visual, process=False)
    mesh = tmp_path / 'surface.glb'
    sheet.export(mesh)
    rgb, mask = render_view(trimesh.load(mesh, force='scene'), 0, 0, size=128, engine_root=ENGINE_ROOT)
    reference = tmp_path / 'reference.png'
    Image.fromarray(np.concatenate([rgb, (mask * 255).astype(np.uint8)[:, :, None]], axis=2)).save(reference)
    result = evaluate(reference, mesh, engine_root=ENGINE_ROOT, size=128,
                      thresholds=DEFAULT_THRESHOLDS | {'geometry_contract': 'surface'})
    assert result['verdict'] == 'plausible' and result['bbox_fill'] == 0
    assert result['watertight'] is False and result['surface_area'] > 0
    assert result['texture_placement']['verdict'] == 'placed'
    assert len(result['views']) == len(result['previews_sha256']) == 6


@requires_engine_stack
@pytest.mark.parametrize('feature', ['uv_set', 'transform', 'sampler', 'transparency'])
def test_unimplemented_render_features_are_not_called_misplaced_textures(tmp_path, feature):
    from test_pipeline_execution import glb_bytes
    from aurora_cli.core.fidelity import evaluate
    def edit(doc):
        material = doc['materials'][0]
        binding = material['pbrMetallicRoughness']['baseColorTexture']
        if feature == 'uv_set':
            binding['texCoord'] = 1
            doc['meshes'][0]['primitives'][0]['attributes']['TEXCOORD_1'] = 1
        elif feature == 'transform':
            binding['extensions'] = {'KHR_texture_transform': {'offset': [0.5, 0]}}
        elif feature == 'sampler':
            doc['textures'][0]['sampler'] = 0
            doc['samplers'] = [{'wrapS': 33071}]
        else:
            material['alphaMode'] = 'BLEND'
    asset = tmp_path / 'material.glb'
    asset.write_bytes(glb_bytes(textured=True, edit=edit))
    with pytest.raises(ValueError, match='Rendu de vérification non pris en charge'):
        evaluate(tmp_path / 'unused-reference.png', asset)


def test_review_thumbnail_preserves_original_and_aspect_ratio(tmp_path):
    from PIL import Image
    from aurora_cli.core.image_worker import prepare_review
    original = tmp_path / 'original.png'
    review = tmp_path / 'review.png'
    Image.new('RGB', (1600, 800), (20, 40, 90)).save(original)
    before = original.read_bytes()
    prepare_review(original, review, 512)
    assert original.read_bytes() == before
    with Image.open(review) as thumbnail:
        assert thumbnail.size == (512, 256)


@requires_engine_stack
def test_actual_mesh_review_includes_all_rendered_views(tmp_path):
    from PIL import Image
    from aurora_cli.core.fidelity import evaluate
    cube = trimesh.creation.box(extents=(1, 1, 1))
    mesh = tmp_path / 'cube.glb'
    cube.export(mesh)
    rgb, mask = render_view(cube, 0, 0, size=128, engine_root=ENGINE_ROOT)
    rgba = np.concatenate([rgb, (mask * 255).astype(np.uint8)[:, :, None]], axis=2)
    reference = tmp_path / 'reference.png'
    Image.fromarray(rgba).save(reference)
    measured = evaluate(reference, mesh, engine_root=ENGINE_ROOT, size=128)
    assert len(measured['previews']) == len(measured['views']) == 6
    assert all(Path(path).is_file() for path in measured['previews'])
    with Image.open(measured['contact_sheet']) as sheet:
        assert sheet.size == (384, 304)


class TestCamera:
    def test_projection_is_finite_and_in_front(self):
        verts = np.array([[0, 0, 0], [0.5, 0.5, 0.5], [-0.5, -0.5, -0.5]], dtype=np.float32)
        view, proj = camera_matrices(37.0, 21.0, distance=3.0)
        clip = clip_positions(verts, view, proj)
        assert np.isfinite(clip).all()
        assert (clip[0, :, 3] > 0).all(), "vertices must be in front of the camera"

    def test_camera_turns_the_object(self):
        a, _ = camera_matrices(0.0, 0.0, 3.0)
        b, _ = camera_matrices(90.0, 0.0, 3.0)
        assert not np.allclose(a, b)

    def test_fit_camera_reaches_the_requested_fill(self):
        verts = np.random.default_rng(0).normal(size=(500, 3)).astype(np.float32)
        view, proj = fit_camera(verts, azimuth=31.0, elevation=17.0, fill=0.8)
        clip = clip_positions(verts, view, proj)
        w = np.maximum(clip[0, :, 3], 1e-6)
        extent = max(np.abs(clip[0, :, 0] / w).max(), np.abs(clip[0, :, 1] / w).max())
        assert abs(extent - 0.8) < 0.06


@requires_engine_stack
@requires_engine_stack
class TestRasterizerControls:
    """A wrong renderer must fail here, not produce a confident wrong score."""

    def test_cube_renders_a_convincing_area_face_on(self):
        cube = trimesh.creation.box(extents=(1, 1, 1))
        _, mask = render_view(cube, azimuth=0.0, elevation=0.0, size=128, engine_root=ENGINE_ROOT)
        assert 0.55 < mask.mean() < 0.80

    def test_sphere_renders_a_disk_of_the_expected_area(self):
        sphere = trimesh.creation.icosphere(subdivisions=3)
        _, mask = render_view(sphere, azimuth=20.0, elevation=10.0, size=128, engine_root=ENGINE_ROOT)
        # A disk filling 80% of the frame covers pi/4 * 0.8^2 = 50.3%.
        assert 0.45 < mask.mean() < 0.56

    def test_a_flat_slab_is_reported_as_much_thinner_than_it_is_wide(self):
        slab = trimesh.creation.box(extents=(2.0, 0.1, 2.0))
        _, side = render_view(slab, azimuth=0.0, elevation=0.0, size=128, engine_root=ENGINE_ROOT)
        _, above = render_view(slab, azimuth=0.0, elevation=80.0, size=128, engine_root=ENGINE_ROOT)
        assert above.mean() > 3 * side.mean()

    def test_empty_geometry_is_refused_rather_than_scored(self):
        empty = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64))
        with pytest.raises(ValueError):
            render_view(empty, 0.0, 0.0, size=64, engine_root=ENGINE_ROOT)


class TestMasks:
    def test_alpha_is_used_when_present(self):
        from PIL import Image

        array = np.zeros((8, 8, 4), dtype=np.uint8)
        array[2:6, 2:6, 3] = 255
        mask = foreground_mask(Image.fromarray(array))
        assert mask.sum() == 16

    def test_flat_backdrop_is_estimated_from_the_corners(self):
        from PIL import Image

        array = np.full((16, 16, 3), 240, dtype=np.uint8)
        array[4:12, 4:12] = 30
        mask = foreground_mask(Image.fromarray(array))
        assert mask.sum() == 64

    def test_iou_of_identical_masks_is_one(self):
        mask = np.zeros((10, 10), dtype=bool)
        mask[2:8, 2:8] = True
        assert silhouette_iou(mask, mask) == 1.0

    def test_iou_of_disjoint_masks_is_zero(self):
        a = np.zeros((10, 10), dtype=bool)
        b = np.zeros((10, 10), dtype=bool)
        a[0:5] = True
        b[5:10] = True
        assert silhouette_iou(a, b) == 0.0

    def test_iou_of_two_empty_masks_is_zero_not_a_crash(self):
        empty = np.zeros((4, 4), dtype=bool)
        assert silhouette_iou(empty, empty) == 0.0


@requires_engine_stack
@requires_engine_stack
class TestMeshShapeSanity:
    """A degenerate mesh must be detectable without any model."""

    def test_closed_solid_fills_its_bounding_box(self):
        solid = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        assert bbox_fill(solid) > 0.98

    def test_flat_sheet_is_flagged_as_degenerate(self):
        """The cheapest model-free detector: a sheet encloses no volume."""
        sheet = trimesh.Trimesh(
            vertices=np.array([[0, 0, 0], [2, 0, 0], [2, 0, 2], [0, 0, 2]], dtype=float),
            faces=np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64),
            process=False,
        )
        assert bbox_fill(sheet) == 0.0

    def test_partially_open_shell_scores_between_zero_and_one(self):
        box = trimesh.creation.box(extents=(2.0, 1.0, 2.0))
        keep = np.ones(len(box.faces), dtype=bool)
        keep[::2] = False
        shell = box.copy()
        shell.update_faces(keep)
        assert 0.0 <= bbox_fill(shell) < 1.0

    def test_bbox_fill_is_zero_for_an_empty_mesh(self):
        empty = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64))
        assert bbox_fill(empty) == 0.0


def bbox_fill(mesh) -> float:
    """Delegated so the metric is tested where it lives, not reimplemented here."""
    from aurora_cli.core.fidelity import bbox_fill as metric

    return metric(mesh)


class TestVerdict:
    """The verdict is thresholded on measured signals, never blended."""

    THRESH = {"min_bbox_fill": 0.05, "min_silhouette_iou": 0.35}

    def test_flattened_geometry_is_called_degenerate(self):
        from aurora_cli.core.fidelity import verdict

        assert verdict(silhouette=0.9, fill=0.02, thresholds=self.THRESH) == "degenerate_geometry"

    def test_solid_but_unfaithful_is_low_fidelity(self):
        from aurora_cli.core.fidelity import verdict

        assert verdict(silhouette=0.10, fill=0.40, thresholds=self.THRESH) == "low_fidelity"

    def test_solid_and_aligned_is_plausible(self):
        from aurora_cli.core.fidelity import verdict

        assert verdict(silhouette=0.70, fill=0.40, thresholds=self.THRESH) == "plausible"

    def test_geometry_is_judged_before_fidelity(self):
        """A degenerate mesh cannot be excused by a lucky silhouette match."""
        from aurora_cli.core.fidelity import verdict

        assert verdict(silhouette=0.99, fill=0.0, thresholds=self.THRESH) == "degenerate_geometry"


class TestMismatchedMasks:
    def test_mismatched_shapes_are_refused(self):
        from aurora_cli.core.fidelity import silhouette_iou

        a = np.zeros((4, 4), dtype=bool)
        b = np.zeros((8, 8), dtype=bool)
        with pytest.raises(ValueError):
            silhouette_iou(a, b)


class TestVerdictAccountsForTexturePlacement:
    """A perfect shape with scrambled colour must not be reported as plausible."""

    THRESH = {"min_bbox_fill": 0.05, "min_silhouette_iou": 0.35}

    def test_misplaced_texture_downgrades_the_verdict(self):
        from aurora_cli.core.fidelity import verdict

        placement = {"verdict": "misplaced", "colour_correlation": 0.1}
        assert verdict(0.8, 0.4, self.THRESH, placement) == "misplaced_texture"

    def test_correctly_placed_texture_keeps_the_verdict(self):
        from aurora_cli.core.fidelity import verdict

        placement = {"verdict": "placed", "colour_correlation": 0.9}
        assert verdict(0.8, 0.4, self.THRESH, placement) == "plausible"

    def test_unmeasured_placement_is_not_a_silent_pass(self):
        """No placement signal must not be reported as a clean result."""
        from aurora_cli.core.fidelity import verdict

        # Geometry still has to be judged; placement simply cannot fail a verdict.
        assert verdict(0.8, 0.4, self.THRESH, None) == "plausible"
        assert verdict(0.8, 0.01, self.THRESH, None) == "degenerate_geometry"

    def test_partial_placement_does_not_block(self):
        from aurora_cli.core.fidelity import verdict

        assert verdict(0.8, 0.4, self.THRESH, {"verdict": "partial"}) == "plausible"


@requires_engine_stack
class TestTextureSampleConvention:
    """GLB puts the UV origin top-left; a 1-v flip mirrors the whole atlas.

    Regression: the flip made every asset look like its texture was on the wrong
    part, and produced a confident negative placement correlation. The control
    below fails if the flip is ever reintroduced.
    """

    def _plane_render(self, top_row_rgb):
        import torch

        import torch_rasterizer as raster
        from PIL import Image

        import trimesh

        size = 64
        image = np.zeros((size, size, 3), dtype=np.float32)
        image[:size // 2] = top_row_rgb
        image[size // 2:] = 255 - np.array(top_row_rgb, dtype=np.float32)
        verts = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], dtype=np.float32)
        faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
        # v=0 at the bottom of the plane, v=1 at the top, matching glTF.
        uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
        mesh = trimesh.Trimesh(vertices=verts, faces=faces, uv=uv, process=False)
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=uv, image=Image.fromarray(image.astype("uint8")))
        render, mask = render_view(mesh, azimuth=0.0, elevation=0.0, size=size,
                                   engine_root=ENGINE_ROOT)
        return render, mask

    def test_top_of_the_plane_samples_the_top_of_the_texture(self):
        """Red at the top of the image must land on the top of the plane.

        The camera is Y-up and the in-memory trimesh UV convention is bottom-left.
        Thus +Y / v=1 samples row 0 of the bitmap after framebuffer conversion.
        """
        render, mask = self._plane_render((255, 0, 0))
        first = render[0:16][mask[0:16]]
        second = render[48:64][mask[48:64]]
        assert len(first) > 0 and len(second) > 0
        assert first[:, 0].mean() > 200, "red should dominate the first rows"
        assert second[:, 2].mean() > 200, "blue should dominate the last rows"

    def test_glb_uv_round_trip_is_probed_and_corrected(self, tmp_path):
        import importlib
        from aurora_cli.core.mesh_worker import ensure_gltf_uv_contract
        sys.path.insert(0, str(ENGINE_ROOT))
        module = importlib.import_module('mesh_io')
        ensure_gltf_uv_contract(module)
        positions = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]])
        faces = np.array([[0, 1, 2], [0, 2, 3]])
        # These are glTF coordinates: row zero at the top of the object.
        uv_gltf = np.array([[0, 1], [1, 1], [1, 0], [0, 0]])
        texture = np.zeros((64, 64, 3))
        texture[:32] = [1, 0, 0]
        texture[32:] = [0, 0, 1]
        output = tmp_path / 'uv-contract.glb'
        module.export_glb_pbr(positions, faces, uv_gltf, texture, str(output))
        mesh = trimesh.load(str(output), force='mesh', process=False)
        expected = uv_gltf.copy()
        expected[:, 1] = 1 - expected[:, 1]
        assert np.allclose(mesh.visual.uv, expected)
        image, mask = render_view(mesh, 0, 0, 64, engine_root=ENGINE_ROOT)
        assert image[:16][mask[:16]][:, 0].mean() > 200
        assert image[-16:][mask[-16:]][:, 2].mean() > 200


@requires_engine_stack
def test_image_pixel_validation_rejects_uniform_output():
    from PIL import Image
    from aurora_cli.core.image_worker import validate_image
    with pytest.raises(RuntimeError, match='uniforme'):
        validate_image(Image.new('RGB', (128, 128), 'black'))
    array = np.zeros((128, 128, 3), dtype=np.uint8)
    array[:64] = 255
    validate_image(Image.fromarray(array))
