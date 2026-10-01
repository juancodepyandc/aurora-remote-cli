"""CPU reference rasterizer: shared depth and perspective-correct barycentrics.

Tiled, bounded batches keep validation independent of an engine's CUDA plugins.
This is an evidence renderer, not a production physically based renderer.
"""
def rasterize(clip_positions, faces, resolution):
    import numpy as np
    import torch

    clip = clip_positions.detach().cpu().numpy()
    if clip.ndim == 3 and len(clip) == 1:
        clip = clip[0]
    triangles = faces.detach().cpu().numpy().astype(np.int64)
    height, width = resolution
    if clip.ndim != 2 or clip.shape[1] != 4 or not np.isfinite(clip).all():
        raise ValueError('Finite homogeneous vertices required.')
    if triangles.ndim != 2 or triangles.shape[1] != 3 or (triangles < 0).any() or (triangles >= len(clip)).any():
        raise ValueError('Valid triangle indices required.')
    if not 1 <= height <= 2048 or not 1 <= width <= 2048:
        raise ValueError('Evidence render resolution outside bounded limits.')
    ids = np.zeros((height, width), dtype=np.int32)
    bary = np.zeros((height, width, 3), dtype=np.float32)
    depth = np.full((height, width), np.inf)
    w = clip[:, 3]
    ndc = clip[:, :3] / np.where(w == 0, 1, w)[:, None]
    points = ndc[triangles]
    xy = (points[:, :, :2] + 1) * np.array([width, height]) / 2 - .5
    low, high = np.floor(xy.min(axis=1)), np.ceil(xy.max(axis=1))
    visible = ((w[triangles] > 0).all(axis=1) & (high[:, 0] >= 0) & (high[:, 1] >= 0)
               & (low[:, 0] < width) & (low[:, 1] < height))
    for top in range(0, height, 32):
        for left in range(0, width, 32):
            bottom, right = min(top + 32, height), min(left + 32, width)
            selected = np.flatnonzero(visible & (low[:, 0] < right) & (high[:, 0] >= left)
                                      & (low[:, 1] < bottom) & (high[:, 1] >= top))
            if not len(selected):
                continue
            yy, xx = np.mgrid[top:bottom, left:right]
            px, py = xx.ravel()[None, :], yy.ravel()[None, :]
            tile_depth = depth[top:bottom, left:right].copy().ravel()
            tile_ids = ids[top:bottom, left:right].copy().ravel()
            tile_bary = bary[top:bottom, left:right].copy().reshape(-1, 3)
            for offset in range(0, len(selected), 256):
                batch = selected[offset:offset + 256]
                a, b, c = (xy[batch, i, :] for i in range(3))
                denominator = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
                valid = np.abs(denominator) > 1e-12
                denominator = np.where(valid, denominator, 1)[:, None]
                first = ((b[:, 1] - c[:, 1])[:, None] * (px - c[:, 0, None])
                         + (c[:, 0] - b[:, 0])[:, None] * (py - c[:, 1, None])) / denominator
                second = ((c[:, 1] - a[:, 1])[:, None] * (px - c[:, 0, None])
                          + (a[:, 0] - c[:, 0])[:, None] * (py - c[:, 1, None])) / denominator
                linear = np.stack([first, second, 1 - first - second], axis=-1)
                z = (linear * points[batch, None, :, 2]).sum(axis=-1)
                inside = valid[:, None] & (linear >= -1e-7).all(axis=-1) & (z >= -1) & (z <= 1)
                z = np.where(inside, z, np.inf)
                winner = z.argmin(axis=0)
                columns = np.arange(z.shape[1])
                closest = z[winner, columns]
                update = closest < tile_depth
                if update.any():
                    face = batch[winner[update]]
                    perspective = linear[winner[update], columns[update]] / w[triangles[face]]
                    perspective /= perspective.sum(axis=-1, keepdims=True)
                    tile_depth[update] = closest[update]
                    tile_ids[update] = face + 1
                    tile_bary[update] = perspective
            depth[top:bottom, left:right] = tile_depth.reshape(bottom-top, right-left)
            ids[top:bottom, left:right] = tile_ids.reshape(bottom-top, right-left)
            bary[top:bottom, left:right] = tile_bary.reshape(bottom-top, right-left, 3)
    return torch.from_numpy(ids), torch.from_numpy(bary)
