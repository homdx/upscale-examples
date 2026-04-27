"""
remwm2.py — Fixed-position watermark remover
============================================
Drop-in replacement for remwm.py that skips Florence-2 detection entirely.
Instead, you supply the watermark bounding box directly.

Usage
-----
Absolute pixel coordinates:
    python remwm2.py INPUT OUTPUT --bbox 1728,952,1920,1080

Anchor-relative (no need to know resolution in advance):
    python remwm2.py INPUT OUTPUT --bbox-anchor bottom-right --bbox-size 192x128

Both modes support images and videos.  All other flags (--transparent,
--force-format, --overwrite, --preview) behave identically to remwm.py.

Why this exists
---------------
When you already know where the watermark lives (e.g. always bottom-right
192×128 px), running Florence-2 on every frame wastes minutes per video.
This script resolves the bbox from the first frame/image, builds a fixed
mask, and feeds it straight to LaMa — no detection model loaded at all.

Orchestrator integration (process_water_file in your wrapper script)
--------------------------------------------------------------------
Replace the cmd list that calls remwm.py with:

    cmd = [
        str(venv_python), "remwm2.py",
        str(tmp_dest), str(output_file),
        "--bbox-anchor", "bottom-right",
        "--bbox-size", f"{CROP_W_PX}x{CROP_H_PX}",
    ]

Stage 2 (extract_watermark_region) and Stage 3 (overlay_cleaned_region)
in your wrapper are completely unaffected.
"""

import sys
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Compatibility shim — must run before iopaint/diffusers are imported,
# because diffusers does `from huggingface_hub import cached_download` at
# module level and that name was removed in huggingface_hub 0.24.
import huggingface_hub
if not hasattr(huggingface_hub, "cached_download"):
    huggingface_hub.cached_download = huggingface_hub.hf_hub_download

import click
import cv2
import numpy as np
import torch
import tqdm
from loguru import logger
from PIL import Image, ImageDraw
from iopaint.model_manager import ModelManager
from iopaint.schema import HDStrategy, LDMSampler, InpaintRequest as InpaintConfig

try:
    from cv2.typing import MatLike
except ImportError:
    MatLike = np.ndarray

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}

ANCHOR_CHOICES = [
    "top-left", "top-right", "bottom-left", "bottom-right",
    "top-center", "bottom-center", "center",
]


# ---------------------------------------------------------------------------
# Bbox resolution
# ---------------------------------------------------------------------------

def resolve_bbox(
    bbox_str: str | None,
    anchor: str | None,
    size_str: str | None,
    frame_w: int,
    frame_h: int,
) -> tuple[int, int, int, int]:
    """
    Return (x1, y1, x2, y2) in absolute pixels.

    Accepts one of:
      • bbox_str  — "x1,y1,x2,y2"  absolute pixels
      • anchor + size_str — e.g. anchor="bottom-right", size="192x128"
    """
    if bbox_str:
        parts = [int(v.strip()) for v in bbox_str.split(",")]
        if len(parts) != 4:
            raise ValueError(f"--bbox must be 'x1,y1,x2,y2', got: {bbox_str!r}")
        x1, y1, x2, y2 = parts
        return x1, y1, x2, y2

    if anchor and size_str:
        try:
            bw, bh = [int(v) for v in size_str.lower().split("x")]
        except ValueError:
            raise ValueError(f"--bbox-size must be 'WxH', got: {size_str!r}")

        if anchor == "top-left":
            x1, y1 = 0, 0
        elif anchor == "top-right":
            x1, y1 = frame_w - bw, 0
        elif anchor == "bottom-left":
            x1, y1 = 0, frame_h - bh
        elif anchor == "bottom-right":
            x1, y1 = frame_w - bw, frame_h - bh
        elif anchor == "top-center":
            x1, y1 = (frame_w - bw) // 2, 0
        elif anchor == "bottom-center":
            x1, y1 = (frame_w - bw) // 2, frame_h - bh
        elif anchor == "center":
            x1, y1 = (frame_w - bw) // 2, (frame_h - bh) // 2
        else:
            raise ValueError(f"Unknown anchor: {anchor!r}")

        return x1, y1, x1 + bw, y1 + bh

    raise click.UsageError(
        "Provide either --bbox x1,y1,x2,y2  OR  both --bbox-anchor and --bbox-size."
    )


def bbox_to_mask(x1: int, y1: int, x2: int, y2: int, size: tuple[int, int]) -> Image.Image:
    """Return a binary PIL mask (L mode) with the bbox filled white."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rectangle([x1, y1, x2, y2], fill=255)
    return mask


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_lama(device: str) -> ModelManager:
    try:
        return ModelManager(name="lama", device=device)
    except NotImplementedError:
        logger.info("LaMa not cached — downloading (~196 MB)…")
        result = subprocess.run(
            [sys.executable, "-m", "iopaint", "download", "--model", "lama"]
        )
        if result.returncode != 0:
            raise RuntimeError(
                "LaMa download failed. Run manually: python -m iopaint download --model lama"
            )
        return ModelManager(name="lama", device=device)


# ---------------------------------------------------------------------------
# Step 2 — Remove  (LaMa inpaint OR transparent)
# ---------------------------------------------------------------------------

def remove(
    image: Image.Image,
    mask: Image.Image,
    lama: ModelManager | None,
    transparent: bool,
) -> Image.Image:
    if transparent:
        rgba = image.convert("RGBA")
        result = Image.new("RGBA", rgba.size)
        grey = mask.convert("L")
        for x in range(rgba.width):
            for y in range(rgba.height):
                result.putpixel(
                    (x, y),
                    (0, 0, 0, 0) if grey.getpixel((x, y)) > 0 else rgba.getpixel((x, y)),
                )
        return result

    config = InpaintConfig(
        ldm_steps=50,
        ldm_sampler=LDMSampler.ddim,
        hd_strategy=HDStrategy.CROP,
        hd_strategy_crop_margin=64,
        hd_strategy_crop_trigger_size=800,
        hd_strategy_resize_limit=1600,
    )
    img_np = np.array(image)
    msk_np = np.array(mask.convert("L"))
    out_np = lama(img_np, msk_np, config)
    if out_np.dtype in (np.float32, np.float64):
        out_np = np.clip(out_np, 0, 255).astype(np.uint8)
    return Image.fromarray(cv2.cvtColor(out_np, cv2.COLOR_BGR2RGB))


def _rgba_to_rgb(image: Image.Image) -> Image.Image:
    bg = Image.new("RGB", image.size, (255, 255, 255))
    bg.paste(image, mask=image.split()[3])
    return bg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def _fourcc(fmt: str) -> int:
    return cv2.VideoWriter_fourcc(*("mp4v" if fmt.upper() == "MP4" else "XVID"))


def _merge_audio(no_audio: Path, original: Path, output: Path) -> None:
    try:
        subprocess.check_output(["ffmpeg", "-version"], stderr=subprocess.STDOUT)
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.warning("FFmpeg not found — output will have no audio.")
        shutil.copy(str(no_audio), str(output))
        return

    cmd = [
        "ffmpeg", "-y",
        "-i", str(no_audio),
        "-i", str(original),
        "-c:v", "copy",
        "-c:a", "aac",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        str(output),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info("Audio merged.")
    except subprocess.CalledProcessError:
        logger.warning("Audio merge failed — saving video without audio.")
        shutil.copy(str(no_audio), str(output))


def _get_video_dimensions(path: Path) -> tuple[int, int]:
    cap = cv2.VideoCapture(str(path))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return w, h


def _get_image_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.width, img.height


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------

def process_image(
    input_path: Path,
    output_path: Path,
    lama: ModelManager | None,
    bbox: tuple[int, int, int, int],
    transparent: bool,
    force_format: str | None,
    progress_offset: int = 0,
    progress_scale: int = 100,
) -> Path:
    image = Image.open(input_path).convert("RGB")
    x1, y1, x2, y2 = bbox
    mask = bbox_to_mask(x1, y1, x2, y2, image.size)

    result = remove(image, mask, lama, transparent)

    if force_format:
        fmt = force_format.upper()
    elif transparent:
        fmt = "PNG"
    else:
        ext = input_path.suffix.lstrip(".").upper()
        fmt = ext if ext in ("PNG", "WEBP", "JPEG", "JPG") else "PNG"
    if fmt == "JPG":
        fmt = "JPEG"

    out = output_path.with_suffix(f".{fmt.lower()}")
    result.save(out, format=fmt)
    print(f"input_path:{input_path}, output_path:{out}, overall_progress:{progress_offset + progress_scale}%")
    return out


# ---------------------------------------------------------------------------
# Video processing — fixed mask applied to every frame
# ---------------------------------------------------------------------------

def process_video(
    input_path: Path,
    output_path: Path,
    lama: ModelManager | None,
    bbox: tuple[int, int, int, int],
    transparent: bool,
    force_format: str | None,
    overwrite: bool,
    progress_offset: int = 0,
    progress_scale: int = 100,
) -> Path:
    cap = cv2.VideoCapture(str(input_path))
    fps   = cap.get(cv2.CAP_PROP_FPS)
    w     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fmt   = (force_format or "MP4").upper()

    out_file = (
        output_path / f"{input_path.stem}_no_watermark.{fmt.lower()}"
        if output_path.is_dir()
        else output_path.with_suffix(f".{fmt.lower()}")
    )
    if out_file.exists() and not overwrite:
        logger.info(f"Skipping (already exists): {out_file}")
        cap.release()
        return out_file

    # Build the fixed mask once — same for every frame
    x1, y1, x2, y2 = bbox
    fixed_mask = bbox_to_mask(x1, y1, x2, y2, (w, h))

    tmp_dir   = tempfile.mkdtemp()
    tmp_video = Path(tmp_dir) / f"tmp.{fmt.lower()}"
    writer    = cv2.VideoWriter(str(tmp_video), _fourcc(fmt), fps, (w, h))

    with tqdm.tqdm(total=total, desc="Inpainting frames") as pbar:
        frame_idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            pil    = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            result = remove(pil, fixed_mask, lama, transparent)
            if result.mode == "RGBA":
                result = _rgba_to_rgb(result)

            writer.write(cv2.cvtColor(np.array(result), cv2.COLOR_RGB2BGR))
            frame_idx += 1
            pbar.update(1)

            progress = int(progress_offset + (frame_idx / total) * progress_scale)
            print(f"frame {frame_idx}/{total}, overall_progress:{progress}%")

    cap.release()
    writer.release()

    _merge_audio(tmp_video, input_path, out_file)

    try:
        os.remove(str(tmp_video))
        os.rmdir(tmp_dir)
    except Exception:
        pass

    print(f"input_path:{input_path}, output_path:{out_file}, overall_progress:{progress_offset + progress_scale}%")
    return out_file


# ---------------------------------------------------------------------------
# Preview — draw bbox on image / mid-frame, print JSON
# ---------------------------------------------------------------------------

def run_preview(
    input_path: Path,
    bbox: tuple[int, int, int, int],
) -> None:
    import json, base64, random
    from io import BytesIO

    if input_path.is_dir():
        candidates = (
            list(input_path.glob("*.[jp][pn]g"))
            + list(input_path.glob("*.webp"))
            + list(input_path.glob("*.mp4"))
            + list(input_path.glob("*.mov"))
        )
        if not candidates:
            print(json.dumps({"error": "No supported files found"}))
            return
        sample = random.choice(candidates)
    else:
        sample = input_path

    if _is_video(sample):
        cap   = cv2.VideoCapture(str(sample))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            print(json.dumps({"error": f"Could not read video frame: {sample}"}))
            return
        pil         = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        source_type = "video"
        source_frame = total // 2
    else:
        pil         = Image.open(sample).convert("RGB")
        source_type = "image"
        source_frame = None

    x1, y1, x2, y2 = bbox
    draw = ImageDraw.Draw(pil)
    draw.rectangle([x1, y1, x2, y2], outline=(0, 255, 0), width=3)
    draw.text((x1, max(0, y1 - 15)), f"fixed bbox {x2-x1}x{y2-y1}", fill=(0, 255, 0))

    buf = BytesIO()
    pil.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    print(json.dumps({
        "image": img_b64,
        "detections": [{"bbox": [x1, y1, x2, y2], "area_percent": None, "accepted": True}],
        "source": str(sample),
        "source_type": source_type,
        "source_frame": source_frame,
        "prompt_used": "fixed-bbox (no detection)",
        "bbox": [x1, y1, x2, y2],
    }))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.argument("input_path",  type=click.Path(exists=True))
@click.argument("output_path", type=click.Path(), required=False, default=None)
# ── bbox source (one of the two groups is required) ──────────────────────
@click.option("--bbox",         default=None, help="Absolute pixel bbox: x1,y1,x2,y2")
@click.option("--bbox-anchor",  default=None, type=click.Choice(ANCHOR_CHOICES), help="Anchor corner for --bbox-size.")
@click.option("--bbox-size",    default=None, help="WxH in pixels when using --bbox-anchor. E.g. 192x128")
# ── behaviour flags (same as remwm.py) ───────────────────────────────────
@click.option("--preview",      is_flag=True, help="Show bbox on a sample frame as JSON; no removal.")
@click.option("--overwrite",    is_flag=True, help="Overwrite existing output files.")
@click.option("--transparent",  is_flag=True, help="Make watermark region transparent instead of inpainting.")
@click.option("--force-format", type=click.Choice(["PNG", "WEBP", "JPG", "MP4", "AVI"], case_sensitive=False), default=None)
def main(
    input_path, output_path, bbox, bbox_anchor, bbox_size,
    preview, overwrite, transparent, force_format,
):
    """
    Fixed-position watermark remover.  Florence-2 is NOT loaded — the bbox
    you supply is used as the mask directly.

    \b
    Examples:
      # Absolute coords
      python remwm2.py input.mp4 output.mp4 --bbox 1728,952,1920,1080

      # Anchor-relative (bottom-right 192×128 px region)
      python remwm2.py input.mp4 output.mp4 --bbox-anchor bottom-right --bbox-size 192x128
    """
    input_path = Path(input_path)

    # Determine frame/image size to resolve anchor-relative bboxes
    if _is_video(input_path):
        frame_w, frame_h = _get_video_dimensions(input_path)
    else:
        frame_w, frame_h = _get_image_dimensions(input_path)

    resolved_bbox = resolve_bbox(bbox, bbox_anchor, bbox_size, frame_w, frame_h)
    x1, y1, x2, y2 = resolved_bbox
    logger.info(f"Using fixed bbox: ({x1}, {y1}) → ({x2}, {y2})  [{x2-x1}×{y2-y1} px]")

    # Preview mode — no models needed
    if preview:
        run_preview(input_path, resolved_bbox)
        return

    if output_path is None:
        raise click.UsageError("output_path is required in non-preview mode.")
    output_path = Path(output_path)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")

    # Load LaMa (skip if transparent mode — only numpy mask ops needed)
    lama = None if transparent else load_lama(device)
    if lama:
        logger.info("LaMa loaded. (Florence-2 skipped — using fixed bbox)")

    # Safety: never overwrite the source
    if input_path.resolve() == output_path.resolve():
        logger.error("Cannot overwrite input file — choose a different output path.")
        return

    if _is_video(input_path):
        if output_path.is_dir():
            out_file = output_path / input_path.name
        else:
            out_file = output_path
        if out_file.suffix.lower() not in VIDEO_EXTENSIONS:
            out_file = out_file.with_suffix(".mp4")

        process_video(
            input_path, out_file, lama, resolved_bbox,
            transparent, force_format, overwrite,
        )
    else:
        if output_path.is_dir():
            out_file = output_path / input_path.name
        else:
            out_file = output_path

        process_image(
            input_path, out_file, lama, resolved_bbox,
            transparent, force_format,
        )
        print(f"input_path:{input_path}, output_path:{out_file}, overall_progress:100%")


if __name__ == "__main__":
    main()
