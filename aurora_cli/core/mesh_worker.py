"""Isolated Hunyuan adapter; the CLI process never imports GPU libraries."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


def bake_tiers() -> list[dict]:
    """Candidate bake tiers, smallest first, read from policy.

    Declared as data rather than inferred from whatever presets the engine
    happens to ship: the ladder is a quality decision, not a property of one
    machine or one model.
    """
    from aurora_cli.evolution import load_policy

    tiers = load_policy().get('bake', {}).get('tiers')
    if not tiers:
        return []
    return sorted(
        ({'render_size': int(t['render_size']), 'texture_size': int(t['texture_size'])}
         for t in tiers),
        key=lambda t: t['render_size'],
    )


def bake_estimate_bytes(render_size: int, texture_size: int) -> int:
    """Working set for one bake: the render targets plus the full PBR atlas.

    Render buffers are float32 RGBA per view; the atlas holds albedo, metallic and
    roughness, each sampled at full resolution, which is what made the delivered
    maps half the size of what the pipeline generated.
    """
    render = (render_size ** 2) * 4 * 2  # albedo + metallic-roughness targets
    atlas = (texture_size ** 2) * 3 * 4  # three maps, float32 during the bake
    return int(render + atlas)


def pick_bake_tier(budget_bytes: int, ceiling_fraction: float = 0.35) -> dict | None:
    """Highest declared tier that fits the measured budget, or None.

    General by construction: it depends only on the tier ladder and the memory
    this machine actually has. It never references a particular subject, model or
    example, so the same rule gives a lower tier on a smaller machine instead of
    silently overcommitting and dying at bake time.
    """
    budget = int(budget_bytes * ceiling_fraction)
    chosen = None
    for tier in bake_tiers():
        if bake_estimate_bytes(tier['render_size'], tier['texture_size']) <= budget:
            chosen = tier
    return chosen


def _placement_setting(key):
    from aurora_cli.evolution import load_policy

    value = load_policy().get('bake', {}).get(key)
    return int(value) if isinstance(value, int) and value > 0 else None


def paint_overrides(presets, quality, *, backend=None, backend_module=None):
    """Raise bake resolution without touching the attention budget.

    The `safe` preset runs at 6 views / 256 px and bakes at render 1024 /
    atlas 2048, which delivered a 1024x1024 albedo — half of what upstream
    Hunyuan3D-2.1 uses. The view count and diffusion resolution are what the
    attention preflight bounds; `render_size` and `texture_size` only size the
    rasteriser and the atlas, so raising them to the upstream tier costs no
    attention memory at all.

    Measured on this machine (MPS, 17.8 GiB pool): 1024/2048 took 59 s and
    produced a 175 KB 1024x1024 atlas; 2048/4096 took 65 s and produced a
    563 KB 2048x2048 atlas. Same view count, same resolution, +10 % time.

    So the high tier is the default. `views`/`resolution` are deliberately left
    alone: raising those is what the preflight refuses, and the honest fix for
    a machine with more memory is a bigger pool, not a smaller safety limit.
    """
    if quality == 'preset':
        return {}
    budget = 0
    try:
        if backend_module is None:
            import backend as backend_module
        budget = backend_module.gpu_memory_gb(backend or backend_module.detect()) * (1 << 30)
    except Exception:
        budget = 0
    if not budget:
        return {}
    chosen = pick_bake_tier(int(budget)) or {}
    # bake_exp controls how sharply view contributions are weighted when baking.
    # Upstream pins it at 4; a low value blends neighbouring views together,
    # which shows up as colour dragged across UV islands. Declared in policy so it
    # is a tunable placement lever rather than a constant.
    exp = _placement_setting('bake_exp')
    if exp:
        chosen['bake_exp'] = exp
    return chosen


def shape_identity(image_bytes: bytes, model: str, settings: dict) -> str:
    """Cache key for a generated mesh: the input, the model and every setting.

    Deliberately not just the input. Keying on the image alone meant raising the
    octree resolution or the step count silently returned the previously cached
    mesh, so a quality change appeared to do nothing.
    """
    payload = model + json.dumps(settings, sort_keys=True)
    return hashlib.sha256(image_bytes + payload.encode()).hexdigest()


def ensure_gltf_uv_contract(mesh_io_module) -> bool:
    """Probe the installed writer; correct a double V conversion only if seen.

    The port's exporter accepts glTF UVs, but trimesh expects bottom-left UVs
    and performs its own glTF conversion. Check an actual GLB round trip instead
    of assuming every future engine/library version contains the same bug.
    """
    import numpy as np
    import tempfile
    import trimesh
    original = mesh_io_module.export_glb_pbr
    if getattr(original, '_jobia_uv_verified', False):
        return False
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.array([[0, 1, 2]])
    uvs = np.array([[0.2, 0.1], [0.8, 0.2], [0.3, 0.9]])
    expected = uvs.copy()
    expected[:, 1] = 1 - expected[:, 1]
    with tempfile.TemporaryDirectory(prefix='jobia-uv-probe-') as directory:
        output = Path(directory) / 'probe.glb'
        original(positions, faces, uvs, np.full((4, 4, 3), 0.5), str(output))
        actual = trimesh.load(str(output), force='mesh', process=False).visual.uv
    if np.allclose(actual, expected):
        return False
    if not np.allclose(actual, uvs):
        raise RuntimeError('Convention UV du moteur non vérifiée ; texturation non livrée.')

    def corrected(vtx_pos, pos_idx, vtx_uv, albedo, out_path, **kwargs):
        native = np.asarray(vtx_uv, dtype=float).copy()
        native[:, 1] = 1 - native[:, 1]
        return original(vtx_pos, pos_idx, native, albedo, out_path, **kwargs)
    corrected._jobia_uv_verified = True
    mesh_io_module.export_glb_pbr = corrected
    print('[paint] conversion UV double détectée et corrigée par sonde GLB', flush=True)
    return True


def shape_paths(output: Path) -> tuple[Path, Path]:
    """Geometry and state file for one output, never shared between outputs.

    `Path.with_name('shape.glb')` replaces the stem rather than extending it, so
    two jobs in one directory would overwrite and then reuse each other's mesh.
    """
    return (output.with_name(output.stem + '.shape.glb'),
            output.with_name(output.stem + '.shape.state.json'))


def attempt_ladder(octree: int, steps: int, dtype_name: str) -> list[dict]:
    """Settings to try in order when a reconstruction comes out degenerate.

    A flattened reconstruction is the failure mode this guards: the shape model
    returns a plate instead of an object, every structural check still passes,
    and the result looks fine until someone opens it. Each rung changes one thing
    that plausibly contributes — resolution, sampling, numeric precision, then a
    different seed — so a successful retry identifies what mattered.

    Ordered cheapest-first. A rung is only reached when the previous one produced
    a mesh below the degeneracy threshold, never speculatively.
    """
    ladder = [
        {'octree_resolution': octree, 'shape_steps': steps, 'dtype': dtype_name, 'seed': 0},
        {'octree_resolution': 512, 'shape_steps': 50, 'dtype': dtype_name, 'seed': 0},
        {'octree_resolution': octree, 'shape_steps': 50, 'dtype': 'float32', 'seed': 0},
        {'octree_resolution': 512, 'shape_steps': 50, 'dtype': 'float32', 'seed': 1},
    ]
    seen, unique = set(), []
    for rung in ladder:
        key = tuple(sorted(rung.items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(rung)
    return unique


def bbox_fill(path: Path) -> float:
    """Volume over bounding box: a plate scores near zero, a solid near one."""
    import trimesh

    mesh = trimesh.load(str(path), force='scene')
    geometry = next(iter(mesh.geometry.values()))
    extent = __import__('numpy').asarray(geometry.bounds, dtype=float)
    box = float(__import__('numpy').prod(__import__('numpy').maximum(extent[1] - extent[0], 0.0)))
    return float(geometry.volume) / box if box else 0.0


def generate_shape(image, model, device, dtype_name, args, output: Path, *,
                   pipeline_factory=None, torch_module=None) -> dict:
    """Generate until the mesh is not degenerate, or every rung has been tried.

    Returns the chosen settings plus the measured fill, so the decision is
    recorded rather than invisible. Raises when nothing worked: a plate is not a
    deliverable and must not be silently shipped.

    The pipeline factory and torch module are injectable so the retry policy can
    be tested without a 6 GB model.
    """
    import gc as _gc

    if torch_module is None:
        import torch as torch_module
    if pipeline_factory is None:
        from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline as pipeline_factory
    torch = torch_module

    ladder = attempt_ladder(args.octree_resolution, args.shape_steps, dtype_name)
    tried = []
    latent_cache = {}
    for index, rung in enumerate(ladder):
        torch_dtype = {'float16': torch.float16, 'float32': torch.float32}[rung['dtype']]
        pipe = pipeline_factory.from_pretrained(
            model, subfolder='hunyuan3d-dit-v2-1', use_safetensors=False,
            variant='fp16', device=device, dtype=torch_dtype)
        print(f"[shape] précision effective de chargement={torch_dtype}", flush=True)
        with torch.inference_mode():
            options = dict(image=image, num_inference_steps=rung['shape_steps'],
                           guidance_scale=args.guidance_scale,
                           octree_resolution=rung['octree_resolution'],
                           generator=torch.Generator(device='cpu').manual_seed(rung['seed']))
            if callable(getattr(pipe, '_export', None)):
                # Sampling and surface decoding have different precision and
                # memory requirements. Free the large denoiser before upcasting
                # the VAE; casting the entire pipeline can exhaust a GPU pool.
                latent_key = (rung['dtype'], rung['shape_steps'], rung['seed'])
                latents = latent_cache.get(latent_key)
                latent_path = output.with_name(output.stem + '.latents.pt')
                latent_state = latent_path.with_suffix('.json')
                sampling_signature = shape_identity(image.tobytes(), model, {
                    'dtype': rung['dtype'], 'steps': rung['shape_steps'], 'seed': rung['seed'],
                    'guidance': args.guidance_scale, 'sampling_contract': 1,
                })
                if latents is None and latent_path.is_file() and latent_state.is_file():
                    cached = json.loads(latent_state.read_text(encoding='utf-8'))
                    if cached.get('signature') == sampling_signature and cached.get('sha256') == hashlib.sha256(latent_path.read_bytes()).hexdigest():
                        latents = torch.load(latent_path, map_location='cpu', weights_only=True)
                        if not bool(torch.isfinite(latents).all()):
                            raise RuntimeError('Cache de latents non fini ; aucune reconstruction livrée.')
                        print('[shape] reprise des latents vérifiés', flush=True)
                if latents is None:
                    latents = pipe(**options, output_type='latent')
                    if not bool(torch.isfinite(latents).all()):
                        raise RuntimeError('Échantillonnage 3D non fini : NaN/Inf dans les latents.')
                    latents = latents.detach().cpu()
                    latent_cache[latent_key] = latents
                    temporary = latent_path.with_suffix('.pt.tmp')
                    torch.save(latents, temporary)
                    temporary.replace(latent_path)
                    latent_state.write_text(json.dumps({'signature': sampling_signature,
                        'sha256': hashlib.sha256(latent_path.read_bytes()).hexdigest()}), encoding='utf-8')
                for component in ('model', 'conditioner'):
                    if hasattr(pipe, component):
                        delattr(pipe, component)
                _gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                else:
                    getattr(getattr(torch, 'mps', None), 'empty_cache', lambda: None)()
                from aurora_cli.evolution import load_policy
                decoder_name = load_policy().get('shape', {}).get('decoder_dtype', 'float32')
                decoder_dtype = getattr(torch, decoder_name)
                shape_policy = load_policy().get('shape', {})
                decoder_device = os.environ.get('JOBIA_SHAPE_DECODER_DEVICE') or shape_policy.get('decoder_device', 'same')
                decoder_device = device if decoder_device == 'same' else decoder_device
                if shape_policy.get('hierarchical_decode', True):
                    from aurora_cli.core.surface_decoder import SurfaceDecoder
                    pipe.vae.volume_decoder = SurfaceDecoder(
                        min_resolution=int(shape_policy.get('min_resolution', 96)),
                        band=float(shape_policy.get('surface_band', 0.95)),
                        chunk_size=int(shape_policy.get('query_chunk_size', 8192)))
                pipe.vae.to(device=decoder_device, dtype=decoder_dtype)
                latents = latents.to(device=decoder_device, dtype=decoder_dtype)
                print(f'[shape] décodage géométrique={decoder_dtype}/{decoder_device}, débruiteur libéré', flush=True)
                mesh = pipe._export(latents, output_type='trimesh',
                                    octree_resolution=rung['octree_resolution'])[0]
                del latents
                rung = dict(rung, decoder_dtype=decoder_name, decoder_device=str(decoder_device))
            else:
                mesh = pipe(**options)[0]
        mesh.export(str(output))
        del pipe, mesh
        _gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        else:
            getattr(getattr(torch, 'mps', None), 'empty_cache', lambda: None)()
        fill = bbox_fill(output)
        tried.append({'rung': rung, 'bbox_fill': round(fill, 4)})
        print(f'[shape] essai {index + 1}/{len(ladder)} {rung} -> bbox_fill {fill:.4f}',
              flush=True)
        if fill >= args.min_bbox_fill:
            return {'settings': rung, 'bbox_fill': round(fill, 4), 'attempts': tried}
    raise RuntimeError(
        'reconstruction non validée : chaque essai reste sous le seuil volumétrique '
        f'configuré (bbox_fill < {args.min_bbox_fill}). essais={tried}. '
        'Ce score seul ne prouve ni une plaque ni une géométrie utile ; les rendus '
        'doivent établir la fidélité. Aucun mesh final n’est livré.'
    )


def rescue(mesh_path: Path, reference: Path, output_dir: Path, stem: str) -> Path:
    """Run Aurora's score -> repair -> score chain and return the best mesh.

    Returns the input unchanged when the rescue cannot run, so a missing optional
    dependency never costs the caller an already-good mesh.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from aurora_cli.mesh.auto_rescue_mesh import auto_rescue
    except Exception as exc:
        print(f'[rescue] indisponible ({exc}) — livraison du mesh brut', flush=True)
        return mesh_path

    result = auto_rescue(mesh_path, reference, prompt='', output_dir=output_dir)
    if not result.get('ok'):
        print(f"[rescue] refusé : {result.get('error')} — mesh inchangé", flush=True)
        return mesh_path
    print(f"[rescue] score {result.get('initial_score')} -> {result.get('final_score')} "
          f"(delta {result.get('score_delta')}), axes en échec : "
          f"{result.get('final_failed_axes') or 'aucune'}", flush=True)
    final = Path(result.get('final_mesh') or '')
    if final.is_file():
        audit = output_dir / f'{stem}.rescue.json'
        audit.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f'[rescue] journal {audit}', flush=True)
        return final
    return mesh_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--octree-resolution', type=int, default=384,
                        help="occupancy grid resolution; 384 is upstream's quality "
                             "setting, 256 trades detail for speed")
    parser.add_argument('--shape-steps', type=int, default=50,
                        help="diffusion steps; upstream's default is 50")
    parser.add_argument('--guidance-scale', type=float, default=5.0)
    parser.add_argument('--min-bbox-fill', type=float, default=0.05,
                        help="below this the mesh is a flattened plate, not an "
                             "object; generation is retried instead of shipped")
    parser.add_argument('--rescue', action='store_true',
                        help="run Aurora's score/repair chain on the result")
    parser.add_argument('--paint', action='store_true')
    parser.add_argument('--paint-quality', choices=('high', 'preset'), default='high',
                        help="high raises the bake to the upstream atlas tier; "
                             "preset keeps the memory preset as-is")
    parser.add_argument('--half-res-maps', action='store_true',
                        help="let the writer halve the PBR maps (halves memory, "
                             "throws away half the generated texture detail)")
    args = parser.parse_args()
    # The isolated engine does not have the CLI package installed, and chdir
    # below moves away from the workspace. Resolve shared policies explicitly.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
    os.environ.setdefault('PYTORCH_MPS_HIGH_WATERMARK_RATIO', '1.0')
    # torch ships default_low_watermark_ratio=1.4 but validates the ratio as <= 1.0,
    # so the first sizeable MPS buffer raises "invalid low watermark ratio 1.4".
    os.environ.setdefault('PYTORCH_MPS_LOW_WATERMARK_RATIO', '1.0')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    sys.path[:0] = [str(args.root), str(args.repo), str(args.repo / 'hy3dshape')]
    os.chdir(args.repo)
    import torch
    from PIL import Image

    portable = (args.root / 'backend.py').is_file() and (args.root / 'compat_patches.py').is_file()
    backend = None
    if portable:
        import backend as backend_mod
        import compat_patches
        backend = backend_mod.detect(force_kind=os.environ.get('JOBIA_DEVICE') or None)
        compat_patches.apply(backend)
        device, dtype = backend.device, backend.dtype
    else:
        device = 'cuda' if torch.cuda.is_available() else ('mps' if torch.backends.mps.is_available() else 'cpu')
        dtype = torch.float16 if device == 'cuda' else torch.float32
        if device != 'cuda':
            raise RuntimeError('Ce dépôt Hunyuan nécessite un adaptateur CPU/Metal ; runtime portable requis.')
    from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
    if portable and hasattr(compat_patches, 'patch_scheduler_timestep_lookup'):
        compat_patches.patch_scheduler_timestep_lookup()

    weights = args.root / 'weights' / 'Hunyuan3D-2.1'
    model = str(weights) if weights.is_dir() and args.model.lower() == 'tencent/hunyuan3d-2.1' else args.model
    from PIL import ImageOps
    with Image.open(args.image) as original:
        image = ImageOps.exif_transpose(original).convert('RGBA')
    if image.getextrema()[3] == (255, 255):
        from hy3dshape.rembg import BackgroundRemover
        image = BackgroundRemover()(image.convert('RGB'))
    image.save(args.output.with_name(args.output.stem + '.input-rgba.png'))
    settings = {'model': model, 'octree': args.octree_resolution,
                'steps': args.shape_steps, 'guidance': args.guidance_scale,
                'dtype': str(dtype), 'device': str(device), 'torch_version': torch.__version__,
                'min_bbox_fill': args.min_bbox_fill,
                'worker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'decoder_sha256': hashlib.sha256(Path(__file__).with_name('surface_decoder.py').read_bytes()).hexdigest()}
    from aurora_cli.evolution import load_policy
    settings['decoder_policy'] = load_policy().get('shape', {})
    settings['decoder_override'] = os.environ.get('JOBIA_SHAPE_DECODER_DEVICE', '')
    signature = shape_identity(args.image.read_bytes(), model, settings)
    shape_path, shape_state = shape_paths(args.output)
    try:
        previous = json.loads(shape_state.read_text())
    except (OSError, ValueError):
        previous = {}
    current_hash = hashlib.sha256(shape_path.read_bytes()).hexdigest() if shape_path.is_file() else ''
    if previous.get('signature') != signature or previous.get('sha256') != current_hash or not current_hash:
        print(f'[shape] modèle={model}, device={device}, dtype={dtype}', flush=True)
        print(f'[shape] budget degeneracy={args.min_bbox_fill}, '
              f'octree={args.octree_resolution}, steps={args.shape_steps}, '
              f'guidance={args.guidance_scale}', flush=True)
        result = generate_shape(image, model, device, str(dtype).rsplit('.', 1)[-1],
                                args, shape_path)
        shape_state.write_text(json.dumps(
            {'signature': signature, 'settings': result['settings'],
             'bbox_fill': result['bbox_fill'],
             'sha256': hashlib.sha256(shape_path.read_bytes()).hexdigest()}))
        if backend:
            backend_mod.empty_cache(backend)
    else:
        previous_settings = previous.get('settings')
        print(f'[shape] reprise de la géométrie conservée ({previous_settings})', flush=True)
    if args.paint:
        if not portable:
            raise RuntimeError('Adaptateur de texturation portable requis pour une livraison PBR contrôlée.')
        import paint
        import mesh_io as mesh_io_mod
        ensure_gltf_uv_contract(mesh_io_mod)
        if not args.half_res_maps:
            # Upstream hardcodes `save_mesh(path, downsample=True)`, and the port
            # honours it by halving albedo/metallic/roughness/normal before
            # writing. That throws away exactly the detail the higher bake tier
            # just paid for: texture_size 4096 was delivered as a 2048 atlas.
            # Wrapped here rather than forked, so a reinstall of the engine
            # cannot silently reintroduce it.
            original = mesh_io_mod.save_textured_mesh

            def full_resolution(render, obj_path, glb_path=None, downsample=True, **kwargs):
                if downsample:
                    print('[paint] atlas pleine résolution (maps non divisées)', flush=True)
                return original(render, obj_path, glb_path, downsample=False, **kwargs)

            mesh_io_mod.save_textured_mesh = full_resolution
        preset = backend_mod.suggest_paint_preset(backend)
        overrides = paint_overrides(getattr(backend_mod, 'PAINT_PRESETS', {}), args.paint_quality,
                                     backend=backend, backend_module=backend_mod)
        print(f'[paint] profil={preset}, surcharges={overrides or "aucune"}, '
              f'limites mémoire mesurées par le moteur', flush=True)
        result = paint.texture_mesh(str(shape_path), image, backend, preset=preset,
                                    overrides=overrides,
                                    output_dir=str(args.output.parent),
                                    log=lambda message: print('[paint] ' + message, flush=True))
        source = Path(result.get('glb') or '')
        if not source.is_file():
            raise RuntimeError('Le moteur de texture n’a pas produit de GLB.')
    else:
        source = shape_path
    if args.paint:
        # Deliver the bare geometry next to the textured asset. Texture quality
        # cannot be judged without the shape it was baked onto, and the two fail
        # for different reasons, so shipping only the finished file hides which.
        bare = args.output.with_name(args.output.stem + '.shape-only.glb')
        shutil.copyfile(shape_path, bare)
        print(f'[output] {bare}', flush=True)
    if args.rescue:
        # Aurora's rescue chain: score on five axes, correct what is wrong, score
        # again. It is texture-aware and will not overwrite a real PBR atlas with
        # a flat vertex-colour projection, so it is safe to run after paint.
        source = rescue(source, args.image, args.output.parent, args.output.stem)
    shutil.copyfile(source, args.output)
    print(f'[output] {args.output}', flush=True)


if __name__ == '__main__':
    main()
