"""Isolated diffusion entry point; input is a local, verified model directory."""
import argparse
import json
import os
import sys
from pathlib import Path


DEFAULT_NEGATIVE = (
    'blurry, low quality, deformed, bad anatomy, extra limbs, missing limbs, '
    'extra fingers, fused fingers, watermark, text, duplicate subjects, collage'
)


def generation_settings(model, metadata):
    """Adapter settings follow model family; increasing Turbo steps is not quality."""
    family = metadata.get("_class_name", "").lower()
    if "turbo" in str(model).lower():
        return dict(num_inference_steps=4, guidance_scale=0.0, height=512, width=512)
    if 'flux2klein' in family:
        return dict(num_inference_steps=4, guidance_scale=1.0, height=1024, width=1024)
    if "flux" in family:
        schnell = "schnell" in str(model).lower()
        return dict(num_inference_steps=4 if schnell else 28,
                    guidance_scale=0.0 if schnell else 3.5, height=1024, width=1024)
    return dict(num_inference_steps=30, guidance_scale=7.0,
                height=1024 if "xl" in family else 512, width=1024 if "xl" in family else 512)


def load_diffusion_pipeline(model, metadata, *, device, torch_module, diffusers_module):
    """Resolve a library-owned class, never executable code from model weights."""
    name = metadata.get('_class_name', '')
    if metadata.get('custom_pipeline') or isinstance(name, (list, tuple)):
        raise RuntimeError('Pipeline distant personnalisé non autorisé automatiquement.')
    if name == 'Flux2KleinPipeline':
        loader = getattr(diffusers_module, name, None)
        if loader is None:
            raise RuntimeError('Flux2KleinPipeline absent : Diffusers >= 0.37 est nécessaire.')
        dtype = torch_module.bfloat16 if device != 'cpu' else torch_module.float32
    else:
        loader = diffusers_module.AutoPipelineForText2Image
        dtype = torch_module.float16 if device in {'cuda', 'mps'} else torch_module.float32
    options = dict(torch_dtype=dtype, local_files_only=True)
    if list(Path(model).glob('unet/*.fp16.safetensors')):
        options['variant'] = 'fp16'
    return loader.from_pretrained(str(model), **options)


def supported_call(pipe, call):
    import inspect
    parameters = inspect.signature(pipe.__call__).parameters
    if any(p.kind == p.VAR_KEYWORD for p in parameters.values()):
        return call
    # FLUX does not implement SDXL's negative conditioning. Do not send an
    # unsupported keyword or pretend this engine obeyed it.
    if 'negative_prompt' not in parameters:
        call = {k: v for k, v in call.items() if k != 'negative_prompt'}
    unknown = set(call) - set(parameters)
    if unknown:
        raise RuntimeError(f'Paramètres incompatibles avec le moteur image : {sorted(unknown)}')
    return call


def validate_image(image):
    from PIL import ImageStat
    from aurora_cli.capability_probe import evaluation_profile
    limits = evaluation_profile('image')['limits']
    extrema = ImageStat.Stat(image.convert('L')).extrema[0]
    if min(image.size) < limits['min_dimension'] or extrema[1] - extrema[0] < limits['min_luminance_range']:
        raise RuntimeError('Sortie image dégénérée ou uniforme ; aucune image finale livrée.')


def prepare_review(source, output, max_dimension):
    """Bound vision-input cost without altering the delivered original image."""
    from PIL import Image, ImageOps
    if max_dimension < 64:
        raise ValueError('Résolution de revue trop faible')
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        image.save(output)


def inspect_input_image(source):
    """Decode a supplied reference without inventing content or changing bytes."""
    import warnings
    from PIL import Image, ImageOps
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(source) as original:
            original.verify()
        with Image.open(source) as original:
            if getattr(original, 'n_frames', 1) != 1:
                raise ValueError('Image animée ou multipage : fournis une image fixe explicite.')
            image = ImageOps.exif_transpose(original)
            image.load()
            if min(image.size) <= 0:
                raise ValueError('Dimensions de l’image invalides.')
            if image.convert('RGBA').getchannel('A').getbbox() is None:
                raise ValueError('Image entièrement transparente : aucun sujet à reconstruire.')
            return dict(format=original.format, width=image.width, height=image.height,
                        mode=image.mode, frames=1)


def prompt_chunks(text, tokenizers):
    """Split on word boundaries using the installed encoders' actual limits."""
    words, chunks, current = text.split(), [], []
    def fits(value):
        return all(len(tokenizer(value, truncation=False)['input_ids']) <= tokenizer.model_max_length
                   for tokenizer in tokenizers)
    for word in words:
        candidate = ' '.join(current + [word])
        if not fits(candidate):
            if current:
                chunks.append(' '.join(current))
            current = [word]
            if not fits(word):
                raise RuntimeError('Un élément de la demande dépasse le contexte image ; aucune coupure silencieuse.')
        else:
            current.append(word)
    if current:
        chunks.append(' '.join(current))
    return chunks or ['']


def encode_image_prompt(pipe, prompt, negative, *, guidance):
    """Use extended embeddings when CLIP would discard requested details.

    Supports the declared Stable Diffusion adapters. Other families keep their
    native encoding contract; this is not a universal text-encoder shim.
    """
    import torch
    from aurora_cli.evolution import load_policy
    tokenizers = [t for t in (getattr(pipe, 'tokenizer', None), getattr(pipe, 'tokenizer_2', None))
                  if t is not None and 2 < getattr(t, 'model_max_length', 0) < 10000]
    if not tokenizers:
        return dict(prompt=prompt, negative_prompt=negative)
    positive = prompt_chunks(prompt, tokenizers)
    negatives = prompt_chunks(negative, tokenizers) if guidance else ['']
    count = max(len(positive), len(negatives))
    if count == 1:
        return dict(prompt=prompt, negative_prompt=negative)
    if count > int(load_policy().get('delivery_image', {}).get('max_prompt_chunks', 8)):
        raise RuntimeError('Demande trop longue pour le budget image configuré ; rien ne sera tronqué.')
    positive += [''] * (count - len(positive))
    negatives += [''] * (count - len(negatives))
    embeddings = pipe.encode_prompt(prompt=positive, negative_prompt=negatives,
                                    do_classifier_free_guidance=guidance)
    if len(embeddings) not in (2, 4):
        raise RuntimeError('Contrat de l’encodeur image non pris en charge pour une demande longue.')
    def join(value):
        return torch.cat(list(value.unbind(0)), dim=0)[None] if value is not None else None
    result = {'prompt_embeds': join(embeddings[0]), 'negative_prompt_embeds': join(embeddings[1])}
    if len(embeddings) == 4:
        result.update(pooled_prompt_embeds=embeddings[2].mean(dim=0, keepdim=True),
                      negative_pooled_prompt_embeds=embeddings[3].mean(dim=0, keepdim=True)
                        if embeddings[3] is not None else None)
    print(f'[image] demande complète encodée en {count} segment(s), sans troncature CLIP', flush=True)
    return result


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--inspect-image':
        parser = argparse.ArgumentParser()
        parser.add_argument('--inspect-image', action='store_true')
        parser.add_argument('source', type=Path)
        args = parser.parse_args()
        print(json.dumps(inspect_input_image(args.source)))
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--prepare-review':
        parser = argparse.ArgumentParser()
        parser.add_argument('--prepare-review', action='store_true')
        parser.add_argument('source', type=Path)
        parser.add_argument('output', type=Path)
        parser.add_argument('--max-dimension', type=int, required=True)
        args = parser.parse_args()
        prepare_review(args.source, args.output, args.max_dimension)
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('model')
    parser.add_argument('prompt')
    parser.add_argument('output')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--negative', default='')
    parser.add_argument('--model-spec', default='', help='Model identity independent of the cache directory')
    parser.add_argument('--low-memory', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    # Adapted from upstream auto_rl/resources.py at the pinned source revision.
    os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True,max_split_size_mb:128')
    os.environ.setdefault('TORCH_CUDNN_V8_API_LRU_CACHE_LIMIT', '0')
    os.environ.setdefault('PYTORCH_MPS_HIGH_WATERMARK_RATIO', '1.0')
    os.environ.setdefault('PYTORCH_MPS_LOW_WATERMARK_RATIO', '0.9')
    import torch
    import diffusers
    device = 'cuda' if torch.cuda.is_available() else ('mps' if torch.backends.mps.is_available() else 'cpu')
    requested_device = os.environ.get('JOBIA_DEVICE', '').strip()
    if requested_device:
        if requested_device not in {'cpu', 'cuda', 'mps'}:
            raise ValueError('JOBIA_DEVICE doit être cpu, cuda ou mps')
        device = requested_device
    metadata = json.loads((Path(args.model) / 'model_index.json').read_text())
    pipe = load_diffusion_pipeline(args.model, metadata, device=device,
                                    torch_module=torch, diffusers_module=diffusers)
    if args.low_memory:
        pipe.enable_attention_slicing()
    if hasattr(pipe, 'enable_vae_tiling') and args.low_memory:
        pipe.enable_vae_tiling()
    # The SDXL VAE is numerically unstable on fp16 and on MPS, where the decoder
    # returns NaN and the delivered PNG comes out uniformly black. Decoding the
    # latents once on CPU in float32 costs seconds and is correct on every backend.
    # SDXL returns spatial latents; Flux returns packed latents and must use
    # its own unpacking, shift and decode contract.
    cpu_decode = 'StableDiffusion' in metadata.get('_class_name', '') and getattr(pipe, 'vae', None) is not None
    if cpu_decode:
        if hasattr(pipe.vae, 'config'):
            pipe.vae.config.force_upcast = True
    if device == 'cuda':
        pipe.enable_model_cpu_offload()
    else:
        pipe.to(device)
    if cpu_decode:
        pipe.vae.to('cpu', dtype=torch.float32)
    settings = generation_settings(args.model_spec or metadata.get('_name_or_path') or args.model, metadata)
    generator = torch.Generator(device='cpu').manual_seed(args.seed)
    call = dict(generator=generator, **settings)
    negative = DEFAULT_NEGATIVE + (', ' + args.negative if args.negative else '')
    if 'StableDiffusion' in metadata.get('_class_name', ''):
        call.update(encode_image_prompt(pipe, args.prompt, negative,
                                        guidance=settings['guidance_scale'] > 1))
    else:
        call.update(prompt=args.prompt, negative_prompt=negative)
    call = supported_call(pipe, call)
    with torch.inference_mode():
        if not cpu_decode:
            image = pipe(**call).images[0]
        else:
            latents = pipe(**call, output_type='latent').images
            latents = latents.to('cpu', dtype=torch.float32) / pipe.vae.config.scaling_factor
            decoded = pipe.vae.decode(latents).sample
            image = pipe.image_processor.postprocess(decoded, output_type='pil')[0]
    validate_image(image)
    image.save(args.output)


if __name__ == '__main__':
    main()
