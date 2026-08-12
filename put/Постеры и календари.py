# -*- coding: utf-8 -*-
import os
import sys
import time
import queue
import threading
from datetime import datetime
from io import BytesIO

import tkinter as tk
from tkinter import filedialog, messagebox

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.scrolled import ScrolledText

from PIL import Image, ImageOps, ImageDraw, ImageFont

try:
    import fitz  # PyMuPDF — только для чтения входных PDF
except Exception:
    fitz = None

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
except Exception:
    canvas = None
    ImageReader = None


TITLE = "Постеры и календари. Макеты для печати"

SUPPORTED_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp", ".pdf")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp")
PDF_EXTS = (".pdf",)

DEFAULT_DPI = 220
JPEG_QUALITY = 92

PAGE_WIDTH_MM = 320.0
PAGE_HEIGHT_MM = 450.0

PAGE_WIDTH_PT = PAGE_WIDTH_MM * 72.0 / 25.4
PAGE_HEIGHT_PT = PAGE_HEIGHT_MM * 72.0 / 25.4

# Эти размеры оставлены для белых листов-разделителей с рамками
LARGE_BOX_W_MM = 310.0
LARGE_BOX_H_MM = 440.0

# Эти размеры оставлены для белых листов-разделителей с рамками A4
A4_DRAW_W_MM = 305.0
A4_DRAW_H_MM = 218.0

CATEGORY_POSTER_A4 = "poster_a4"
CATEGORY_POSTER_SET_A4_4 = "poster_set_a4_4"
CATEGORY_POSTER_SET_A3_3 = "poster_set_a3_3"
CATEGORY_CALENDAR_A3 = "calendar_a3"
CATEGORY_POSTER_A3 = "poster_a3"

CATEGORY_LABELS = {
    CATEGORY_POSTER_A4: "Постеры А4",
    CATEGORY_POSTER_SET_A4_4: "Набор постеров А4 4шт",
    CATEGORY_POSTER_SET_A3_3: "Набор постеров А3 3шт",
    CATEGORY_CALENDAR_A3: "Календари А3",
    CATEGORY_POSTER_A3: "Постеры А3",
}


def fmt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    s = int(round(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def unique_output_path(path: str) -> str:
    if not path.lower().endswith(".pdf"):
        path += ".pdf"

    if not os.path.exists(path):
        return path

    base, ext = os.path.splitext(path)
    i = 2

    while True:
        candidate = f"{base}_{i}{ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


def get_desktop_folder() -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            CSIDL_DESKTOP = 0
            SHGFP_TYPE_CURRENT = 0
            buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOP, None, SHGFP_TYPE_CURRENT, buf)
            return buf.value
        except Exception:
            pass

    return os.path.expanduser("~/Desktop")


def make_output_pdf_path(save_folder: str, names: list[str]) -> str:
    has_posters = False
    has_calendars = False

    for name in names:
        category = get_category_by_name(name)

        if category == CATEGORY_CALENDAR_A3:
            has_calendars = True
        else:
            has_posters = True

    if has_posters and has_calendars:
        prefix = "Постеры и календари"
    elif has_calendars:
        prefix = "Календари"
    else:
        prefix = "Постеры"

    date_str = datetime.now().strftime("%d.%m")
    return unique_output_path(os.path.join(save_folder, f"{prefix} от {date_str}.pdf"))


def normalize_name(s: str) -> str:
    return "".join(str(s).casefold().split())


def compact_name(s: str) -> str:
    return "".join(ch for ch in str(s).casefold() if not ch.isspace())


def mm_to_px(value_mm: float, dpi: int) -> int:
    return max(1, int(round(value_mm / 25.4 * dpi)))


def get_category_by_name(name: str) -> str:
    c = compact_name(name)

    if c.startswith("постер(а4)4шт") or c.startswith("постера44шт"):
        return CATEGORY_POSTER_SET_A4_4

    if c.startswith("постера4"):
        return CATEGORY_POSTER_A4

    if c.startswith("постер3шт"):
        return CATEGORY_POSTER_SET_A3_3

    if c.startswith("календарь"):
        return CATEGORY_CALENDAR_A3

    return CATEGORY_POSTER_A3


def build_file_index(root_dir: str) -> dict[str, str]:
    index: dict[str, str] = {}

    for dirpath, _, files in os.walk(root_dir):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()

            if ext in SUPPORTED_EXTS:
                key = normalize_name(os.path.splitext(filename)[0])
                index.setdefault(key, os.path.join(dirpath, filename))

    return index


def get_system_font(size_px: int):
    font_paths = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size_px)
            except Exception:
                pass

    return ImageFont.load_default()


def fit_font_pil(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int):
    size = max(14, int(max_h * 0.28))

    while size >= 8:
        font = get_system_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]

        if w <= max_w and h <= max_h:
            return font

        size -= 1

    return get_system_font(8)


def image_to_rgb_white(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg

    return img.convert("RGB")


def page_to_jpeg_stream(page_img: Image.Image) -> bytes:
    buf = BytesIO()
    page_img.save(
        buf,
        format="JPEG",
        quality=JPEG_QUALITY,
        optimize=False,
        progressive=False,
        subsampling=0,
    )
    buf.seek(0)
    return buf.getvalue()


def create_white_placeholder_page_stream(dpi: int) -> bytes:
    page_img = create_blank_sra3_page(dpi)
    return page_to_jpeg_stream(page_img)


def create_blank_sra3_page(dpi: int) -> Image.Image:
    page_w_px = mm_to_px(PAGE_WIDTH_MM, dpi)
    page_h_px = mm_to_px(PAGE_HEIGHT_MM, dpi)
    return Image.new("RGB", (page_w_px, page_h_px), "white")


def create_separator_page_stream(category_key: str, dpi: int) -> bytes:
    page_img = create_blank_sra3_page(dpi)
    draw = ImageDraw.Draw(page_img)

    label = CATEGORY_LABELS[category_key]
    line_w = max(2, page_img.width // 450)

    def draw_centered_label(box, text):
        x0, y0, x1, y1 = box
        font = fit_font_pil(draw, text, int((x1 - x0) * 0.86), int((y1 - y0) * 0.30))

        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        tx = int((x0 + x1 - tw) / 2)
        ty = int((y0 + y1 - th) / 2)

        draw.text((tx, ty), text, fill="black", font=font)

    if category_key in (CATEGORY_POSTER_A4, CATEGORY_POSTER_SET_A4_4):
        x = mm_to_px((PAGE_WIDTH_MM - A4_DRAW_W_MM) / 2.0, dpi)
        top_margin = mm_to_px((PAGE_HEIGHT_MM - A4_DRAW_H_MM * 2.0) / 2.0, dpi)

        w = mm_to_px(A4_DRAW_W_MM, dpi)
        h = mm_to_px(A4_DRAW_H_MM, dpi)

        boxes = [
            (x, top_margin, x + w, top_margin + h),
            (x, top_margin + h, x + w, top_margin + h * 2),
        ]

        for box in boxes:
            draw.rectangle(box, outline="black", width=line_w)
            draw_centered_label(box, label)

    else:
        x = mm_to_px((PAGE_WIDTH_MM - LARGE_BOX_W_MM) / 2.0, dpi)
        y = mm_to_px((PAGE_HEIGHT_MM - LARGE_BOX_H_MM) / 2.0, dpi)

        w = mm_to_px(LARGE_BOX_W_MM, dpi)
        h = mm_to_px(LARGE_BOX_H_MM, dpi)

        box = (x, y, x + w, y + h)

        draw.rectangle(box, outline="black", width=line_w)
        draw_centered_label(box, label)

    return page_to_jpeg_stream(page_img)


def create_large_image_page_stream(path: str, dpi: int) -> bytes:
    """
    Постеры А3 и Календари А3:
    фото растягивается ровно на весь SRA3 320×450, без белых полей.
    """
    page_img = create_blank_sra3_page(dpi)

    target_w = page_img.width
    target_h = page_img.height

    if not path or not os.path.exists(path):
        return page_to_jpeg_stream(page_img)

    with Image.open(path) as img:
        img = image_to_rgb_white(img)
        img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        page_img.paste(img, (0, 0))

    return page_to_jpeg_stream(page_img)


def create_poster_a4_page_stream(paths: list[str], dpi: int) -> bytes:
    """
    Постеры А4:
    2 фото на SRA3, каждая половина листа без белых полей.
    Фото поворачивается на 90° вправо.
    Если фото одно — оно остаётся в верхней позиции, низ остаётся пустым.
    """
    page_img = create_blank_sra3_page(dpi)

    page_w = page_img.width
    half_h = page_img.height // 2

    boxes = [
        (0, 0, page_w, half_h),
        (0, half_h, page_w, page_img.height),
    ]

    for i, path in enumerate(paths[:2]):
        x0, y0, x1, y1 = boxes[i]
        target_w = x1 - x0
        target_h = y1 - y0

        if path and os.path.exists(path):
            with Image.open(path) as img:
                img = image_to_rgb_white(img)
                img = img.rotate(-90, expand=True)
                img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                page_img.paste(img, (x0, y0))
        else:
            placeholder = Image.new("RGB", (target_w, target_h), (255, 255, 255))
            page_img.paste(placeholder, (x0, y0))

    return page_to_jpeg_stream(page_img)


def render_pdf_page_to_sra3_stream(pdf_path: str, page_index: int, dpi: int) -> bytes:
    """
    Входные PDF для категорий:
    - Набор постеров А4 4шт
    - Набор постеров А3 3шт

    Рендерятся в картинку и растягиваются ровно на весь SRA3 320×450
    БЕЗ сохранения пропорций и без белых полей.
    """
    if fitz is None:
        raise RuntimeError("Модуль PyMuPDF не установлен. Установи: pip install pymupdf")

    page_img = create_blank_sra3_page(dpi)

    with fitz.open(pdf_path) as doc:
        src_page = doc.load_page(page_index)
        zoom = dpi / 72.0
        pix = src_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)

        src_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        src_img = image_to_rgb_white(src_img)

    # ВАЖНО: растягиваем на весь SRA3 без сохранения пропорций
    src_img = src_img.resize((page_img.width, page_img.height), Image.Resampling.LANCZOS)

    page_img.paste(src_img, (0, 0))

    return page_to_jpeg_stream(page_img)


class PDFGenerator:
    def __init__(self, threads: int = 4, dpi: int = DEFAULT_DPI, add_white_page: bool = True):
        self.threads = max(1, int(threads))
        self.dpi = max(120, min(300, int(dpi)))
        self.add_white_page = bool(add_white_page)

    def create_pdf(
        self,
        root_folder: str,
        names: list[str],
        output_path: str,
        progress_callback=None,
        log_fn=None,
        err_fn=None,
        pause_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
    ):
        if canvas is None or ImageReader is None:
            raise RuntimeError("Модуль reportlab не установлен. Установи: pip install reportlab")

        if fitz is None:
            raise RuntimeError("Модуль PyMuPDF не установлен. Установи: pip install pymupdf")

        output_path = unique_output_path(output_path)
        index = build_file_index(root_folder)

        total = len(names)
        done = 0
        missing: list[str] = []
        page_count = 0

        c = canvas.Canvas(output_path, pagesize=(PAGE_WIDTH_PT, PAGE_HEIGHT_PT))
        c.setPageCompression(True)

        separator_cache: dict[str, bytes] = {}
        image_page_cache: dict[str, bytes] = {}
        poster_a4_page_cache: dict[tuple[str, ...], bytes] = {}
        pdf_page_cache: dict[tuple[str, int], bytes] = {}

        def add_jpeg_page(stream: bytes):
            nonlocal page_count
            bio = BytesIO(stream)
            c.drawImage(ImageReader(bio), 0, 0, width=PAGE_WIDTH_PT, height=PAGE_HEIGHT_PT)
            c.showPage()
            page_count += 1

        def add_separator(category_key: str):
            if not self.add_white_page:
                return

            stream = separator_cache.get(category_key)

            if stream is None:
                stream = create_separator_page_stream(category_key, self.dpi)
                separator_cache[category_key] = stream

            add_jpeg_page(stream)

        def add_large_image_page(file_path: str):
            stream = image_page_cache.get(file_path)

            if stream is None:
                stream = create_large_image_page_stream(file_path, self.dpi)
                image_page_cache[file_path] = stream

            add_jpeg_page(stream)

        def add_poster_a4_page(file_paths: list[str]):
            key = tuple(file_paths)
            stream = poster_a4_page_cache.get(key)

            if stream is None:
                stream = create_poster_a4_page_stream(file_paths, self.dpi)
                poster_a4_page_cache[key] = stream

            add_jpeg_page(stream)

        def add_pdf_file_as_images(file_path: str):
            with fitz.open(file_path) as src_doc:
                src_pages = len(src_doc)

            for page_i in range(src_pages):
                cache_key = (file_path, page_i)
                stream = pdf_page_cache.get(cache_key)

                if stream is None:
                    stream = render_pdf_page_to_sra3_stream(file_path, page_i, self.dpi)
                    pdf_page_cache[cache_key] = stream

                add_jpeg_page(stream)

        items = []

        for idx, orig_name in enumerate(names, start=1):
            file_path = index.get(normalize_name(orig_name))

            items.append(
                {
                    "idx": idx,
                    "name": orig_name,
                    "category": get_category_by_name(orig_name),
                    "file_path": file_path,
                    "ext": os.path.splitext(file_path)[1].lower() if file_path else "",
                }
            )

        i = 0

        try:
            while i < len(items):
                if stop_event and stop_event.is_set():
                    break

                if pause_event:
                    pause_event.wait()

                category = items[i]["category"]
                category_label = CATEGORY_LABELS.get(category, category)

                run = []

                while i < len(items) and items[i]["category"] == category:
                    run.append(items[i])
                    i += 1

                if log_fn:
                    log_fn(f"{category_label}:")

                add_separator(category)

                if category == CATEGORY_POSTER_A4:
                    buffer_paths = []

                    for item in run:
                        if stop_event and stop_event.is_set():
                            break

                        if pause_event:
                            pause_event.wait()

                        try:
                            if not item["file_path"] or item["ext"] not in IMAGE_EXTS:
                                missing.append(item["name"])

                                if err_fn:
                                    err_fn(f"{category_label}:\n{item['name']} - отсутствует или не подходит файл")

                                buffer_paths.append(None)
                            else:
                                buffer_paths.append(item["file_path"])

                                if log_fn:
                                    log_fn(f"{item['name']} - успешно")

                            if len(buffer_paths) == 2:
                                add_poster_a4_page(buffer_paths)
                                buffer_paths = []

                        except Exception as e:
                            missing.append(item["name"])

                            if err_fn:
                                err_fn(f"{category_label}:\n{item['name']} - ошибка: {e}")

                        done += 1

                        if progress_callback:
                            progress_callback(done, total)

                    if buffer_paths:
                        add_poster_a4_page(buffer_paths)

                else:
                    for item in run:
                        if stop_event and stop_event.is_set():
                            break

                        if pause_event:
                            pause_event.wait()

                        try:
                            if category in (CATEGORY_POSTER_SET_A4_4, CATEGORY_POSTER_SET_A3_3):
                                if item["file_path"] and item["ext"] in PDF_EXTS:
                                    add_pdf_file_as_images(item["file_path"])
                                    if log_fn:
                                        log_fn(f"{item['name']} - успешно")
                                else:
                                    missing.append(item["name"])

                                    if err_fn:
                                        err_fn(f"{category_label}:\n{item['name']} - отсутствует или не подходит файл")

                                    add_jpeg_page(create_white_placeholder_page_stream(self.dpi))

                            elif category in (CATEGORY_CALENDAR_A3, CATEGORY_POSTER_A3):
                                if item["file_path"] and item["ext"] in IMAGE_EXTS:
                                    add_large_image_page(item["file_path"])
                                    if log_fn:
                                        log_fn(f"{item['name']} - успешно")
                                else:
                                    missing.append(item["name"])

                                    if err_fn:
                                        err_fn(f"{category_label}:\n{item['name']} - отсутствует или не подходит файл")

                                    add_jpeg_page(create_white_placeholder_page_stream(self.dpi))

                            else:
                                raise RuntimeError("неизвестная категория")

                        except Exception as e:
                            missing.append(item["name"])

                            if err_fn:
                                err_fn(f"{category_label}:\n{item['name']} - ошибка: {e}")

                        done += 1

                        if progress_callback:
                            progress_callback(done, total)

            if page_count == 0:
                raise RuntimeError("В итоговый PDF не добавлено ни одной страницы.")

            c.save()

        except Exception:
            try:
                c.save()
            except Exception:
                pass
            raise

        size_kb = os.path.getsize(output_path) // 1024 if os.path.exists(output_path) else 0

        return output_path, size_kb, missing, page_count


class App:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title(TITLE)
        self.root.geometry("1350x1100")
        self.root.minsize(1100, 680)

        self.style = tb.Style("darkly")

        self.root_folder_var = tk.StringVar()
        self.output_pdf_var = tk.StringVar(value=get_desktop_folder())
        self.threads_var = tk.IntVar(value=min(7, max(2, (os.cpu_count() or 8) - 1)))
        self.dpi_var = tk.IntVar(value=DEFAULT_DPI)
        self.add_white_page_var = tk.BooleanVar(value=True)

        self.pause_event = threading.Event()
        self.pause_event.set()

        self.stop_event = threading.Event()
        self.ui_queue = queue.Queue()

        self.worker_thread: threading.Thread | None = None
        self.run_start_ts: float | None = None

        self._build_ui()
        self._poll_ui_queue()

    def _build_ui(self):
        outer = tb.Frame(self.root, padding=12)
        outer.pack(fill=BOTH, expand=YES)

        paths = tb.Labelframe(outer, text="Пути", padding=10)
        paths.pack(fill=X)

        row1 = tb.Frame(paths)
        row1.pack(fill=X, pady=4)

        tb.Label(row1, text="Папка с материалами:", width=24, anchor=W).pack(side=LEFT)
        tb.Entry(row1, textvariable=self.root_folder_var).pack(side=LEFT, fill=X, expand=YES, padx=8)
        tb.Button(row1, text="Выбрать", bootstyle=PRIMARY, command=self.choose_root_folder).pack(side=LEFT)

        row2 = tb.Frame(paths)
        row2.pack(fill=X, pady=4)

        tb.Label(row2, text="Куда сохранить макеты:", width=24, anchor=W).pack(side=LEFT)
        tb.Entry(row2, textvariable=self.output_pdf_var).pack(side=LEFT, fill=X, expand=YES, padx=8)
        tb.Button(row2, text="Выбрать", bootstyle=PRIMARY, command=self.choose_output_pdf).pack(side=LEFT)

        row3 = tb.Frame(paths)
        row3.pack(fill=X, pady=4)

        tb.Label(row3, text="Потоки:", width=24, anchor=W).pack(side=LEFT)
        tb.Spinbox(row3, from_=1, to=16, textvariable=self.threads_var, width=6).pack(side=LEFT, padx=(0, 14))

        tb.Label(row3, text="DPI картинок:").pack(side=LEFT)
        tb.Spinbox(row3, from_=120, to=300, increment=10, textvariable=self.dpi_var, width=6).pack(side=LEFT, padx=(8, 14))

        tb.Label(row3, text="Больше 7 потоков не дает прироста", foreground="#c9c9c9").pack(side=LEFT)

        row4 = tb.Frame(paths)
        row4.pack(fill=X, pady=(4, 0))

        tb.Label(row4, text="", width=24, anchor=W).pack(side=LEFT)

        self.white_page_switch = tb.Checkbutton(
            row4,
            text="Добавлять белые листы",
            variable=self.add_white_page_var,
            bootstyle="success-round-toggle",
        )
        self.white_page_switch.pack(side=LEFT)

        names_frame = tb.Labelframe(outer, text="Артикулы/Наименования файлов", padding=10)
        names_frame.pack(fill=X, pady=(10, 0))

        self.names_box = ScrolledText(names_frame, height=10, autohide=True)
        self.names_box.pack(fill=X)

        self._bind_names_box_shortcuts()

        btns = tb.Frame(outer)
        btns.pack(fill=X, pady=(10, 8))

        self.b_paste = tb.Button(btns, text="Вставить из буфера", command=self.paste_names, bootstyle=INFO)
        self.b_paste.pack(side=LEFT)

        self.b_start = tb.Button(btns, text="Старт", command=self.start, bootstyle=SUCCESS)
        self.b_start.pack(side=LEFT, padx=8)

        self.b_pause = tb.Button(btns, text="Пауза", command=self.pause, bootstyle=WARNING, state=DISABLED)
        self.b_pause.pack(side=LEFT)

        self.b_resume = tb.Button(btns, text="Продолжить", command=self.resume, bootstyle=PRIMARY, state=DISABLED)
        self.b_resume.pack(side=LEFT, padx=8)

        self.b_stop = tb.Button(btns, text="Стоп", command=self.stop, bootstyle=DANGER, state=DISABLED)
        self.b_stop.pack(side=LEFT)

        self.b_clear = tb.Button(btns, text="Очистить артикулы", command=self.clear_articles, bootstyle=SECONDARY)
        self.b_clear.pack(side=LEFT, padx=8)

        self.b_theme = tb.Button(btns, text="Сменить тему", command=self.toggle_theme, bootstyle=INFO)
        self.b_theme.pack(side=LEFT)

        progress_frame = tb.Labelframe(outer, text="Прогресс", padding=10)
        progress_frame.pack(fill=X)

        self.progress = tb.Progressbar(progress_frame, maximum=1, mode="determinate", bootstyle="success-striped")
        self.progress.pack(fill=X)

        self.lbl_progress = tb.Label(progress_frame, text="0 / 0 тем (0.0%)")
        self.lbl_progress.pack(anchor=W, pady=(6, 0))

        self.lbl_eta = tb.Label(progress_frame, text="Осталось: --:--  |  Прошло: --:--", foreground="#c9c9c9")
        self.lbl_eta.pack(anchor=W, pady=(4, 0))

        logs_wrap = tb.Frame(outer)
        logs_wrap.pack(fill=BOTH, expand=YES, pady=(10, 0))

        logs_wrap.columnconfigure(0, weight=1)
        logs_wrap.columnconfigure(1, weight=1)
        logs_wrap.rowconfigure(0, weight=1)

        left = tb.Labelframe(logs_wrap, text="Логи", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        right = tb.Labelframe(logs_wrap, text="Ошибки / предупреждения", padding=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self.log_box = ScrolledText(left, height=16, autohide=True)
        self.log_box.pack(fill=BOTH, expand=YES)

        self.err_box = ScrolledText(right, height=16, autohide=True)
        self.err_box.pack(fill=BOTH, expand=YES)

    def _bind_names_box_shortcuts(self):
        widgets = [self.names_box]
        inner_text_widget = getattr(self.names_box, "text", None)

        if inner_text_widget is not None:
            widgets.append(inner_text_widget)

        for widget in widgets:
            try:
                widget.bind("<Control-KeyPress>", self._handle_names_box_control_shortcut)
            except Exception:
                pass

    def _handle_names_box_control_shortcut(self, event):
        keycode = getattr(event, "keycode", None)
        keysym = str(getattr(event, "keysym", "")).lower()
        char = str(getattr(event, "char", "")).lower()

        is_paste = (
            keycode == 86
            or keysym in ("v", "м")
            or char in ("v", "м")
        )

        is_copy = (
            keycode == 67
            or keysym in ("c", "с")
            or char in ("c", "с")
        )

        if is_paste:
            return self._paste_into_names_box(event)

        if is_copy:
            return self._copy_from_names_box(event)

        return None

    def _paste_into_names_box(self, event=None):
        try:
            text = self.root.clipboard_get()
        except Exception:
            return "break"

        widget = event.widget if event is not None else self.names_box

        try:
            widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        except Exception:
            pass

        try:
            widget.insert("insert", text)
            widget.see("insert")
        except Exception:
            self.names_box.insert("insert", text)
            self.names_box.see("insert")

        return "break"

    def _copy_from_names_box(self, event=None):
        widget = event.widget if event is not None else self.names_box

        try:
            selected_text = widget.get("sel.first", "sel.last")
        except Exception:
            return "break"

        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(selected_text)
        except Exception:
            pass

        return "break"

    def choose_root_folder(self):
        folder = filedialog.askdirectory(title="Выбери папку с файлами")

        if folder:
            self.root_folder_var.set(folder)

    def choose_output_pdf(self):
        folder = filedialog.askdirectory(title="Выбери папку для сохранения PDF")

        if folder:
            self.output_pdf_var.set(folder)

    def paste_names(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            messagebox.showerror("Ошибка", "Не удалось получить данные из буфера обмена.")
            return

        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

        if not text:
            return

        current_text = self.names_box.get("1.0", "end-1c")

        if current_text and not current_text.endswith("\n"):
            self.names_box.insert("end", "\n")

        self.names_box.insert("end", text)
        self.names_box.see("end")

    def clear_articles(self):
        self.names_box.delete("1.0", "end")

    def pause(self):
        self.pause_event.clear()
        self.b_pause.config(state=DISABLED)
        self.b_resume.config(state=NORMAL)

    def resume(self):
        self.pause_event.set()
        self.b_pause.config(state=NORMAL)
        self.b_resume.config(state=DISABLED)

    def stop(self):
        self.stop_event.set()
        self.pause_event.set()
        self.b_stop.config(state=DISABLED)

    def clear_logs(self):
        self._clear_text(self.log_box)
        self._clear_text(self.err_box)

    def _log(self, msg: str):
        self.ui_queue.put(("log", msg))

    def _err(self, msg: str):
        self.ui_queue.put(("err", msg))

    def _set_progress(self, done: int, total: int):
        self.ui_queue.put(("prog", done, total))

    def _finish_ui(self, msg: str | None = None, out_path: str | None = None):
        self.ui_queue.put(("finish", msg, out_path))

    def _poll_ui_queue(self):
        processed = 0

        try:
            while processed < 25:
                item = self.ui_queue.get_nowait()
                processed += 1
                kind = item[0]

                if kind == "log":
                    self._append_text(self.log_box, item[1])

                elif kind == "err":
                    self._append_text(self.err_box, item[1])

                elif kind == "prog":
                    done, total = item[1], item[2]

                    self.progress["maximum"] = max(1, total)
                    self.progress["value"] = done

                    pct = (done / total * 100.0) if total else 0.0
                    self.lbl_progress.config(text=f"{done} / {total} тем ({pct:.1f}%)")

                    elapsed = time.time() - self.run_start_ts if self.run_start_ts else 0

                    if done > 0 and total > 0:
                        avg = elapsed / done
                        remaining = (total - done) * avg
                        self.lbl_eta.config(text=f"Осталось: {fmt_time(remaining)}  |  Прошло: {fmt_time(elapsed)}")
                    else:
                        self.lbl_eta.config(text=f"Осталось: --:--  |  Прошло: {fmt_time(elapsed)}")

                elif kind == "finish":
                    self.b_start.config(state=NORMAL)
                    self.b_paste.config(state=NORMAL)
                    self.b_pause.config(state=DISABLED)
                    self.b_resume.config(state=DISABLED)
                    self.b_stop.config(state=DISABLED)

                    msg, _out_path = item[1], item[2]

                    if msg:
                        messagebox.showinfo("Готово", msg)

        except queue.Empty:
            pass

        self.root.after(60, self._poll_ui_queue)

    @staticmethod
    def _append_text(widget: ScrolledText, text: str):
        widget.insert("end", text + "\n")
        widget.see("end")

    @staticmethod
    def _clear_text(widget: ScrolledText):
        widget.delete("1.0", "end")

    def start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        root_folder = self.root_folder_var.get().strip()
        save_folder = self.output_pdf_var.get().strip()

        names = [n.strip() for n in self.names_box.get("1.0", "end").splitlines() if n.strip()]

        if not root_folder or not os.path.isdir(root_folder):
            messagebox.showerror("Ошибка", "Укажите корректную папку с материалами.")
            return

        if not save_folder or not os.path.isdir(save_folder):
            messagebox.showerror("Ошибка", "Выбери корректную папку для сохранения!")
            return

        if not names:
            messagebox.showerror("Ошибка", "Вставь наименования файлов!")
            return

        out_pdf = make_output_pdf_path(save_folder, names)

        try:
            threads = max(1, min(16, int(self.threads_var.get())))
        except Exception:
            threads = 4

        try:
            dpi = max(120, min(300, int(self.dpi_var.get())))
        except Exception:
            dpi = DEFAULT_DPI

        self.clear_logs()
        self.stop_event.clear()
        self.pause_event.set()

        self.b_start.config(state=DISABLED)
        self.b_paste.config(state=DISABLED)
        self.b_pause.config(state=NORMAL)
        self.b_resume.config(state=DISABLED)
        self.b_stop.config(state=NORMAL)

        self.run_start_ts = time.time()
        self._set_progress(0, len(names))
        self._log("Работа началась...")

        self.worker_thread = threading.Thread(
            target=self.process_generate,
            args=(root_folder, out_pdf, names, threads, dpi, self.add_white_page_var.get()),
            daemon=True,
        )
        self.worker_thread.start()

    def process_generate(
        self,
        root_folder: str,
        out_pdf: str,
        names: list[str],
        threads: int,
        dpi: int,
        add_white_page: bool,
    ):
        t0 = time.time()
        created_path = None

        try:
            gen = PDFGenerator(threads=threads, dpi=dpi, add_white_page=add_white_page)

            created_path, size, missing, page_count = gen.create_pdf(
                root_folder,
                names,
                out_pdf,
                progress_callback=lambda d, t: self._set_progress(d, t),
                log_fn=self._log,
                err_fn=self._err,
                pause_event=self.pause_event,
                stop_event=self.stop_event,
            )

            dt = time.time() - t0

            extra = f"\nНе найдено / с ошибкой: {len(missing)}" if missing else ""

            if self.stop_event.is_set():
                msg = (
                    f"Остановлено.\nPDF сохранён частично:\n{created_path}\n\n"
                    f"Время: {fmt_time(dt)}\nPDF листов: {page_count}{extra}"
                )
            else:
                msg = (
                    f"PDF создан:\n{created_path}\n\n"
                    f"Размер: {size} КБ\nВремя: {fmt_time(dt)}\nPDF листов: {page_count}{extra}"
                )

            self._log(f"Готово. Время: {fmt_time(dt)}. PDF листов: {page_count}")
            self._finish_ui(msg, created_path)

        except Exception as e:
            self._err(f"Критическая ошибка: {e}")
            self._finish_ui(None, created_path)

    def toggle_theme(self):
        cur = self.style.theme.name
        self.style.theme_use("cosmo" if cur == "darkly" else "darkly")


def main():
    root = tb.Window(themename="darkly")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()