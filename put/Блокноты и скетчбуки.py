# -*- coding: utf-8 -*-

import os
import sys
import time
import math
import queue
import threading
from dataclasses import dataclass
from io import BytesIO
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import tkinter as tk
from tkinter import filedialog, messagebox

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText

from PIL import Image, ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


TITLE = "Блокноты и скетчбуки. Макеты для печати"

IMG_EXTS = (
    ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".tif",
)

PAGE_WIDTH_MM = 320
PAGE_HEIGHT_MM = 450
PAGE_WIDTH = PAGE_WIDTH_MM * mm
PAGE_HEIGHT = PAGE_HEIGHT_MM * mm

DEFAULT_DPI = 220
JPEG_QUALITY = 92

PDF_FONT_NAME = "Helvetica"
_PDF_FONT_READY = False


def get_pdf_font_name() -> str:
    global PDF_FONT_NAME, _PDF_FONT_READY

    if _PDF_FONT_READY:
        return PDF_FONT_NAME

    candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\Arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    for font_path in candidates:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont("AppCyrillicFont", font_path))
                PDF_FONT_NAME = "AppCyrillicFont"
                break
            except Exception:
                pass

    _PDF_FONT_READY = True
    return PDF_FONT_NAME


@dataclass
class PreparedImage:
    reader: ImageReader
    buffer: BytesIO


@dataclass
class ThemeItem:
    theme_name: str
    path: str | None


@dataclass
class LayoutSpec:
    key: str
    photo_w_mm: float
    photo_h_mm: float
    rotate_left: bool
    themes_per_page: int
    duplicate_mode: str
    description: str

    @property
    def photo_w_pt(self) -> float:
        return self.photo_w_mm * mm

    @property
    def photo_h_pt(self) -> float:
        return self.photo_h_mm * mm


@dataclass
class PageSpec:
    category_key: str
    category_label: str
    layout: LayoutSpec
    items: list[ThemeItem]


CATEGORY_LABELS = {
    "notebook_a4": "Блокноты А4",
    "sketchbook_a4": "Скетчбуки А4",
    "notebook_a5": "Блокноты А5",
    "sketchbook_a5": "Скетчбуки А5",
    "notebook_a6": "Блокноты А6",
    "sketchbook_a6": "Скетчбуки А6",
    "other_a5": "Прочие А5",
}


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"

    return f"{m:02d}:{s:02d}"


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

            buffer = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buffer)
            return buffer.value
        except Exception:
            pass

    return os.path.expanduser("~/Desktop")


def normalize_name(value: str) -> str:
    return "".join(str(value).casefold().split())


def get_category_key(theme_name: str) -> str:
    normalized = normalize_name(theme_name)

    if normalized.startswith("блокнота4"):
        return "notebook_a4"

    if normalized.startswith("скетчбука4"):
        return "sketchbook_a4"

    if normalized.startswith("блокнота6"):
        return "notebook_a6"

    if normalized.startswith("скетчбука6"):
        return "sketchbook_a6"

    if normalized.startswith("блокнота5") or normalized.startswith("блокнот"):
        return "notebook_a5"

    if normalized.startswith("скетчбука5") or normalized.startswith("скетчбук"):
        return "sketchbook_a5"

    return "other_a5"


def make_output_pdf_path(save_folder: str, theme_names: list[str]) -> str:
    has_notebooks = False
    has_sketchbooks = False

    for name in theme_names:
        key = get_category_key(name)

        if key.startswith("notebook_"):
            has_notebooks = True
        elif key.startswith("sketchbook_"):
            has_sketchbooks = True

    if has_notebooks and has_sketchbooks:
        prefix = "Блокноты и скетчбуки"
    elif has_sketchbooks:
        prefix = "Скетчбуки"
    elif has_notebooks:
        prefix = "Блокноты"
    else:
        prefix = "Блокноты и скетчбуки"

    date_string = datetime.now().strftime("%d.%m")

    return unique_output_path(
        os.path.join(save_folder, f"{prefix} от {date_string}.pdf")
    )


def build_file_index(root_dir: str) -> dict[str, str]:
    index: dict[str, str] = {}

    for directory_path, _, filenames in os.walk(root_dir):
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()

            if ext not in IMG_EXTS:
                continue

            stem = os.path.splitext(filename)[0]
            key = normalize_name(stem)

            index.setdefault(
                key,
                os.path.join(directory_path, filename),
            )

    return index


def get_category_label(category_key: str) -> str:
    return CATEGORY_LABELS.get(category_key, category_key)


def match_files(
    root_dir: str,
    theme_names: list[str],
    err_fn=None,
) -> tuple[list[ThemeItem], list[str]]:
    index = build_file_index(root_dir)

    items: list[ThemeItem] = []
    missing: list[str] = []

    for theme in theme_names:
        path = index.get(normalize_name(theme))
        category_label = get_category_label(get_category_key(theme))

        if path:
            items.append(ThemeItem(theme_name=theme, path=path))
        else:
            items.append(ThemeItem(theme_name=theme, path=None))
            missing.append(theme)

            if err_fn:
                err_fn(f"{category_label}:\n{theme} - не найдено")

    return items, missing


def points_to_pixels(value_points: float, dpi: int) -> int:
    return max(1, int(round(float(value_points) / 72.0 * dpi)))


def prepare_image_for_pdf(
    path: str,
    width_points: float,
    height_points: float,
    dpi: int,
    rotate_left: bool = False,
) -> PreparedImage:
    target_width = points_to_pixels(width_points, dpi)
    target_height = points_to_pixels(height_points, dpi)

    with Image.open(path) as image:
        try:
            image.draft("RGB", (target_width, target_height))
        except Exception:
            pass

        image = ImageOps.exif_transpose(image)

        if rotate_left:
            image = image.rotate(90, expand=True)

        if (
            image.mode in ("RGBA", "LA")
            or (image.mode == "P" and "transparency" in image.info)
        ):
            rgba = image.convert("RGBA")

            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))

            image = background
        else:
            image = image.convert("RGB")

        if image.size != (target_width, target_height):
            image = image.resize(
                (target_width, target_height),
                Image.Resampling.LANCZOS,
            )

        buffer = BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=JPEG_QUALITY,
            optimize=False,
            progressive=False,
            subsampling=0,
        )

        buffer.seek(0)

        return PreparedImage(
            reader=ImageReader(buffer),
            buffer=buffer,
        )


def prepare_batch(
    paths: list[str],
    width_points: float,
    height_points: float,
    dpi: int,
    rotate_left: bool,
    workers: int,
    err_fn=None,
) -> dict[str, PreparedImage | None]:
    clean_paths = [path for path in paths if path]

    if not clean_paths:
        return {}

    unique_paths = list(dict.fromkeys(clean_paths))
    workers = max(1, min(int(workers), len(unique_paths)))

    def prepare_one(path: str):
        try:
            return (
                path,
                prepare_image_for_pdf(
                    path,
                    width_points,
                    height_points,
                    dpi,
                    rotate_left=rotate_left,
                ),
            )
        except Exception as error:
            if err_fn:
                err_fn(f"Ошибка подготовки изображения {path}: {error}")

            return path, None

    if workers == 1:
        return dict(prepare_one(path) for path in unique_paths)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return dict(executor.map(prepare_one, unique_paths))


def get_layout_for_category(category_key: str) -> LayoutSpec:
    if category_key in ("notebook_a4", "sketchbook_a4"):
        return LayoutSpec(
            key="A4",
            photo_w_mm=308,
            photo_h_mm=221,
            rotate_left=True,
            themes_per_page=1,
            duplicate_mode="vertical",
            description=(
                "A4: лист 320×450 вертикальный, фото повернуто на 90°, "
                "итоговый размер после поворота 308×221 мм, 2 дубля один над другим"
            ),
        )

    if category_key in ("notebook_a6", "sketchbook_a6"):
        return LayoutSpec(
            key="A6",
            photo_w_mm=113,
            photo_h_mm=156,
            rotate_left=False,
            themes_per_page=2,
            duplicate_mode="horizontal",
            description="A6: 2 темы на лист, каждая дублируется рядом",
        )

    return LayoutSpec(
        key="A5",
        photo_w_mm=156,
        photo_h_mm=218,
        rotate_left=False,
        themes_per_page=2,
        duplicate_mode="horizontal",
        description="A5: 2 темы на лист, каждая дублируется рядом",
    )


def get_custom_layout(width_mm: float, height_mm: float) -> LayoutSpec:
    pair_width_pt = 2.0 * width_mm * mm
    height_pt = height_mm * mm

    if pair_width_pt > PAGE_WIDTH or height_pt > PAGE_HEIGHT:
        raise ValueError(
            f"Размер {width_mm}×{height_mm} мм не помещается на вертикальный лист 320×450 мм "
            f"для пары дублей рядом без отступов."
        )

    themes_per_page = max(1, int(math.floor(PAGE_HEIGHT / height_pt)))

    return LayoutSpec(
        key="custom",
        photo_w_mm=width_mm,
        photo_h_mm=height_mm,
        rotate_left=False,
        themes_per_page=themes_per_page,
        duplicate_mode="horizontal",
        description=f"Пользовательский размер: {width_mm}×{height_mm} мм, тем на лист: {themes_per_page}",
    )


def build_pages(
    items: list[ThemeItem],
    custom_layout: LayoutSpec | None = None,
) -> list[PageSpec]:
    pages: list[PageSpec] = []

    index = 0
    total = len(items)

    while index < total:
        category_key = get_category_key(items[index].theme_name)
        category_label = get_category_label(category_key)

        layout = (
            custom_layout
            if custom_layout is not None
            else get_layout_for_category(category_key)
        )

        chunk = [items[index]]
        index += 1

        while index < total and len(chunk) < layout.themes_per_page:
            next_category_key = get_category_key(items[index].theme_name)

            if next_category_key != category_key:
                break

            chunk.append(items[index])
            index += 1

        pages.append(
            PageSpec(
                category_key=category_key,
                category_label=category_label,
                layout=layout,
                items=chunk,
            )
        )

    return pages


class PDFGenerator:
    def __init__(
        self,
        threads: int = 4,
        dpi: int = DEFAULT_DPI,
        custom_width_mm: float | None = None,
        custom_height_mm: float | None = None,
        add_white_page: bool = True,
    ):
        self.threads = max(1, int(threads))
        self.dpi = max(120, min(300, int(dpi)))
        self.custom_width_mm = custom_width_mm
        self.custom_height_mm = custom_height_mm
        self.add_white_page = bool(add_white_page)

    @staticmethod
    def _calc_block_origin(layout: LayoutSpec) -> tuple[float, float, float, float]:
        photo_width = layout.photo_w_pt
        photo_height = layout.photo_h_pt

        if layout.duplicate_mode == "vertical":
            block_width = photo_width
            block_height = 2 * photo_height
        else:
            block_width = 2 * photo_width
            block_height = layout.themes_per_page * photo_height

        x_left = (PAGE_WIDTH - block_width) / 2
        y_bottom = (PAGE_HEIGHT - block_height) / 2

        return photo_width, photo_height, x_left, y_bottom

    @staticmethod
    def _fit_font_size(
        pdf_canvas: canvas.Canvas,
        text: str,
        max_width: float,
        max_height: float,
    ) -> float:
        font_name = get_pdf_font_name()

        size = min(42.0, max(16.0, max_height * 0.20))

        while size > 8:
            width = pdf_canvas.stringWidth(text, font_name, size)

            if width <= max_width:
                return size

            size -= 0.5

        return 8.0

    def _draw_category_separator_page(
        self,
        pdf_canvas: canvas.Canvas,
        page: PageSpec,
    ):
        layout = page.layout
        label = page.category_label

        photo_width, photo_height, x_left, y_bottom = self._calc_block_origin(layout)

        pdf_canvas.setStrokeColorRGB(0, 0, 0)
        pdf_canvas.setFillColorRGB(1, 1, 1)
        pdf_canvas.setLineWidth(1.2)

        if layout.duplicate_mode == "vertical":
            positions = [
                (x_left, y_bottom + photo_height),
                (x_left, y_bottom),
            ]
        else:
            positions = []

            for row_index in range(layout.themes_per_page):
                y = y_bottom + (layout.themes_per_page - 1 - row_index) * photo_height
                positions.append((x_left, y))
                positions.append((x_left + photo_width, y))

        font_name = get_pdf_font_name()

        for x, y in positions:
            pdf_canvas.rect(x, y, photo_width, photo_height, stroke=1, fill=0)

            font_size = self._fit_font_size(
                pdf_canvas,
                label,
                photo_width * 0.82,
                photo_height * 0.22,
            )

            pdf_canvas.setFont(font_name, font_size)
            pdf_canvas.setFillColorRGB(0, 0, 0)

            pdf_canvas.drawCentredString(
                x + photo_width / 2,
                y + photo_height / 2 - font_size / 3,
                label,
            )

    def create_pdf(
        self,
        items: list[ThemeItem],
        output_path: str,
        progress_callback=None,
        log_fn=None,
        err_fn=None,
        pause_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
    ):
        output_path = unique_output_path(output_path)

        if not items:
            if err_fn:
                err_fn("Нет данных для PDF.")

            return output_path, 0, 0

        custom_layout = None

        if self.custom_width_mm is not None and self.custom_height_mm is not None:
            custom_layout = get_custom_layout(
                self.custom_width_mm,
                self.custom_height_mm,
            )

        pages = build_pages(items, custom_layout=custom_layout)

        pdf_canvas = canvas.Canvas(
            output_path,
            pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        )

        pdf_canvas.setPageCompression(True)

        total_themes = len(items)
        done_themes = 0
        pdf_pages_count = 0
        first_output_page = True
        prev_category_key = None

        try:
            for page in pages:
                if stop_event and stop_event.is_set():
                    break

                if pause_event:
                    pause_event.wait()

                category_changed = page.category_key != prev_category_key

                if category_changed and log_fn:
                    log_fn(f"{page.category_label}:")

                if self.add_white_page and category_changed:
                    if not first_output_page:
                        pdf_canvas.showPage()

                    self._draw_category_separator_page(pdf_canvas, page)
                    pdf_pages_count += 1
                    first_output_page = False
                    pdf_canvas.showPage()

                elif not first_output_page:
                    pdf_canvas.showPage()

                layout = page.layout
                photo_width, photo_height, x_left, y_bottom = self._calc_block_origin(layout)

                if err_fn:
                    def category_err(message, category_label=page.category_label):
                        err_fn(f"{category_label}:\n{message}")
                else:
                    category_err = None

                prepared_map = prepare_batch(
                    [item.path for item in page.items if item.path],
                    photo_width,
                    photo_height,
                    self.dpi,
                    rotate_left=layout.rotate_left,
                    workers=self.threads,
                    err_fn=category_err,
                )

                for row_index, item in enumerate(page.items):
                    if stop_event and stop_event.is_set():
                        break

                    if pause_event:
                        pause_event.wait()

                    if layout.duplicate_mode == "vertical":
                        draw_width = photo_width
                        draw_height = photo_height

                        positions = [
                            (x_left, y_bottom + photo_height),
                            (x_left, y_bottom),
                        ]

                    else:
                        draw_width = photo_width
                        draw_height = photo_height

                        y = y_bottom + (layout.themes_per_page - 1 - row_index) * photo_height

                        positions = [
                            (x_left, y),
                            (x_left + photo_width, y),
                        ]

                    if item.path:
                        for x, y in positions:
                            try:
                                prepared = prepared_map.get(item.path)

                                if prepared is not None:
                                    pdf_canvas.drawImage(
                                        prepared.reader,
                                        x,
                                        y,
                                        draw_width,
                                        draw_height,
                                        preserveAspectRatio=False,
                                        mask=None,
                                    )

                            except Exception as error:
                                if err_fn:
                                    err_fn(
                                        f"{page.category_label}:\n"
                                        f"{item.theme_name} - ошибка вставки: {error}"
                                    )

                        if log_fn:
                            log_fn(f"{item.theme_name} - успешно")

                    done_themes += 1

                    if progress_callback:
                        progress_callback(done_themes, total_themes)

                pdf_pages_count += 1
                first_output_page = False
                prev_category_key = page.category_key

        finally:
            pdf_canvas.save()

        size_kb = os.path.getsize(output_path) // 1024 if os.path.exists(output_path) else 0

        return output_path, size_kb, pdf_pages_count


class App:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title(TITLE)
        self.root.geometry("1350x1100")
        self.root.minsize(1100, 700)
        self.style = tb.Style("darkly")

        self.root_folder_var = tk.StringVar()
        self.output_pdf_var = tk.StringVar(value=get_desktop_folder())
        self.threads_var = tk.IntVar(value=min(7, max(2, (os.cpu_count() or 8) - 1)))
        self.dpi_var = tk.IntVar(value=DEFAULT_DPI)
        self.custom_width_var = tk.StringVar()
        self.custom_height_var = tk.StringVar()
        self.add_white_page_var = tk.BooleanVar(value=True)
        self.add_separator_var = self.add_white_page_var

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

        tb.Label(
            row1,
            text="Папка с материалами:",
            width=24,
            anchor=W,
        ).pack(side=LEFT)

        tb.Entry(row1, textvariable=self.root_folder_var).pack(
            side=LEFT,
            fill=X,
            expand=YES,
            padx=8,
        )

        tb.Button(
            row1,
            text="Выбрать",
            bootstyle=PRIMARY,
            command=self.choose_root_folder,
        ).pack(side=LEFT)

        row2 = tb.Frame(paths)
        row2.pack(fill=X, pady=4)

        tb.Label(
            row2,
            text="Куда сохранить макеты:",
            width=24,
            anchor=W,
        ).pack(side=LEFT)

        tb.Entry(row2, textvariable=self.output_pdf_var).pack(
            side=LEFT,
            fill=X,
            expand=YES,
            padx=8,
        )

        tb.Button(
            row2,
            text="Выбрать",
            bootstyle=PRIMARY,
            command=self.choose_output_pdf,
        ).pack(side=LEFT)

        row3 = tb.Frame(paths)
        row3.pack(fill=X, pady=4)

        tb.Label(
            row3,
            text="Потоки:",
            width=24,
            anchor=W,
        ).pack(side=LEFT)

        tb.Spinbox(
            row3,
            from_=1,
            to=16,
            textvariable=self.threads_var,
            width=6,
        ).pack(side=LEFT, padx=(0, 14))

        tb.Label(row3, text="DPI картинок:").pack(side=LEFT)

        tb.Spinbox(
            row3,
            from_=120,
            to=300,
            increment=10,
            textvariable=self.dpi_var,
            width=6,
        ).pack(side=LEFT, padx=(8, 14))

        tb.Label(
            row3,
            text="Больше 7 потоков не дает прироста",
            foreground="#c9c9c9",
        ).pack(side=LEFT)

        row4 = tb.Frame(paths)
        row4.pack(fill=X, pady=4)

        tb.Label(
            row4,
            text="Ширина / высота (мм):",
            width=24,
            anchor=W,
        ).pack(side=LEFT)

        tb.Entry(row4, textvariable=self.custom_width_var, width=8).pack(side=LEFT)

        tb.Label(row4, text="×").pack(side=LEFT, padx=6)

        tb.Entry(row4, textvariable=self.custom_height_var, width=8).pack(side=LEFT)

        tb.Label(
            row4,
            text="Указывать, если нужны другие размеры",
            foreground="#c9c9c9",
        ).pack(side=LEFT, padx=(12, 0))

        row5 = tb.Frame(paths)
        row5.pack(fill=X, pady=(4, 0))

        tb.Label(row5, text="", width=24, anchor=W).pack(side=LEFT)

        self.white_page_switch = tb.Checkbutton(
            row5,
            text="Добавлять белые листы",
            variable=self.add_white_page_var,
            bootstyle="success-round-toggle",
        )
        self.white_page_switch.pack(side=LEFT)

        names_frame = tb.Labelframe(
            outer,
            text="Артикулы/Наименования тем",
            padding=10,
        )
        names_frame.pack(fill=X, pady=(10, 0))

        self.names_box = ScrolledText(names_frame, height=8, autohide=True)
        self.names_box.pack(fill=X)

        self._bind_names_box_shortcuts()

        buttons = tb.Frame(outer)
        buttons.pack(fill=X, pady=(10, 8))

        self.b_paste = tb.Button(
            buttons,
            text="Вставить из буфера",
            command=self.paste_names,
            bootstyle=INFO,
        )
        self.b_paste.pack(side=LEFT)

        self.b_start = tb.Button(
            buttons,
            text="Старт",
            command=self.start,
            bootstyle=SUCCESS,
        )
        self.b_start.pack(side=LEFT, padx=8)

        self.b_pause = tb.Button(
            buttons,
            text="Пауза",
            command=self.pause,
            bootstyle=WARNING,
            state=DISABLED,
        )
        self.b_pause.pack(side=LEFT)

        self.b_resume = tb.Button(
            buttons,
            text="Продолжить",
            command=self.resume,
            bootstyle=PRIMARY,
            state=DISABLED,
        )
        self.b_resume.pack(side=LEFT, padx=8)

        self.b_stop = tb.Button(
            buttons,
            text="Стоп",
            command=self.stop,
            bootstyle=DANGER,
            state=DISABLED,
        )
        self.b_stop.pack(side=LEFT)

        self.b_clear = tb.Button(
            buttons,
            text="Очистить артикулы",
            command=self.clear_articles,
            bootstyle=SECONDARY,
        )
        self.b_clear.pack(side=LEFT, padx=(16, 8))

        self.b_theme = tb.Button(
            buttons,
            text="Сменить тему",
            command=self.toggle_theme,
            bootstyle=INFO,
        )
        self.b_theme.pack(side=LEFT)

        progress_frame = tb.Labelframe(outer, text="Прогресс", padding=10)
        progress_frame.pack(fill=X)

        self.progress = tb.Progressbar(
            progress_frame,
            maximum=1,
            mode="determinate",
            bootstyle="success-striped",
        )
        self.progress.pack(fill=X)

        self.lbl_progress = tb.Label(progress_frame, text="0 / 0 тем (0.0%)")
        self.lbl_progress.pack(anchor=W, pady=(6, 0))

        self.lbl_eta = tb.Label(
            progress_frame,
            text="Осталось: --:--  |  Прошло: --:--",
            foreground="#c9c9c9",
        )
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
                widget.bind(
                    "<Control-KeyPress>",
                    self._handle_names_box_control_shortcut,
                )
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
            messagebox.showerror(
                "Ошибка",
                "Не удалось получить данные из буфера обмена.",
            )
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
        self._log("Пауза.")

    def resume(self):
        self.pause_event.set()
        self.b_pause.config(state=NORMAL)
        self.b_resume.config(state=DISABLED)
        self._log("Продолжили.")

    def stop(self):
        self.stop_event.set()
        self.pause_event.set()
        self.b_stop.config(state=DISABLED)
        self._log("Остановка...")

    def clear_logs(self):
        self._clear_text(self.log_box)
        self._clear_text(self.err_box)

    def _log(self, message: str):
        self.ui_queue.put(("log", message))

    def _err(self, message: str):
        self.ui_queue.put(("err", message))

    def _set_progress(self, done: int, total: int):
        self.ui_queue.put(("prog", done, total))

    def _finish_ui(self, message: str | None = None, output_path: str | None = None):
        self.ui_queue.put(("finish", message, output_path))

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
                    done = item[1]
                    total = item[2]

                    self.progress["maximum"] = max(1, total)
                    self.progress["value"] = done

                    percent = done / total * 100.0 if total else 0.0

                    self.lbl_progress.config(
                        text=f"{done} / {total} тем ({percent:.1f}%)"
                    )

                    elapsed = time.time() - self.run_start_ts if self.run_start_ts else 0

                    if done > 0 and total > 0:
                        average = elapsed / done
                        remaining = (total - done) * average

                        self.lbl_eta.config(
                            text=f"Осталось: {fmt_time(remaining)}  |  Прошло: {fmt_time(elapsed)}"
                        )
                    else:
                        self.lbl_eta.config(
                            text=f"Осталось: --:--  |  Прошло: {fmt_time(elapsed)}"
                        )

                elif kind == "finish":
                    self.b_start.config(state=NORMAL)
                    self.b_paste.config(state=NORMAL)
                    self.b_pause.config(state=DISABLED)
                    self.b_resume.config(state=DISABLED)
                    self.b_stop.config(state=DISABLED)

                    message = item[1]

                    if message:
                        messagebox.showinfo("Готово", message)

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

        names = [
            name.strip()
            for name in self.names_box.get("1.0", "end").splitlines()
            if name.strip()
        ]

        if not root_folder or not os.path.isdir(root_folder):
            messagebox.showerror("Ошибка", "Укажите корректную папку с материалами.")
            return

        if not save_folder or not os.path.isdir(save_folder):
            messagebox.showerror("Ошибка", "Выбери корректную папку для сохранения!")
            return

        if not names:
            messagebox.showerror("Ошибка", "Вставь наименования тем!")
            return

        output_pdf = make_output_pdf_path(save_folder, names)

        try:
            threads = max(1, min(16, int(self.threads_var.get())))
        except Exception:
            threads = 4

        try:
            dpi = max(120, min(300, int(self.dpi_var.get())))
        except Exception:
            dpi = DEFAULT_DPI

        custom_width_text = self.custom_width_var.get().strip().replace(",", ".")
        custom_height_text = self.custom_height_var.get().strip().replace(",", ".")

        custom_width = None
        custom_height = None

        if custom_width_text or custom_height_text:
            if not (custom_width_text and custom_height_text):
                messagebox.showerror(
                    "Ошибка",
                    "Если задаешь пользовательский размер, укажи и ширину, и высоту.",
                )
                return

            try:
                custom_width = float(custom_width_text)
                custom_height = float(custom_height_text)
            except Exception:
                messagebox.showerror("Ошибка", "Ширина и высота должны быть числами.")
                return

            if custom_width <= 0 or custom_height <= 0:
                messagebox.showerror("Ошибка", "Ширина и высота должны быть больше нуля.")
                return

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
            args=(
                root_folder,
                output_pdf,
                names,
                threads,
                dpi,
                custom_width,
                custom_height,
                self.add_white_page_var.get(),
            ),
            daemon=True,
        )

        self.worker_thread.start()

    def process_generate(
        self,
        root_folder: str,
        output_pdf: str,
        names: list[str],
        threads: int,
        dpi: int,
        custom_width: float | None,
        custom_height: float | None,
        add_white_page: bool,
    ):
        start_time = time.time()
        created_path = None

        try:
            items, missing = match_files(
                root_folder,
                names,
                err_fn=self._err,
            )

            self._set_progress(0, len(items))

            generator = PDFGenerator(
                threads=threads,
                dpi=dpi,
                custom_width_mm=custom_width,
                custom_height_mm=custom_height,
                add_white_page=add_white_page,
            )

            created_path, size_kb, pdf_pages_count = generator.create_pdf(
                items,
                output_pdf,
                progress_callback=lambda done, total: self._set_progress(done, total),
                log_fn=self._log,
                err_fn=self._err,
                pause_event=self.pause_event,
                stop_event=self.stop_event,
            )

            elapsed = time.time() - start_time

            extra = f"\nНе найдено: {len(missing)}" if missing else ""

            final_log = (
                f"Готово. Время: {fmt_time(elapsed)}. "
                f"PDF листов: {pdf_pages_count}"
            )

            if missing:
                final_log += f". Не найдено: {len(missing)}"

            self._log(final_log)

            if self.stop_event.is_set():
                message = (
                    "Остановлено.\n"
                    f"PDF сохранён частично:\n{created_path}\n\n"
                    f"Размер: {size_kb} КБ\n"
                    f"Время: {fmt_time(elapsed)}\n"
                    f"PDF листов: {pdf_pages_count}"
                    f"{extra}"
                )
            else:
                message = (
                    "PDF создан:\n"
                    f"{created_path}\n\n"
                    f"Размер: {size_kb} КБ\n"
                    f"Время: {fmt_time(elapsed)}\n"
                    f"PDF листов: {pdf_pages_count}"
                    f"{extra}"
                )

            self._finish_ui(message, created_path)

        except Exception as error:
            self._err(f"Критическая ошибка: {error}")
            self._finish_ui(None, created_path)

    def toggle_theme(self):
        current_theme = self.style.theme.name
        self.style.theme_use("cosmo" if current_theme == "darkly" else "darkly")


def main():
    root = tb.Window(themename="darkly")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()