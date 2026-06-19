import numpy as np

from viewer.sam2_device_segment import Sam2DeviceSegmenter


class _StubPredictor:
    def __init__(self) -> None:
        self.image = None
        self.calls = []

    def set_image(self, image: np.ndarray) -> None:
        self.image = image

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        masks = np.array(
            [
                [[0, 1], [0, 0]],
                [[1, 1], [0, 0]],
            ],
            dtype=np.uint8,
        )
        scores = np.array([0.1, 0.9], dtype=np.float32)
        return masks, scores, None


def test_reset_for_image_and_re_predict_reuse_predictor() -> None:
    segmenter = Sam2DeviceSegmenter()
    segmenter.predictor = _StubPredictor()

    rgb = np.zeros((2, 2, 3), dtype=np.uint8)
    segmenter.reset_for_image(rgb)
    mask = segmenter.re_predict(np.array([[1, 1]], dtype=np.float32), np.array([1], dtype=np.int32))

    assert segmenter.predictor.image is not None
    assert mask is not None
    assert mask.dtype == bool
    assert mask.tolist() == [[True, True], [False, False]]
