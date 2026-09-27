# 04 · Measurements

**Goal:** turn thumbnails into numbers and flags, independent of Resolve.

**File:** `davigen/basic/measure.py`. Tests go in `tests/test_basic_measure.py`, with synthetic images built in the
test.

## Tasks

1. **Masks:**
   - clipped in camera: any channel of the log input at the clip's clip level, taken from the sample's maximum
     values and the profile
   - near black
   - midtones
   - skin: a YCbCr hue and chroma window on the simulated display image
2. **Exposure:**
   - log-average luminance of the frame (linear DWG), masked, centre-weighted
   - skin median, when skin covers at least 2 %
   - high-key and low-key tests (concept §5.1)
3. **EV100** from ISO, aperture and shutter.
   - The scanner already reads ISO. Aperture and shutter are added in `mediainfo.py` / `scanner.py` (exiftool
     `FNumber`, `ExposureTime`), stored in the clip metadata at import, and read back here.
   - Missing values mean no EV. The correction then skips the night rules and lowers the confidence.
4. **White balance:**
   - four estimators (concept §5.2) on the linear DWG midtones
   - their median, and their angular spread in degrees
   - the achromatic-pixel fraction
   - the dominant-hue fraction (hue histogram, largest 30° bin)
   - left/right and top/bottom halves estimated separately, for the "mixed light" flag
5. **Contrast:** 0.5th and 99.5th luminance percentiles of the simulated display image. A local-contrast measure
   (mean gradient magnitude) for the haze test.
6. **Saturation:** mean CIELAB C\* in the display image, masked. The skin hue angle on the vectorscope, as Cb/Cr
   angle.
7. **Within the clip:** per statistic, the spread across the clip's samples. The median over samples is the value
   used.
8. Everything is returned as a dataclass `Measurement`, JSON-serialisable for the record.

## Tests

Build images in DWG/Intermediate with known properties:

- A grey card at −1 stop measures −1 ± 0.05 stops.
- A neutral scene tinted with a known gain: the WB estimate is within 1° of the true illuminant.
- A 70 % green frame gives a high dominant-hue fraction and low WB confidence.
- Snow (bright, low chroma) is recognised as high key, and a backlit silhouette as low key.
- A flat, low-gradient frame is recognised as haze.
- Two samples one stop apart give the "changes within clip" flag.
- EV100 from f/2.8, 1/50 s, ISO 100 ≈ 8.6.

## Done when

All tests pass, and `measure(samples, clip_meta, simulator) -> Measurement` is the only entry point.
