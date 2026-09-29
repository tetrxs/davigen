import pytest

np = pytest.importorskip("numpy")

from davigen.basic import wb_model  # noqa: E402


def test_model_ships_and_loads():
    model = wb_model.load()
    assert model is not None and model["F"].shape == (2, wb_model.BINS, wb_model.BINS)
    assert "SimpleCube++" in model["note"]


def test_histograms_are_normalised_and_centred():
    rng = np.random.default_rng(0)
    img = rng.uniform(0.05, 0.6, (64, 96, 1)) * np.ones(3)            # neutral: log-chroma (0, 0)
    h = wb_model.histograms(img)
    assert h.shape == (2, wb_model.BINS, wb_model.BINS)
    assert h[0].sum() == pytest.approx(1.0)
    centre = wb_model.BINS // 2
    assert h[0][centre - 1:centre + 1, centre - 1:centre + 1].sum() == pytest.approx(1.0)


def test_prediction_follows_a_strong_cast():
    """Not a benchmark (that is scripts/train_wb.py on SimpleCube++: 1.15° median): a clearly warm scene must
    come out warm."""
    rng = np.random.default_rng(1)
    base = np.kron(rng.uniform(0.3, 1.7, (8, 12, 3)) * rng.uniform(0.05, 0.5, (8, 12, 1)), np.ones((8, 8, 1)))
    neutral = wb_model.predict(wb_model.load(), wb_model.histograms(base))
    warm = wb_model.predict(wb_model.load(), wb_model.histograms(base * [1.4, 1.0, 0.6]))
    assert warm[0] / warm[2] > 1.15 * neutral[0] / neutral[2]                # at least the right direction
