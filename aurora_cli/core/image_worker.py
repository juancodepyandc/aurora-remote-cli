"""Isolated diffusion entry point; input is a local, verified model directory."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('model')
    parser.add_argument('prompt')
    parser.add_argument('output')
    args = parser.parse_args()
    # Adapted from upstream auto_rl/resources.py at the pinned source revision.
    os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True,max_split_size_mb:128')
    os.environ.setdefault('TORCH_CUDNN_V8_API_LRU_CACHE_LIMIT', '0')
    import torch
    from diffusers import AutoPipelineForText2Image
    device = 'cuda' if torch.cuda.is_available() else ('mps' if torch.backends.mps.is_available() else 'cpu')
    dtype = torch.float16 if device == 'cuda' else torch.float32
    pipe = AutoPipelineForText2Image.from_pretrained(args.model, torch_dtype=dtype,
                                                  local_files_only=True)
    pipe.enable_attention_slicing()
    if device == 'cuda':
        pipe.enable_model_cpu_offload()
    else:
        pipe.to(device)
    turbo = 'turbo' in args.model.lower()
    image = pipe(prompt=args.prompt, num_inference_steps=4 if turbo else 25,
                 guidance_scale=0.0 if turbo else 7.0, height=512, width=512).images[0]
    image.save(args.output)


if __name__ == '__main__':
    main()
