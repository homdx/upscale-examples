#!/usr/bin/env python3
"""
Накладывает текст и обратный отсчёт на видео.

Использование:
  python3 add_text_overlay.py input.mp4
  python3 add_text_overlay.py input.mp4 -o output.mp4
  python3 add_text_overlay.py input.mp4 -t "Ваш текст здесь"
  python3 add_text_overlay.py input.mp4 -f text.txt

Если -t и -f не заданы — используется TEXT_DEFAULT из кода.
"""

import argparse
import json
import os
import subprocess
import sys
import textwrap

# ── Текст по умолчанию (редактируйте здесь) ───────────────────────
TEXT_DEFAULT = "What did one wall say to the other wall?"

# ── Шрифт ─────────────────────────────────────────────────────────
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# ── Параметры отображения ──────────────────────────────────────────
FONT_SIZE   = 58
COUNT_SIZE  = 120
FONT_COLOR  = "white"
BOX_COLOR   = "black@0.55"
BOX_BORDER  = 14

# Оценка ширины символа относительно размера шрифта (DejaVu Sans Bold)
CHAR_WIDTH_RATIO = 0.58

# Горизонтальные отступы от края экрана (суммарно с двух сторон)
SIDE_PADDING = 80


# ── Получить размеры видео через ffprobe ───────────────────────────
def get_video_size(path):
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
        capture_output=True, text=True,
    )
    try:
        streams = json.loads(result.stdout)["streams"]
        for s in streams:
            if s.get("codec_type") == "video":
                return int(s["width"]), int(s["height"])
    except Exception:
        pass
    return 720, 1280  # fallback


# ── Автоперенос текста ─────────────────────────────────────────────
def wrap_text(text, vid_width):
    usable_px      = vid_width - SIDE_PADDING
    char_px        = FONT_SIZE * CHAR_WIDTH_RATIO
    chars_per_line = max(10, int(usable_px / char_px))
    return textwrap.wrap(text, width=chars_per_line)


# ── Экранирование текста для ffmpeg drawtext (без кавычек вокруг значения) ──
def escape_ffmpeg_text(text):
    """
    ffmpeg drawtext разбирает значение text= посимвольно.
    Спецсимволы нужно экранировать обратным слешем.
    Порядок важен: сначала сам обратный слеш.
    """
    text = text.replace("\\", "\\\\")  # \ → \\ (первым!)
    text = text.replace("'",  "\\'")   # ' → \'
    text = text.replace(":",  "\\:")   # : → \:
    text = text.replace(",",  "\\,")   # , → \,
    text = text.replace("[",  "\\[")
    text = text.replace("]",  "\\]")
    text = text.replace("%",  "%%")    # % → %% (printf-стиль)
    text = text.replace("?",  "\\?")
    return text


# ── Построить один фильтр drawtext ────────────────────────────────
def dt(text, size, color, x, y, box=False, enable=None):
    safe = escape_ffmpeg_text(text)
    parts = [
        f"fontfile={FONT}",
        f"text={safe}",   # без одинарных кавычек — экранирование явное
        f"fontsize={size}",
        f"fontcolor={color}",
        f"x={x}",
        f"y={y}",
    ]
    if box:
        parts += ["box=1", f"boxcolor={BOX_COLOR}", f"boxborderw={BOX_BORDER}"]
    if enable:
        parts.append(f"enable='{enable}'")
    return "drawtext=" + ":".join(parts)


# ── Собрать все фильтры ────────────────────────────────────────────
def build_filters(lines, vid_width, vid_height, position=None):
    """
    position: int 1..9 или None.
    Высота делится на 10 равных зон (0..10).
    Центр текстового блока помещается на отметку position/10 * vid_height.
    Если None — центрируем в верхних 4/5 (прежнее поведение).
    """
    cx = "(w-text_w)/2"

    line_h  = int(FONT_SIZE * 1.35)
    block_h = len(lines) * line_h

    if position is None:
        # Прежнее поведение: центр блока в верхних 4/5 экрана
        text_zone_h = int(vid_height * 4 / 5)
        start_y     = (text_zone_h - block_h) // 2
    else:
        # Центр блока = position/10 * vid_height
        center_y = int(vid_height * position / 10)
        start_y  = center_y - block_h // 2
        # Не выходить за границы кадра
        start_y  = max(0, min(start_y, vid_height - block_h))

    filters = []
    for i, line in enumerate(lines):
        y = start_y + i * line_h
        filters.append(dt(line, FONT_SIZE, FONT_COLOR, cx, str(y), box=True))

    # Обратный отсчёт: центр нижней 1/5
    zone_top    = int(vid_height * 4 / 5)
    zone_h      = vid_height - zone_top
    countdown_y = zone_top + (zone_h - COUNT_SIZE) // 2

    for digit, t0, t1 in [("5", 0, 1), ("4", 1, 2), ("3", 2, 3), ("2", 3, 4)]:
        filters.append(dt(
            digit, COUNT_SIZE, "yellow", cx, str(countdown_y),
            enable=f"gte(t,{t0})*lt(t,{t1})"
        ))
    filters.append(dt("1", COUNT_SIZE, "yellow", cx, str(countdown_y),
                       enable="gte(t,4)"))

    return filters


# ── main ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Наложить текст и обратный отсчёт на видео."
    )
    parser.add_argument("input",           help="Входной видеофайл")
    parser.add_argument("-o", "--output",  help="Выходной файл (по умолчанию: <input>_overlay.mp4)")
    parser.add_argument("-t", "--text",    help="Текст для наложения")
    parser.add_argument("-f", "--file",    help="Файл с текстом (.txt)")
    parser.add_argument(
        "-p", "--position",
        type=int, choices=range(1, 10), metavar="1..9",
        help="Вертикальная позиция текста: 1=верх, 5=центр, 9=низ (высота делится на 10 зон)"
    )
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Ошибка: файл '{args.input}' не найден.")
        sys.exit(1)

    # Выходной файл
    if args.output:
        output = args.output
    else:
        base, ext = os.path.splitext(args.input)
        output = f"{base}_overlay{ext}"

    # Источник текста
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read().strip()
    elif args.text:
        text = args.text.strip()
    else:
        text = TEXT_DEFAULT

    if not text:
        print("Ошибка: текст пустой.")
        sys.exit(1)

    # Размеры видео
    vid_w, vid_h = get_video_size(args.input)
    print(f"Видео: {vid_w}x{vid_h}")

    # Автоперенос
    lines = wrap_text(text, vid_w)
    print(f"Текст разбит на {len(lines)} строк(и):")
    for i, line in enumerate(lines, 1):
        print(f"  {i}: {line}")

    if args.position:
        print(f"Позиция текста: {args.position}/10 высоты экрана")
    else:
        print("Позиция текста: авто (центр верхних 4/5)")

    # ffmpeg
    vf = ",".join(build_filters(lines, vid_w, vid_h, position=args.position))
    cmd = [
        "ffmpeg", "-y",
        "-i", args.input,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        output,
    ]

    print("\nЗапускаю ffmpeg...")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        print(f"\n✅  Готово -> {output}")
    else:
        print("\n❌  ffmpeg завершился с ошибкой.")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
