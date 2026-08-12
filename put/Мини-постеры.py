# -*- coding: utf-8 -*-
import os
import sys
import time
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

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

PAGE_WIDTH = 320 * mm
PAGE_HEIGHT = 450 * mm

# Мини-постеры 10×15 см
PHOTO_WIDTH_MM = 104
PHOTO_HEIGHT_MM = 149
PHOTO_WIDTH = PHOTO_WIDTH_MM * mm
PHOTO_HEIGHT = PHOTO_HEIGHT_MM * mm

PHOTOS_PER_PAGE = 9
GRID_COLS = 3
GRID_ROWS = 3

SUPPORTED_EXTS = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff', '.webp')

DEFAULT_DPI = 220
JPEG_QUALITY = 92
PREP_BATCH_SIZE = 9

PDF_FONT_NAME = "Helvetica"
_PDF_FONT_READY = False

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_LANCZOS = Image.LANCZOS


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

            CSIDL_DESKTOP = 0
            SHGFP_TYPE_CURRENT = 0
            buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOP, None, SHGFP_TYPE_CURRENT, buf)
            return buf.value
        except Exception:
            pass

    return os.path.expanduser("~/Desktop")


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
    if not os.path.exists(path):
        return path

    base, ext = os.path.splitext(path)
    i = 2

    while True:
        candidate = f"{base}_{i}{ext}"

        if not os.path.exists(candidate):
            return candidate

        i += 1


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


def get_category_info(theme_name: str) -> CategoryInfo:
    norm = "".join(theme_name.casefold().split())

    if norm.startswith("54минипостеры") or norm.startswith("54мп"):
        return CategoryInfo("mini_54", "Мини-постеры 54шт", 54)

    if norm.startswith("36минипостеры") or norm.startswith("36мп"):
        return CategoryInfo("mini_36", "Мини-постеры 36шт", 36)

    if norm.startswith("18минипостеры") or norm.startswith("18мп"):
        return CategoryInfo("mini_18", "Мини-постеры 18шт", 18)

    return CategoryInfo("mini_other", "Мини-постеры", None)


def _scan_direct_child_folders(root_dir: str) -> dict[str, str]:
    index: dict[str, str] = {}

    try:
        with os.scandir(root_dir) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        index.setdefault(entry.name.casefold(), entry.path)
                except Exception:
                    continue
    except Exception:
        pass

    return index


def find_theme_folders_fast(
    root_dir: str,
    target_names: list[str],
    log_fn=None,
    err_fn=None,
    stop_event: threading.Event | None = None,
) -> list[ThemeFolder]:
    wanted_keys = [name.casefold() for name in target_names]
    unique_wanted = set(wanted_keys)

    found_map = _scan_direct_child_folders(root_dir)
    missing_keys = unique_wanted - set(found_map.keys())

    if log_fn:
        direct_found_count = sum(1 for key in unique_wanted if key in found_map)
        log_fn(f"Быстрый поиск: найдено {direct_found_count}/{len(unique_wanted)} папок")

    if missing_keys:
        stack = [root_dir]

        while stack and missing_keys:
            if stop_event and stop_event.is_set():
                break

            cur = stack.pop()

            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        if stop_event and stop_event.is_set():
                            break

                        if not missing_keys:
                            break

                        try:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                        except Exception:
                            continue

                        key = entry.name.casefold()

                        if key in missing_keys:
                            found_map.setdefault(key, entry.path)
                            missing_keys.discard(key)
                        else:
                            stack.append(entry.path)

            except Exception:
                continue

        if log_fn and not (stop_event and stop_event.is_set()):
            total_found_count = sum(1 for key in unique_wanted if key in found_map)
            log_fn(f"Глубокий поиск: найдено {total_found_count}/{len(unique_wanted)} папок")

    found: list[ThemeFolder] = []

    for name in target_names:
        if stop_event and stop_event.is_set():
            break

        key = name.casefold()
        folder = found_map.get(key)
        category = get_category_info(name)

        if folder and os.path.isdir(folder):
            found.append(ThemeFolder(name=name, folder=folder, category=category))
        else:
            if err_fn:
                err_fn(f"{category.label}:\n{name} - папка не найдена")

    return found


def get_image_files(folder: str, stop_event: threading.Event | None = None) -> list[str]:
    files: list[str] = []

    try:
        with os.scandir(folder) as it:
            for entry in it:
                if stop_event and stop_event.is_set():
                    break

                try:
                    if entry.is_file() and os.path.splitext(entry.name)[1].lower() in SUPPORTED_EXTS:
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
    photos: list[str] = []
    current_category_key = None
    processed_themes = 0
    total_themes = len(items)

    for item in items:
        if stop_event and stop_event.is_set():
            break

        if item.category.key != current_category_key:
            current_category_key = item.category.key

            if log_fn:
                log_fn(f"{item.category.label}:")

        files = get_image_files(item.folder, stop_event=stop_event)

        if not files:
            if err_fn and not (stop_event and stop_event.is_set()):
                err_fn(f"{item.category.label}:\n{item.name} - в папке нет изображений")

            processed_themes += 1
            if theme_progress_callback:
                theme_progress_callback(processed_themes, total_themes)
            continue

        if item.category.expected_count is not None and len(files) != item.category.expected_count:
            if err_fn and not (stop_event and stop_event.is_set()):
                err_fn(
                    f"{item.category.label}:\n"
                    f"{item.name} - найдено {len(files)} фото вместо {item.category.expected_count}"
                )

        if item.category.expected_count is not None:
            files = files[:item.category.expected_count]

        photos.extend(files)

        if log_fn:
            log_fn(f"{item.name} - успешно")

        processed_themes += 1
        if theme_progress_callback:
            theme_progress_callback(processed_themes, total_themes)

    return photos


def collect_ready_layers(
    items: list[ThemeFolder],
    err_fn=None,
    stop_event: threading.Event | None = None,
) -> list[list[tuple[str, str | None]]]:
    """
    Для готового набора сохраняет позиции тем.
    Если у какой-то темы нет фото на конкретном номере, место остается пустым,
    а остальные темы не сдвигаются.
    """
    if not items:
        return []

    category = items[0].category
    expected_count = category.expected_count
    all_lists: list[list[str]] = []

    for item in items:
        if stop_event and stop_event.is_set():
            break

        files = get_image_files(item.folder, stop_event=stop_event)

        if not files:
            if err_fn and not (stop_event and stop_event.is_set()):
                err_fn(f"{item.category.label}:\n{item.name} - в папке нет изображений")

            all_lists.append([])
            continue

        if expected_count is not None and len(files) != expected_count:
            if err_fn and not (stop_event and stop_event.is_set()):
                err_fn(
                    f"{item.category.label}:\n"
                    f"{item.name} - найдено {len(files)} фото вместо {expected_count}"
                )

        if expected_count is not None:
            files = files[:expected_count]

        all_lists.append(files)

    if expected_count is None:
        layer_count = max((len(lst) for lst in all_lists), default=0)
    else:
        layer_count = expected_count

    layers: list[list[tuple[str, str | None]]] = []

    for idx in range(layer_count):
        if stop_event and stop_event.is_set():
            break

        layer: list[tuple[str, str | None]] = []

        for item, theme_files in zip(items, all_lists):
            path = theme_files[idx] if idx < len(theme_files) else None
            layer.append((item.name, path))

        layers.append(layer)

    return layers


def split_by_category_runs(items: list[ThemeFolder]) -> list[list[ThemeFolder]]:
    if not items:
        return []

    runs: list[list[ThemeFolder]] = []
    current: list[ThemeFolder] = [items[0]]
    current_key = items[0].category.key

    for item in items[1:]:
        if item.category.key == current_key:
            current.append(item)
        else:
            runs.append(current)
            current = [item]
            current_key = item.category.key

    runs.append(current)
    return runs


def points_to_pixels(value_points: float, dpi: int) -> int:
    inches = float(value_points) / 72.0
    return max(1, int(round(inches * dpi)))


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


def prepare_image_for_pdf(path: str, width_points: float, height_points: float, dpi: int) -> PreparedImage | None:
    if not path or not os.path.exists(path):
        return create_white_placeholder_image(width_points, height_points, dpi)

    target_w = points_to_pixels(width_points, dpi)
    target_h = points_to_pixels(height_points, dpi)

    try:
        with Image.open(path) as img:
            try:
                img.draft("RGB", (target_w, target_h))
            except Exception:
                pass

            img = ImageOps.exif_transpose(img)

            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.getchannel("A"))
                img = bg
            else:
                img = img.convert("RGB")

            # Фото тянется точно в прямоугольник макета, без сохранения пропорций.
            if img.size != (target_w, target_h):
                img = img.resize((target_w, target_h), RESAMPLE_LANCZOS)

            buf = BytesIO()
            img.save(
                buf,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=False,
                progressive=False,
                subsampling=0,
            )
            buf.seek(0)

            return PreparedImage(reader=ImageReader(buf), buffer=buf)
    except Exception:
        return create_white_placeholder_image(width_points, height_points, dpi)


def prepare_batch(
    paths: list[str],
    width_points: float,
    height_points: float,
    dpi: int,
    workers: int,
    err_fn=None,
    stop_event: threading.Event | None = None,
):
    if not paths:
        return []

    if stop_event and stop_event.is_set():
        return []

    workers = max(1, min(int(workers), len(paths)))

    def one(path: str):
        if stop_event and stop_event.is_set():
            return path, None

        try:
            return path, prepare_image_for_pdf(path, width_points, height_points, dpi)
        except Exception as e:
            if err_fn and not (stop_event and stop_event.is_set()):
                err_fn(f"Ошибка подготовки изображения {path}: {e}")
            return path, None

    if workers == 1:
        result = []

        for p in paths:
            if stop_event and stop_event.is_set():
                break

            result.append(one(p))

        return result

    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {}

    try:
        for idx, path in enumerate(paths):
            if stop_event and stop_event.is_set():
                break

            futures[executor.submit(one, path)] = idx

        result = [None] * len(futures)
        pending = set(futures.keys())

        while pending:
            if stop_event and stop_event.is_set():
                for f in pending:
                    f.cancel()
                break

            done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)

            for future in done:
                idx = futures[future]

                try:
                    result[idx] = future.result()
                except Exception as e:
                    if err_fn and not (stop_event and stop_event.is_set()):
                        err_fn(f"Ошибка подготовки изображения: {e}")

                    result[idx] = None

        return [item for item in result if item is not None]

    finally:
        if stop_event and stop_event.is_set():
            executor.shutdown(wait=False, cancel_futures=True)
        else:
            executor.shutdown(wait=True)


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

    def _normal_grid_position(self, pos: int) -> tuple[float, float]:
        x_off = (PAGE_WIDTH - GRID_COLS * self.photo_width) / 2
        y_off = (PAGE_HEIGHT - GRID_ROWS * self.photo_height) / 2

        row, col = divmod(pos, GRID_COLS)
        x = col * self.photo_width + x_off
        y = PAGE_HEIGHT - (row + 1) * self.photo_height - y_off

        return x, y

    @staticmethod
    def _fit_font_size(c: canvas.Canvas, text: str, max_width: float, start_size: float, min_size: float = 8) -> float:
        font_name = get_pdf_font_name()
        size = start_size

        while size >= min_size:
            if c.stringWidth(text, font_name, size) <= max_width:
                return size

            size -= 0.5

        return min_size

    @staticmethod
    def _trim_line_to_width(
        c: canvas.Canvas,
        text: str,
        font_name: str,
        font_size: float,
        max_width: float,
    ) -> str:
        if c.stringWidth(text, font_name, font_size) <= max_width:
            return text

        suffix = "..."

        while text and c.stringWidth(text + suffix, font_name, font_size) > max_width:
            text = text[:-1]

        return text + suffix if text else suffix

    @staticmethod
    def _wrap_text_to_width(
        c: canvas.Canvas,
        text: str,
        font_name: str,
        font_size: float,
        max_width: float,
    ) -> list[str]:
        text = " ".join(str(text).split())

        if not text:
            return [""]

        def split_long_word(word: str) -> list[str]:
            if c.stringWidth(word, font_name, font_size) <= max_width:
                return [word]

            parts: list[str] = []
            current = ""

            for ch in word:
                test = current + ch

                if c.stringWidth(test, font_name, font_size) <= max_width:
                    current = test
                else:
                    if current:
                        parts.append(current)
                    current = ch

            if current:
                parts.append(current)

            return parts

        lines: list[str] = []
        current_line = ""

        for word in text.split():
            word_parts = split_long_word(word)

            for part in word_parts:
                test_line = part if not current_line else current_line + " " + part

                if c.stringWidth(test_line, font_name, font_size) <= max_width:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line)

                    current_line = part

        if current_line:
            lines.append(current_line)

        return lines if lines else [""]

    def _fit_wrapped_text(
        self,
        c: canvas.Canvas,
        text: str,
        max_width: float,
        max_height: float,
        start_size: float,
        min_size: float = 7,
    ) -> tuple[list[str], float, float]:
        font_name = get_pdf_font_name()
        size = start_size

        while size >= min_size:
            lines = self._wrap_text_to_width(c, text, font_name, size, max_width)
            line_height = size * 1.14
            total_height = len(lines) * line_height

            if total_height <= max_height:
                return lines, size, line_height

            size -= 0.5

        size = min_size
        line_height = size * 1.14
        lines = self._wrap_text_to_width(c, text, font_name, size, max_width)

        max_lines = max(1, int(max_height // line_height))

        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self._trim_line_to_width(
                c,
                lines[-1],
                font_name,
                size,
                max_width,
            )

        return lines, size, line_height

    def _draw_wrapped_centered(
        self,
        c: canvas.Canvas,
        lines: list[str],
        center_x: float,
        center_y: float,
        font_size: float,
        line_height: float,
    ):
        font_name = get_pdf_font_name()
        c.setFont(font_name, font_size)

        total_height = len(lines) * line_height
        y = center_y + total_height / 2 - font_size

        for line in lines:
            c.drawCentredString(center_x, y, line)
            y -= line_height

    def _draw_separator_page(
        self,
        c: canvas.Canvas,
        batch_items: list[ThemeFolder],
        pack_start: int,
        category_label: str | None,
    ):
        font_name = get_pdf_font_name()

        c.setStrokeColorRGB(0, 0, 0)
        c.setFillColorRGB(0, 0, 0)

        rect_count = max(0, min(PHOTOS_PER_PAGE, len(batch_items)))

        def draw_underlined_centered(text: str, x_center: float, y_base: float, font_size: float):
            c.setFont(font_name, font_size)
            c.drawCentredString(x_center, y_base, text)

            text_w = c.stringWidth(text, font_name, font_size)
            underline_y = y_base - 3

            c.setLineWidth(1.7)
            c.line(
                x_center - text_w / 2,
                underline_y,
                x_center + text_w / 2,
                underline_y,
            )
            c.setLineWidth(1.1)

        for idx in range(rect_count):
            item = batch_items[idx]
            pack_number = pack_start + idx
            pack_text = f"{pack_number} пак"

            x, y = self._normal_grid_position(idx)

            c.setLineWidth(1.1)
            c.rect(x, y, self.photo_width, self.photo_height, stroke=1, fill=0)

            center_x = x + self.photo_width / 2

            # Верхняя часть: категория + пак или только пак
            top_center_y = y + self.photo_height * 0.72

            if category_label:
                title_size = self._fit_font_size(c, category_label, self.photo_width * 0.90, 25, min_size=11)
                pack_size = self._fit_font_size(c, pack_text, self.photo_width * 0.90, 30, min_size=13)

                gap = 10
                block_h = title_size + gap + pack_size

                title_y = top_center_y + block_h / 2 - title_size
                pack_y = title_y - gap - pack_size

                c.setFont(font_name, title_size)
                c.drawCentredString(center_x, title_y, category_label)

                draw_underlined_centered(pack_text, center_x, pack_y, pack_size)

            else:
                pack_size = self._fit_font_size(c, pack_text, self.photo_width * 0.90, 32, min_size=15)
                pack_y = top_center_y - pack_size / 3

                draw_underlined_centered(pack_text, center_x, pack_y, pack_size)

            # Нижняя часть: название темы из списка.
            # Крупный текст с переносом строк, чтобы не вылезал за прямоугольник.
            theme_lines, theme_font_size, theme_line_height = self._fit_wrapped_text(
                c,
                item.name,
                max_width=self.photo_width * 0.90,
                max_height=self.photo_height * 0.42,
                start_size=14,
                min_size=7,
            )

            bottom_center_y = y + self.photo_height * 0.23

            self._draw_wrapped_centered(
                c,
                theme_lines,
                center_x,
                bottom_center_y,
                theme_font_size,
                theme_line_height,
            )

    def _draw_layer(
        self,
        c: canvas.Canvas,
        layer: list[tuple[str, str | None]],
        progress_done: int,
        progress_total: int,
        progress_callback=None,
        err_fn=None,
        pause_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
    ) -> int:
        existing_paths = [path for _, path in layer]

        prepared = prepare_batch(
            existing_paths,
            self.photo_width,
            self.photo_height,
            self.dpi,
            self.threads,
            err_fn=err_fn,
            stop_event=stop_event,
        )

        prepared_map = {path: prepared_img for path, prepared_img in prepared}

        for idx, (theme_name, src_path) in enumerate(layer):
            if stop_event and stop_event.is_set():
                break

            if pause_event:
                pause_event.wait()

            try:
                prepared_img = prepared_map.get(src_path)
                x, y = self._normal_grid_position(idx)

                if prepared_img is not None:
                    c.drawImage(
                        prepared_img.reader,
                        x,
                        y,
                        self.photo_width,
                        self.photo_height,
                        preserveAspectRatio=False,
                        mask=None,
                    )
                else:
                    placeholder = create_white_placeholder_image(PHOTO_WIDTH, PHOTO_HEIGHT, self.dpi)
                    c.drawImage(
                        placeholder.reader,
                        x,
                        y,
                        PHOTO_WIDTH,
                        PHOTO_HEIGHT,
                        preserveAspectRatio=False,
                        mask=None,
                    )

                progress_done += 1

                if progress_callback and (progress_done % 5 == 0 or progress_done == progress_total):
                    progress_callback(progress_done, progress_total)

            except Exception as e:
                if err_fn and not (stop_event and stop_event.is_set()):
                    err_fn(f"{theme_name} - ошибка вставки изображения: {e}")

        return progress_done

    def create_pdf(
        self,
        photo_paths: list[str],
        output_path: str,
        progress_callback=None,
        log_fn=None,
        err_fn=None,
        pause_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
    ):
        output_path = unique_output_path(output_path)

        total = len(photo_paths)

        if total == 0:
            if err_fn:
                err_fn("Нет изображений для PDF.")

            return output_path, 0, 0

        c = canvas.Canvas(output_path, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
        c.setPageCompression(True)

        processed = 0
        pdf_pages = 0

        try:
            for batch_start in range(0, total, self.batch_size):
                if stop_event and stop_event.is_set():
                    break

                if pause_event:
                    pause_event.wait()

                batch = photo_paths[batch_start:batch_start + self.batch_size]

                prepared = prepare_batch(
                    batch,
                    self.photo_width,
                    self.photo_height,
                    self.dpi,
                    self.threads,
                    err_fn=err_fn,
                    stop_event=stop_event,
                )

                for local_idx, (src_path, prepared_img) in enumerate(prepared, start=1):
                    if stop_event and stop_event.is_set():
                        break

                    if pause_event:
                        pause_event.wait()

                    try:
                        global_idx = batch_start + local_idx
                        pos = (global_idx - 1) % PHOTOS_PER_PAGE
                        x, y = self._normal_grid_position(pos)

                        if prepared_img is not None:
                            c.drawImage(
                                prepared_img.reader,
                                x,
                                y,
                                self.photo_width,
                                self.photo_height,
                                preserveAspectRatio=False,
                                mask=None,
                            )

                        if pos == PHOTOS_PER_PAGE - 1 or global_idx == total:
                            c.showPage()
                            pdf_pages += 1

                        processed += 1

                        if progress_callback and (processed % 5 == 0 or processed == total):
                            progress_callback(processed, total)

                    except Exception as e:
                        if err_fn and not (stop_event and stop_event.is_set()):
                            err_fn(f"Ошибка вставки изображения {src_path}: {e}")

        finally:
            c.save()

        size_kb = os.path.getsize(output_path) // 1024 if os.path.exists(output_path) else 0

        if log_fn:
            log_fn(f"PDF сохранён: {output_path} ({size_kb} КБ)")

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
        pause_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
    ):
        output_path = unique_output_path(output_path)

        if not items:
            if err_fn:
                err_fn("Нет тем для PDF.")

            return output_path, 0, 0

        total_images_estimate = 0

        for item in items:
            if item.category.expected_count is not None:
                total_images_estimate += item.category.expected_count
            else:
                total_images_estimate += len(get_image_files(item.folder, stop_event=stop_event))

        c = canvas.Canvas(output_path, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
        c.setPageCompression(True)

        done_images = 0
        done_themes = 0
        pdf_pages = 0
        total_themes = len(items)

        try:
            runs = split_by_category_runs(items)

            for run in runs:
                if stop_event and stop_event.is_set():
                    break

                category = run[0].category
                pack_start = 1
                first_batch_in_category = True

                if log_fn:
                    log_fn(f"{category.label}:")

                for batch_start in range(0, len(run), PHOTOS_PER_PAGE):
                    if stop_event and stop_event.is_set():
                        break

                    if pause_event:
                        pause_event.wait()

                    batch_items = run[batch_start:batch_start + PHOTOS_PER_PAGE]

                    if separator_pages:
                        self._draw_separator_page(
                            c,
                            batch_items=batch_items,
                            pack_start=pack_start,
                            category_label=category.label if first_batch_in_category else None,
                        )

                        c.showPage()
                        pdf_pages += 1

                    layers = collect_ready_layers(batch_items, err_fn=err_fn, stop_event=stop_event)

                    for layer_idx, layer in enumerate(layers, start=1):
                        if stop_event and stop_event.is_set():
                            break

                        if pause_event:
                            pause_event.wait()

                        done_images = self._draw_layer(
                            c,
                            layer,
                            done_images,
                            total_images_estimate,
                            progress_callback=progress_callback,
                            err_fn=err_fn,
                            pause_event=pause_event,
                            stop_event=stop_event,
                        )

                        if stop_event and stop_event.is_set():
                            break

                        c.showPage()
                        pdf_pages += 1

                    for item in batch_items:
                        if log_fn:
                            log_fn(f"{item.name} - успешно")

                        done_themes += 1

                        if theme_progress_callback:
                            theme_progress_callback(done_themes, total_themes)

                    pack_start += len(batch_items)
                    first_batch_in_category = False

        finally:
            c.save()

        size_kb = os.path.getsize(output_path) // 1024 if os.path.exists(output_path) else 0

        if log_fn:
            log_fn(f"PDF сохранён: {output_path} ({size_kb} КБ)")

        return output_path, size_kb, pdf_pages


# ---------------- GUI ----------------

class App:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title("Мини-постеры 10х15см. Макеты для печати")
        self.root.geometry("1550x1100")
        self.root.minsize(1100, 680)

        self.style = tb.Style("darkly")

        self.root_folder_var = tk.StringVar()
        self.save_folder_var = tk.StringVar(value=get_desktop_folder())
        self.threads_var = tk.IntVar(value=min(7, max(2, (os.cpu_count() or 8) - 1)))
        self.dpi_var = tk.IntVar(value=DEFAULT_DPI)

        self.photo_width_mm_var = tk.StringVar(value=str(PHOTO_WIDTH_MM))
        self.photo_height_mm_var = tk.StringVar(value=str(PHOTO_HEIGHT_MM))

        # Тумблер готового набора включен по умолчанию
        self.butter_var = tk.BooleanVar(value=True)

        # Новый отдельный тумблер белых листов включен по умолчанию
        self.separator_pages_var = tk.BooleanVar(value=True)

        self.pause_event = threading.Event()
        self.pause_event.set()

        self.stop_event = threading.Event()
        self.ui_queue = queue.Queue()
        self.worker_thread: threading.Thread | None = None

        self.run_start_ts: float | None = None
        self.last_created_pdf: str | None = None

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
        tb.Entry(row2, textvariable=self.save_folder_var).pack(side=LEFT, fill=X, expand=YES, padx=8)
        tb.Button(row2, text="Выбрать", bootstyle=PRIMARY, command=self.choose_save_folder).pack(side=LEFT)

        row3 = tb.Frame(paths)
        row3.pack(fill=X, pady=4)

        tb.Label(row3, text="Потоки:", width=24, anchor=W).pack(side=LEFT)
        tb.Spinbox(row3, from_=1, to=16, textvariable=self.threads_var, width=6).pack(side=LEFT, padx=(0, 14))

        tb.Label(row3, text="DPI картинок:", anchor=W).pack(side=LEFT)
        tb.Spinbox(row3, from_=120, to=300, increment=10, textvariable=self.dpi_var, width=6).pack(side=LEFT, padx=(8, 14))

        self.butter_cb = tb.Checkbutton(
            row3,
            text="Вставить фото в готовый набор",
            variable=self.butter_var,
            bootstyle="round-toggle",
        )
        self.butter_cb.pack(side=LEFT, padx=(0, 12))

        self.separator_pages_cb = tb.Checkbutton(
            row3,
            text="Добавлять белые листы",
            variable=self.separator_pages_var,
            bootstyle="round-toggle",
        )
        self.separator_pages_cb.pack(side=LEFT, padx=(0, 12))

        tb.Label(
            row3,
            text="Больше 7 потоков не дает прироста",
            foreground="#c9c9c9",
        ).pack(side=LEFT)

        row4 = tb.Frame(paths)
        row4.pack(fill=X, pady=4)

        tb.Label(row4, text="Размер фото:", width=24, anchor=W).pack(side=LEFT)

        tb.Label(row4, text="Ширина,мм:").pack(side=LEFT)
        tb.Entry(row4, textvariable=self.photo_width_mm_var, width=8).pack(side=LEFT, padx=(6, 14))

        tb.Label(row4, text="Высота,мм:").pack(side=LEFT)
        tb.Entry(row4, textvariable=self.photo_height_mm_var, width=8).pack(side=LEFT, padx=(6, 14))

        tb.Label(
            row4,
            text=f"",
            foreground="#c9c9c9",
        ).pack(side=LEFT)

        names_frame = tb.Labelframe(outer, text="Артикулы/Наименования тем", padding=10)
        names_frame.pack(fill=X, pady=(10, 0))

        self.names_box = ScrolledText(names_frame, height=8, autohide=True)
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

        self.lbl_progress = tb.Label(progress_frame, text="0 / 0 тем (0.0%)  |  0 / 0 файлов (0.0%)")
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
        folder = filedialog.askdirectory(title="Выбери корневую папку")

        if folder:
            self.root_folder_var.set(folder)

    def choose_save_folder(self):
        folder = filedialog.askdirectory(title="Выбери папку сохранения")

        if folder:
            self.save_folder_var.set(folder)

    def paste_names(self):
        try:
            names = self.root.clipboard_get()
        except Exception:
            messagebox.showerror("Ошибка", "Не удалось получить данные из буфера обмена.")
            return

        names = names.replace("\r\n", "\n").replace("\r", "\n").strip()

        if not names:
            return

        current_text = self.names_box.get("1.0", "end-1c")

        if current_text and not current_text.endswith("\n"):
            self.names_box.insert("end", "\n")

        self.names_box.insert("end", names)
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

    def start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        root_folder = self.root_folder_var.get().strip()

        if not root_folder or not os.path.isdir(root_folder):
            messagebox.showerror("Ошибка", "Укажите корректную папку с материалами.")
            return

        save_folder = self.save_folder_var.get().strip()

        if not save_folder or not os.path.isdir(save_folder):
            messagebox.showerror("Ошибка", "Выбери корректную папку сохранения!")
            return

        names = [n.strip() for n in self.names_box.get("1.0", "end").splitlines() if n.strip()]

        if not names:
            messagebox.showerror("Ошибка", "Вставь названия папок!")
            return

        try:
            threads = max(1, min(16, int(self.threads_var.get())))
        except Exception:
            threads = 4

        try:
            dpi = max(120, min(300, int(self.dpi_var.get())))
        except Exception:
            dpi = DEFAULT_DPI

        try:
            photo_width_mm = float(self.photo_width_mm_var.get().strip().replace(",", "."))
            photo_height_mm = float(self.photo_height_mm_var.get().strip().replace(",", "."))

            if photo_width_mm <= 0 or photo_height_mm <= 0:
                raise ValueError

        except Exception:
            messagebox.showerror("Ошибка", "Ширина и высота картинки должны быть числами больше 0.")
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
        self.last_created_pdf = None
        self._set_progress(0, 0, 0, 0)

        self.worker_thread = threading.Thread(
            target=self.process_generate,
            args=(
                root_folder,
                save_folder,
                names,
                threads,
                dpi,
                self.butter_var.get(),
                self.separator_pages_var.get(),
                photo_width_mm,
                photo_height_mm,
            ),
            daemon=True,
        )
        self.worker_thread.start()

    def toggle_theme(self):
        cur = self.style.theme.name
        new = "cosmo" if cur == "darkly" else "darkly"
        self.style.theme_use(new)

    def _log(self, msg: str):
        self.ui_queue.put(("log", msg))

    def _err(self, msg: str):
        self.ui_queue.put(("err", msg))

    def _set_progress(
        self,
        done_themes: int,
        total_themes: int,
        done_files: int,
        total_files: int,
    ):
        self.ui_queue.put(("prog", done_themes, total_themes, done_files, total_files))

    def _finish_ui(self, done_message: str | None = None, out_path: str | None = None):
        self.ui_queue.put(("finish", done_message, out_path))

    def _poll_ui_queue(self):
        max_items_per_tick = 25
        processed = 0

        try:
            while processed < max_items_per_tick:
                item = self.ui_queue.get_nowait()
                processed += 1
                kind = item[0]

                if kind == "log":
                    self._append_text(self.log_box, item[1])

                elif kind == "err":
                    self._append_text(self.err_box, item[1])

                elif kind == "prog":
                    done_themes, total_themes, done_files, total_files = item[1], item[2], item[3], item[4]

                    self.progress["maximum"] = max(1, total_themes)
                    self.progress["value"] = done_themes

                    theme_pct = (done_themes / total_themes * 100.0) if total_themes else 0.0
                    file_pct = (done_files / total_files * 100.0) if total_files else 0.0

                    self.lbl_progress.config(
                        text=(
                            f"Тем: {done_themes} / {total_themes} ({theme_pct:.1f}%)"
                            f"  |  Файлов: {done_files} / {total_files} ({file_pct:.1f}%)"
                        )
                    )

                    elapsed = time.time() - self.run_start_ts if self.run_start_ts else 0

                    if done_files > 0 and total_files > 0:
                        avg = elapsed / done_files
                        remaining = (total_files - done_files) * avg
                        self.lbl_eta.config(text=f"Осталось: {fmt_time(remaining)}  |  Прошло: {fmt_time(elapsed)}")
                    elif done_themes > 0 and total_themes > 0:
                        avg = elapsed / done_themes
                        remaining = (total_themes - done_themes) * avg
                        self.lbl_eta.config(text=f"Осталось: {fmt_time(remaining)}  |  Прошло: {fmt_time(elapsed)}")
                    else:
                        self.lbl_eta.config(text=f"Осталось: --:--  |  Прошло: {fmt_time(elapsed)}")

                elif kind == "finish":
                    done_message, out_path = item[1], item[2]

                    self.b_start.config(state=NORMAL)
                    self.b_paste.config(state=NORMAL)
                    self.b_pause.config(state=DISABLED)
                    self.b_resume.config(state=DISABLED)
                    self.b_stop.config(state=DISABLED)

                    if out_path:
                        self.last_created_pdf = out_path

                    if done_message:
                        messagebox.showinfo("Готово", done_message)

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

    def process_generate(
        self,
        root_folder: str,
        save_folder: str,
        names: list[str],
        threads: int,
        dpi: int,
        butter_mode: bool,
        separator_pages: bool,
        photo_width_mm: float,
        photo_height_mm: float,
    ):
        t0 = time.time()
        out_path: str | None = None

        try:
            self._log("Старт создания PDF")
            self._log(f"Тем: {len(names)}")
            self._log(f"Потоки подготовки изображений: {threads}")
            self._log(f"DPI подготовки изображений: {dpi}")
            self._log(f"Формат: мини-постеры {photo_width_mm:g}×{photo_height_mm:g} мм, 9 фото на листе, сетка 3×3")

            if butter_mode:
                self._log("Режим: вставить фото в готовый набор")
                self._log(f"Белые листы с прямоугольниками: {'включены' if separator_pages else 'выключены'}")

            items = find_theme_folders_fast(
                root_folder,
                names,
                log_fn=self._log,
                err_fn=self._err,
                stop_event=self.stop_event,
            )

            if self.stop_event.is_set():
                dt = time.time() - t0
                msg = f"Остановлено.\nВремя: {fmt_time(dt)}"
                self._log(msg.replace("\n", " "))
                self._finish_ui(msg, None)
                return

            if not items:
                self._err("Не найдено ни одной папки из списка.")
                self._finish_ui(None, None)
                return

            total_themes = len(items)

            date_str = datetime.now().strftime("%d.%m")
            out = os.path.join(save_folder, f"Мини-постеры от {date_str}.pdf")

            gen = PDFGenerator(
                threads=threads,
                dpi=dpi,
                photo_width_mm=photo_width_mm,
                photo_height_mm=photo_height_mm,
            )

            if butter_mode:
                total_files = 0

                for item in items:
                    if item.category.expected_count is not None:
                        total_files += item.category.expected_count
                    else:
                        total_files += len(get_image_files(item.folder, stop_event=self.stop_event))

                done_themes = 0
                done_files = 0

                self._set_progress(done_themes, total_themes, done_files, total_files)

                def file_progress(done, total_count):
                    nonlocal done_files
                    done_files = done
                    self._set_progress(done_themes, total_themes, done_files, total_files)

                def theme_progress(done, total_count):
                    nonlocal done_themes
                    done_themes = done
                    self._set_progress(done_themes, total_themes, done_files, total_files)

                out_path, size, pdf_pages = gen.create_pdf_ready_sets(
                    items,
                    out,
                    progress_callback=file_progress,
                    theme_progress_callback=theme_progress,
                    separator_pages=separator_pages,
                    log_fn=self._log,
                    err_fn=self._err,
                    pause_event=self.pause_event,
                    stop_event=self.stop_event,
                )

            else:
                self._log("Режим: обычный PDF")

                done_themes = 0
                done_files = 0

                self._set_progress(done_themes, total_themes, done_files, 0)

                def theme_progress(done, total_count):
                    nonlocal done_themes
                    done_themes = done
                    self._set_progress(done_themes, total_themes, done_files, 0)

                photos = collect_images_normal(
                    items,
                    log_fn=self._log,
                    err_fn=self._err,
                    stop_event=self.stop_event,
                    theme_progress_callback=theme_progress,
                )

                if self.stop_event.is_set():
                    dt = time.time() - t0
                    msg = f"Остановлено.\nВремя: {fmt_time(dt)}"
                    self._log(msg.replace("\n", " "))
                    self._finish_ui(msg, None)
                    return

                if not photos:
                    self._err("Не найдено ни одной картинки в найденных папках.")
                    self._finish_ui(None, None)
                    return

                total_files = len(photos)

                self._set_progress(done_themes, total_themes, done_files, total_files)

                def file_progress(done, total_count):
                    nonlocal done_files
                    done_files = done
                    self._set_progress(total_themes, total_themes, done_files, total_files)

                out_path, size, pdf_pages = gen.create_pdf(
                    photos,
                    out,
                    progress_callback=file_progress,
                    log_fn=self._log,
                    err_fn=self._err,
                    pause_event=self.pause_event,
                    stop_event=self.stop_event,
                )

            dt = time.time() - t0

            if self.stop_event.is_set():
                msg = f"Остановлено.\nPDF сохранён частично:\n{out_path}\n\nВремя: {fmt_time(dt)}"
                self._log(msg.replace("\n", " "))
                self._finish_ui(msg, out_path)

            else:
                msg = (
                    f"PDF создан:\n{out_path}\n\n"
                    f"Размер: {size} КБ\n"
                    f"PDF листов: {pdf_pages}\n"
                    f"Время: {fmt_time(dt)}"
                )
                self._log(f"Готово! Время: {fmt_time(dt)}")
                self._finish_ui(msg, out_path)

        except Exception as e:
            self._err(f"Критическая ошибка: {e}")
            self._finish_ui(None, out_path)


def main():
    root = tb.Window(themename="darkly")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()