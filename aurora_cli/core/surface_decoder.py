"""Sparse coarse-to-fine surface queries with floating-point coordinates.

The installed upstream hierarchical decoder casts voxel spacing to the integer
index dtype. All refined queries then collapse onto one corner of the box.
This implementation keeps indices integer and physical coordinates floating,
and checks each completed batch instead of queuing unbounded accelerator work.
Numerical dependencies are imported only in the engine environment.
"""
from __future__ import annotations


def physical_queries(indices, minimum, maximum, resolution, *, dtype):
    import torch
    if not dtype.is_floating_point or resolution <= 0:
        raise ValueError('Surface queries require floating-point coordinates and positive resolution')
    low = torch.as_tensor(minimum, device=indices.device, dtype=dtype)
    high = torch.as_tensor(maximum, device=indices.device, dtype=dtype)
    return low + indices.to(dtype) * ((high - low) / resolution)


class SurfaceDecoder:
    def __init__(self, min_resolution=96, band=0.95, chunk_size=8192):
        if min_resolution < 8 or band <= 0 or chunk_size < 1:
            raise ValueError('Invalid surface decoding policy')
        self.min_resolution, self.band, self.chunk_size = min_resolution, band, chunk_size

    def __call__(self, latents, geo_decoder, *, bounds=1.01, octree_resolution=384,
                 mc_level=0.0, **kwargs):
        import torch
        import torch.nn.functional as functional
        from tqdm import tqdm
        if latents.shape[0] != 1:
            raise ValueError('Surface decoding currently requires one object per job')
        if isinstance(bounds, (float, int)):
            minimum, maximum = [-float(bounds)] * 3, [float(bounds)] * 3
        else:
            minimum, maximum = list(bounds[:3]), list(bounds[3:])
        resolutions = [int(octree_resolution)]
        while resolutions[-1] > self.min_resolution:
            resolutions.append(resolutions[-1] // 2)
        resolutions.reverse()
        device, dtype = latents.device, latents.dtype
        grid = None
        for resolution in resolutions:
            side = resolution + 1
            if grid is None:
                indices = torch.cartesian_prod(*[torch.arange(side, device=device)] * 3)
                grid = torch.empty((side, side, side), device=device, dtype=torch.float32)
            else:
                # Interpolation carries signs away from the queried surface;
                # no missing samples are silently converted to zero or a plane.
                grid = functional.interpolate(grid[None, None], size=(side,) * 3,
                            mode='trilinear', align_corners=True)[0, 0]
                band = (grid - mc_level).abs() < self.band
                signs = grid >= mc_level
                for axis in range(3):
                    left, right = [slice(None)] * 3, [slice(None)] * 3
                    left[axis], right[axis] = slice(None, -1), slice(1, None)
                    crossing = signs[tuple(left)] != signs[tuple(right)]
                    band[tuple(left)] |= crossing
                    band[tuple(right)] |= crossing
                band = functional.max_pool3d(band[None, None].float(), kernel_size=3,
                                             stride=1, padding=1)[0, 0].bool()
                indices = band.nonzero()
            if not len(indices):
                raise RuntimeError('Décodage sans surface à affiner ; aucune géométrie inventée en remplacement.')
            for start in tqdm(range(0, len(indices), self.chunk_size), desc=f'Surface decoding [r{side}]'):
                batch = indices[start:start + self.chunk_size]
                queries = physical_queries(batch, minimum, maximum, resolution, dtype=dtype)
                values = geo_decoder(queries=queries[None], latents=latents).reshape(-1)
                if values.numel() != len(batch) or not bool(torch.isfinite(values).all()):
                    raise RuntimeError('Champ de surface invalide : taille incorrecte ou NaN/Inf.')
                grid[batch[:, 0], batch[:, 1], batch[:, 2]] = values.float()
            low, high = float(grid.min()), float(grid.max())
            print(f'[surface] résolution={resolution}, requêtes={len(indices)}, champ=[{low:.4f}, {high:.4f}]', flush=True)
            if not low < mc_level < high:
                raise RuntimeError(f'Champ sans surface : [{low}, {high}] ne traverse pas {mc_level}.')
        return grid[None]
