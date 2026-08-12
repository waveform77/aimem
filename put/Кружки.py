import os
import sys
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont
import ttkbootstrap as tb
from ttkbootstrap.scrolled import ScrolledText

# ---------------- НАСТРОЙКИ ----------------

DPI = 300
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp')

SPECIAL_PREFIX = "кружки3шт-"
NORMAL_PREFIX = "кружка-"

SPECIAL_SETS = {
    1: [1, 10, 2],
    2: [3, 11, 4],
    3: [5, 12, 6],
    4: [7, 8, 9],
}


def mm_to_px(mm_value: float) -> int:
    return int(round(mm_value / 25.4 * DPI))


def fmt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0

    total = int(round(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{minutes:02d}:{secs:02d}"


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


def make_output_pdf_path(save_folder: str) -> str:
    date_str = datetime.now().strftime("%d.%m")
    return unique_output_path(os.path.join(save_folder, f"Кружки от {date_str}.pdf"))


class PhotoLayoutApp:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title("Кружки. Макеты для печати")
        self.root.geometry("1800x1100")

        self.style = tb.Style("darkly")

        self.folder_var = tk.StringVar()
        self.save_folder_var = tk.StringVar(value=get_desktop_folder())
        self.width_mm_var = tk.StringVar(value="")
        self.height_mm_var = tk.StringVar(value="96")
        self.add_white_page_var = tk.BooleanVar(value=True)

        self.is_processing = False
        self.thread = None

        self.pause_event = threading.Event()
        self.pause_event.set()

        self.start_time = None

        self._build_ui()

    def _build_ui(self):
        top_frame = tb.Frame(self.root, padding=10)
        top_frame.pack(fill="x")

        tb.Label(top_frame, text="Папка с материалами:", anchor="w").pack(side="left")
        tb.Entry(top_frame, textvariable=self.folder_var, width=60).pack(side="left", padx=5)
        tb.Button(top_frame, text="Выбрать", command=self.browse_folder, bootstyle="secondary").pack(side="left")

        save_frame = tb.Frame(self.root, padding=(10, 0))
        save_frame.pack(fill="x", pady=5)

        tb.Label(save_frame, text="Куда сохранить макеты:", anchor="w").pack(side="left")
        tb.Entry(save_frame, textvariable=self.save_folder_var, width=60).pack(side="left", padx=5)
        tb.Button(
            save_frame,
            text="Выбрать",
            command=self.browse_save_folder,
            bootstyle="secondary",
        ).pack(side="left")

        opts_frame = tb.Frame(self.root, padding=(10, 0))
        opts_frame.pack(fill="x", pady=5)

        size_frame = tb.Labelframe(opts_frame, text="Размер фотографий", padding=10)
        size_frame.pack(side="left", padx=5)

        row1 = tb.Frame(size_frame)
        row1.pack(anchor="w", pady=2)

        tb.Label(row1, text="Ширина, мм:").pack(side="left")
        tb.Entry(row1, textvariable=self.width_mm_var, width=8).pack(side="left", padx=5)

        row2 = tb.Frame(size_frame)
        row2.pack(anchor="w", pady=2)

        tb.Label(row2, text="Высота, мм:").pack(side="left")
        tb.Entry(row2, textvariable=self.height_mm_var, width=8).pack(side="left", padx=5)

        row3 = tb.Frame(size_frame)
        row3.pack(anchor="w", pady=(8, 0))

        tb.Checkbutton(
            row3,
            text="Добавлять белые листы",
            variable=self.add_white_page_var,
            bootstyle="success-round-toggle",
        ).pack(side="left")

        tb.Label(
            opts_frame,
            text="Высота стоит по умолчанию, в ширине значения не нужны",
            justify="left",
            wraplength=400,
        ).pack(side="left", padx=20)

        list_frame = tb.Labelframe(self.root, text="Артикулы/Наименования тем", padding=10)
        list_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.names_text = ScrolledText(list_frame, height=10, autohide=True)
        self.names_text.pack(fill="both", expand=True)

        self._bind_names_text_shortcuts()

        btns_frame = tb.Frame(list_frame)
        btns_frame.pack(fill="x", pady=(5, 0))

        tb.Button(
            btns_frame,
            text="Вставить из буфера",
            command=self.paste_from_clipboard,
            bootstyle="info",
        ).pack(side="left")

        tb.Button(
            btns_frame,
            text="Очистить артикулы",
            command=lambda: self.names_text.delete("1.0", "end"),
            bootstyle="secondary",
        ).pack(side="left", padx=5)

        bottom_top = tb.Frame(self.root, padding=(10, 5))
        bottom_top.pack(fill="x")

        self.start_button = tb.Button(
            bottom_top,
            text="Старт",
            command=self.start_processing,
            bootstyle="success",
        )
        self.start_button.pack(side="left")

        self.pause_button = tb.Button(
            bottom_top,
            text="Пауза",
            command=self.pause_processing,
            bootstyle="warning",
        )
        self.pause_button.pack(side="left", padx=5)
        self.pause_button.config(state="disabled")

        self.resume_button = tb.Button(
            bottom_top,
            text="Продолжить",
            command=self.resume_processing,
            bootstyle="primary",
        )
        self.resume_button.pack(side="left", padx=5)
        self.resume_button.config(state="disabled")

        self.progress = tb.Progressbar(
            bottom_top,
            orient="horizontal",
            length=300,
            mode="determinate",
        )
        self.progress.pack(side="left", padx=10)

        self.progress_label = tb.Label(
            bottom_top,
            text="Прогресс: 0 / 0 тем (0%)  Осталось: --:--",
        )
        self.progress_label.pack(side="left")

        logs_container = tb.Frame(self.root, padding=10)
        logs_container.pack(fill="both", expand=True, padx=0, pady=(0, 10))

        log_frame = tb.Labelframe(logs_container, text="Логи", padding=10)
        log_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        self.log_text = ScrolledText(log_frame, height=10, autohide=True)
        self.log_text.pack(fill="both", expand=True)

        error_frame = tb.Labelframe(logs_container, text="Ошибки / предупреждения", padding=10)
        error_frame.pack(side="left", fill="both", expand=True, padx=(5, 0))

        self.error_log_text = ScrolledText(error_frame, height=10, autohide=True)
        self.error_log_text.pack(fill="both", expand=True)

    def _bind_names_text_shortcuts(self):
        widgets = [self.names_text]
        inner_text_widget = getattr(self.names_text, "text", None)

        if inner_text_widget is not None:
            widgets.append(inner_text_widget)

        for widget in widgets:
            try:
                widget.bind("<Control-KeyPress>", self._handle_names_text_control_shortcut)
            except Exception:
                pass

    def _handle_names_text_control_shortcut(self, event):
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
            return self._paste_into_names_text(event)

        if is_copy:
            return self._copy_from_names_text(event)

        return None

    def _paste_into_names_text(self, event=None):
        try:
            text = self.root.clipboard_get()
        except Exception:
            return "break"

        widget = event.widget if event is not None else self.names_text

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
            self.names_text.insert("insert", text)
            self.names_text.see("insert")

        return "break"

    def _copy_from_names_text(self, event=None):
        widget = event.widget if event is not None else self.names_text

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

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Выберите главную папку")
        if folder:
            self.folder_var.set(folder)

    def browse_save_folder(self):
        folder = filedialog.askdirectory(title="Выберите папку для сохранения")
        if folder:
            self.save_folder_var.set(folder)

    def paste_from_clipboard(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            return

        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

        if not text:
            return

        current_text = self.names_text.get("1.0", "end-1c")

        if current_text and not current_text.endswith("\n"):
            self.names_text.insert("end", "\n")

        self.names_text.insert("end", text)
        self.names_text.see("end")

    def log(self, msg: str):
        def _append():
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")

        self.root.after(0, _append)

    def err(self, msg: str):
        def _append():
            self.error_log_text.insert("end", msg + "\n")
            self.error_log_text.see("end")

        self.root.after(0, _append)

    def _format_eta(self, current: int, total: int) -> str:
        if not self.start_time or current <= 0 or total <= 0 or current > total:
            return "--:--"

        elapsed = time.time() - self.start_time
        per_item = elapsed / current
        remaining_items = total - current
        remaining = per_item * remaining_items

        return fmt_time(remaining)

    def set_progress(self, current: int, total: int):
        def _update():
            self.progress["maximum"] = max(1, total)
            self.progress["value"] = current

            if total > 0:
                percent = int(current / total * 100)
                percent = max(0, min(100, percent))
            else:
                percent = 0

            eta = self._format_eta(current, total)
            self.progress_label.config(
                text=f"Прогресс: {current} / {total} ({percent}%)  Осталось: {eta}"
            )

        self.root.after(0, _update)

    def pause_processing(self):
        if not self.is_processing:
            return

        self.pause_event.clear()
        self.pause_button.config(state="disabled")
        self.resume_button.config(state="normal")
        self.log("Пауза.")

    def resume_processing(self):
        if not self.is_processing:
            return

        self.pause_event.set()
        self.pause_button.config(state="normal")
        self.resume_button.config(state="disabled")
        self.log("Продолжили.")

    def start_processing(self):
        if self.is_processing:
            return

        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Ошибка", "Укажите корректную папку с фото.")
            return

        save_folder = self.save_folder_var.get().strip()
        if save_folder and not os.path.isdir(save_folder):
            messagebox.showerror("Ошибка", "Укажите корректную папку сохранения.")
            return

        width_mm = self._parse_mm(self.width_mm_var.get())
        height_mm = self._parse_mm(self.height_mm_var.get())

        names_raw = self.names_text.get("1.0", "end").strip()
        if not names_raw:
            messagebox.showerror("Ошибка", "Введите список имён.")
            return

        names = [n.strip() for n in names_raw.splitlines() if n.strip()]

        self.is_processing = True
        self.pause_event.set()

        self.start_button.config(state="disabled")
        self.pause_button.config(state="normal")
        self.resume_button.config(state="disabled")

        self.progress["value"] = 0
        self.start_time = time.time()
        self.set_progress(0, len(names))

        self.log_text.delete("1.0", "end")
        self.error_log_text.delete("1.0", "end")

        self.log("Работа началась...")

        self.thread = threading.Thread(
            target=self.process_worker,
            args=(folder, names, width_mm, height_mm, self.add_white_page_var.get()),
            daemon=True,
        )
        self.thread.start()

    def _parse_mm(self, s):
        if not s.strip():
            return None

        try:
            v = float(s.replace(",", "."))
            return v if v > 0 else None
        except Exception:
            return None

    def process_worker(self, folder, names, width_mm, height_mm, add_white_pages):
        try:
            self._process(folder, names, width_mm, height_mm, add_white_pages)
        except Exception as e:
            self.err(f"Критическая ошибка:\n{e}")
        finally:
            def _reset():
                self.is_processing = False
                self.start_button.config(state="normal")
                self.pause_button.config(state="disabled")
                self.resume_button.config(state="disabled")
                self.pause_event.set()

            self.root.after(0, _reset)

    def _is_special_name(self, name: str) -> bool:
        return name.lower().startswith(SPECIAL_PREFIX)

    def _parse_special_name(self, name: str):
        raw = name.strip()
        low = raw.lower()

        if not low.startswith(SPECIAL_PREFIX):
            return None, None

        body = raw[len(SPECIAL_PREFIX):]

        m = re.match(r"^(.*)_(\d+)$", body)
        if not m:
            return None, None

        base = m.group(1)
        set_num = int(m.group(2))

        return base, set_num

    def _parse_normal_numbered_stem(self, stem: str):
        low = stem.lower()

        if not low.startswith(NORMAL_PREFIX):
            return None, None

        body = stem[len(NORMAL_PREFIX):]
        m = re.match(r"^(.*)_(\d+)$", body)

        if not m:
            return None, None

        base = m.group(1)
        num = int(m.group(2))

        return base, num

    def _scan_images(self, folder):
        full_map = {}
        stem_map = {}
        series_map = {}

        stack = [folder]

        while stack:
            self.pause_event.wait()
            cur = stack.pop()

            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        self.pause_event.wait()

                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                                continue

                            if not entry.is_file(follow_symlinks=False):
                                continue
                        except Exception:
                            continue

                        fn_l = entry.name.lower()

                        if not fn_l.endswith(IMAGE_EXTS):
                            continue

                        full_path = entry.path
                        stem_l = os.path.splitext(fn_l)[0]

                        if fn_l not in full_map:
                            full_map[fn_l] = full_path

                        if stem_l not in stem_map:
                            stem_map[stem_l] = full_path

                        base, num = self._parse_normal_numbered_stem(stem_l)
                        if base is not None and num is not None:
                            base_l = base.lower()

                            if base_l not in series_map:
                                series_map[base_l] = {}

                            if num not in series_map[base_l]:
                                series_map[base_l][num] = full_path

            except Exception as e:
                self.err(f"Системные ошибки:\nНе удалось прочитать папку: {cur} | {e}")
                continue

        return full_map, stem_map, series_map

    def _resolve_normal_file(self, name, full_map, stem_map):
        key = name.strip().lower()

        if "." in key:
            return full_map.get(key)

        return stem_map.get(key)

    def _prepare_image(self, path, target_w_px, target_h_px, max_w_page):
        img = Image.open(path).convert("RGBA")

        ow, oh = img.size
        nw, nh = ow, oh

        if target_w_px and target_h_px:
            nw, nh = target_w_px, target_h_px
        elif target_w_px:
            scale = target_w_px / ow
            nw = target_w_px
            nh = int(oh * scale)
        elif target_h_px:
            scale = target_h_px / oh
            nh = target_h_px
            nw = int(ow * scale)

        if nw > max_w_page:
            scale = max_w_page / nw
            nw = int(nw * scale)
            nh = int(nh * scale)

        img_res = img.resize((nw, nh), Image.LANCZOS)

        return img_res, nw, nh

    def _draw_marks(self, draw, x, y, nw, nh):
        mark_len_px = mm_to_px(4)

        draw.line([(x, y), (x + mark_len_px, y)], fill="black", width=1)
        draw.line([(x + nw - mark_len_px, y), (x + nw, y)], fill="black", width=1)
        draw.line([(x, y + nh), (x + mark_len_px, y + nh)], fill="black", width=1)
        draw.line([(x + nw - mark_len_px, y + nh), (x + nw, y + nh)], fill="black", width=1)

    def _get_font(self, size_px):
        candidates = [
            "arial.ttf",
            "Arial.ttf",
            "segoeui.ttf",
            "tahoma.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\Arial.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\tahoma.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]

        for path in candidates:
            try:
                if os.path.isabs(path) and not os.path.isfile(path):
                    continue

                return ImageFont.truetype(path, size_px)
            except Exception:
                continue

        return ImageFont.load_default()

    def _get_category_info(self, name: str):
        if self._is_special_name(name):
            return "special", "Набор кружек 3шт"

        return "normal", "Кружки 1шт"

    def _get_separator_rect_size(self, target_w_px, target_h_px, max_w_page):
        if target_w_px:
            rect_w = min(target_w_px, max_w_page)
        else:
            rect_w = max_w_page

        if target_h_px:
            rect_h = target_h_px
        else:
            rect_h = mm_to_px(96)

        return rect_w, rect_h

    def _make_separator_page(self, category_title, W_px, H_px, gap_px, rect_w_px, rect_h_px):
        page = Image.new("RGB", (W_px, H_px), "white")
        draw = ImageDraw.Draw(page)

        total_block_h = rect_h_px * 3 + gap_px * 2
        start_y = (H_px - total_block_h) // 2
        x = (W_px - rect_w_px) // 2

        title_font = self._get_font(max(14, mm_to_px(8)))
        num_font = self._get_font(max(14, mm_to_px(9)))

        def draw_text_block(target_page, box_x, box_y, box_w, box_h, title, number, mirrored=False):
            layer = Image.new("RGBA", (box_w, box_h), (255, 255, 255, 0))
            layer_draw = ImageDraw.Draw(layer)

            title_bbox = layer_draw.textbbox((0, 0), title, font=title_font)
            title_w = title_bbox[2] - title_bbox[0]
            title_h = title_bbox[3] - title_bbox[1]

            num_bbox = layer_draw.textbbox((0, 0), number, font=num_font)
            num_w = num_bbox[2] - num_bbox[0]
            num_h = num_bbox[3] - num_bbox[1]

            inner_gap = mm_to_px(4)
            block_h = title_h + inner_gap + num_h

            text_top = (box_h - block_h) // 2

            title_x = (box_w - title_w) // 2
            title_y = text_top

            num_x = (box_w - num_w) // 2
            num_y = title_y + title_h + inner_gap

            layer_draw.text((title_x, title_y), title, fill="black", font=title_font)
            layer_draw.text((num_x, num_y), number, fill="black", font=num_font)

            if mirrored:
                layer = layer.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

            target_page.paste(layer, (box_x, box_y), layer)

        for idx in range(3):
            number = str(idx + 1)
            y = start_y + idx * (rect_h_px + gap_px)

            draw.rectangle(
                [(x, y), (x + rect_w_px, y + rect_h_px)],
                outline="black",
                width=1,
            )

            top_h = rect_h_px // 2
            bottom_h = rect_h_px - top_h

            draw_text_block(
                page,
                x,
                y,
                rect_w_px,
                top_h,
                category_title,
                number,
                mirrored=True,
            )

            draw_text_block(
                page,
                x,
                y + top_h,
                rect_w_px,
                bottom_h,
                category_title,
                number,
                mirrored=False,
            )

        return page

    def _make_page(self, page_items, W_px, H_px, gap_px, top_margin_px):
        page = Image.new("RGB", (W_px, H_px), "white")
        draw = ImageDraw.Draw(page)

        n_items = len(page_items)
        total_block_h = sum(h for _, _, h, _ in page_items) + gap_px * (n_items - 1)

        if n_items == 3:
            y = (H_px - total_block_h) // 2
        else:
            y = top_margin_px

        for img_res, nw, nh, base in page_items:
            self.pause_event.wait()

            x = (W_px - nw) // 2

            page.paste(img_res, (x, y), img_res)
            self._draw_marks(draw, x, y, nw, nh)

            y += nh + gap_px

        return page

    def _process(self, folder, names, width_mm, height_mm, add_white_pages):
        full_map, stem_map, series_map = self._scan_images(folder)

        if not full_map and not stem_map:
            self.err("Системные ошибки:\nНет изображений в папке.")
            return

        W_mm, H_mm = 210, 297
        W_px = mm_to_px(W_mm)
        H_px = mm_to_px(H_mm)

        gap_px = mm_to_px(1)
        top_margin_px = mm_to_px(10)

        target_w_px = mm_to_px(width_mm) if width_mm else None
        target_h_px = mm_to_px(height_mm) if height_mm else None

        max_w_page = W_px - mm_to_px(10)
        rect_w_px, rect_h_px = self._get_separator_rect_size(
            target_w_px,
            target_h_px,
            max_w_page,
        )

        save_dir = self.save_folder_var.get().strip()
        if not save_dir or not os.path.isdir(save_dir):
            desktop = get_desktop_folder()
            if not os.path.isdir(desktop):
                desktop = os.getcwd()
            save_dir = desktop

        pages = []
        current_normal_items = []
        total = len(names)
        current_category_key = None

        def flush_normal_page():
            nonlocal current_normal_items

            if not current_normal_items:
                return

            page = self._make_page(
                current_normal_items,
                W_px,
                H_px,
                gap_px,
                top_margin_px,
            )

            pages.append(page)
            current_normal_items = []

        for i, name in enumerate(names, start=1):
            self.pause_event.wait()

            name = name.strip()

            if not name:
                self.set_progress(i, total)
                continue

            category_key, category_title = self._get_category_info(name)

            if category_key != current_category_key:
                if current_category_key == "normal":
                    flush_normal_page()

                if add_white_pages:
                    sep_page = self._make_separator_page(
                        category_title,
                        W_px,
                        H_px,
                        gap_px,
                        rect_w_px,
                        rect_h_px,
                    )
                    pages.append(sep_page)

                current_category_key = category_key
                self.log(f"{category_title}:")

            if self._is_special_name(name):
                base, set_num = self._parse_special_name(name)

                if base is None or set_num is None:
                    self.err(f"{category_title}:\n{name} - неверный формат имени")
                    self.set_progress(i, total)
                    continue

                if set_num not in SPECIAL_SETS:
                    self.err(f"{category_title}:\n{name} - нет схемы, доступны только _1, _2, _3, _4")
                    self.set_progress(i, total)
                    continue

                base_l = base.lower()

                if base_l not in series_map:
                    self.err(
                        f"{category_title}:\n{name} - не найдена серия файлов вида "
                        f"'кружка-{base}_1', 'кружка-{base}_2' и т.д."
                    )
                    self.set_progress(i, total)
                    continue

                numbers = SPECIAL_SETS[set_num]
                page_items = []
                missing = []

                for num in numbers:
                    path = series_map[base_l].get(num)

                    if not path:
                        missing.append(num)
                        continue

                    try:
                        img_res, nw, nh = self._prepare_image(
                            path,
                            target_w_px,
                            target_h_px,
                            max_w_page,
                        )
                        page_items.append((img_res, nw, nh, os.path.basename(path)))

                    except Exception as e:
                        self.err(f"{category_title}:\n{name} - не открыть фото №{num}: {e}")

                if missing:
                    self.err(
                        f"{category_title}:\n{name} - не найдены номера: "
                        + ", ".join(map(str, missing))
                    )

                if page_items and not missing:
                    page = self._make_page(
                        page_items,
                        W_px,
                        H_px,
                        gap_px,
                        top_margin_px,
                    )

                    pages.append(page)
                    self.log(f"{name} - успешно")

            else:
                path = self._resolve_normal_file(name, full_map, stem_map)

                if not path:
                    self.err(f"{category_title}:\n{name} - не найдено")
                    self.set_progress(i, total)
                    continue

                try:
                    img_res, nw, nh = self._prepare_image(
                        path,
                        target_w_px,
                        target_h_px,
                        max_w_page,
                    )

                    current_normal_items.append((img_res, nw, nh, os.path.basename(path)))
                    self.log(f"{name} - успешно")

                    if len(current_normal_items) >= 3:
                        flush_normal_page()

                except Exception as e:
                    self.err(f"{category_title}:\n{name} - не открыть файл: {e}")

            self.set_progress(i, total)

        flush_normal_page()

        if not pages:
            self.err("Системные ошибки:\nНичего не найдено.")
            return

        pdf_path = make_output_pdf_path(save_dir)

        try:
            pages[0].save(
                pdf_path,
                "PDF",
                resolution=DPI,
                save_all=True,
                append_images=pages[1:],
            )

            elapsed = time.time() - self.start_time if self.start_time else 0
            self.log(f"Готово. Время: {fmt_time(elapsed)}. PDF листов: {len(pages)}")

            messagebox.showinfo(
                "Готово",
                f"PDF создан:\n{pdf_path}\n\nВремя: {fmt_time(elapsed)}\nPDF листов: {len(pages)}",
            )

        except Exception as e:
            self.err(f"Системные ошибки:\nНе сохранить PDF: {e}")


if __name__ == "__main__":
    root = tb.Window(themename="darkly")
    app = PhotoLayoutApp(root)
    root.mainloop()