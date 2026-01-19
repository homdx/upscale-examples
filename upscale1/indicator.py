#!/bin/python3

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import os

# ================= CONFIGURATION =================
ENABLE_INDICATOR = False     # Set False to disable
INDICATOR_SCALE = 2.0        # Scale multiplier (2.0 = 2x larger)
INDICATOR_OPACITY = 0.85     # 0.0 (Invisible) to 1.0 (Fully Opaque)

# [NEW] 1. Disable numbers/text
ENABLE_TEXT_NUMBERS = True

# [NEW] 3. Move indicator to left (0.5 = move left by 50% of its own width)
SHIFT_LEFT_RATIO = 0.5      
# =================================================

def add_video_frame_indicator_png(src_png: Path,
                                  dst_png: Path,
                                  frame_index_1based: int,
                                  total_frames: int,
                                  video_num: int,
                                  video_total: int,
                                  segment_start_pct: float = 0.0,  # [NEW] 2. Start % (0.0 to 1.0)
                                  segment_end_pct: float = 1.0     # [NEW] 2. End % (0.0 to 1.0)
                                  ) -> bool:
    """
    Draw in top-right: [battery/progress bar]
    Supports segments (multi-run) and hiding text.
    """
    if not ENABLE_INDICATOR:
        return False

    try:
        # Load image
        img = Image.open(src_png).convert("RGBA")
        w, h = img.size

        # Scale calculation
        base_scale = max(0.6, min(2.2, w / 1920.0))
        s = base_scale * INDICATOR_SCALE

        margin = int(18 * s)
        font_size = int(26 * s)

        # Geometry
        bar_w = int(110 * s)
        bar_h = int(34 * s)
        tip_w = int(7 * s)
        tip_h = int(14 * s)
        stroke = max(2, int(3 * s))
        radius = int(7 * s)
        gap = int(10 * s)

        # Style Colors (Gray Theme)
        text_fill = (230, 230, 230, 245)     # Light Gray Text
        text_stroke = (40, 40, 40, 220)      # Dark Gray Stroke
        text_sw = max(2, int(3 * s))

        # === 1. HANDLE TEXT VISIBILITY ===
        font = None
        lt_w, lt_h = 0, 0
        rt_w, rt_h = 0, 0
        left_text = ""
        right_text = ""

        if ENABLE_TEXT_NUMBERS:
            # Only load fonts if text is enabled
            font_candidates = [
                "arialbd.ttf", "arial.ttf", "seguiemj.ttf", "tahoma.ttf", "calibrib.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "DejaVuSans-Bold.ttf"
            ]
            for f_name in font_candidates:
                try:
                    font = ImageFont.truetype(f_name, font_size)
                    break
                except (OSError, IOError):
                    continue
            
            if font is None:
                try: font = ImageFont.load_default(size=font_size)
                except TypeError: font = ImageFont.load_default()

            left_text = str(video_num)
            right_text = str(video_total)

            dummy = ImageDraw.Draw(img)
            lt_box = dummy.textbbox((0, 0), left_text, font=font, stroke_width=text_sw)
            rt_box = dummy.textbbox((0, 0), right_text, font=font, stroke_width=text_sw)
            lt_w = lt_box[2] - lt_box[0]
            lt_h = lt_box[3] - lt_box[1]
            rt_w = rt_box[2] - rt_box[0]
            rt_h = rt_box[3] - rt_box[1]

        # Calculate total UI size
        # If text is disabled, lt_w and rt_w are 0
        
        ui_h = max(bar_h, lt_h, rt_h)
        
        if ENABLE_TEXT_NUMBERS:
            ui_w = lt_w + gap + (bar_w + tip_w) + gap + rt_w
        else:
            ui_w = (bar_w + tip_w) # No gaps needed if no text

        # === 3. POSITION LOGIC (SHIFT LEFT) ===
        # Standard right align: x0 = w - margin - ui_w
        # Shift left logic: Subtract extra width based on setting
        
        shift_amount = int(ui_w * SHIFT_LEFT_RATIO)
        
        x0 = w - margin - ui_w - shift_amount
        y0 = margin
        x1 = x0 + ui_w
        y1 = y0 + ui_h

        # Create separate overlay layer
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)

        # Background Plate
        back_fill = (30, 30, 30, 140)
        back_outline = (180, 180, 180, 120)
        d.rounded_rectangle([x0 - int(10*s), y0 - int(8*s), x1 + int(10*s), y1 + int(8*s)],
                            radius=int(10*s), fill=back_fill, outline=back_outline, width=max(1, int(2*s)))

        # Draw Left Text (Only if enabled)
        if ENABLE_TEXT_NUMBERS:
            tx = x0
            ty = y0 + (ui_h - lt_h) // 2
            d.text((tx, ty), left_text, font=font, fill=text_fill,
                   stroke_width=text_sw, stroke_fill=text_stroke)
            
            # Update Battery X start position based on text existence
            bx = x0 + lt_w + gap
        else:
            # If no text, battery starts at x0
            bx = x0

        # Battery Body
        by = y0 + (ui_h - bar_h) // 2
        body_fill = (50, 50, 50, 170)
        body_outline_light = (200, 200, 200, 200)
        body_outline_dark = (20, 20, 20, 140)

        d.rounded_rectangle([bx, by, bx + bar_w, by + bar_h],
                            radius=radius, fill=body_fill, outline=body_outline_dark, width=stroke+1)
        d.rounded_rectangle([bx, by, bx + bar_w, by + bar_h],
                            radius=radius, fill=None, outline=body_outline_light, width=stroke)

        # Battery Tip
        tip_x0 = bx + bar_w
        tip_y0 = by + (bar_h - tip_h) // 2
        d.rounded_rectangle([tip_x0, tip_y0, tip_x0 + tip_w, tip_y0 + tip_h],
                            radius=max(1, int(3*s)), fill=body_outline_light)

        # === 2. FILL BAR LOGIC (PERCENTAGE SEGMENTS) ===
        if total_frames > 0:
            start_pad = 12
            end_pad = 12
            
            # Calculate LOCAL fraction for this specific video run (0.0 to 1.0)
            if frame_index_1based <= start_pad:
                local_frac = 0.0
            elif frame_index_1based >= (total_frames - end_pad):
                local_frac = 1.0
            else:
                steps_done = frame_index_1based - start_pad
                steps_total = total_frames - start_pad - end_pad
                if steps_total > 0:
                    local_frac = steps_done / float(steps_total)
                else:
                    local_frac = 1.0 
            
            local_frac = max(0.0, min(1.0, local_frac))

            # Calculate GLOBAL fraction based on segments
            # Ex: Run 1 (0.0 to 0.5): local 0.5 -> global 0.25
            segment_span = segment_end_pct - segment_start_pct
            global_frac = segment_start_pct + (local_frac * segment_span)
            
            # Use global_frac for logic
            frac = max(0.0, min(1.0, global_frac))

            # Draw Fill
            inner_pad = stroke + 2
            inner_x0 = bx + inner_pad
            inner_y0 = by + inner_pad
            inner_w = max(0, (bx + bar_w - inner_pad) - inner_x0)
            fill_w = int(inner_w * frac)

            if fill_w > 0:
                # Color interpolation
                def lerp_color(c1, c2, t):
                    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

                RED    = (220, 40, 40)
                YELLOW = (230, 210, 40)
                GREEN  = (40, 210, 80)
                HI_RED    = (255, 180, 180)
                HI_YELLOW = (255, 255, 190)
                HI_GREEN  = (200, 255, 210)

                # Color depends on GLOBAL frac
                if frac < 0.5:
                    local_t = frac * 2.0
                    base_rgb = lerp_color(RED, YELLOW, local_t)
                    hi_rgb   = lerp_color(HI_RED, HI_YELLOW, local_t)
                else:
                    local_t = (frac - 0.5) * 2.0
                    base_rgb = lerp_color(YELLOW, GREEN, local_t)
                    hi_rgb   = lerp_color(HI_YELLOW, HI_GREEN, local_t)

                fill_base = base_rgb + (210,)
                fill_hi   = hi_rgb + (150,)

                d.rounded_rectangle([inner_x0, inner_y0, inner_x0 + fill_w, by + bar_h - inner_pad],
                                    radius=max(1, int(5*s)), fill=fill_base)
                d.line([(inner_x0, inner_y0 + int(2*s)), (inner_x0 + fill_w, inner_y0 + int(2*s))],
                       fill=fill_hi, width=max(1, int(2*s)))

        # Draw Right Text (Only if enabled)
        if ENABLE_TEXT_NUMBERS:
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
