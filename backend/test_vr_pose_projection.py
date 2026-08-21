import numpy as np

from viewer.pose_projection import extract_world_contour, rgb_pixel_to_world, world_to_rgb_pixel


def _intrinsics() -> np.ndarray:
    return np.array(
        [
            [100.0, 0.0, 50.0],
            [0.0, 100.0, 40.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
    )


def test_world_to_rgb_pixel_and_back_round_trip() -> None:
    world = np.array([[0.0, 0.0, 2.0], [0.2, 0.1, 4.0]], dtype=np.float32)
    pose = np.eye(4, dtype=np.float32)
    pixels, in_frame = world_to_rgb_pixel(world, pose, _intrinsics(), 100, 100)

    assert in_frame.tolist() == [True, True]

    depth = np.zeros((100, 100), dtype=np.float32)
    depth[pixels[0, 1], pixels[0, 0]] = 2.0
    depth[pixels[1, 1], pixels[1, 0]] = 4.0
    world_back = rgb_pixel_to_world(pixels, depth, pose, _intrinsics())

    assert world_back is not None
    np.testing.assert_allclose(world_back, world, atol=0.03)


def test_extract_world_contour_returns_world_points() -> None:
    mask = np.zeros((100, 100), dtype=bool)
    mask[30:70, 35:75] = True
    depth = np.full((100, 100), 2.5, dtype=np.float32)

    contour = extract_world_contour(mask, depth, np.eye(4, dtype=np.float32), _intrinsics(), max_points=32)

    assert contour
    assert len(contour) <= 32
    assert all(len(point) == 3 for point in contour)
