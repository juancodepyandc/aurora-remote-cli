"""TRELLIS.2 native image-to-GLB adapter using its published PBR export API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def local_pipeline_config(receipt, directory):
    """Resolve all components into managed local files, without changing weights."""
    weights = Path(receipt['weights'])
    config = json.loads((weights / 'pipeline.json').read_text())
    dependencies = receipt.get('dependencies', {})
    arguments = config['args']
    for key, name in arguments['models'].items():
        if name.startswith('ckpts/'):
            path = weights / name
        else:
            parts = name.split('/')
            spec = '/'.join(parts[:2])
            if spec not in dependencies:
                raise RuntimeError(f'Composant non préparé : {spec}')
            path = Path(dependencies[spec]).joinpath(*parts[2:])
        allowed = weights if name.startswith('ckpts/') else Path(dependencies[spec])
        if not path.resolve().is_relative_to(allowed.resolve()):
            raise ValueError('Checkpoint hors des poids gérés.')
        if not Path(str(path) + '.json').is_file() or not Path(str(path) + '.safetensors').is_file():
            raise RuntimeError(f'Checkpoint natif incomplet : {path}')
        arguments['models'][key] = os.path.relpath(path.resolve(), directory.resolve())
    for key in ('image_cond_model', 'rembg_model'):
        spec = arguments[key]['args']['model_name']
        if spec not in dependencies:
            raise RuntimeError(f'Composant non préparé : {spec}')
        arguments[key]['args']['model_name'] = dependencies[spec]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'pipeline.json').write_text(json.dumps(config), encoding='utf-8')
    return directory


def export_pbr(mesh, output, *, postprocess, texture_size, target_faces):
    glb = postprocess.to_glb(vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
        coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
        aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        decimation_target=target_faces, texture_size=texture_size,
        remesh=True, remesh_band=1, remesh_project=0, verbose=True)
    # Standard PNG textures remain portable to viewers lacking EXT_texture_webp.
    glb.export(str(output), extension_webp=False)


def run_conditioned(pipe, image, output, resolution):
    """Retain the exact native crop/matte; do not preprocess it a second time."""
    conditioned = pipe.preprocess_image(image)
    output.parent.mkdir(parents=True, exist_ok=True)
    conditioning_path = output.with_name(output.stem + '.conditioning.png')
    conditioned.save(conditioning_path)
    recipe = '1536_cascade' if resolution == 1536 else str(resolution)
    mesh = pipe.run(conditioned, pipeline_type=recipe, preprocess_image=False)[0]
    return mesh, conditioning_path


def main():
    parser = argparse.ArgumentParser()
    for key in ('root', 'repo', 'image', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--family', choices=['trellis2'], required=True)
    parser.add_argument('--paint', action='store_true')
    parser.add_argument('--texture-size', type=int, default=2048)
    parser.add_argument('--resolution', type=int, choices=[512, 1024, 1536], default=512)
    parser.add_argument('--target-faces', type=int, default=100000)
    args = parser.parse_args()
    sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(args.repo.resolve())]
    os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')
    from PIL import Image, ImageOps
    import torch
    from trellis2.pipelines import Trellis2ImageTo3DPipeline
    import o_voxel
    from aurora_cli.core.glb_validation import validate_glb
    if not torch.cuda.is_available():
        raise RuntimeError('TRELLIS.2 exige un runtime CUDA effectif ; aucun repli fictif vers Metal.')
    receipt = json.loads((args.root / 'runtime.json').read_text())
    if receipt['signature']['model'] != args.model:
        raise RuntimeError('Les poids préparés ne correspondent pas au modèle demandé.')
    from aurora_cli.adapters import AdapterRegistry
    from aurora_cli.core.native_runtime import verify_dependencies
    runner, reason = AdapterRegistry().resolve('3d', args.model)
    if runner is None:
        raise RuntimeError(reason)
    verify_dependencies(runner, receipt.get('dependencies', {}))
    config = local_pipeline_config(receipt, args.output.parent / 'trellis-runtime-config')
    os.environ['HF_HUB_OFFLINE'] = '1'
    pipe = Trellis2ImageTo3DPipeline.from_pretrained(str(config))
    pipe.cuda()
    with Image.open(args.image) as original:
        image = ImageOps.exif_transpose(original).convert('RGBA')
    with torch.inference_mode():
        mesh, conditioning_path = run_conditioned(pipe, image, args.output, args.resolution)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.stem + '.candidate.glb')
    export_pbr(mesh, temporary, postprocess=o_voxel.postprocess,
               texture_size=args.texture_size, target_faces=args.target_faces)
    validate_glb(temporary, require_textures=args.paint, require_pbr_maps=args.paint,
                 decode_textures=True)
    temporary.replace(args.output)
    args.output.with_suffix('.generation.json').write_text(json.dumps(dict(
        model=args.model, family=args.family, requested_resolution=args.resolution,
        effective_voxel_size=float(mesh.voxel_size), texture_size=args.texture_size,
        source_sha256=hashlib.sha256(args.image.read_bytes()).hexdigest(),
        conditioning_sha256=hashlib.sha256(conditioning_path.read_bytes()).hexdigest(),
        export='embedded_png_pbr'), indent=2), encoding='utf-8')
    print(f'TRELLIS.2 : GLB et matériaux exportés, fidélité restant à vérifier : {args.output}', flush=True)


if __name__ == '__main__':
    main()
