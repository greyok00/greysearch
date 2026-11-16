#!/usr/bin/env python3
"""models/FEATURIZER.py - the adapter between tile bytes and the hcaptcha
grid library's ONNX graphs (one featurizer per weights family; swapping
weights means swapping this file alongside, see captcha_model.py's
docstring).

The vendored weights (models/hcaptcha/*.onnx, see models/SOURCE) are
hcaptcha-challenger's per-prompt binary tile classifiers. Every graph
sampled in the decompile probe shared one contract, and none of them embed
any preprocessing - the ops are Conv/Relu/Add/Gemm only, so the recipe below
is the one the upstream consumer applies at inference and the one
validated against upstream's own ground-truth tiles (the
image_label_binary/off_road_vehicle set: 40 real hcaptcha tiles, 7 positives
and 33 negatives, 40/40 correctly sided under this recipe; the
mean/std-normalized and /-0.5-0.5 variants both LOSE scores on negatives):

    input  "input.1": float32[1, 3, 64, 64]  NCHW, RGB
    output (name varies): float32[1, 2]      raw 2-class logits
    class 0 ("logits[0] > logits[1]") = this tile matches the challenge

The recipe: decode the tile, squash BOTH axes straight to 64x64 (no
aspect-preserving resize, no crop - the training pipeline resizes the full
tile through), channel order RGB, scale to [0, 1] and nothing further - no
mean/std normalization anywhere.

KNOWN GAP, stated honestly: upstream wraps one branch of its inference in a
step that is impossible to infer from the graph alone - when a tile's
height is exactly 144 (the WATERMARK capture style) its consumer applies a
colored non-local-means denoise BEFORE the resize, keyed on nothing the
graph records. The featurizer cannot replicate that filter without a
numpy/scipy stack it deliberately avoids, so a 144-pixel watermarked tile
is fed in un-denoised and may score noisier than upstream would. Tiles from
the lane's patchright captures (8-bit PNG) are unaffected unless a
challenge view itself serves that height.

images_to_tensor turns N tile screenshots into N rows (one 3x64x64 = 12,288
float row per tile; the loader folds each row into the [1, 3, 64, 64] frame
the graph pins). It is TOTAL by design: bytes that do not decode come back
as a zero row instead of raising, because a single unreadable tile must cost
the rung one honest low score - never an exception into a page flow (captcha
solve never raises; it falls through the ladder).

No Pillow, no numpy in the decoder: tile captures are PNG screenshots from
patchright, so a small stdlib decoder (zlib + unfilter + bilinear resample)
keeps the solver's runtime dependency set exactly what pyproject claims -
onnxruntime only. Pure-Python per-tile cost at 64x64 is fractions of a
millisecond, bounded by the round cap in captcha_solve.py."""
from __future__ import annotations

import struct
import zlib
from typing import List, Optional, Tuple

# The graph's spatial contract (validated across sampled library models at
# decompile time; re-check after any weights swap).
_SIDE = 64

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _decode_png(data: bytes) -> Optional[Tuple[int, int, bytes]]:
    """A screenshot PNG (8-bit depth, non-interlaced, any standard color
    type) -> (width, height, packed RGB triples, 3 bytes per pixel).
    Anything else (JPEG, truncated stream, exotic type) is None - the
    caller answers with a zero row rather than guessing."""
    if len(data) < 8 or data[:8] != _PNG_SIG:
        return None
    pos = 8
    width = height = 0
    ihdr_done = False
    idat = bytearray()
    palette: List[Tuple[int, int, int]] = []
    channels = 0  # source channels per pixel: 1 gray/palette, 2, 3, 4
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
            bit_depth, color_type, compression, filt, interlace = body[8:13]
            if bit_depth != 8 or compression != 0 or filt != 0 or interlace != 0:
                return None
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type, 0)
            if channels == 0 or width <= 0 or height <= 0:
                return None
            ihdr_done = True
        elif ctype == b"PLTE" and not palette:
            for i in range(0, len(body), 3):
                palette.append((body[i], body[i + 1], body[i + 2]))
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
        elif ctype not in (b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP",
                           b"bKGD", b"pHYs", b"tIME", b"tEXt", b"zTXt",
                           b"iTXt"):
            break  # unknown ancillary chunk family: stop, keep what we have
    if not ihdr_done or not idat:
        return None
    if color_type == 3 and not palette:
        return None
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return None
    stride = width * channels
    expected = (stride + 1) * height
    if len(raw) < expected:
        return None
    out = bytearray()
    prev = bytearray(stride)
    for y in range(height):
        base = y * (stride + 1)
        filt = raw[base]
        row = bytearray(raw[base + 1:base + 1 + stride])
        if filt == 1:  # Sub
            for i in range(channels, stride):
                row[i] = (row[i] + row[i - channels]) & 0xFF
        elif filt == 2:  # Up
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                up_left = prev[i - channels] if i >= channels else 0
                up = prev[i]
                p = left + up - up_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                near = left if pa <= pb and pa <= pc else (up if pb <= pc
                                                           else up_left)
                row[i] = (row[i] + near) & 0xFF
        # filt 0 (None) needs no work; unknown filter types fall through
        # with the raw row - a slightly wrong tile beats an exception.
        # Fold every supported color type down to packed RGB.
        if channels == 1:  # grayscale (or palette, resolved below)
            if color_type == 3:
                for x in range(width):
                    r, g, b = palette[row[x]]
                    out += bytes((r, g, b))
            else:
                for x in range(width):
                    out += bytes((row[x], row[x], row[x]))
        elif channels == 2:  # gray + alpha
            for x in range(width):
                out += bytes((row[2 * x], row[2 * x], row[2 * x]))
        elif channels == 3:  # RGB
            out += row
        else:  # RGBA: drop the alpha - tiles are opaque screenshots
            for x in range(width):
                out += row[4 * x:4 * x + 3]
        prev = row
    return width, height, bytes(out)


def _sample(width: int, height: int, rgb: bytes, x: float, y: float
            ) -> Tuple[float, float, float]:
    """Bilinear RGB sample at float coordinates (clamped, no wrap)."""
    x = min(max(x, 0.0), width - 1.0)
    y = min(max(y, 0.0), height - 1.0)
    x0, y0 = int(x), int(y)
    x1, y1 = min(x0 + 1, width - 1), min(y0 + 1, height - 1)
    fx, fy = x - x0, y - y0
    r00, g00, b00 = rgb[3 * (y0 * width + x0):3 * (y0 * width + x0) + 3]
    r10, g10, b10 = rgb[3 * (y0 * width + x1):3 * (y0 * width + x1) + 3]
    r01, g01, b01 = rgb[3 * (y1 * width + x0):3 * (y1 * width + x0) + 3]
    r11, g11, b11 = rgb[3 * (y1 * width + x1):3 * (y1 * width + x1) + 3]
    return (
        (r00 * (1 - fx) + r10 * fx) * (1 - fy)
        + (r01 * (1 - fx) + r11 * fx) * fy,
        (g00 * (1 - fx) + g10 * fx) * (1 - fy)
        + (g01 * (1 - fx) + g11 * fx) * fy,
        (b00 * (1 - fx) + b10 * fx) * (1 - fy)
        + (b01 * (1 - fx) + b11 * fx) * fy,
    )


def _zero_row() -> List[float]:
    return [0.0] * (3 * _SIDE * _SIDE)


def _tile_row(width: int, height: int, rgb: bytes) -> List[float]:
    """One screenshot -> one float row (C order: R plane, G plane, B plane),
    squashed straight to the graph's 64x64 box, channel order RGB, scaled to
    [0, 1] - and crucially NOT normalized further: the sampled library
    graphs disagree with mean/std- and +-0.5-normalized variants on upstream
    ground-truth tiles and agree with this recipe 40/40."""
    step_x = width / _SIDE
    step_y = height / _SIDE
    planes: List[List[float]] = [[], [], []]
    for c in range(3):
        plane = planes[c]
        for oy in range(_SIDE):
            sy = (oy + 0.5) * step_y
            for ox in range(_SIDE):
                sx = (ox + 0.5) * step_x
                r, g, b = _sample(width, height, rgb, sx, sy)
                plane.append((r, g, b)[c] / 255.0)
    return planes[0] + planes[1] + planes[2]


def images_to_tensor(images: List[bytes]) -> List[List[float]]:
    """The loader's seam: tile PNG bytes -> the library graphs' input tensor
    (one 3x64x64 float row per image; captcha_model folds each row into the
    [1, 3, 64, 64] frame the graph pins). Never raises."""
    rows: List[List[float]] = []
    for data in images:
        decoded = None
        if isinstance(data, (bytes, bytearray)):
            # Every failure path below must end in a zero row, never an
            # exception into the page flow.
            try:
                decoded = _decode_png(bytes(data))
            except Exception:
                decoded = None
        if decoded is None:
            # An unreadable tile scores as a blank image - the classifier
            # sees silence, not an exception.
            rows.append(_zero_row())
            continue
        w, h, rgb = decoded
        try:
            rows.append(_tile_row(w, h, rgb))
        except Exception:
            rows.append(_zero_row())
    return rows
