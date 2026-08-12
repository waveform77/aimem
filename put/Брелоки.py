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
from ttkbootstrap.scrolled import ScrolledText

from PIL import Image, ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, A3
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader


# ---------------- НАСТРОЙКИ ----------------

TITLE = "Брелоки. Макеты для печати"

PHOTO_WIDTH_MM = 37
PHOTO_HEIGHT_MM = 54

SUPPORTED_EXTS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tiff",
    ".tif",
    ".webp",
)

DEFAULT_DPI = 220
JPEG_QUALITY = 92

PAGE_SIZES = {
    "A4 (210×297 мм)": A4,
    "A3 (297×420 мм)": A3,
    "SRA3 (320×450 мм)": (320 * mm, 450 * mm),
}

PAGE_LAYOUTS = {
    "A4 (210×297 мм)": {
        "cols": 5,
        "rows": 5,
        "margin_mm": 0,
    },
    "A3 (297×420 мм)": {
        "cols": 7,
        "rows": 7,
        "margin_mm": 10,
    },
    "SRA3 (320×450 мм)": {
        "cols": 8,
        "rows": 8,
        "margin_mm": 0,
    },
}


@dataclass
class PreparedImage:
    reader: ImageReader
    buffer: BytesIO


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{minutes:02d}:{secs:02d}"


def unique_output_path(path: str) -> str:
    if not path.lower().endswith(".pdf"):
        path += ".pdf"

    if not os.path.exists(path):
        return path

    base, extension = os.path.splitext(path)
    number = 2

    while True:
        candidate = f"{base}_{number}{extension}"
        if not os.path.exists(candidate):
            return candidate
        number += 1


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


def make_output_pdf_path(save_folder: str) -> str:
    date_string = datetime.now().strftime("%d.%m")
    return unique_output_path(os.path.join(save_folder, f"Брелоки от {date_string}.pdf"))


def normalize_name(value: str) -> str:
    return "".join(str(value).casefold().split())


def mirror_row_order(batch: list[str], row_size: int) -> list[str]:
    mirrored = []

    for index in range(0, len(batch), row_size):
        row = batch[index:index + row_size]
        mirrored.extend(row[::-1])

    return mirrored


def build_file_index(root_dir: str, log_fn=None) -> dict[str, str]:
    index: dict[str, str] = {}
    total_files = 0

    for directory_path, _, filenames in os.walk(root_dir):
        for filename in filenames:
            extension = os.path.splitext(filename)[1].lower()

            if extension not in SUPPORTED_EXTS:
                continue

            filename_without_extension = os.path.splitext(filename)[0]
            key = normalize_name(filename_without_extension)

            index.setdefault(key, os.path.join(directory_path, filename))
            total_files += 1

    if log_fn:
        log_fn(
            f"Индекс файлов готов: "
            f"{len(index)} уникальных имён, "
            f"всего файлов: {total_files}"
        )

    return index


def match_files(
    root_dir: str,
    names: list[str],
    log_fn=None,
    err_fn=None,
) -> tuple[list[str], list[str]]:
    index = build_file_index(root_dir, log_fn=log_fn)

    matched: list[str] = []
    missing: list[str] = []

    for name in names:
        path = index.get(normalize_name(name))

        if path:
            matched.append(path)

            if log_fn:
                log_fn(f"{name} - успешно")
        else:
            missing.append(name)
            matched.append("")

            if err_fn:
                err_fn(f"{name} - файл не найден")

    return matched, missing


def points_to_pixels(value_points: float, dpi: int) -> int:
    return max(1, int(round(float(value_points) / 72.0 * dpi)))


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
    width_points: float,
    height_points: float,
    dpi: int,
) -> PreparedImage:
    if not path or not os.path.exists(path):
        return create_white_placeholder_image(width_points, height_points, dpi)

    target_width = points_to_pixels(width_points, dpi)
    target_height = points_to_pixels(height_points, dpi)

    try:
        with Image.open(path) as image:
            try:
                image.draft("RGB", (target_width, target_height))
            except Exception:
                pass

            image = ImageOps.exif_transpose(image)

            if (
                image.mode in ("RGBA", "LA")
                or (image.mode == "P" and "transparency" in image.info)
            ):
                rgba = image.convert("RGBA")

                alpha_bbox = rgba.getchannel("A").getbbox()
                if alpha_bbox:
                    rgba = rgba.crop(alpha_bbox)

                base = Image.new("RGB", rgba.size, (255, 255, 255))
                base.paste(rgba, (0, 0), rgba.getchannel("A"))
                image = base
            else:
                image = image.convert("RGB")

            prepared_image = ImageOps.fit(
                image,
                (target_width, target_height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )

            buffer = BytesIO()

            prepared_image.save(
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
        return create_white_placeholder_image(width_points, height_points, dpi)


def prepare_batch(
    paths: list[str],
    width_points: float,
    height_points: float,
    dpi: int,
    workers: int,
    err_fn=None,
) -> list[tuple[str, PreparedImage | None]]:
    if not paths:
        return []

    workers = max(1, min(int(workers), len(paths)))

    def prepare_one(path: str) -> tuple[str, PreparedImage | None]:
        try:
            prepared = prepare_image_for_pdf(
                path,
                width_points,
                height_points,
                dpi,
            )

            return path, prepared

        except Exception as error:
            if err_fn:
                err_fn(f"Ошибка подготовки изображения {path}: {error}")

            return path, create_white_placeholder_image(width_points, height_points, dpi)

    if workers == 1:
        return [prepare_one(path) for path in paths]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(prepare_one, paths))


class PDFGenerator:
    def __init__(
        self,
        threads: int = 4,
        dpi: int = DEFAULT_DPI,
        page_key: str = "A3 (297×420 мм)",
        photo_width_mm: float = PHOTO_WIDTH_MM,
        photo_height_mm: float = PHOTO_HEIGHT_MM,
    ):
        self.threads = max(1, int(threads))
        self.dpi = max(120, min(300, int(dpi)))

        self.page_key = page_key if page_key in PAGE_SIZES else "A3 (297×420 мм)"
        self.page_size = PAGE_SIZES[self.page_key]
        self.layout = PAGE_LAYOUTS[self.page_key]

        self.photo_width_mm = float(photo_width_mm)
        self.photo_height_mm = float(photo_height_mm)
        self.photo_width = self.photo_width_mm * mm
        self.photo_height = self.photo_height_mm * mm

    def get_photos_per_page(self) -> int:
        return int(self.layout["cols"] * self.layout["rows"])

    def _positions(self, count: int) -> list[tuple[float, float]]:
        columns = int(self.layout["cols"])
        rows = int(self.layout["rows"])
        margin_mm = float(self.layout["margin_mm"])

        page_width, page_height = self.page_size

        cell_width = self.photo_width
        cell_height = self.photo_height

        grid_width = columns * cell_width
        grid_height = rows * cell_height

        usable_width = page_width - 2 * margin_mm * mm
        usable_height = page_height - 2 * margin_mm * mm

        if grid_width > usable_width or grid_height > usable_height:
            raise ValueError(
                f"Размер {self.photo_width_mm:g}×{self.photo_height_mm:g} мм "
                f"не помещается на лист {self.page_key} "
                f"при сетке {columns}×{rows}."
            )

        start_x = margin_mm * mm + (usable_width - grid_width) / 2
        start_y = margin_mm * mm + (usable_height - grid_height) / 2

        positions = []

        for row_index in range(rows):
            row_start = row_index * columns
            row_count = min(columns, max(0, count - row_start))

            if row_count <= 0:
                break

            row_total_width = row_count * cell_width
            row_x = start_x + (grid_width - row_total_width) / 2
            y = start_y + (rows - 1 - row_index) * cell_height

            for column_index in range(row_count):
                x = row_x + column_index * cell_width
                positions.append((x, y))

        return positions

    def _draw_page(
        self,
        pdf_canvas,
        prepared_items: list[tuple[str, PreparedImage | None]],
        progress_tick=None,
        err_fn=None,
    ):
        positions = self._positions(len(prepared_items))

        for (source_path, prepared_image), (x, y) in zip(prepared_items, positions):
            try:
                if prepared_image is not None:
                    pdf_canvas.drawImage(
                        prepared_image.reader,
                        x,
                        y,
                        self.photo_width,
                        self.photo_height,
                        preserveAspectRatio=False,
                        mask=None,
                    )

            except Exception as error:
                if err_fn:
                    err_fn(f"Ошибка вставки {source_path}: {error}")

            if progress_tick:
                progress_tick()

        pdf_canvas.showPage()

    def create_pdf(
        self,
        image_paths: list[str],
        output_path: str,
        progress_callback=None,
        log_fn=None,
        err_fn=None,
        pause_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
    ) -> tuple[str, int, int]:
        output_path = unique_output_path(output_path)
        total_themes = len(image_paths)

        if total_themes == 0:
            if err_fn:
                err_fn("Нет изображений для PDF.")

            return output_path, 0, 0

        pdf_canvas = canvas.Canvas(output_path, pagesize=self.page_size)
        pdf_canvas.setPageCompression(True)

        photos_per_page = self.get_photos_per_page()
        columns = int(self.layout["cols"])

        completed_themes = 0
        pages_created = 0

        def theme_completed():
            nonlocal completed_themes

            completed_themes += 1

            if progress_callback:
                progress_callback(completed_themes, total_themes)

        try:
            page_number = 1

            for start_index in range(0, total_themes, photos_per_page):
                if stop_event and stop_event.is_set():
                    break

                if pause_event:
                    pause_event.wait()

                batch_paths = image_paths[start_index:start_index + photos_per_page]

                prepared = prepare_batch(
                    batch_paths,
                    self.photo_width,
                    self.photo_height,
                    self.dpi,
                    self.threads,
                    err_fn=err_fn,
                )

                prepared_by_path = {
                    path: prepared_image
                    for path, prepared_image in prepared
                }

                if log_fn:
                    log_fn(f"Создаётся страница {page_number} — обычная")

                self._draw_page(
                    pdf_canvas,
                    prepared,
                    progress_tick=None,
                    err_fn=err_fn,
                )

                pages_created += 1
                page_number += 1

                if stop_event and stop_event.is_set():
                    break

                if pause_event:
                    pause_event.wait()

                mirrored_paths = mirror_row_order(batch_paths, row_size=columns)

                mirrored_prepared = [
                    (path, prepared_by_path.get(path))
                    for path in mirrored_paths
                ]

                if log_fn:
                    log_fn(f"Создаётся страница {page_number} — зеркальная")

                self._draw_page(
                    pdf_canvas,
                    mirrored_prepared,
                    progress_tick=theme_completed,
                    err_fn=err_fn,
                )

                pages_created += 1
                page_number += 1

        finally:
            pdf_canvas.save()

        size_kb = os.path.getsize(output_path) // 1024 if os.path.exists(output_path) else 0

        return output_path, size_kb, pages_created


class App:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title(TITLE)
        self.root.geometry("1350x1100")
        self.root.minsize(1100, 680)

        self.style = tb.Style("darkly")

        self.root_folder_var = tk.StringVar()
        self.output_pdf_var = tk.StringVar(value=get_desktop_folder())

        self.threads_var = tk.IntVar(
            value=min(
                7,
                max(2, (os.cpu_count() or 8) - 1),
            )
        )

        self.dpi_var = tk.IntVar(value=DEFAULT_DPI)
        self.page_var = tk.StringVar(value="A3 (297×420 мм)")

        self.photo_width_var = tk.StringVar(value=str(PHOTO_WIDTH_MM))
        self.photo_height_var = tk.StringVar(value=str(PHOTO_HEIGHT_MM))

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

        tb.Entry(
            row2,
            textvariable=self.output_pdf_var,
        ).pack(
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

        tb.Label(row3, text="Лист:").pack(side=LEFT)

        tb.Combobox(
            row3,
            textvariable=self.page_var,
            values=list(PAGE_SIZES.keys()),
            state="readonly",
            width=18,
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
            text="Размер фото:",
            width=24,
            anchor=W,
        ).pack(side=LEFT)

        tb.Label(row4, text="Ширина,мм").pack(side=LEFT)

        tb.Entry(
            row4,
            textvariable=self.photo_width_var,
            width=8,
        ).pack(side=LEFT, padx=(6, 12))

        tb.Label(row4, text="Высота,мм").pack(side=LEFT)

        tb.Entry(
            row4,
            textvariable=self.photo_height_var,
            width=8,
        ).pack(side=LEFT, padx=(6, 12))

        tb.Label(
            row4,
            text=f"",
            foreground="#c9c9c9",
        ).pack(side=LEFT)

        names_frame = tb.Labelframe(
            outer,
            text="Артикулы/Наименования файлов",
            padding=10,
        )

        names_frame.pack(fill=X, pady=(10, 0))

        self.names_box = ScrolledText(
            names_frame,
            height=8,
            autohide=True,
        )

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
        self.b_clear.pack(side=LEFT, padx=8)

        self.b_theme = tb.Button(
            buttons,
            text="Сменить тему",
            command=self.toggle_theme,
            bootstyle=INFO,
        )
        self.b_theme.pack(side=LEFT)

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

        self.lbl_progress = tb.Label(
            progress_frame,
            text="0 / 0 тем (0.0%)",
        )
        self.lbl_progress.pack(anchor=W, pady=(6, 0))

        self.lbl_eta = tb.Label(
            progress_frame,
            text="Осталось: --:--  |  Прошло: --:--",
            foreground="#c9c9c9",
        )
        self.lbl_eta.pack(anchor=W, pady=(4, 0))

        logs_wrapper = tb.Frame(outer)
        logs_wrapper.pack(fill=BOTH, expand=YES, pady=(10, 0))
        logs_wrapper.columnconfigure(0, weight=1)
        logs_wrapper.columnconfigure(1, weight=1)
        logs_wrapper.rowconfigure(0, weight=1)

        left = tb.Labelframe(
            logs_wrapper,
            text="Логи",
            padding=8,
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        right = tb.Labelframe(
            logs_wrapper,
            text="Ошибки / предупреждения",
            padding=8,
        )
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self.log_box = ScrolledText(
            left,
            height=16,
            autohide=True,
        )
        self.log_box.pack(fill=BOTH, expand=YES)

        self.err_box = ScrolledText(
            right,
            height=16,
            autohide=True,
        )
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
        folder = filedialog.askdirectory(title="Выбери папку с фото")

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
        self._log("Работа продолжена.")

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

    def _finish_ui(self, message: str | None = None):
        self.ui_queue.put(("finish", message))

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
                            text=(
                                f"Осталось: {fmt_time(remaining)}"
                                f"  |  Прошло: {fmt_time(elapsed)}"
                            )
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

    def _parse_positive_float(self, value: str, field_name: str) -> float:
        value = value.strip().replace(",", ".")

        try:
            result = float(value)
        except Exception:
            raise ValueError(f"{field_name} должна быть числом.")

        if result <= 0:
            raise ValueError(f"{field_name} должна быть больше нуля.")

        return result

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
            messagebox.showerror("Ошибка", "Вставь наименования файлов!")
            return

        output_pdf = make_output_pdf_path(save_folder)

        try:
            threads = max(1, min(16, int(self.threads_var.get())))
        except Exception:
            threads = 4

        try:
            dpi = max(120, min(300, int(self.dpi_var.get())))
        except Exception:
            dpi = DEFAULT_DPI

        try:
            photo_width_mm = self._parse_positive_float(
                self.photo_width_var.get(),
                "Ширина картинки",
            )
            photo_height_mm = self._parse_positive_float(
                self.photo_height_var.get(),
                "Высота картинки",
            )
        except ValueError as error:
            messagebox.showerror("Ошибка", str(error))
            return

        page_key = self.page_var.get()

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
        self._log("Работа началась.")
        self._log(f"Размер картинки: {photo_width_mm:g}×{photo_height_mm:g} мм")

        self.worker_thread = threading.Thread(
            target=self.process_generate,
            args=(
                root_folder,
                output_pdf,
                names,
                threads,
                dpi,
                page_key,
                photo_width_mm,
                photo_height_mm,
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
        page_key: str,
        photo_width_mm: float,
        photo_height_mm: float,
    ):
        start_time = time.time()
        created_path = None

        try:
            self._log(f"Тем: {len(names)}")
            self._log(f"Лист: {page_key}")
            self._log(f"Потоки: {threads}")
            self._log(f"DPI: {dpi}")

            matched, missing = match_files(
                root_folder,
                names,
                log_fn=self._log,
                err_fn=self._err,
            )

            missing_count = len(missing)

            self._set_progress(
                missing_count,
                len(names),
            )

            if not matched:
                elapsed = time.time() - start_time

                self._err("Нет ни одного совпадения. Работа остановлена.")
                self._log(f"Время работы: {fmt_time(elapsed)}")
                self._finish_ui(None)

                return

            generator = PDFGenerator(
                threads=threads,
                dpi=dpi,
                page_key=page_key,
                photo_width_mm=photo_width_mm,
                photo_height_mm=photo_height_mm,
            )

            created_path, size_kb, page_count = generator.create_pdf(
                matched,
                output_pdf,
                progress_callback=(
                    lambda done, total:
                    self._set_progress(
                        missing_count + done,
                        len(names),
                    )
                ),
                log_fn=self._log,
                err_fn=self._err,
                pause_event=self.pause_event,
                stop_event=self.stop_event,
            )

            elapsed = time.time() - start_time

            filename = os.path.basename(created_path) if created_path else "-"

            if not self.stop_event.is_set():
                self._set_progress(len(names), len(names))
                self._log("Успешно.")
            else:
                self._log("Работа остановлена.")

            self._log(f"PDF: {filename}")
            self._log(f"Страниц в PDF: {page_count}")
            self._log(f"Время работы: {fmt_time(elapsed)}")

            missing_text = f"\nНе найдено тем: {missing_count}" if missing_count else ""

            if self.stop_event.is_set():
                message = (
                    "Работа остановлена.\n\n"
                    f"Файл: {filename}\n"
                    f"Страниц в PDF: {page_count}\n"
                    f"Размер: {size_kb} КБ\n"
                    f"Время: {fmt_time(elapsed)}"
                    f"{missing_text}"
                )
            else:
                message = (
                    "PDF создан.\n\n"
                    f"Файл: {filename}\n"
                    f"Страниц в PDF: {page_count}\n"
                    f"Размер: {size_kb} КБ\n"
                    f"Время: {fmt_time(elapsed)}"
                    f"{missing_text}"
                )

            self._finish_ui(message)

        except Exception as error:
            elapsed = time.time() - start_time

            self._err(f"Критическая ошибка: {error}")
            self._log(f"Время до ошибки: {fmt_time(elapsed)}")
            self._finish_ui(None)

    def toggle_theme(self):
        current_theme = self.style.theme.name
        self.style.theme_use("cosmo" if current_theme == "darkly" else "darkly")


def main():
    root = tb.Window(themename="darkly")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()