import pytest

np = pytest.importorskip('numpy')
torch = pytest.importorskip('torch')
from aurora_cli.core.portable_rasterizer import rasterize


def test_shared_depth_selects_front_face_independent_of_order():
    front = [[-.8, -.8, -.5, 1], [.8, -.8, -.5, 1], [0, .8, -.5, 1]]
    back = [[-.8, -.8, .5, 1], [.8, -.8, .5, 1], [0, .8, .5, 1]]
    for faces, expected in [([[0,1,2], [3,4,5]], 1), ([[3,4,5], [0,1,2]], 2)]:
        ids, bary = rasterize(torch.tensor([front + back]), torch.tensor(faces), (64,64))
        assert ids[32,32] == expected
        assert float(bary[32,32].sum()) == pytest.approx(1)
        assert ids[0,0] == 0


def test_barycentrics_are_perspective_correct():
    # Same NDC triangle, but the top vertex is twice as far from the camera.
    clip = torch.tensor([[[-.8,-.8,0,1], [.8,-.8,0,1], [0,1.6,0,2]]])
    ids, bary = rasterize(clip, torch.tensor([[0,1,2]]), (64,64))
    y, x = 31, 31
    ndc_y = (y+.5) / 64 * 2 - 1
    linear_top = (ndc_y + .8) / 1.6
    expected = (linear_top / 2) / (1 - linear_top + linear_top / 2)
    assert ids[y,x] == 1
    assert float(bary[y,x,2]) == pytest.approx(expected, abs=1e-5)


def test_reversed_winding_and_offscreen_faces_do_not_corrupt_result():
    clip = torch.tensor([[[-.5,-.5,0,1], [.5,-.5,0,1], [0,.5,0,1],
                          [10,10,0,1], [11,10,0,1], [10,11,0,1]]])
    first, _ = rasterize(clip, torch.tensor([[0,1,2], [3,4,5]]), (32,32))
    second, _ = rasterize(clip, torch.tensor([[2,1,0]]), (32,32))
    assert torch.equal(first, second)


def test_waveform_is_not_accepted_if_silent_nonfinite_or_clipped():
    from aurora_cli.core.audio_worker import validate_waveform
    for value in (np.zeros(16000), np.full(16000, np.nan), np.full(16000, 2)):
        with pytest.raises(RuntimeError):
            validate_waveform(value, 16000)
    assert len(validate_waveform(np.full(16000, .1), 16000)) == 16000
