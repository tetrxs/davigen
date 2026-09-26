"""Project format: master resolution, aspect ratio, frame rate, deliveries – and the names derived from them."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .config import Config

# Frame rates Resolve accepts in Project.SetSetting("timelineFrameRate", …)
RESOLVE_FPS = {23.976: "23.976", 24.0: "24", 25.0: "25", 29.97: "29.97", 30.0: "30", 47.952: "47.952",
               48.0: "48", 50.0: "50", 59.94: "59.94", 60.0: "60", 95.904: "95.904", 96.0: "96", 100.0: "100",
               119.88: "119.88", 120.0: "120"}


@dataclass
class Format:
    width: int
    height: int
    fps: float
    aspect: str                                   # id from workflow.toml, or "custom"
    deliveries: list[str] = field(default_factory=list)

    @property
    def aspect_token(self) -> str:
        """'3:2' -> '3X2', '2.39:1' -> '239X1', custom -> '5952X3968'."""
        if self.aspect == "custom":
            return f"{self.width}X{self.height}"
        return self.aspect.replace(".", "").replace(":", "X")

    @property
    def fps_token(self) -> str:
        """25 -> '25', 23.976 -> '2398', 29.97 -> '2997'."""
        if float(self.fps).is_integer():
            return str(int(self.fps))
        return f"{self.fps:.2f}".replace(".", "")

    @property
    def resolve_fps(self) -> str:
        return RESOLVE_FPS.get(round(float(self.fps), 3), f"{self.fps:g}")

    def as_dict(self) -> dict:
        return {"width": self.width, "height": self.height, "fps": self.fps, "aspect": self.aspect,
                "deliveries": self.deliveries, "aspect_token": self.aspect_token, "fps_token": self.fps_token}


# ------------------------------------------------------------------------ editions

def fits_free(cfg: Config, width: int, height: int) -> bool:
    long_max, short_max = cfg.workflow["project"]["free_max"]
    return max(width, height) <= long_max and min(width, height) <= short_max


def clamp_free(cfg: Config, width: int, height: int) -> tuple[int, int]:
    """Largest size with the same aspect ratio that Resolve Free can render (even numbers)."""
    if fits_free(cfg, width, height):
        return width, height
    long_max, short_max = cfg.workflow["project"]["free_max"]
    scale = min(long_max / max(width, height), short_max / min(width, height))
    return round(width * scale / 2) * 2, round(height * scale / 2) * 2


def aspects(cfg: Config) -> list[dict]:
    return cfg.workflow["aspect"]


def aspect_of(cfg: Config, width: int, height: int, tolerance: float = 0.01) -> str:
    ratio = width / height
    for a in aspects(cfg):
        w, h = a["presets"][0]
        if abs(ratio - w / h) / (w / h) < tolerance:
            return a["id"]
    return "custom"


def presets(cfg: Config, aspect: str, studio: bool) -> list[dict]:
    """Presets for an aspect ratio; in Resolve Free the too-large ones are offered at the free maximum."""
    a = next((x for x in aspects(cfg) if x["id"] == aspect), None)
    out, seen = [], set()
    for w, h in (a["presets"] if a else []):
        fw, fh = (w, h) if studio else clamp_free(cfg, w, h)
        if (fw, fh) in seen:
            continue
        seen.add((fw, fh))
        out.append({"width": fw, "height": fh, "studio_only": not fits_free(cfg, w, h) and studio})
    return out


def default_format(cfg: Config, studio: bool) -> Format:
    aspect = cfg.workflow["project"]["aspect"]
    first = presets(cfg, aspect, studio)[0]
    return Format(first["width"], first["height"], float(cfg.workflow["project"]["fps"]), aspect,
                  [d["id"] for d in cfg.workflow["deliver"] if d.get("default")])


def suggest(cfg: Config, clips, studio: bool) -> dict | None:
    """Master format suggested by the footage: the resolution with the most footage, its main frame rate."""
    usable = [c for c in clips if not c.error and c.width and c.height]
    if not usable:
        return None
    weight = Counter()
    for c in usable:
        weight[(c.width, c.height)] += max(c.duration, 1.0)
    (w, h), _ = weight.most_common(1)[0]
    fps_weight = Counter()
    for c in usable:
        if (c.width, c.height) == (w, h) and c.fps:
            fps_weight[round(c.fps, 3)] += max(c.duration, 1.0)
    fps = fps_weight.most_common(1)[0][0] if fps_weight else float(cfg.workflow["project"]["fps"])
    fps = min(cfg.workflow["project"]["fps_choices"], key=lambda f: abs(f - fps))
    sw, sh = (w, h) if studio else clamp_free(cfg, w, h)
    return {"width": sw, "height": sh, "fps": fps, "aspect": aspect_of(cfg, w, h),
            "source": f"{w}×{h} · {fps:g} fps", "clamped": (sw, sh) != (w, h)}


# ----------------------------------------------------------------- timelines / deliveries

def timelines(cfg: Config, fmt: Format) -> list[dict]:
    """Working timelines (master resolution) plus one timeline per non-master delivery."""
    out = [dict(t, name=t["name"].format(aspect=fmt.aspect_token, fps=fmt.fps_token), resolution="master")
           for t in cfg.workflow["timelines"]]
    n = len(out)
    for d in deliveries(cfg, fmt, project="PROJECT"):
        if d["resolution"] == "master":
            continue
        n += 1
        w, h = d["resolution"]
        token = Format(w, h, fmt.fps, aspect_of(cfg, w, h)).aspect_token
        out.append({"role": f"delivery:{d['id']}", "resolution": [w, h], "bin": "03_TIMELINES/04_DELIVERABLES",
                    "name": f"TL_{n:02d}_DELIVERY_{token}_{fmt.fps_token}_v001"})
    return out


def deliveries(cfg: Config, fmt: Format, project: str) -> list[dict]:
    chosen = fmt.deliveries or [d["id"] for d in cfg.workflow["deliver"] if d.get("default")]
    out = []
    for d in cfg.workflow["deliver"]:
        if d["id"] in chosen:
            out.append(dict(d, filename=d["filename"].format(project=project, aspect=fmt.aspect_token),
                            preset=f"DAVIGEN_{d['id'].upper()}"))
    return out


def timeline_for(cfg: Config, fmt: Format, delivery: dict) -> str:
    role = "master" if delivery["resolution"] == "master" else f"delivery:{delivery['id']}"
    return next(t["name"] for t in timelines(cfg, fmt) if t["role"] == role)
