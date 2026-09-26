"""Minimal .cube 3D-LUT reading, sampling and composition (pure Python)."""

from __future__ import annotations

from pathlib import Path


class Lut3D:
    def __init__(self, size: int, table: list[tuple[float, float, float]], title: str = ""):
        if len(table) != size ** 3:
            raise ValueError(f"LUT has {len(table)} entries, expected {size ** 3}")
        self.size, self.table, self.title = size, table, title

    @classmethod
    def read(cls, path: str | Path) -> "Lut3D":
        size, title, rows = 0, "", []
        dmin, dmax = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            key = parts[0].upper()
            if key == "LUT_3D_SIZE":
                size = int(parts[1])
            elif key == "TITLE":
                title = line.split(None, 1)[1].strip().strip('"')
            elif key == "DOMAIN_MIN":
                dmin = tuple(map(float, parts[1:4]))
            elif key == "DOMAIN_MAX":
                dmax = tuple(map(float, parts[1:4]))
            elif key == "LUT_1D_SIZE":
                raise ValueError("1D LUTs are not supported")
            elif _is_number(parts[0]) and len(parts) >= 3:
                rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
        if dmin != (0.0, 0.0, 0.0) or dmax != (1.0, 1.0, 1.0):
            raise ValueError("Only LUTs with a 0–1 domain are supported")
        return cls(size, rows, title)

    def write(self, path: str | Path, title: str | None = None) -> Path:
        path = Path(path)
        lines = [f'TITLE "{title or self.title or path.stem}"', f"LUT_3D_SIZE {self.size}", ""]
        lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in self.table]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def sample(self, r: float, g: float, b: float) -> tuple[float, float, float]:
        """Trilinear lookup; red varies fastest (standard .cube order)."""
        n = self.size - 1
        t = self.table
        s = self.size

        def split(v: float) -> tuple[int, int, float]:
            v = min(max(v, 0.0), 1.0) * n
            i = min(int(v), n - 1) if n else 0
            return i, min(i + 1, n), v - i

        r0, r1, fr = split(r)
        g0, g1, fg = split(g)
        b0, b1, fb = split(b)

        def at(ri, gi, bi):
            return t[ri + s * gi + s * s * bi]

        out = []
        for c in range(3):
            c00 = at(r0, g0, b0)[c] * (1 - fr) + at(r1, g0, b0)[c] * fr
            c10 = at(r0, g1, b0)[c] * (1 - fr) + at(r1, g1, b0)[c] * fr
            c01 = at(r0, g0, b1)[c] * (1 - fr) + at(r1, g0, b1)[c] * fr
            c11 = at(r0, g1, b1)[c] * (1 - fr) + at(r1, g1, b1)[c] * fr
            c0 = c00 * (1 - fg) + c10 * fg
            c1 = c01 * (1 - fg) + c11 * fg
            out.append(c0 * (1 - fb) + c1 * fb)
        return out[0], out[1], out[2]


def compose(first: Lut3D, then: Lut3D, size: int = 65) -> Lut3D:
    """LUT equivalent to applying `first` and then `then`."""
    n = size - 1
    table = []
    for bi in range(size):
        for gi in range(size):
            for ri in range(size):
                table.append(then.sample(*first.sample(ri / n, gi / n, bi / n)))
    return Lut3D(size, table)


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
