# -*- coding: utf-8 -*-

import os
import sys
import time
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, colorchooser
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image, ImageDraw, ImageColor, ImageFont
import ttkbootstrap as tb
from ttkbootstrap.widgets.scrolled import ScrolledText


DPI = 300
IMAGE_EXTS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
)

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.LANCZOS


def mm_to_px(value_mm: float) -> int:
    return int(round(value_mm / 25.4 * DPI))


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours:
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

            buffer = ctypes.create_unicode_buffer(
                ctypes.wintypes.MAX_PATH
            )

            ctypes.windll.shell32.SHGetFolderPathW(
                None,
                0,
                None,
                0,
                buffer,
            )

            return buffer.value

        except Exception:
            pass

    return os.path.expanduser("~/Desktop")


def make_output_pdf_path(save_folder: str) -> str:
    today = datetime.now().strftime("%d.%m")

    return unique_output_path(
        os.path.join(
            save_folder,
            f"Значки от {today}.pdf",
        )
    )


def get_auto_border_color(
    image: Image.Image,
    fallback=(255, 255, 255),
):
    try:
        sample = image.convert("RGBA")
        sample.thumbnail((96, 96), RESAMPLE)

        width, height = sample.size

        if width < 2 or height < 2:
            return fallback

        edge_size = max(
            1,
            int(min(width, height) * 0.15),
        )

        edge_pixels = []

        for y in range(height):
            for x in range(width):
                is_edge = (
                    x < edge_size
                    or x >= width - edge_size
                    or y < edge_size
                    or y >= height - edge_size
                )

                if not is_edge:
                    continue

                red, green, blue, alpha = sample.getpixel(
                    (x, y)
                )

                if alpha >= 80:
                    edge_pixels.append(
                        (red, green, blue)
                    )

        if len(edge_pixels) < 20:
            edge_pixels = []

            for red, green, blue, alpha in sample.getdata():
                if alpha >= 80:
                    edge_pixels.append(
                        (red, green, blue)
                    )

        if not edge_pixels:
            return fallback

        color_strip = Image.new(
            "RGB",
            (len(edge_pixels), 1),
        )

        color_strip.putdata(edge_pixels)

        try:
            quantize_method = Image.Quantize.MEDIANCUT
        except AttributeError:
            quantize_method = 0

        quantized = color_strip.quantize(
            colors=8,
            method=quantize_method,
        )

        colors = quantized.getcolors()
        palette = quantized.getpalette()

        if not colors or not palette:
            return fallback

        _, palette_index = max(
            colors,
            key=lambda item: item[0],
        )

        palette_offset = palette_index * 3

        return (
            palette[palette_offset],
            palette[palette_offset + 1],
            palette[palette_offset + 2],
        )

    except Exception:
        return fallback


class EllipseLayoutApp:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title("Значки. Макеты для печати")
        self.root.geometry("1100x1100")

        self.style = tb.Style("darkly")

        self.folder_var = tk.StringVar()

        self.save_folder_var = tk.StringVar(
            value=get_desktop_folder()
        )

        self.paper_size_var = tk.StringVar(
            value="SRA3"
        )

        self.photo_diameter_var = tk.StringVar(
            value="37"
        )

        self.border_add_mm_var = tk.StringVar(
            value="10"
        )

        self.final_diameter_var = tk.StringVar(
            value="47 мм"
        )

        self.border_color_var = tk.StringVar(
            value="#FFFFFF"
        )

        self.is_processing = False
        self.thread = None
        self.process_start_time = None

        self.pause_event = threading.Event()
        self.pause_event.set()

        self._build_ui()

        self.photo_diameter_var.trace_add(
            "write",
            lambda *_: self.update_final_diameter_preview(),
        )

        self.border_add_mm_var.trace_add(
            "write",
            lambda *_: self.update_final_diameter_preview(),
        )

        self.update_final_diameter_preview()

    def update_final_diameter_preview(self):
        try:
            photo_diameter_mm = float(
                self.photo_diameter_var
                .get()
                .replace(",", ".")
            )

            border_add_mm = float(
                self.border_add_mm_var
                .get()
                .replace(",", ".")
            )

            if (
                photo_diameter_mm <= 0
                or border_add_mm < 0
            ):
                raise ValueError

            final_diameter_mm = (
                photo_diameter_mm
                + border_add_mm
            )

            self.final_diameter_var.set(
                f"{final_diameter_mm:g} мм"
            )

        except Exception:
            self.final_diameter_var.set("—")

    def _build_ui(self):
        top_frame = tb.Frame(
            self.root,
            padding=10,
        )

        top_frame.pack(fill="x")

        tb.Label(
            top_frame,
            text="Папка с материлами:",
            anchor="w",
        ).pack(side="left")

        tb.Entry(
            top_frame,
            textvariable=self.folder_var,
            width=60,
        ).pack(
            side="left",
            padx=5,
        )

        tb.Button(
            top_frame,
            text="Выбрать",
            command=self.browse_folder,
            bootstyle="secondary",
        ).pack(side="left")

        save_frame = tb.Frame(
            self.root,
            padding=(10, 0, 10, 5),
        )

        save_frame.pack(fill="x")

        tb.Label(
            save_frame,
            text="Куда сохранить макеты:",
            anchor="w",
        ).pack(side="left")

        tb.Entry(
            save_frame,
            textvariable=self.save_folder_var,
            width=60,
        ).pack(
            side="left",
            padx=5,
        )

        tb.Button(
            save_frame,
            text="Выбрать",
            command=self.browse_save_folder,
            bootstyle="secondary",
        ).pack(side="left")

        options_frame = tb.Frame(
            self.root,
            padding=(10, 0),
        )

        options_frame.pack(
            fill="x",
            pady=5,
        )

        paper_frame = tb.Labelframe(
            options_frame,
            text="Размер листа",
            padding=10,
        )

        paper_frame.pack(
            side="left",
            padx=5,
        )

        tb.Radiobutton(
            paper_frame,
            text="A4 (210×297 мм)",
            variable=self.paper_size_var,
            value="A4",
        ).pack(anchor="w")

        tb.Radiobutton(
            paper_frame,
            text="SRA3 (320×450 мм)",
            variable=self.paper_size_var,
            value="SRA3",
        ).pack(anchor="w")

        diameter_frame = tb.Labelframe(
            options_frame,
            text="Размер фото и выступ",
            padding=10,
        )

        diameter_frame.pack(
            side="left",
            padx=15,
        )

        row1 = tb.Frame(diameter_frame)
        row1.pack(anchor="w")

        tb.Label(
            row1,
            text="Размер фотографий, мм:",
        ).pack(side="left")

        tb.Entry(
            row1,
            textvariable=self.photo_diameter_var,
            width=8,
        ).pack(
            side="left",
            padx=5,
        )

        row2 = tb.Frame(diameter_frame)

        row2.pack(
            anchor="w",
            pady=(5, 0),
        )

        tb.Label(
            row2,
            text="Овбодка, мм:",
        ).pack(side="left")

        tb.Entry(
            row2,
            textvariable=self.border_add_mm_var,
            width=8,
        ).pack(
            side="left",
            padx=5,
        )

        row3 = tb.Frame(diameter_frame)

        row3.pack(
            anchor="w",
            pady=(5, 0),
        )

        tb.Label(
            row3,
            text="Итоговый размер, мм:",
        ).pack(side="left")

        tb.Entry(
            row3,
            textvariable=self.final_diameter_var,
            width=8,
            state="readonly",
        ).pack(
            side="left",
            padx=5,
        )

        row3 = tb.Frame(diameter_frame)

        row3.pack(
            anchor="w",
            pady=(5, 0),
        )

        tb.Label(
            row3,
            text="Запасной цвет обводки:",
        ).pack(side="left")

        tb.Entry(
            row3,
            textvariable=self.border_color_var,
            width=10,
        ).pack(
            side="left",
            padx=5,
        )

        tb.Button(
            row3,
            text="Палитра",
            command=self.pick_color,
            bootstyle="secondary",
        ).pack(
            side="left",
            padx=5,
        )

        tb.Label(
            diameter_frame,
            text=(
                "Основной цвет обводки подбирается "
                "автоматически для каждого фото"
            ),
        ).pack(
            anchor="w",
            pady=(5, 0),
        )

        list_frame = tb.Labelframe(
            self.root,
            text="Артикулы/Наименования тем",
            padding=10,
        )

        list_frame.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=10,
        )

        self.names_text = ScrolledText(
            list_frame,
            height=10,
            autohide=True,
        )

        self.names_text.pack(
            fill="both",
            expand=True,
        )

        self._bind_names_text_shortcuts()

        buttons_frame = tb.Frame(
            list_frame
        )

        buttons_frame.pack(
            fill="x",
            pady=(5, 0),
        )

        tb.Button(
            buttons_frame,
            text="Вставить из буфера",
            command=self.paste_from_clipboard,
            bootstyle="info",
        ).pack(side="left")

        tb.Button(
            buttons_frame,
            text="Очистить артикулы",
            command=lambda: self.names_text.delete(
                "1.0",
                "end",
            ),
            bootstyle="secondary",
        ).pack(
            side="left",
            padx=5,
        )

        bottom_frame = tb.Frame(
            self.root,
            padding=(10, 5),
        )

        bottom_frame.pack(fill="x")

        self.start_button = tb.Button(
            bottom_frame,
            text="Старт",
            command=self.start_processing,
            bootstyle="success",
        )

        self.start_button.pack(side="left")

        self.pause_button = tb.Button(
            bottom_frame,
            text="Пауза",
            command=self.pause_processing,
            bootstyle="warning",
            state="disabled",
        )

        self.pause_button.pack(
            side="left",
            padx=(10, 0),
        )

        self.resume_button = tb.Button(
            bottom_frame,
            text="Продолжить",
            command=self.resume_processing,
            bootstyle="info",
            state="disabled",
        )

        self.resume_button.pack(
            side="left",
            padx=5,
        )

        self.progress = tb.Progressbar(
            bottom_frame,
            orient="horizontal",
            length=300,
            mode="determinate",
        )

        self.progress.pack(
            side="left",
            padx=10,
        )

        self.progress_label = tb.Label(
            bottom_frame,
            text=(
                "Прогресс: 0 / 0 тем (0.0%) | "
                "Осталось: --:--"
            ),
        )

        self.progress_label.pack(side="left")

        logs_wrapper = tb.Frame(
            self.root,
            padding=(10, 0, 10, 10),
        )

        logs_wrapper.pack(
            fill="both",
            expand=True,
        )

        logs_wrapper.columnconfigure(
            0,
            weight=1,
        )

        logs_wrapper.columnconfigure(
            1,
            weight=1,
        )

        logs_wrapper.rowconfigure(
            0,
            weight=1,
        )

        log_frame = tb.Labelframe(
            logs_wrapper,
            text="Логи",
            padding=10,
        )

        log_frame.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(0, 5),
        )

        error_frame = tb.Labelframe(
            logs_wrapper,
            text="Ошибки / предупреждения",
            padding=10,
        )

        error_frame.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=(5, 0),
        )

        self.log_text = ScrolledText(
            log_frame,
            height=10,
            autohide=True,
        )

        self.log_text.pack(
            fill="both",
            expand=True,
        )

        self.error_text = ScrolledText(
            error_frame,
            height=10,
            autohide=True,
        )

        self.error_text.pack(
            fill="both",
            expand=True,
        )

    def _bind_names_text_shortcuts(self):
        widgets = [
            self.names_text,
        ]

        inner_text_widget = getattr(
            self.names_text,
            "text",
            None,
        )

        if inner_text_widget is not None:
            widgets.append(
                inner_text_widget
            )

        for widget in widgets:
            try:
                widget.bind(
                    "<Control-KeyPress>",
                    self._handle_names_text_control_shortcut,
                )
            except Exception:
                pass

    def _handle_names_text_control_shortcut(self, event):
        keycode = getattr(
            event,
            "keycode",
            None,
        )

        keysym = str(
            getattr(
                event,
                "keysym",
                "",
            )
        ).lower()

        char = str(
            getattr(
                event,
                "char",
                "",
            )
        ).lower()

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
            return self._paste_into_names_text(
                event
            )

        if is_copy:
            return self._copy_from_names_text(
                event
            )

        return None

    def _paste_into_names_text(self, event=None):
        try:
            text = self.root.clipboard_get()
        except Exception:
            return "break"

        widget = (
            event.widget
            if event is not None
            else self.names_text
        )

        try:
            widget.delete(
                "sel.first",
                "sel.last",
            )
        except tk.TclError:
            pass
        except Exception:
            pass

        try:
            widget.insert(
                "insert",
                text,
            )

            widget.see(
                "insert"
            )

        except Exception:
            self.names_text.insert(
                "insert",
                text,
            )

            self.names_text.see(
                "insert"
            )

        return "break"

    def _copy_from_names_text(self, event=None):
        widget = (
            event.widget
            if event is not None
            else self.names_text
        )

        try:
            selected_text = widget.get(
                "sel.first",
                "sel.last",
            )
        except Exception:
            return "break"

        try:
            self.root.clipboard_clear()

            self.root.clipboard_append(
                selected_text
            )

        except Exception:
            pass

        return "break"

    def browse_folder(self):
        folder = filedialog.askdirectory(
            title="Выберите главную папку"
        )

        if folder:
            self.folder_var.set(folder)

    def browse_save_folder(self):
        folder = filedialog.askdirectory(
            title="Выберите папку для сохранения"
        )

        if folder:
            self.save_folder_var.set(folder)

    def pick_color(self):
        initial_color = (
            self.border_color_var.get().strip()
            or "#FFFFFF"
        )

        try:
            _, hex_color = colorchooser.askcolor(
                color=initial_color,
                title="Выберите запасной цвет обводки",
            )

        except Exception:
            hex_color = None

        if hex_color:
            self.border_color_var.set(
                hex_color
            )

    def paste_from_clipboard(self):
        try:
            text = self.root.clipboard_get()

        except tk.TclError:
            messagebox.showwarning(
                "Буфер обмена",
                "Буфер обмена пуст или содержит не текст.",
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

        current_text = self.names_text.get(
            "1.0",
            "end-1c",
        )

        if (
            current_text
            and not current_text.endswith("\n")
        ):
            self.names_text.insert(
                "end",
                "\n",
            )

        self.names_text.insert(
            "end",
            text,
        )

        self.names_text.see("end")

    def log(self, message: str):
        def append_message():
            target = (
                self.error_text
                if "[ОШИБКА]" in message
                else self.log_text
            )

            target.insert(
                "end",
                message + "\n",
            )

            target.see("end")

        self.root.after(
            0,
            append_message,
        )

    def set_progress(
        self,
        current: int,
        total: int,
    ):
        def update_progress():
            current_value = min(
                current,
                total,
            )

            self.progress["maximum"] = max(
                total,
                1,
            )

            self.progress["value"] = current_value

            percent = (
                current_value / total * 100
                if total
                else 0
            )

            if (
                self.process_start_time is not None
                and current_value > 0
                and total > 0
            ):
                elapsed = (
                    time.time()
                    - self.process_start_time
                )

                average_time = (
                    elapsed / current_value
                )

                remaining = (
                    total - current_value
                ) * average_time

                remaining_text = fmt_time(
                    remaining
                )

            else:
                remaining_text = "--:--"

            self.progress_label.config(
                text=(
                    f"Прогресс: {current_value} / {total} "
                    f"({percent:.1f}%) | "
                    f"Осталось: {remaining_text}"
                )
            )

        self.root.after(
            0,
            update_progress,
        )

    def pause_processing(self):
        if not self.is_processing:
            return

        self.pause_event.clear()

        self.pause_button.config(
            state="disabled"
        )

        self.resume_button.config(
            state="normal"
        )

        self.log(
            "[ПАУЗА] Работа приостановлена."
        )

    def resume_processing(self):
        if not self.is_processing:
            return

        self.pause_event.set()

        self.pause_button.config(
            state="normal"
        )

        self.resume_button.config(
            state="disabled"
        )

        self.log(
            "[ПРОДОЛЖИТЬ] Работа продолжена."
        )

    def _wait_if_paused(self):
        self.pause_event.wait()

    def start_processing(self):
        if self.is_processing:
            return

        folder = (
            self.folder_var
            .get()
            .strip()
        )

        if (
            not folder
            or not os.path.isdir(folder)
        ):
            messagebox.showerror(
                "Ошибка",
                "Укажите корректную папку с материалами.",
            )

            return

        save_folder = (
            self.save_folder_var
            .get()
            .strip()
        )

        if (
            not save_folder
            or not os.path.isdir(save_folder)
        ):
            messagebox.showerror(
                "Ошибка",
                "Укажите корректную папку для сохранения.",
            )

            return

        try:
            photo_diameter_mm = float(
                self.photo_diameter_var
                .get()
                .replace(",", ".")
            )

            if photo_diameter_mm <= 0:
                raise ValueError

        except ValueError:
            messagebox.showerror(
                "Ошибка",
                "Введите корректный размер фотографии в мм (> 0).",
            )

            return

        try:
            border_add_mm = float(
                self.border_add_mm_var
                .get()
                .replace(",", ".")
            )

            if border_add_mm < 0:
                raise ValueError

        except ValueError:
            messagebox.showerror(
                "Ошибка",
                (
                    "Добавка к диаметру должна быть "
                    "не меньше 0."
                ),
            )

            return

        names_raw = self.names_text.get(
            "1.0",
            "end",
        ).strip()

        if not names_raw:
            messagebox.showerror(
                "Ошибка",
                "Введите список имён файлов.",
            )

            return

        names = [
            line.strip()
            for line in names_raw.splitlines()
            if line.strip()
        ]

        if not names:
            messagebox.showerror(
                "Ошибка",
                "Список имён пуст после обработки.",
            )

            return

        self.is_processing = True
        self.pause_event.set()
        self.process_start_time = time.time()

        self.start_button.config(
            state="disabled"
        )

        self.pause_button.config(
            state="normal"
        )

        self.resume_button.config(
            state="disabled"
        )

        self.log_text.delete(
            "1.0",
            "end",
        )

        self.error_text.delete(
            "1.0",
            "end",
        )

        self.set_progress(
            0,
            len(names),
        )

        self.log(
            "Работа началась."
        )

        self.thread = threading.Thread(
            target=self.process_worker,
            args=(
                folder,
                save_folder,
                names,
                photo_diameter_mm,
                border_add_mm,
                (
                    self.border_color_var
                    .get()
                    .strip()
                    or "#FFFFFF"
                ),
            ),
            daemon=True,
        )

        self.thread.start()

    def process_worker(
        self,
        folder,
        save_folder,
        names,
        photo_diameter_mm,
        border_add_mm,
        border_color,
    ):
        try:
            self._process(
                folder,
                save_folder,
                names,
                photo_diameter_mm,
                border_add_mm,
                border_color,
            )

        except Exception as error:
            self.log(
                f"[ОШИБКА] Непредвиденная ошибка: {error}"
            )

        finally:
            def finish_processing():
                self.is_processing = False

                self.start_button.config(
                    state="normal"
                )

                self.pause_button.config(
                    state="disabled"
                )

                self.resume_button.config(
                    state="disabled"
                )

            self.root.after(
                0,
                finish_processing,
            )

    @staticmethod
    def _parse_input_name(name: str):
        value = name.strip()

        if "_" not in value:
            return None, None, None

        left_part, right_part = value.rsplit(
            "_",
            1,
        )

        if not right_part.strip().isdigit():
            return None, None, None

        number = int(
            right_part.strip()
        )

        left_part = left_part.strip()

        if left_part.lower().startswith(
            "значки1шт-"
        ):
            if not 1 <= number <= 54:
                return None, None, None

            name_part = left_part[
                len("значки1шт-"):
            ]

            return (
                "single",
                "значки6шт-" + name_part,
                number,
            )

        if left_part.lower().startswith(
            "значки6шт-"
        ):
            if not 1 <= number <= 9:
                return None, None, None

            return (
                "six",
                left_part,
                number,
            )

        return None, None, None

    def _build_folder_index(
        self,
        root_folder,
    ):
        index = {}

        for (
            directory_path,
            directory_names,
            _,
        ) in os.walk(root_folder):

            for directory_name in directory_names:
                key = directory_name.lower()

                if key not in index:
                    index[key] = os.path.join(
                        directory_path,
                        directory_name,
                    )

        return index

    def _scan_needed_subfolders(
        self,
        folder_index,
        needed_subfolders,
    ):
        cache = {}

        for subfolder_lower in needed_subfolders:
            subfolder_path = folder_index.get(
                subfolder_lower
            )

            if not subfolder_path:
                continue

            file_index = {}

            for (
                root_directory,
                _,
                filenames,
            ) in os.walk(subfolder_path):

                for filename in filenames:
                    filename_lower = filename.lower()

                    if not filename_lower.endswith(
                        IMAGE_EXTS
                    ):
                        continue

                    stem = os.path.splitext(
                        filename_lower
                    )[0]

                    if stem not in file_index:
                        file_index[stem] = os.path.join(
                            root_directory,
                            filename,
                        )

            cache[subfolder_lower] = file_index

        return cache

    @staticmethod
    def _group_range(
        group_number: int,
    ):
        start_number = (
            group_number - 1
        ) * 6 + 1

        return (
            start_number,
            start_number + 5,
        )

    @staticmethod
    def _find_file_by_stem(
        file_index,
        stem,
    ):
        return file_index.get(
            stem.lower()
        )

    @staticmethod
    def _get_cyrillic_font(size_px):
        font_paths = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "C:/Windows/Fonts/tahoma.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
        ]

        for font_path in font_paths:
            if os.path.exists(font_path):
                return ImageFont.truetype(
                    font_path,
                    size_px,
                )

        return ImageFont.load_default()

    def _process(
        self,
        folder,
        save_folder,
        names,
        photo_diameter_mm,
        border_add_mm,
        border_color,
    ):
        parsed_items = []

        for name in names:
            mode, subfolder, number = (
                self._parse_input_name(name)
            )

            if mode:
                parsed_items.append(
                    (
                        name,
                        mode,
                        subfolder,
                        number,
                    )
                )

            else:
                self.log(
                    f"[ОШИБКА] Имя '{name}' "
                    f"не подходит под шаблон."
                )

        if not parsed_items:
            self.log(
                "[ОШИБКА] Нет корректных имён."
            )

            return

        total_work = len(parsed_items)

        work_done = 0

        self.set_progress(
            0,
            total_work,
        )

        folder_index = (
            self._build_folder_index(
                folder
            )
        )

        needed_subfolders = {
            subfolder.lower()
            for _, _, subfolder, _ in parsed_items
        }

        files_cache = (
            self._scan_needed_subfolders(
                folder_index,
                needed_subfolders,
            )
        )

        six_groups = []
        single_paths = []

        for (
            category_title,
            category_mode,
        ) in (
            ("Значки 6шт", "six"),
            ("Значки 1шт", "single"),
        ):
            category_items = [
                item
                for item in parsed_items
                if item[1] == category_mode
            ]

            if not category_items:
                continue

            self.log(
                f"{category_title}:"
            )

            for (
                raw_name,
                mode,
                subfolder,
                number,
            ) in category_items:

                self._wait_if_paused()

                expected_for_theme = (
                    6
                    if mode == "six"
                    else 1
                )

                missing_count = 0
                theme_success = True
                found_paths = []

                subfolder_lower = (
                    subfolder.lower()
                )

                subfolder_path = (
                    folder_index.get(
                        subfolder_lower
                    )
                )

                if not subfolder_path:
                    self.log(
                        f"[ОШИБКА] Подпапка "
                        f"'{subfolder}' для "
                        f"'{raw_name}' не найдена."
                    )

                    missing_count = (
                        expected_for_theme
                    )

                    theme_success = False

                else:
                    file_index = (
                        files_cache.get(
                            subfolder_lower,
                            {},
                        )
                    )

                    if mode == "six":
                        (
                            start_number,
                            end_number,
                        ) = self._group_range(
                            number
                        )

                        for image_number in range(
                            start_number,
                            end_number + 1,
                        ):
                            stem = (
                                f"{image_number}_circle"
                            )

                            path = (
                                self._find_file_by_stem(
                                    file_index,
                                    stem,
                                )
                            )

                            if path:
                                found_paths.append(
                                    path
                                )

                            else:
                                found_paths.append(
                                    None
                                )

                                missing_count += 1
                                theme_success = False

                                self.log(
                                    f"[ОШИБКА] "
                                    f"В '{subfolder}' "
                                    f"нет файла '{stem}'."
                                )

                    else:
                        stem = (
                            f"{number}_circle"
                        )

                        path = (
                            self._find_file_by_stem(
                                file_index,
                                stem,
                            )
                        )

                        if path:
                            single_paths.append(
                                path
                            )

                        else:
                            missing_count = 1
                            theme_success = False

                            self.log(
                                f"[ОШИБКА] "
                                f"В '{subfolder}' "
                                f"нет файла '{stem}'."
                            )

                if (
                    mode == "six"
                    and any(found_paths)
                ):
                    six_groups.append(
                        {
                            "label": raw_name,
                            "paths": found_paths,
                        }
                    )

                work_done += 1

                if theme_success:
                    self.log(
                        f"{raw_name} - успешно"
                    )

                self.set_progress(
                    work_done,
                    total_work,
                )

        six_paths_flat = [
            path
            for group in six_groups
            for path in group["paths"]
            if path
        ]

        if (
            not six_paths_flat
            and not single_paths
        ):
            self.log(
                "[ОШИБКА] Ни один файл не найден."
            )

            self.set_progress(
                total_work,
                total_work,
            )

            return

        if (
            self.paper_size_var.get()
            == "A4"
        ):
            width_mm = 210
            height_mm = 297
            is_sra3 = False

        else:
            width_mm = 320
            height_mm = 450
            is_sra3 = True

        width_px = mm_to_px(
            width_mm
        )

        height_px = mm_to_px(
            height_mm
        )

        final_diameter_mm = (
            photo_diameter_mm
            + border_add_mm
        )

        final_diameter_px = mm_to_px(
            final_diameter_mm
        )

        base_diameter_px = mm_to_px(
            photo_diameter_mm
        )

        gap_px = mm_to_px(5)
        top_margin_px = mm_to_px(10)
        bottom_margin_px = mm_to_px(10)

        bottom_limit = (
            height_px - bottom_margin_px
        )

        calculated_per_row = max(
            1,
            (
                width_px + gap_px
            )
            // (
                final_diameter_px
                + gap_px
            ),
        )

        generic_per_row = (
            calculated_per_row
        )

        six_sra3_per_row = min(
            5,
            calculated_per_row,
        )

        try:
            fallback_border_rgb = (
                ImageColor.getrgb(
                    border_color
                )
            )

            if len(
                fallback_border_rgb
            ) > 3:
                fallback_border_rgb = (
                    fallback_border_rgb[:3]
                )

        except Exception:
            fallback_border_rgb = (
                255,
                255,
                255,
            )

        def make_badge_tile(
            image_path,
        ):
            self._wait_if_paused()

            try:
                with Image.open(
                    image_path
                ) as source_image:

                    image = (
                        source_image.convert(
                            "RGBA"
                        )
                    )

            except Exception as error:
                self.log(
                    f"[ОШИБКА] "
                    f"Не удалось открыть "
                    f"'{image_path}': {error}"
                )

                return None

            auto_border_rgb = (
                get_auto_border_color(
                    image,
                    fallback=(
                        fallback_border_rgb
                    ),
                )
            )

            resized_image = image.resize(
                (
                    base_diameter_px,
                    base_diameter_px,
                ),
                RESAMPLE,
            )

            tile = Image.new(
                "RGBA",
                (
                    final_diameter_px,
                    final_diameter_px,
                ),
                (
                    0,
                    0,
                    0,
                    0,
                ),
            )

            tile_draw = ImageDraw.Draw(
                tile
            )

            tile_draw.ellipse(
                (
                    0,
                    0,
                    final_diameter_px - 1,
                    final_diameter_px - 1,
                ),
                fill=auto_border_rgb,
            )

            if (
                auto_border_rgb[0] >= 245
                and auto_border_rgb[1] >= 245
                and auto_border_rgb[2] >= 245
            ):
                tile_draw.ellipse(
                    (
                        0,
                        0,
                        final_diameter_px - 1,
                        final_diameter_px - 1,
                    ),
                    outline=(
                        200,
                        200,
                        200,
                    ),
                    width=1,
                )

            offset = (
                final_diameter_px
                - base_diameter_px
            ) // 2

            tile.paste(
                resized_image,
                (
                    offset,
                    offset,
                ),
                resized_image,
            )

            return tile

        def wrap_text_exact(
            draw,
            text,
            font,
            max_width,
        ):
            words = text.split(" ")

            lines = []
            current_line = ""

            for word in words:
                candidate = (
                    word
                    if not current_line
                    else current_line
                    + " "
                    + word
                )

                bbox = draw.textbbox(
                    (0, 0),
                    candidate,
                    font=font,
                )

                if (
                    bbox[2] - bbox[0]
                    <= max_width
                ):
                    current_line = candidate
                    continue

                if current_line:
                    lines.append(
                        current_line
                    )

                    current_line = ""

                bbox = draw.textbbox(
                    (0, 0),
                    word,
                    font=font,
                )

                if (
                    bbox[2] - bbox[0]
                    <= max_width
                ):
                    current_line = word
                    continue

                character_line = ""

                for character in word:
                    character_candidate = (
                        character_line
                        + character
                    )

                    bbox = draw.textbbox(
                        (0, 0),
                        character_candidate,
                        font=font,
                    )

                    if (
                        bbox[2] - bbox[0]
                        <= max_width
                        or not character_line
                    ):
                        character_line = (
                            character_candidate
                        )

                    else:
                        lines.append(
                            character_line
                        )

                        character_line = (
                            character
                        )

                current_line = (
                    character_line
                )

            if current_line:
                lines.append(
                    current_line
                )

            return lines

        def fit_theme_text(
            draw,
            text,
            max_width,
            max_height,
        ):
            max_font_size = mm_to_px(4.8)

            min_font_size = max(
                10,
                mm_to_px(1.5),
            )

            for font_size in range(
                max_font_size,
                min_font_size - 1,
                -2,
            ):
                font = (
                    self._get_cyrillic_font(
                        font_size
                    )
                )

                lines = wrap_text_exact(
                    draw,
                    text,
                    font,
                    max_width,
                )

                line_heights = []

                for line in lines:
                    bbox = draw.textbbox(
                        (0, 0),
                        line,
                        font=font,
                    )

                    line_heights.append(
                        bbox[3] - bbox[1]
                    )

                spacing = max(
                    1,
                    font_size // 8,
                )

                total_height = (
                    sum(line_heights)
                    + spacing
                    * max(
                        0,
                        len(lines) - 1,
                    )
                )

                if (
                    total_height
                    <= max_height
                ):
                    return (
                        font,
                        lines,
                        line_heights,
                        spacing,
                    )

            font = (
                self._get_cyrillic_font(
                    min_font_size
                )
            )

            lines = wrap_text_exact(
                draw,
                text,
                font,
                max_width,
            )

            line_heights = []

            for line in lines:
                bbox = draw.textbbox(
                    (0, 0),
                    line,
                    font=font,
                )

                line_heights.append(
                    bbox[3] - bbox[1]
                )

            return (
                font,
                lines,
                line_heights,
                1,
            )

        def make_pack_tile(
            pack_number,
            theme_label,
        ):
            tile = Image.new(
                "RGBA",
                (
                    final_diameter_px,
                    final_diameter_px,
                ),
                (
                    255,
                    255,
                    255,
                    255,
                ),
            )

            draw = ImageDraw.Draw(
                tile
            )

            outline_width = max(
                2,
                mm_to_px(0.3),
            )

            draw.ellipse(
                (
                    outline_width // 2,
                    outline_width // 2,
                    (
                        final_diameter_px
                        - 1
                        - outline_width // 2
                    ),
                    (
                        final_diameter_px
                        - 1
                        - outline_width // 2
                    ),
                ),
                fill=(
                    255,
                    255,
                    255,
                    255,
                ),
                outline=(
                    0,
                    0,
                    0,
                    255,
                ),
                width=outline_width,
            )

            number_text = str(
                pack_number
            )

            number_font = (
                self._get_cyrillic_font(
                    mm_to_px(8)
                )
            )

            number_bbox = draw.textbbox(
                (0, 0),
                number_text,
                font=number_font,
            )

            number_width = (
                number_bbox[2]
                - number_bbox[0]
            )

            number_height = (
                number_bbox[3]
                - number_bbox[1]
            )

            number_top = int(
                final_diameter_px * 0.075
            )

            draw.text(
                (
                    (
                        final_diameter_px
                        - number_width
                    ) // 2,
                    number_top
                    - number_bbox[1],
                ),
                number_text,
                fill=(
                    0,
                    0,
                    0,
                    255,
                ),
                font=number_font,
            )

            underline_y = (
                number_top
                + number_height
                + mm_to_px(0.8)
            )

            underline_width = max(
                number_width
                + 2 * mm_to_px(1),
                mm_to_px(10),
            )

            underline_x = (
                final_diameter_px
                - underline_width
            ) // 2

            draw.line(
                (
                    underline_x,
                    underline_y,
                    underline_x
                    + underline_width,
                    underline_y,
                ),
                fill=(
                    0,
                    0,
                    0,
                    255,
                ),
                width=max(
                    2,
                    mm_to_px(0.35),
                ),
            )

            theme_top = (
                underline_y
                + mm_to_px(2.2)
            )

            theme_bottom = int(
                final_diameter_px * 0.91
            )

            max_theme_width = int(
                final_diameter_px * 0.76
            )

            max_theme_height = max(
                1,
                theme_bottom - theme_top,
            )

            (
                theme_font,
                theme_lines,
                line_heights,
                line_spacing,
            ) = fit_theme_text(
                draw,
                theme_label,
                max_theme_width,
                max_theme_height,
            )

            total_theme_height = (
                sum(line_heights)
                + line_spacing
                * max(
                    0,
                    len(theme_lines) - 1,
                )
            )

            theme_y = (
                theme_top
                + max(
                    0,
                    (
                        max_theme_height
                        - total_theme_height
                    ) // 2,
                )
            )

            for (
                line,
                line_height,
            ) in zip(
                theme_lines,
                line_heights,
            ):
                bbox = draw.textbbox(
                    (0, 0),
                    line,
                    font=theme_font,
                )

                line_width = (
                    bbox[2] - bbox[0]
                )

                draw.text(
                    (
                        (
                            final_diameter_px
                            - line_width
                        ) // 2,
                        theme_y - bbox[1],
                    ),
                    line,
                    fill=(
                        0,
                        0,
                        0,
                        255,
                    ),
                    font=theme_font,
                )

                theme_y += (
                    line_height
                    + line_spacing
                )

            return tile

        category_font = (
            self._get_cyrillic_font(
                mm_to_px(7)
            )
        )

        measure_image = Image.new(
            "RGB",
            (
                100,
                100,
            ),
            "white",
        )

        measure_draw = ImageDraw.Draw(
            measure_image
        )

        title_height = 0

        for title in (
            "Значки 6шт",
            "Значки 1шт",
        ):
            bbox = measure_draw.textbbox(
                (0, 0),
                title,
                font=category_font,
            )

            title_height = max(
                title_height,
                bbox[3] - bbox[1],
            )

        clearance_px = mm_to_px(3)
        line_gap_px = mm_to_px(1)

        line_width_px = max(
            1,
            mm_to_px(0.2),
        )

        header_height_px = (
            title_height
            + line_gap_px
            + line_width_px
        )

        max_workers = max(
            2,
            os.cpu_count() or 8,
        )

        def draw_category_header(
            page,
            title,
            start_x,
            row_width,
            header_top,
        ):
            draw = ImageDraw.Draw(
                page
            )

            bbox = draw.textbbox(
                (0, 0),
                title,
                font=category_font,
            )

            text_width = (
                bbox[2] - bbox[0]
            )

            text_x = (
                width_px - text_width
            ) // 2

            text_y = (
                header_top - bbox[1]
            )

            draw.text(
                (
                    text_x,
                    text_y,
                ),
                title,
                fill=(
                    0,
                    0,
                    0,
                ),
                font=category_font,
            )

            line_y = (
                header_top
                + title_height
                + line_gap_px
            )

            draw.line(
                (
                    start_x,
                    line_y,
                    start_x + row_width,
                    line_y,
                ),
                fill=(
                    0,
                    0,
                    0,
                ),
                width=line_width_px,
            )

            return line_y

        def prepare_tiles(
            page_items,
        ):
            tiles = [
                None
            ] * len(page_items)

            with ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:

                futures = {
                    executor.submit(
                        make_badge_tile,
                        image_path,
                    ): item_index
                    for (
                        item_index,
                        (
                            image_path,
                            _,
                            _,
                        ),
                    ) in enumerate(
                        page_items
                    )
                    if image_path
                }

                for future in as_completed(
                    futures
                ):
                    item_index = (
                        futures[future]
                    )

                    try:
                        tiles[item_index] = (
                            future.result()
                        )

                    except Exception as error:
                        self.log(
                            f"[ОШИБКА] "
                            f"Ошибка обработки: "
                            f"{error}"
                        )

            return tiles

        def render_sra3_six_pages():
            pages = []

            if not six_groups:
                return pages

            columns = (
                six_sra3_per_row
            )

            full_row_width = (
                columns
                * final_diameter_px
                + (
                    columns - 1
                )
                * gap_px
            )

            fixed_start_x = (
                width_px
                - full_row_width
            ) // 2

            first_header_top = (
                top_margin_px
            )

            first_line_y = (
                first_header_top
                + title_height
                + line_gap_px
            )

            first_ellipse_y = (
                first_line_y
                + line_width_px
                + clearance_px
            )

            continuation_ellipse_y = (
                top_margin_px
            )

            first_photo_y = (
                first_ellipse_y
                + final_diameter_px
                + gap_px
            )

            continuation_photo_y = (
                continuation_ellipse_y
                + final_diameter_px
                + gap_px
            )

            first_required_bottom = (
                first_photo_y
                + 6 * final_diameter_px
                + 5 * gap_px
            )

            continuation_required_bottom = (
                continuation_photo_y
                + 6 * final_diameter_px
                + 5 * gap_px
            )

            if (
                first_required_bottom
                > bottom_limit
                or continuation_required_bottom
                > bottom_limit
            ):
                raise ValueError(
                    f"При итоговом диаметре "
                    f"{final_diameter_mm:g} мм "
                    f"на лист SRA3 не помещается "
                    f"один эллипс и 6 фотографий "
                    f"по высоте."
                )

            for (
                page_index,
                batch_start,
            ) in enumerate(
                range(
                    0,
                    len(six_groups),
                    columns,
                )
            ):
                groups = six_groups[
                    batch_start:
                    batch_start + columns
                ]

                page = Image.new(
                    "RGB",
                    (
                        width_px,
                        height_px,
                    ),
                    "white",
                )

                if page_index == 0:
                    draw_category_header(
                        page,
                        "Значки 6шт",
                        fixed_start_x,
                        full_row_width,
                        first_header_top,
                    )

                    ellipse_y = (
                        first_ellipse_y
                    )

                    photo_start_y = (
                        first_photo_y
                    )

                else:
                    ellipse_y = (
                        continuation_ellipse_y
                    )

                    photo_start_y = (
                        continuation_photo_y
                    )

                page_items = []

                for (
                    column,
                    group,
                ) in enumerate(groups):

                    x = (
                        fixed_start_x
                        + column
                        * (
                            final_diameter_px
                            + gap_px
                        )
                    )

                    pack_number = (
                        batch_start
                        + column
                        + 1
                    )

                    pack_tile = (
                        make_pack_tile(
                            pack_number,
                            group["label"],
                        )
                    )

                    page.paste(
                        pack_tile,
                        (
                            x,
                            ellipse_y,
                        ),
                        pack_tile,
                    )

                    for (
                        row_index,
                        image_path,
                    ) in enumerate(
                        group["paths"]
                    ):
                        if not image_path:
                            continue

                        y = (
                            photo_start_y
                            + row_index
                            * (
                                final_diameter_px
                                + gap_px
                            )
                        )

                        page_items.append(
                            (
                                image_path,
                                x,
                                y,
                            )
                        )

                tiles = prepare_tiles(
                    page_items
                )

                for (
                    item_index,
                    (
                        _,
                        x,
                        y,
                    ),
                ) in enumerate(
                    page_items
                ):
                    tile = tiles[
                        item_index
                    ]

                    if tile is not None:
                        page.paste(
                            tile,
                            (
                                x,
                                y,
                            ),
                            tile,
                        )

                pages.append(
                    page
                )

            return pages

        def build_generic_page_plans(
            categories,
            per_row,
        ):
            plans = []
            current_page = None
            cursor_y = 0

            total_row_width = (
                per_row
                * final_diameter_px
                + (
                    per_row - 1
                )
                * gap_px
            )

            start_x = (
                width_px
                - total_row_width
            ) // 2

            def new_page():
                nonlocal current_page
                nonlocal cursor_y

                current_page = {
                    "headers": [],
                    "items": [],
                    "start_x": start_x,
                    "row_width": (
                        total_row_width
                    ),
                }

                plans.append(
                    current_page
                )

                cursor_y = (
                    top_margin_px
                )

            for (
                category_title,
                category_paths,
            ) in categories:

                if not category_paths:
                    continue

                path_index = 0
                header_added = False

                while path_index < len(
                    category_paths
                ):
                    if (
                        current_page
                        is None
                    ):
                        new_page()

                    if not header_added:
                        page_has_content = bool(
                            current_page[
                                "headers"
                            ]
                            or current_page[
                                "items"
                            ]
                        )

                        gap_before_header = (
                            clearance_px
                            if page_has_content
                            else 0
                        )

                        required_height = (
                            gap_before_header
                            + header_height_px
                            + clearance_px
                            + final_diameter_px
                        )

                        if (
                            cursor_y
                            + required_height
                            > bottom_limit
                        ):
                            new_page()

                            gap_before_header = 0

                        header_top = (
                            cursor_y
                            + gap_before_header
                        )

                        current_page[
                            "headers"
                        ].append(
                            (
                                category_title,
                                header_top,
                            )
                        )

                        cursor_y = (
                            header_top
                            + header_height_px
                            + clearance_px
                        )

                        header_added = True

                    if (
                        cursor_y
                        + final_diameter_px
                        > bottom_limit
                    ):
                        new_page()
                        continue

                    row_paths = (
                        category_paths[
                            path_index:
                            path_index
                            + per_row
                        ]
                    )

                    for (
                        column,
                        image_path,
                    ) in enumerate(
                        row_paths
                    ):
                        x = (
                            start_x
                            + column
                            * (
                                final_diameter_px
                                + gap_px
                            )
                        )

                        current_page[
                            "items"
                        ].append(
                            (
                                image_path,
                                x,
                                cursor_y,
                            )
                        )

                    path_index += len(
                        row_paths
                    )

                    cursor_y += (
                        final_diameter_px
                    )

                    if path_index < len(
                        category_paths
                    ):
                        if (
                            cursor_y
                            + gap_px
                            + final_diameter_px
                            <= bottom_limit
                        ):
                            cursor_y += gap_px

                        else:
                            new_page()

            return plans

        def render_generic_pages(
            plans,
        ):
            pages = []

            for plan in plans:
                self._wait_if_paused()

                page = Image.new(
                    "RGB",
                    (
                        width_px,
                        height_px,
                    ),
                    "white",
                )

                for (
                    title,
                    header_top,
                ) in plan["headers"]:

                    draw_category_header(
                        page,
                        title,
                        plan["start_x"],
                        plan["row_width"],
                        header_top,
                    )

                tiles = prepare_tiles(
                    plan["items"]
                )

                for (
                    item_index,
                    (
                        _,
                        x,
                        y,
                    ),
                ) in enumerate(
                    plan["items"]
                ):
                    tile = tiles[
                        item_index
                    ]

                    if tile is not None:
                        page.paste(
                            tile,
                            (
                                x,
                                y,
                            ),
                            tile,
                        )

                pages.append(
                    page
                )

            return pages

        pages_rgb = []

        if is_sra3:
            pages_rgb.extend(
                render_sra3_six_pages()
            )

            single_plans = (
                build_generic_page_plans(
                    [
                        (
                            "Значки 1шт",
                            single_paths,
                        )
                    ],
                    generic_per_row,
                )
            )

            pages_rgb.extend(
                render_generic_pages(
                    single_plans
                )
            )

        else:
            generic_plans = (
                build_generic_page_plans(
                    [
                        (
                            "Значки 6шт",
                            six_paths_flat,
                        ),
                        (
                            "Значки 1шт",
                            single_paths,
                        ),
                    ],
                    generic_per_row,
                )
            )

            pages_rgb.extend(
                render_generic_pages(
                    generic_plans
                )
            )

        if not pages_rgb:
            self.log(
                "[ОШИБКА] Не удалось "
                "сформировать ни одного листа."
            )

            return

        output_path = (
            make_output_pdf_path(
                save_folder
            )
        )

        try:
            if len(pages_rgb) == 1:
                pages_rgb[0].save(
                    output_path,
                    "PDF",
                    resolution=DPI,
                )

            else:
                pages_rgb[0].save(
                    output_path,
                    "PDF",
                    resolution=DPI,
                    save_all=True,
                    append_images=(
                        pages_rgb[1:]
                    ),
                )

        except Exception as error:
            self.log(
                f"[ОШИБКА] "
                f"Не удалось сохранить PDF: "
                f"{error}"
            )

            return

        self.set_progress(
            total_work,
            total_work,
        )

        elapsed = (
            time.time()
            - self.process_start_time
        )

        self.log(
            "Успешно."
        )

        self.log(
            f"PDF: "
            f"{os.path.basename(output_path)}"
        )

        self.log(
            f"Листов в PDF: "
            f"{len(pages_rgb)}"
        )

        self.log(
            f"Время работы: "
            f"{fmt_time(elapsed)}"
        )


if __name__ == "__main__":
    root = tb.Window(
        themename="darkly"
    )

    app = EllipseLayoutApp(
        root
    )

    root.mainloop()