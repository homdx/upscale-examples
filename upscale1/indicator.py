#!/bin/python3

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import os

# ================= CONFIGURATION =================
ENABLE_INDICATOR = True    # Set False to disable
INDICATOR_SCALE = 2.0      # Scale multiplier (2.0 = 2x larger)
INDICATOR_OPACITY = 0.85    # 0.0 (Invisible) to 1.0 (Fully Opaque)
# =================================================

def add_video_frame_indicator_png(src_png: Path,
                                  dst_png: Path,
                                  frame_index_1based: int,
                                  total_frames: int,
                                  video_num: int,
                                  video_total: int) -> bool:
    """
    Draw in top-right: <video_num> [battery/progress bar] <video_total>
    Features:
    - Cross-platform fonts (Windows/Linux)
    - Smooth Gradient (Red->Yellow->Green)
    - Gray Color Theme
    - 12-frame padding for Empty/Full states
    """
    if not ENABLE_INDICATOR:
        return False

    try:
        # Load image
        img = Image.open(src_png).convert("RGBA")
        w, h = img.size

        # Scale calculation (Base scale * User Multiplier)
        base_scale = max(0.6, min(2.2, w / 1920.0))
        s = base_scale * INDICATOR_SCALE

        margin = int(18 * s)
        font_size = int(26 * s)

        # === IMPROVED FONT LOADING (WINDOWS & LINUX) ===
        font_candidates = [
            # Windows Standard Fonts
            "arialbd.ttf",       # Arial Bold
            "arial.ttf",         # Arial
            "seguiemj.ttf",      # Segoe UI
            "tahoma.ttf",        # Tahoma
            "calibrib.ttf",      # Calibri Bold

            # Linux Standard Paths
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",

            # Local/Fallback
            "DejaVuSans-Bold.ttf"
        ]

        font = None
        for f_name in font_candidates:
            try:
                font = ImageFont.truetype(f_name, font_size)
                break
            except (OSError, IOError):
                continue

        if font is None:
            # Fallback logic
            try:
                # Pillow >= 10.1.0 supports scalable default font
                font = ImageFont.load_default(size=font_size)
            except TypeError:
                # Old Pillow fallback (This causes the tiny text, but is last resort)
                print(f"⚠️ Warning: No suitable fonts found. Text may be small.")
                font = ImageFont.load_default()
        # =================================================

        # Geometry
        bar_w = int(110 * s)
        bar_h = int(34 * s)
        tip_w = int(7 * s)
        tip_h = int(14 * s)
        stroke = max(2, int(3 * s))
        radius = int(7 * s)
        gap = int(10 * s)

        left_text = str(video_num)
        right_text = str(video_total)

        # Style Colors (Gray Theme)
        text_fill = (230, 230, 230, 245)     # Light Gray Text
        text_stroke = (40, 40, 40, 220)      # Dark Gray Stroke
        text_sw = max(2, int(3 * s))

        # Measure text size
        dummy = ImageDraw.Draw(img)
        lt_box = dummy.textbbox((0, 0), left_text, font=font, stroke_width=text_sw)
        rt_box = dummy.textbbox((0, 0), right_text, font=font, stroke_width=text_sw)

        lt_w = lt_box[2] - lt_box[0]
        lt_h = lt_box[3] - lt_box[1]
        rt_w = rt_box[2] - rt_box[0]
        rt_h = rt_box[3] - rt_box[1]

        ui_h = max(bar_h, lt_h, rt_h)
        ui_w = lt_w + gap + (bar_w + tip_w) + gap + rt_w

        # Position (Top Right)
        x0 = w - margin - ui_w
        y0 = margin
        x1 = x0 + ui_w
        y1 = y0 + ui_h

        # Create separate overlay layer for opacity control
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)

        # Background Plate (Grayer)
        back_fill = (30, 30, 30, 140)        # Dark Gray Background
        back_outline = (180, 180, 180, 120)  # Medium Gray Outline
        d.rounded_rectangle([x0 - int(10*s), y0 - int(8*s), x1 + int(10*s), y1 + int(8*s)],
                            radius=int(10*s), fill=back_fill, outline=back_outline, width=max(1, int(2*s)))

        # Left Text
        tx = x0
        ty = y0 + (ui_h - lt_h) // 2
        d.text((tx, ty), left_text, font=font, fill=text_fill,
               stroke_width=text_sw, stroke_fill=text_stroke)

        # Battery Body (Grayer)
        bx = x0 + lt_w + gap
        by = y0 + (ui_h - bar_h) // 2

        body_fill = (50, 50, 50, 170)             # Medium Gray Fill
        body_outline_light = (200, 200, 200, 200) # Light Gray Outline
        body_outline_dark = (20, 20, 20, 140)     # Dark Gray Shadow

        d.rounded_rectangle([bx, by, bx + bar_w, by + bar_h],
                            radius=radius, fill=body_fill, outline=body_outline_dark, width=stroke+1)
        d.rounded_rectangle([bx, by, bx + bar_w, by + bar_h],
                            radius=radius, fill=None, outline=body_outline_light, width=stroke)

        # Battery Tip
        tip_x0 = bx + bar_w
        tip_y0 = by + (bar_h - tip_h) // 2
        d.rounded_rectangle([tip_x0, tip_y0, tip_x0 + tip_w, tip_y0 + tip_h],
                            radius=max(1, int(3*s)), fill=body_outline_light)

        # Fill Bar Logic (Explicit Zones + Logic Gates)
        if total_frames > 0:
            start_pad = 12  # Frames to stay EMPTY
            end_pad = 12    # Frames to stay FULL

            if frame_index_1based <= start_pad:
                frac = 0.0 # Force Empty
            elif frame_index_1based >= (total_frames - end_pad):
                frac = 1.0 # Force Full
            else:
                # Middle: Scale smoothly
                steps_done = frame_index_1based - start_pad
                steps_total = total_frames - start_pad - end_pad

                if steps_total > 0:
                    frac = steps_done / float(steps_total)
                else:
                    frac = 1.0

            frac = max(0.0, min(1.0, frac))

            # Draw Fill
            inner_pad = stroke + 2
            inner_x0 = bx + inner_pad
            inner_y0 = by + inner_pad
            inner_w = max(0, (bx + bar_w - inner_pad) - inner_x0)
            fill_w = int(inner_w * frac)

            if fill_w > 0:
                # === SMOOTH COLOR INTERPOLATION (Red -> Yellow -> Green) ===
                def lerp_color(c1, c2, t):
                    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

                # Key Colors (RGB)
                RED    = (220, 40, 40)
                YELLOW = (230, 210, 40)
                GREEN  = (40, 210, 80)

                # Highlight Colors (RGB)
                HI_RED    = (255, 180, 180)
                HI_YELLOW = (255, 255, 190)
                HI_GREEN  = (200, 255, 210)

                # Calculate specific color
                if frac < 0.5:
                    # 0% - 50% (Red to Yellow)
                    local_t = frac * 2.0
                    base_rgb = lerp_color(RED, YELLOW, local_t)
                    hi_rgb   = lerp_color(HI_RED, HI_YELLOW, local_t)
                else:
                    # 50% - 100% (Yellow to Green)
                    local_t = (frac - 0.5) * 2.0
                    base_rgb = lerp_color(YELLOW, GREEN, local_t)
                    hi_rgb   = lerp_color(HI_YELLOW, HI_GREEN, local_t)

                fill_base = base_rgb + (210,)  # Add Alpha
                fill_hi   = hi_rgb + (150,)    # Add Alpha

                d.rounded_rectangle([inner_x0, inner_y0, inner_x0 + fill_w, by + bar_h - inner_pad],
                                    radius=max(1, int(5*s)), fill=fill_base)
                d.line([(inner_x0, inner_y0 + int(2*s)), (inner_x0 + fill_w, inner_y0 + int(2*s))],
                       fill=fill_hi, width=max(1, int(2*s)))

        # Right Text
        rtx = bx + bar_w + tip_w + gap
        rty = y0 + (ui_h - rt_h) // 2
        d.text((rtx, rty), right_text, font=font, fill=text_fill,
               stroke_width=text_sw, stroke_fill=text_stroke)

        # Apply Global Opacity
        if INDICATOR_OPACITY < 1.0:
            r, g, b, a = overlay.split()
            a = a.point(lambda p: int(p * INDICATOR_OPACITY))
            overlay.putalpha(a)

        # Save result
        out = Image.alpha_composite(img, overlay)
        out.convert("RGB").save(dst_png, "PNG", optimize=True)
        return True

    except Exception as e:
        print(f"⚠️ Indicator failed for {src_png}: {e}")
        return False
