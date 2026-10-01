import pytest

torch = pytest.importorskip('torch', reason='surface decoding runs in the engine environment')

from aurora_cli.core.surface_decoder import SurfaceDecoder, physical_queries


def test_voxel_spacing_is_not_truncated_to_integer():
    indices = torch.tensor([[0, 0, 0], [1, 2, 3], [384, 384, 384]])
    queries = physical_queries(indices, [-1.01] * 3, [1.01] * 3, 384, dtype=torch.float32)
    assert torch.allclose(queries[0], torch.tensor([-1.01] * 3))
    assert torch.allclose(queries[-1], torch.tensor([1.01] * 3))
    assert torch.all(queries[1] > queries[0])
    assert len(torch.unique(queries, dim=0)) == 3


def test_sparse_refinement_preserves_a_known_sphere():
    calls = []
    def sphere(*, queries, latents):
        calls.append(queries)
        return 0.5 - torch.linalg.vector_norm(queries, dim=-1, keepdim=True)
    grid = SurfaceDecoder(min_resolution=8, band=0.15, chunk_size=2048)(
        torch.zeros(1, 4, 8), sphere, octree_resolution=32)
    assert grid.shape == (1, 33, 33, 33)
    assert grid[0, 16, 16, 16] > 0
    assert grid[0, 0, 0, 0] < 0
    assert all(torch.isfinite(q).all() for q in calls)
    assert torch.allclose(grid[0, 16, 16, 24], torch.tensor(-0.005), atol=0.01)


def test_nonfinite_surface_is_refused():
    with pytest.raises(RuntimeError, match='NaN/Inf'):
        SurfaceDecoder(min_resolution=8)(torch.zeros(1, 4, 8),
            lambda **kw: torch.full_like(kw['queries'][..., :1], float('nan')), octree_resolution=8)


def test_empty_surface_is_not_replaced_by_a_plane():
    with pytest.raises(RuntimeError, match='sans surface'):
        SurfaceDecoder(min_resolution=8)(torch.zeros(1, 4, 8),
            lambda **kw: torch.ones_like(kw['queries'][..., :1]), octree_resolution=8)


def test_long_image_prompt_retains_every_segment():
    from types import SimpleNamespace
    from aurora_cli.core.image_worker import encode_image_prompt, prompt_chunks
    class Tokenizer:
        model_max_length = 7
        def __call__(self, text, **kwargs):
            return {'input_ids': [0] + text.split() + [1]}
    text = ' '.join(f'feature{i}' for i in range(12))
    assert ' '.join(prompt_chunks(text, [Tokenizer()])) == text
    seen = []
    def encode(**kwargs):
        seen.append(kwargs)
        count = len(kwargs['prompt'])
        return torch.zeros(count, 7, 8), None, torch.zeros(count, 4), None
    pipe = SimpleNamespace(tokenizer=Tokenizer(), encode_prompt=encode)
    embeddings = encode_image_prompt(pipe, text, '', guidance=False)
    assert 'feature11' in seen[0]['prompt'][-1]
    assert embeddings['prompt_embeds'].shape == (1, 21, 8)
    assert embeddings['pooled_prompt_embeds'].shape == (1, 4)
