from davigen.lut import Lut3D, compose


def identity(size):
    n = size - 1
    return Lut3D(size, [(r / n, g / n, b / n) for b in range(size) for g in range(size) for r in range(size)])


def gain(size, k):
    lut = identity(size)
    return Lut3D(size, [(min(r * k, 1), min(g * k, 1), min(b * k, 1)) for r, g, b in lut.table])


def test_identity_sampling_is_exact_between_grid_points():
    lut = identity(17)
    r, g, b = lut.sample(0.31, 0.5, 0.77)
    assert abs(r - 0.31) < 1e-9 and abs(g - 0.5) < 1e-9 and abs(b - 0.77) < 1e-9


def test_compose_applies_in_order():
    out = compose(gain(9, 0.5), gain(9, 1.5), size=9)
    r, _, _ = out.sample(0.5, 0.5, 0.5)
    assert abs(r - 0.375) < 1e-6


def test_roundtrip_file(tmp_path):
    lut = gain(5, 0.8)
    p = lut.write(tmp_path / "x.cube")
    back = Lut3D.read(p)
    assert back.size == 5
    assert abs(back.sample(1, 1, 1)[0] - 0.8) < 1e-6
