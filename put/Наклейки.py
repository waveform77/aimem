# -*- coding: utf-8 -*-
import os
import sys
import time
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

import tkinter as tk
from tkinter import filedialog, messagebox

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.scrolled import ScrolledText

from PIL import Image, ImageOps
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ---------------- НАСТРОЙКИ ----------------

TITLE = "Наклейки. Макеты для печати"

PAGE_WIDTH = 320 * mm
PAGE_HEIGHT = 450 * mm

PHOTO_WIDTH_MM = 49
PHOTO_HEIGHT_MM = 69

PHOTO_WIDTH = PHOTO_WIDTH_MM * mm
PHOTO_HEIGHT = PHOTO_HEIGHT_MM * mm

COLS = 6
ROWS = 6
PHOTOS_PER_PAGE = 36

SUPPORTED_EXTS = (
    ".jpg", ".jpeg", ".png", ".bmp",
    ".gif", ".tif", ".tiff", ".webp"
)

DEFAULT_DPI = 220
JPEG_QUALITY = 92
PREP_BATCH_SIZE = 72

PDF_FONT_NAME = "Helvetica"
PDF_FONT_READY = False

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.LANCZOS


# ---------------- ДАННЫЕ ----------------

@dataclass
class PreparedImage:
    reader: ImageReader
    buffer: BytesIO


@dataclass
class CategoryInfo:
    key: str
    label: str
    expected_count: int | None


@dataclass
class ThemeFolder:
    name: str
    folder: str
    category: CategoryInfo


# ---------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------------

def get_desktop_folder() -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)

            ctypes.windll.shell32.SHGetFolderPathW(
                None,
                0,
                None,
                0,
                buf,
            )

            return buf.value
        except Exception:
            pass

    return os.path.expanduser("~/Desktop")


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

        expected = item.category.expected_count

        if not files:
            if err_fn and not (stop_event and stop_event.is_set()):
                err_fn(f"{item.category.label}:\n{item.name} - в папке нет изображений")

            placeholder_count = expected if expected is not None else 1
            photos.extend([""] * placeholder_count)
        else:
            if expected is not None and len(files) != expected:
                if err_fn and not (stop_event and stop_event.is_set()):
                    err_fn(
                        f"{item.category.label}:\n"
                        f"{item.name} - найдено {len(files)} фото вместо {expected}"
                    )

            if expected is not None:
                if len(files) < expected:
                    files = files + [""] * (expected - len(files))
                else:
                    files = files[:expected]

            photos.extend(files)

            if log_fn:
                log_fn(f"{item.name} - успешно")

        processed_themes += 1
        if theme_progress_callback:
            theme_progress_callback(processed_themes, total_themes)


def normalize_name(value: str) -> str:
    return "".join(str(value).casefold().split())


def normalize_category_name(value: str) -> str:
    return "".join(
        char
        for char in str(value).casefold()
        if char.isalnum()
    )


def get_category_info(theme_name: str) -> CategoryInfo:
    name = normalize_category_name(theme_name)

    if "фотонаклейки24" in name or "фотонаклейка24" in name:
        return CategoryInfo(
            key="photo_stickers_24",
            label="Фотонаклейки 24шт",
            expected_count=24,
        )

    if "loveis" in name:
        return CategoryInfo(
            key="love_is_12",
            label="Наклейки Love is 12шт",
            expected_count=12,
        )

    return CategoryInfo(
        key="stickers_other",
        label="Наклейки",
        expected_count=None,
    )


def get_pdf_font_name() -> str:
    global PDF_FONT_NAME, PDF_FONT_READY

    if PDF_FONT_READY:
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
        if not os.path.exists(font_path):
            continue

        try:
            pdfmetrics.registerFont(
                TTFont("AppCyrillicFont", font_path)
            )

            PDF_FONT_NAME = "AppCyrillicFont"
            break
        except Exception:
            pass

    PDF_FONT_READY = True
    return PDF_FONT_NAME


def scan_direct_folders(root_folder: str) -> dict[str, str]:
    index = {}

    try:
        with os.scandir(root_folder) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        index.setdefault(
                            normalize_name(entry.name),
                            entry.path,
                        )
                except Exception:
                    continue
    except Exception:
        pass

    return index


def find_theme_folders_fast(
    root_folder: str,
    names: list[str],
    log_fn=None,
    err_fn=None,
    stop_event: threading.Event | None = None,
) -> list[ThemeFolder]:
    wanted = {normalize_name(name) for name in names}
    found_map = scan_direct_folders(root_folder)
    missing = wanted - set(found_map)

    if log_fn:
        direct_count = sum(key in found_map for key in wanted)

        log_fn(
            f"Быстрый поиск: найдено "
            f"{direct_count}/{len(wanted)} папок"
        )

    if missing:
        stack = [root_folder]

        while stack and missing:
            if stop_event and stop_event.is_set():
                break

            current = stack.pop()

            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if stop_event and stop_event.is_set():
                            break

                        try:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                        except Exception:
                            continue

                        key = normalize_name(entry.name)

                        if key in missing:
                            found_map.setdefault(key, entry.path)
                            missing.discard(key)
                        else:
                            stack.append(entry.path)

            except Exception:
                continue

        if log_fn and not (stop_event and stop_event.is_set()):
            total_count = sum(key in found_map for key in wanted)

            log_fn(
                f"Глубокий поиск: найдено "
                f"{total_count}/{len(wanted)} папок"
            )

    result = []

    for name in names:
        if stop_event and stop_event.is_set():
            break

        folder = found_map.get(normalize_name(name))
        category = get_category_info(name)

        if folder and os.path.isdir(folder):
            result.append(
                ThemeFolder(
                    name=name,
                    folder=folder,
                    category=category,
                )
            )
        elif err_fn:
            err_fn(
                f"{category.label}:\n"
                f"{name} - папка не найдена"
            )

    return result


def get_image_files(
    folder: str,
    stop_event: threading.Event | None = None,
) -> list[str]:
    files = []

    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if stop_event and stop_event.is_set():
                    break

                try:
                    ext = os.path.splitext(entry.name)[1].lower()

                    if entry.is_file() and ext in SUPPORTED_EXTS:
                        files.append(entry.path)
                except Exception:
                    continue
    except Exception:
        return []

    files.sort()
    return files


def collect_images_normal(
    items: list[ThemeFolder],
    log_fn=None,
    err_fn=None,
    stop_event: threading.Event | None = None,
    theme_progress_callback=None,
) -> list[str]:
    photos = []
    current_category = None
    total_themes = len(items)

    for index, item in enumerate(items, start=1):
        if stop_event and stop_event.is_set():
            break

        if item.category.key != current_category:
            current_category = item.category.key

            if log_fn:
                log_fn(f"{item.category.label}:")

        files = get_image_files(
            item.folder,
            stop_event=stop_event,
        )

        if not files:
            if err_fn:
                err_fn(
                    f"{item.category.label}:\n"
                    f"{item.name} - в папке нет изображений"
                )
        else:
            expected = item.category.expected_count

            if expected is not None and len(files) != expected:
                if err_fn:
                    err_fn(
                        f"{item.category.label}:\n"
                        f"{item.name} - найдено {len(files)} фото "
                        f"вместо {expected}"
                    )

            if expected is not None:
                files = files[:expected]

            photos.extend(files)

            if log_fn:
                log_fn(f"{item.name} - успешно")

        if theme_progress_callback:
            theme_progress_callback(index, total_themes)

    return photos


def collect_ready_layers(
    items: list[ThemeFolder],
    err_fn=None,
    stop_event: threading.Event | None = None,
) -> list[list[tuple[str, str | None]]]:
    if not items:
        return []

    expected = items[0].category.expected_count
    all_files = []

    for item in items:
        files = get_image_files(
            item.folder,
            stop_event=stop_event,
        )

        if not files:
            if err_fn:
                err_fn(
                    f"{item.category.label}:\n"
                    f"{item.name} - в папке нет изображений"
                )

            all_files.append([])
            continue

        if expected is not None and len(files) != expected:
            if err_fn:
                err_fn(
                    f"{item.category.label}:\n"
                    f"{item.name} - найдено {len(files)} фото "
                    f"вместо {expected}"
                )

        if expected is not None:
            files = files[:expected]

        all_files.append(files)

    if expected is None:
        layer_count = max(
            (len(files) for files in all_files),
            default=0,
        )
    else:
        layer_count = expected

    layers = []

    for image_index in range(layer_count):
        if stop_event and stop_event.is_set():
            break

        layer = []

        for item, files in zip(items, all_files):
            path = files[image_index] if image_index < len(files) else None
            layer.append((item.name, path))

        layers.append(layer)

    return layers


def split_by_category_runs(
    items: list[ThemeFolder],
) -> list[list[ThemeFolder]]:
    if not items:
        return []

    runs = []
    current_run = [items[0]]
    current_key = items[0].category.key

    for item in items[1:]:
        if item.category.key == current_key:
            current_run.append(item)
        else:
            runs.append(current_run)
            current_run = [item]
            current_key = item.category.key

    runs.append(current_run)
    return runs


def points_to_pixels(points: float, dpi: int) -> int:
    return max(
        1,
        int(round(points / 72 * dpi)),
    )


def create_white_placeholder_image(
    width_points: float,
    height_points: float,
    dpi: int,
) -> PreparedImage:
    target_width = points_to_pixels(width_points, dpi)
    target_height = points_to_pixels(height_points, dpi)

    image = Image.new("RGB", (target_width, target_height), (255, 255, 255))
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

    return PreparedImage(reader=ImageReader(buffer), buffer=buffer)


def prepare_image_for_pdf(
    path: str,
    width: float,
    height: float,
    dpi: int,
) -> PreparedImage:
    if not path or not os.path.exists(path):
        return create_white_placeholder_image(width, height, dpi)

    target_width = points_to_pixels(width, dpi)
    target_height = points_to_pixels(height, dpi)

    try:
        with Image.open(path) as image:
            try:
                image.draft(
                    "RGB",
                    (target_width, target_height),
                )
            except Exception:
                pass

            image = ImageOps.exif_transpose(image)

            if (
                image.mode in ("RGBA", "LA")
                or (
                    image.mode == "P"
                    and "transparency" in image.info
                )
            ):
                rgba = image.convert("RGBA")

                background = Image.new(
                    "RGB",
                    rgba.size,
                    (255, 255, 255),
                )

                background.paste(
                    rgba,
                    mask=rgba.getchannel("A"),
                )

                image = background
            else:
                image = image.convert("RGB")

            if image.size != (target_width, target_height):
                image = image.resize(
                    (target_width, target_height),
                    RESAMPLE,
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
    except Exception:
        return create_white_placeholder_image(width, height, dpi)


def prepare_batch(
    paths: list[str],
    width: float,
    height: float,
    dpi: int,
    workers: int,
    err_fn=None,
    stop_event: threading.Event | None = None,
):
    if not paths:
        return []

    workers = max(
        1,
        min(int(workers), len(paths)),
    )

    def prepare_one(path: str):
        if stop_event and stop_event.is_set():
            return path, None

        try:
            return (
                path,
                prepare_image_for_pdf(
                    path,
                    width,
                    height,
                    dpi,
                ),
            )
        except Exception as error:
            if err_fn:
                err_fn(
                    f"Ошибка подготовки изображения "
                    f"{path}: {error}"
                )

            return path, create_white_placeholder_image(width, height, dpi)

    if workers == 1:
        return [prepare_one(path) for path in paths]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(prepare_one, paths))


# ---------------- ГЕНЕРАТОР PDF ----------------

class PDFGenerator:
    def __init__(
        self,
        threads: int = 4,
        dpi: int = DEFAULT_DPI,
        batch_size: int = PREP_BATCH_SIZE,
        photo_width_mm: float = PHOTO_WIDTH_MM,
        photo_height_mm: float = PHOTO_HEIGHT_MM,
    ):
        self.threads = max(1, int(threads))
        self.dpi = max(120, int(dpi))
        self.batch_size = max(1, int(batch_size))

        self.photo_width_mm = float(photo_width_mm)
        self.photo_height_mm = float(photo_height_mm)

        self.photo_width = self.photo_width_mm * mm
        self.photo_height = self.photo_height_mm * mm

    def grid_position(self, position: int) -> tuple[float, float]:
        x_offset = (
            PAGE_WIDTH - COLS * self.photo_width
        ) / 2

        y_offset = (
            PAGE_HEIGHT - ROWS * self.photo_height
        ) / 2

        row, column = divmod(position, COLS)

        x = column * self.photo_width + x_offset

        y = (
            PAGE_HEIGHT
            - (row + 1) * self.photo_height
            - y_offset
        )

        return x, y

    @staticmethod
    def fit_font_size(
        pdf: canvas.Canvas,
        text: str,
        max_width: float,
        start_size: float,
        min_size: float = 6,
    ) -> float:
        font_name = get_pdf_font_name()
        size = start_size

        while size >= min_size:
            if pdf.stringWidth(
                text,
                font_name,
                size,
            ) <= max_width:
                return size

            size -= 0.5

        return min_size

    @staticmethod
    def wrap_text(
        pdf: canvas.Canvas,
        text: str,
        font_size: float,
        max_width: float,
    ) -> list[str]:
        font_name = get_pdf_font_name()
        text = " ".join(str(text).split())

        if not text:
            return [""]

        lines = []
        current = ""

        for word in text.split():
            if pdf.stringWidth(
                word,
                font_name,
                font_size,
            ) > max_width:
                parts = []
                part = ""

                for char in word:
                    test = part + char

                    if pdf.stringWidth(
                        test,
                        font_name,
                        font_size,
                    ) <= max_width:
                        part = test
                    else:
                        if part:
                            parts.append(part)

                        part = char

                if part:
                    parts.append(part)
            else:
                parts = [word]

            for part in parts:
                test = part if not current else f"{current} {part}"

                if pdf.stringWidth(
                    test,
                    font_name,
                    font_size,
                ) <= max_width:
                    current = test
                else:
                    if current:
                        lines.append(current)

                    current = part

        if current:
            lines.append(current)

        return lines or [""]

    def fit_wrapped_text(
        self,
        pdf: canvas.Canvas,
        text: str,
        max_width: float,
        max_height: float,
        start_size: float = 9,
        min_size: float = 5.5,
    ) -> tuple[list[str], float, float]:
        size = start_size

        while size >= min_size:
            lines = self.wrap_text(
                pdf,
                text,
                size,
                max_width,
            )

            line_height = size * 1.14

            if len(lines) * line_height <= max_height:
                return lines, size, line_height

            size -= 0.5

        lines = self.wrap_text(
            pdf,
            text,
            min_size,
            max_width,
        )

        line_height = min_size * 1.14
        max_lines = max(1, int(max_height // line_height))

        return (
            lines[:max_lines],
            min_size,
            line_height,
        )

    def draw_separator_page(
        self,
        pdf: canvas.Canvas,
        items: list[ThemeFolder],
        pack_start: int,
        category_label: str | None,
    ):
        font_name = get_pdf_font_name()

        pdf.setStrokeColorRGB(0, 0, 0)
        pdf.setFillColorRGB(0, 0, 0)

        for index, item in enumerate(items[:PHOTOS_PER_PAGE]):
            x, y = self.grid_position(index)
            center_x = x + self.photo_width / 2

            pdf.setLineWidth(0.8)

            pdf.rect(
                x,
                y,
                self.photo_width,
                self.photo_height,
                stroke=1,
                fill=0,
            )

            pack_text = f"{pack_start + index} пак"
            top_center_y = y + self.photo_height * 0.72

            if category_label:
                title_size = self.fit_font_size(
                    pdf,
                    category_label,
                    self.photo_width * 0.90,
                    13,
                )

                pack_size = self.fit_font_size(
                    pdf,
                    pack_text,
                    self.photo_width * 0.90,
                    18,
                )

                title_y = top_center_y + 8
                pack_y = top_center_y - pack_size - 2

                pdf.setFont(
                    font_name,
                    title_size,
                )

                pdf.drawCentredString(
                    center_x,
                    title_y,
                    category_label,
                )
            else:
                pack_size = self.fit_font_size(
                    pdf,
                    pack_text,
                    self.photo_width * 0.90,
                    20,
                )

                pack_y = top_center_y

            pdf.setFont(
                font_name,
                pack_size,
            )

            pdf.drawCentredString(
                center_x,
                pack_y,
                pack_text,
            )

            text_width = pdf.stringWidth(
                pack_text,
                font_name,
                pack_size,
            )

            pdf.line(
                center_x - text_width / 2,
                pack_y - 2,
                center_x + text_width / 2,
                pack_y - 2,
            )

            lines, size, line_height = self.fit_wrapped_text(
                pdf,
                item.name,
                self.photo_width * 0.90,
                self.photo_height * 0.40,
            )

            pdf.setFont(font_name, size)

            total_height = len(lines) * line_height

            text_y = (
                y
                + self.photo_height * 0.24
                + total_height / 2
                - size
            )

            for line in lines:
                pdf.drawCentredString(
                    center_x,
                    text_y,
                    line,
                )

                text_y -= line_height

    def draw_layer(
        self,
        pdf: canvas.Canvas,
        layer: list[tuple[str, str | None]],
        done_files: int,
        total_files: int,
        progress_callback=None,
        err_fn=None,
        pause_event=None,
        stop_event=None,
    ) -> int:
        paths = [path for _, path in layer]

        prepared = prepare_batch(
            paths,
            self.photo_width,
            self.photo_height,
            self.dpi,
            self.threads,
            err_fn=err_fn,
            stop_event=stop_event,
        )

        prepared_map = {path: image for path, image in prepared}

        for index, (theme_name, path) in enumerate(layer):
            if stop_event and stop_event.is_set():
                break

            if pause_event:
                pause_event.wait()

            try:
                image = prepared_map.get(path)
                x, y = self.grid_position(index)

                if image is not None:
                    pdf.drawImage(
                        image.reader,
                        x,
                        y,
                        self.photo_width,
                        self.photo_height,
                        preserveAspectRatio=False,
                        mask=None,
                    )
                else:
                    placeholder = create_white_placeholder_image(PHOTO_WIDTH, PHOTO_HEIGHT, self.dpi)
                    pdf.drawImage(
                        placeholder.reader,
                        x,
                        y,
                        PHOTO_WIDTH,
                        PHOTO_HEIGHT,
                        preserveAspectRatio=False,
                        mask=None,
                    )

                done_files += 1

                if (
                    progress_callback
                    and (
                        done_files % 5 == 0
                        or done_files == total_files
                    )
                ):
                    progress_callback(
                        done_files,
                        total_files,
                    )

            except Exception as error:
                if err_fn:
                    err_fn(
                        f"{theme_name} - ошибка вставки "
                        f"изображения: {error}"
                    )

        return done_files

    def create_pdf(
        self,
        photo_paths: list[str],
        output_path: str,
        progress_callback=None,
        log_fn=None,
        err_fn=None,
        pause_event=None,
        stop_event=None,
    ):
        output_path = unique_output_path(output_path)
        total = len(photo_paths)

        if not total:
            if err_fn:
                err_fn("Нет изображений для PDF.")

            return output_path, 0, 0

        pdf = canvas.Canvas(
            output_path,
            pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        )

        pdf.setPageCompression(True)

        processed = 0
        pdf_pages = 0

        try:
            for batch_start in range(
                0,
                total,
                self.batch_size,
            ):
                if stop_event and stop_event.is_set():
                    break

                if pause_event:
                    pause_event.wait()

                batch = photo_paths[
                    batch_start:
                    batch_start + self.batch_size
                ]

                prepared = prepare_batch(
                    batch,
                    self.photo_width,
                    self.photo_height,
                    self.dpi,
                    self.threads,
                    err_fn=err_fn,
                    stop_event=stop_event,
                )

                for local_index, (path, image) in enumerate(
                    prepared,
                    start=1,
                ):
                    if stop_event and stop_event.is_set():
                        break

                    if pause_event:
                        pause_event.wait()

                    global_index = batch_start + local_index
                    position = (global_index - 1) % PHOTOS_PER_PAGE
                    x, y = self.grid_position(position)

                    try:
                        if image is not None:
                            pdf.drawImage(
                                image.reader,
                                x,
                                y,
                                self.photo_width,
                                self.photo_height,
                                preserveAspectRatio=False,
                                mask=None,
                            )
                    except Exception as error:
                        if err_fn:
                            err_fn(
                                f"Ошибка вставки изображения "
                                f"{path}: {error}"
                            )

                    if (
                        position == PHOTOS_PER_PAGE - 1
                        or global_index == total
                    ):
                        pdf.showPage()
                        pdf_pages += 1

                    processed += 1

                    if (
                        progress_callback
                        and (
                            processed % 5 == 0
                            or processed == total
                        )
                    ):
                        progress_callback(
                            processed,
                            total,
                        )

        finally:
            pdf.save()

        size_kb = (
            os.path.getsize(output_path) // 1024
            if os.path.exists(output_path)
            else 0
        )

        if log_fn:
            log_fn(
                f"PDF сохранён: {output_path} "
                f"({size_kb} КБ)"
            )

        return output_path, size_kb, pdf_pages

    def create_pdf_ready_sets(
        self,
        items: list[ThemeFolder],
        output_path: str,
        progress_callback=None,
        theme_progress_callback=None,
        separator_pages=True,
        log_fn=None,
        err_fn=None,
        pause_event=None,
        stop_event=None,
    ):
        output_path = unique_output_path(output_path)

        if not items:
            if err_fn:
                err_fn("Нет тем для PDF.")

            return output_path, 0, 0

        total_files = sum(
            item.category.expected_count
            if item.category.expected_count is not None
            else len(get_image_files(item.folder))
            for item in items
        )

        pdf = canvas.Canvas(
            output_path,
            pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        )

        pdf.setPageCompression(True)

        done_files = 0
        done_themes = 0
        pdf_pages = 0
        total_themes = len(items)

        try:
            for category_run in split_by_category_runs(items):
                if stop_event and stop_event.is_set():
                    break

                category = category_run[0].category
                pack_start = 1
                first_batch = True

                if log_fn:
                    log_fn(f"{category.label}:")

                for batch_start in range(
                    0,
                    len(category_run),
                    PHOTOS_PER_PAGE,
                ):
                    if stop_event and stop_event.is_set():
                        break

                    batch_items = category_run[
                        batch_start:
                        batch_start + PHOTOS_PER_PAGE
                    ]

                    if separator_pages:
                        self.draw_separator_page(
                            pdf,
                            batch_items,
                            pack_start,
                            category.label if first_batch else None,
                        )

                        pdf.showPage()
                        pdf_pages += 1

                    layers = collect_ready_layers(
                        batch_items,
                        err_fn=err_fn,
                        stop_event=stop_event,
                    )

                    for layer in layers:
                        if stop_event and stop_event.is_set():
                            break

                        done_files = self.draw_layer(
                            pdf,
                            layer,
                            done_files,
                            total_files,
                            progress_callback=progress_callback,
                            err_fn=err_fn,
                            pause_event=pause_event,
                            stop_event=stop_event,
                        )

                        if stop_event and stop_event.is_set():
                            break

                        pdf.showPage()
                        pdf_pages += 1

                    for item in batch_items:
                        if log_fn:
                            log_fn(
                                f"{item.name} - успешно"
                            )

                        done_themes += 1

                        if theme_progress_callback:
                            theme_progress_callback(
                                done_themes,
                                total_themes,
                            )

                    pack_start += len(batch_items)
                    first_batch = False

        finally:
            pdf.save()

        size_kb = (
            os.path.getsize(output_path) // 1024
            if os.path.exists(output_path)
            else 0
        )

        if log_fn:
            log_fn(
                f"PDF сохранён: {output_path} "
                f"({size_kb} КБ)"
            )

        return output_path, size_kb, pdf_pages


# ---------------- GUI ----------------

class App:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title(TITLE)
        self.root.geometry("1550x1100")
        self.root.minsize(1100, 680)

        self.style = tb.Style("darkly")

        self.root_folder_var = tk.StringVar()

        self.save_folder_var = tk.StringVar(
            value=get_desktop_folder()
        )

        self.threads_var = tk.IntVar(
            value=min(
                7,
                max(
                    2,
                    (os.cpu_count() or 8) - 1,
                ),
            )
        )

        self.dpi_var = tk.IntVar(
            value=DEFAULT_DPI
        )

        self.photo_width_mm_var = tk.StringVar(
            value=str(PHOTO_WIDTH_MM)
        )

        self.photo_height_mm_var = tk.StringVar(
            value=str(PHOTO_HEIGHT_MM)
        )

        self.ready_set_var = tk.BooleanVar(
            value=True
        )

        self.separator_pages_var = tk.BooleanVar(
            value=True
        )

        self.pause_event = threading.Event()
        self.pause_event.set()

        self.stop_event = threading.Event()
        self.ui_queue = queue.Queue()

        self.worker_thread = None
        self.run_start_ts = None

        self.build_ui()
        self.poll_ui_queue()

    def build_ui(self):
        outer = tb.Frame(
            self.root,
            padding=12,
        )

        outer.pack(
            fill=BOTH,
            expand=YES,
        )

        paths = tb.Labelframe(
            outer,
            text="Пути",
            padding=10,
        )

        paths.pack(fill=X)

        row1 = tb.Frame(paths)
        row1.pack(fill=X, pady=4)

        tb.Label(
            row1,
            text="Папка с материалами:",
            width=24,
            anchor=W,
        ).pack(side=LEFT)

        tb.Entry(
            row1,
            textvariable=self.root_folder_var,
        ).pack(
            side=LEFT,
            fill=X,
            expand=YES,
            padx=8,
        )

        tb.Button(
            row1,
            text="Выбрать",
            command=self.choose_root_folder,
            bootstyle=PRIMARY,
        ).pack(side=LEFT)

        row2 = tb.Frame(paths)
        row2.pack(fill=X, pady=4)

        tb.Label(
            row2,
            text="Куда сохранить макеты:",
            width=24,
            anchor=W,
        ).pack(side=LEFT)

        tb.Entry(
            row2,
            textvariable=self.save_folder_var,
        ).pack(
            side=LEFT,
            fill=X,
            expand=YES,
            padx=8,
        )

        tb.Button(
            row2,
            text="Выбрать",
            command=self.choose_save_folder,
            bootstyle=PRIMARY,
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
            width=6,
            textvariable=self.threads_var,
        ).pack(
            side=LEFT,
            padx=(0, 14),
        )

        tb.Label(
            row3,
            text="DPI картинок:",
        ).pack(side=LEFT)

        tb.Spinbox(
            row3,
            from_=120,
            to=300,
            increment=10,
            width=6,
            textvariable=self.dpi_var,
        ).pack(
            side=LEFT,
            padx=(8, 14),
        )

        tb.Checkbutton(
            row3,
            text="Вставить фото в готовый набор",
            variable=self.ready_set_var,
            bootstyle="round-toggle",
        ).pack(
            side=LEFT,
            padx=(0, 12),
        )

        tb.Checkbutton(
            row3,
            text="Добавлять белые листы",
            variable=self.separator_pages_var,
            bootstyle="round-toggle",
        ).pack(
            side=LEFT,
            padx=(0, 12),
        )

        tb.Label(
            row3,
            text="Больше 7 потоков не дает прироста",
            foreground="#c9c9c9",
        ).pack(side=LEFT)

        row4 = tb.Frame(paths)
        row4.pack(fill=X, pady=4)

        tb.Label(
            row4,
            text="Размер фото:",
            width=24,
            anchor=W,
        ).pack(side=LEFT)

        tb.Label(row4, text="Ширина,мм:").pack(side=LEFT)

        tb.Entry(
            row4,
            textvariable=self.photo_width_mm_var,
            width=8,
        ).pack(
            side=LEFT,
            padx=(6, 14),
        )

        tb.Label(row4, text="Высота,мм:").pack(side=LEFT)

        tb.Entry(
            row4,
            textvariable=self.photo_height_mm_var,
            width=8,
        ).pack(
            side=LEFT,
            padx=(6, 14),
        )

        tb.Label(
            row4,
            text=f"",
            foreground="#c9c9c9",
        ).pack(side=LEFT)

        names_frame = tb.Labelframe(
            outer,
            text="Артикулы/Наименования тем",
            padding=10,
        )

        names_frame.pack(
            fill=X,
            pady=(10, 0),
        )

        self.names_box = ScrolledText(
            names_frame,
            height=8,
            autohide=True,
        )

        self.names_box.pack(fill=X)

        self.bind_names_box_shortcuts()

        buttons = tb.Frame(outer)

        buttons.pack(
            fill=X,
            pady=(10, 8),
        )

        self.paste_button = tb.Button(
            buttons,
            text="Вставить из буфера",
            command=self.paste_names,
            bootstyle=INFO,
        )

        self.paste_button.pack(side=LEFT)

        self.start_button = tb.Button(
            buttons,
            text="Старт",
            command=self.start,
            bootstyle=SUCCESS,
        )

        self.start_button.pack(
            side=LEFT,
            padx=8,
        )

        self.pause_button = tb.Button(
            buttons,
            text="Пауза",
            command=self.pause,
            bootstyle=WARNING,
            state=DISABLED,
        )

        self.pause_button.pack(side=LEFT)

        self.resume_button = tb.Button(
            buttons,
            text="Продолжить",
            command=self.resume,
            bootstyle=PRIMARY,
            state=DISABLED,
        )

        self.resume_button.pack(
            side=LEFT,
            padx=8,
        )

        self.stop_button = tb.Button(
            buttons,
            text="Стоп",
            command=self.stop,
            bootstyle=DANGER,
            state=DISABLED,
        )

        self.stop_button.pack(side=LEFT)

        tb.Button(
            buttons,
            text="Очистить артикулы",
            command=self.clear_articles,
            bootstyle=SECONDARY,
        ).pack(
            side=LEFT,
            padx=(16, 8),
        )

        tb.Button(
            buttons,
            text="Сменить тему",
            command=self.toggle_theme,
            bootstyle=INFO,
        ).pack(side=LEFT)

        progress_frame = tb.Labelframe(
            outer,
            text="Прогресс",
            padding=10,
        )

        progress_frame.pack(fill=X)

        self.progress = tb.Progressbar(
            progress_frame,
            maximum=1,
            mode="determinate",
            bootstyle="success-striped",
        )

        self.progress.pack(fill=X)

        self.progress_label = tb.Label(
            progress_frame,
            text=(
                "0 / 0 тем (0.0%)  |  "
                "0 / 0 файлов (0.0%)"
            ),
        )

        self.progress_label.pack(
            anchor=W,
            pady=(6, 0),
        )

        self.eta_label = tb.Label(
            progress_frame,
            text="Осталось: --:--  |  Прошло: --:--",
            foreground="#c9c9c9",
        )

        self.eta_label.pack(
            anchor=W,
            pady=(4, 0),
        )

        logs_wrapper = tb.Frame(outer)

        logs_wrapper.pack(
            fill=BOTH,
            expand=YES,
            pady=(10, 0),
        )

        logs_wrapper.columnconfigure(0, weight=1)
        logs_wrapper.columnconfigure(1, weight=1)
        logs_wrapper.rowconfigure(0, weight=1)

        logs_frame = tb.Labelframe(
            logs_wrapper,
            text="Логи",
            padding=8,
        )

        logs_frame.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(0, 6),
        )

        errors_frame = tb.Labelframe(
            logs_wrapper,
            text="Ошибки / предупреждения",
            padding=8,
        )

        errors_frame.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=(6, 0),
        )

        self.log_box = ScrolledText(
            logs_frame,
            height=16,
            autohide=True,
        )

        self.log_box.pack(
            fill=BOTH,
            expand=YES,
        )

        self.error_box = ScrolledText(
            errors_frame,
            height=16,
            autohide=True,
        )

        self.error_box.pack(
            fill=BOTH,
            expand=YES,
        )

    def bind_names_box_shortcuts(self):
        widgets = [self.names_box]
        inner_text_widget = getattr(self.names_box, "text", None)

        if inner_text_widget is not None:
            widgets.append(inner_text_widget)

        for widget in widgets:
            try:
                widget.bind(
                    "<Control-KeyPress>",
                    self.handle_names_box_control_shortcut,
                )
            except Exception:
                pass

    def handle_names_box_control_shortcut(self, event):
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
            return self.paste_into_names_box(event)

        if is_copy:
            return self.copy_from_names_box(event)

        return None

    def paste_into_names_box(self, event=None):
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

    def copy_from_names_box(self, event=None):
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
        folder = filedialog.askdirectory(
            title="Выбери корневую папку"
        )

        if folder:
            self.root_folder_var.set(folder)

    def choose_save_folder(self):
        folder = filedialog.askdirectory(
            title="Выбери папку сохранения"
        )

        if folder:
            self.save_folder_var.set(folder)

    def paste_names(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            messagebox.showerror(
                "Ошибка",
                "Не удалось получить данные "
                "из буфера обмена.",
            )
            return

        text = (
            text
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .strip()
        )

        if not text:
            return

        current_text = self.names_box.get(
            "1.0",
            "end-1c",
        )

        if current_text and not current_text.endswith("\n"):
            self.names_box.insert("end", "\n")

        self.names_box.insert("end", text)
        self.names_box.see("end")

    def clear_articles(self):
        self.names_box.delete("1.0", "end")

    def pause(self):
        self.pause_event.clear()
        self.pause_button.config(state=DISABLED)
        self.resume_button.config(state=NORMAL)
        self.log("Пауза.")

    def resume(self):
        self.pause_event.set()
        self.pause_button.config(state=NORMAL)
        self.resume_button.config(state=DISABLED)
        self.log("Продолжили.")

    def stop(self):
        self.stop_event.set()
        self.pause_event.set()
        self.stop_button.config(state=DISABLED)
        self.log("Остановка...")

    def clear_logs(self):
        self.log_box.delete("1.0", "end")
        self.error_box.delete("1.0", "end")

    def toggle_theme(self):
        current = self.style.theme.name

        self.style.theme_use(
            "cosmo"
            if current == "darkly"
            else "darkly"
        )

    def log(self, message: str):
        self.ui_queue.put(
            ("log", message)
        )

    def error(self, message: str):
        self.ui_queue.put(
            ("error", message)
        )

    def set_progress(
        self,
        done_themes,
        total_themes,
        done_files,
        total_files,
    ):
        self.ui_queue.put(
            (
                "progress",
                done_themes,
                total_themes,
                done_files,
                total_files,
            )
        )

    def finish_ui(
        self,
        message=None,
        output_path=None,
    ):
        self.ui_queue.put(
            (
                "finish",
                message,
                output_path,
            )
        )

    def poll_ui_queue(self):
        processed = 0

        try:
            while processed < 25:
                item = self.ui_queue.get_nowait()
                processed += 1

                event = item[0]

                if event == "log":
                    self.log_box.insert(
                        "end",
                        item[1] + "\n",
                    )

                    self.log_box.see("end")

                elif event == "error":
                    self.error_box.insert(
                        "end",
                        item[1] + "\n",
                    )

                    self.error_box.see("end")

                elif event == "progress":
                    self.update_progress(*item[1:])

                elif event == "finish":
                    self.start_button.config(state=NORMAL)
                    self.paste_button.config(state=NORMAL)
                    self.pause_button.config(state=DISABLED)
                    self.resume_button.config(state=DISABLED)
                    self.stop_button.config(state=DISABLED)

                    if item[1]:
                        messagebox.showinfo(
                            "Готово",
                            item[1],
                        )

        except queue.Empty:
            pass

        self.root.after(
            60,
            self.poll_ui_queue,
        )

    def update_progress(
        self,
        done_themes,
        total_themes,
        done_files,
        total_files,
    ):
        self.progress["maximum"] = max(
            1,
            total_themes,
        )

        self.progress["value"] = done_themes

        theme_percent = (
            done_themes / total_themes * 100
            if total_themes
            else 0
        )

        file_percent = (
            done_files / total_files * 100
            if total_files
            else 0
        )

        self.progress_label.config(
            text=(
                f"Тем: {done_themes} / {total_themes} "
                f"({theme_percent:.1f}%)  |  "
                f"Файлов: {done_files} / {total_files} "
                f"({file_percent:.1f}%)"
            )
        )

        elapsed = (
            time.time() - self.run_start_ts
            if self.run_start_ts
            else 0
        )

        if done_files and total_files:
            remaining = (
                total_files - done_files
            ) * (elapsed / done_files)

        elif done_themes and total_themes:
            remaining = (
                total_themes - done_themes
            ) * (elapsed / done_themes)

        else:
            remaining = None

        remaining_text = (
            fmt_time(remaining)
            if remaining is not None
            else "--:--"
        )

        self.eta_label.config(
            text=(
                f"Осталось: {remaining_text}  |  "
                f"Прошло: {fmt_time(elapsed)}"
            )
        )

    def start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        root_folder = self.root_folder_var.get().strip()
        save_folder = self.save_folder_var.get().strip()

        names = [
            name.strip()
            for name in self.names_box.get(
                "1.0",
                "end",
            ).splitlines()
            if name.strip()
        ]

        if not root_folder or not os.path.isdir(root_folder):
            messagebox.showerror(
                "Ошибка",
                "Укажи корректную корневую папку!",
            )
            return

        if not save_folder or not os.path.isdir(save_folder):
            messagebox.showerror(
                "Ошибка",
                "Выбери корректную папку сохранения!",
            )
            return

        if not names:
            messagebox.showerror(
                "Ошибка",
                "Вставь названия папок!",
            )
            return

        try:
            threads = max(
                1,
                min(
                    16,
                    int(self.threads_var.get()),
                ),
            )
        except Exception:
            threads = 4

        try:
            dpi = max(
                120,
                min(
                    300,
                    int(self.dpi_var.get()),
                ),
            )
        except Exception:
            dpi = DEFAULT_DPI

        try:
            photo_width_mm = float(
                self.photo_width_mm_var
                .get()
                .strip()
                .replace(",", ".")
            )

            photo_height_mm = float(
                self.photo_height_mm_var
                .get()
                .strip()
                .replace(",", ".")
            )

            if photo_width_mm <= 0 or photo_height_mm <= 0:
                raise ValueError

        except Exception:
            messagebox.showerror(
                "Ошибка",
                "Ширина и высота картинки должны быть числами больше 0.",
            )
            return

        self.clear_logs()

        self.stop_event.clear()
        self.pause_event.set()

        self.start_button.config(state=DISABLED)
        self.paste_button.config(state=DISABLED)
        self.pause_button.config(state=NORMAL)
        self.resume_button.config(state=DISABLED)
        self.stop_button.config(state=NORMAL)

        self.run_start_ts = time.time()

        self.set_progress(
            0,
            0,
            0,
            0,
        )

        self.worker_thread = threading.Thread(
            target=self.process_generate,
            args=(
                root_folder,
                save_folder,
                names,
                threads,
                dpi,
                self.ready_set_var.get(),
                self.separator_pages_var.get(),
                photo_width_mm,
                photo_height_mm,
            ),
            daemon=True,
        )

        self.worker_thread.start()

    def process_generate(
        self,
        root_folder,
        save_folder,
        names,
        threads,
        dpi,
        ready_set_mode,
        separator_pages,
        photo_width_mm,
        photo_height_mm,
    ):
        start_time = time.time()
        output_path = None

        try:
            self.log("Старт создания PDF")
            self.log(f"Тем: {len(names)}")
            self.log(
                f"Потоки подготовки изображений: {threads}"
            )
            self.log(
                f"DPI подготовки изображений: {dpi}"
            )
            self.log(
                f"Формат: наклейки {photo_width_mm:g}×{photo_height_mm:g} мм, "
                "36 фото на листе, сетка 6×6"
            )

            if ready_set_mode:
                self.log(
                    "Режим: вставить фото "
                    "в готовый набор"
                )

                self.log(
                    "Белые листы с прямоугольниками: "
                    f"{'включены' if separator_pages else 'выключены'}"
                )

            items = find_theme_folders_fast(
                root_folder,
                names,
                log_fn=self.log,
                err_fn=self.error,
                stop_event=self.stop_event,
            )

            if not items:
                self.error(
                    "Не найдено ни одной папки из списка."
                )

                self.finish_ui()
                return

            total_themes = len(items)

            date_string = datetime.now().strftime(
                "%d.%m"
            )

            output_file = os.path.join(
                save_folder,
                f"Наклейки от {date_string}.pdf",
            )

            generator = PDFGenerator(
                threads=threads,
                dpi=dpi,
                photo_width_mm=photo_width_mm,
                photo_height_mm=photo_height_mm,
            )

            done_themes = 0
            done_files = 0

            if ready_set_mode:
                total_files = sum(
                    item.category.expected_count
                    if item.category.expected_count is not None
                    else len(get_image_files(item.folder))
                    for item in items
                )

                self.set_progress(
                    0,
                    total_themes,
                    0,
                    total_files,
                )

                def file_progress(done, total):
                    nonlocal done_files

                    done_files = done

                    self.set_progress(
                        done_themes,
                        total_themes,
                        done_files,
                        total_files,
                    )

                def theme_progress(done, total):
                    nonlocal done_themes

                    done_themes = done

                    self.set_progress(
                        done_themes,
                        total_themes,
                        done_files,
                        total_files,
                    )

                output_path, size_kb, pdf_pages = (
                    generator.create_pdf_ready_sets(
                        items,
                        output_file,
                        progress_callback=file_progress,
                        theme_progress_callback=theme_progress,
                        separator_pages=separator_pages,
                        log_fn=self.log,
                        err_fn=self.error,
                        pause_event=self.pause_event,
                        stop_event=self.stop_event,
                    )
                )

            else:
                self.log("Режим: обычный PDF")

                self.set_progress(
                    0,
                    total_themes,
                    0,
                    0,
                )

                def theme_progress(done, total):
                    nonlocal done_themes

                    done_themes = done

                    self.set_progress(
                        done_themes,
                        total_themes,
                        0,
                        0,
                    )

                photos = collect_images_normal(
                    items,
                    log_fn=self.log,
                    err_fn=self.error,
                    stop_event=self.stop_event,
                    theme_progress_callback=theme_progress,
                )

                if not photos:
                    self.error(
                        "Не найдено ни одной картинки "
                        "в найденных папках."
                    )

                    self.finish_ui()
                    return

                total_files = len(photos)

                def file_progress(done, total):
                    nonlocal done_files

                    done_files = done

                    self.set_progress(
                        total_themes,
                        total_themes,
                        done_files,
                        total_files,
                    )

                output_path, size_kb, pdf_pages = (
                    generator.create_pdf(
                        photos,
                        output_file,
                        progress_callback=file_progress,
                        log_fn=self.log,
                        err_fn=self.error,
                        pause_event=self.pause_event,
                        stop_event=self.stop_event,
                    )
                )

            elapsed = time.time() - start_time

            if self.stop_event.is_set():
                message = (
                    "Остановлено.\n"
                    "PDF сохранён частично:\n"
                    f"{output_path}\n\n"
                    f"Время: {fmt_time(elapsed)}"
                )
            else:
                message = (
                    "PDF создан:\n"
                    f"{output_path}\n\n"
                    f"Размер: {size_kb} КБ\n"
                    f"PDF листов: {pdf_pages}\n"
                    f"Время: {fmt_time(elapsed)}"
                )

            self.log(
                f"Готово! Время: {fmt_time(elapsed)}"
            )

            self.finish_ui(
                message,
                output_path,
            )

        except Exception as error:
            self.error(
                f"Критическая ошибка: {error}"
            )

            self.finish_ui(
                None,
                output_path,
            )


def main():
    root = tb.Window(
        themename="darkly"
    )

    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()