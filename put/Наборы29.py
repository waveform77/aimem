# -*- coding: utf-8 -*-
import os
import sys
import time
import queue
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.scrolled import ScrolledText

from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader


# ---------------- НАСТРОЙКИ ----------------

# Состав и порядок наборов оставлены как в исходной программе
JOBS = [
    {
        "name": "Брелок",
        "subfolder": "Брелок",
        "filename": "Брелок.pdf",
        "page_size": (210, 297),
        "photo_size": (37, 54),
        "cols": 5,
        "rows": 5,
        "per_page": 25,
        "mirror": True,
        "notepad_dup": False,
    },
    {
        "name": "Love is",
        "subfolder": "Наклейки Love is",
        "filename": "Love is.pdf",
        "page_size": (320, 450),
        "photo_size": (49, 69),
        "cols": 6,
        "rows": 6,
        "per_page": 36,
        "mirror": False,
        "notepad_dup": False,
    },
    {
        "name": "Фото-наклейки",
        "subfolder": "Фото наклейки",
        "filename": "Фото-наклейки.pdf",
        "page_size": (320, 450),
        "photo_size": (49, 69),
        "cols": 6,
        "rows": 6,
        "per_page": 36,
        "mirror": False,
        "notepad_dup": False,
    },
    {
        "name": "Постер А3",
        "subfolder": "Постер А3",
        "filename": "Постеры А3.pdf",
        "page_size": (297, 420),
        "photo_size": None,  # на весь лист
        "cols": 1,
        "rows": 1,
        "per_page": 1,
        "mirror": False,
        "notepad_dup": False,
    },
    {
        "name": "Мини-постеры",
        "subfolder": "Мини-постеры",
        "filename": "Мини-постеры.pdf",
        "page_size": (320, 450),
        "photo_size": (104, 149),
        "cols": 3,
        "rows": 3,
        "per_page": 9,
        "mirror": False,
        "notepad_dup": False,
    },
    {
        "name": "Открытки 7х10см",
        "subfolder": "Открытки",
        "filename": "Открытки 7х10см.pdf",
        "page_size": (320, 450),
        "photo_size": (74, 104),
        "cols": 4,
        "rows": 4,
        "per_page": 16,
        "mirror": False,
        "notepad_dup": False,
    },
    {
        "name": "Блокнот",
        "subfolder": "Блокнот",
        "filename": "Блокнот.pdf",
        "page_size": (320, 450),
        "photo_size": (156, 218),
        "cols": 2,
        "rows": 2,
        "per_page": 4,
        "mirror": False,
        "notepad_dup": True,
    },
]

IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp')


TARGET_IMAGE_DPI = 180


def points_to_pixels(value_points: float, dpi: int = TARGET_IMAGE_DPI) -> int:
    """
    Перевод размеров из points (единицы ReportLab) в пиксели целевого растра.
    Мы не тянем в PDF исходник целиком, а заранее уменьшаем его до разумного размера
    под фактический размер печати. Это сильно снижает лишнюю перепаковку и ускоряет вставку.
    """
    inches = float(value_points) / 72.0
    return max(1, int(round(inches * dpi)))


def create_white_placeholder_image(
    width_points: float,
    height_points: float,
    dpi: int = TARGET_IMAGE_DPI,
):
    target_w_px = points_to_pixels(width_points, dpi)
    target_h_px = points_to_pixels(height_points, dpi)

    image = Image.new("RGB", (target_w_px, target_h_px), (255, 255, 255))
    return ImageReader(image)


def prepare_image_reader(img_path: str, width_points: float, height_points: float, cache: dict | None = None):
    """
    Готовит ImageReader без промежуточного сохранения PNG в BytesIO.
    Дополнительно уменьшаем изображение до целевого размера, чтобы не тащить в PDF
    огромные исходники, которые все равно будут напечатаны маленькими.
    """
    target_w_px = points_to_pixels(width_points)
    target_h_px = points_to_pixels(height_points)

    key = (img_path, target_w_px, target_h_px)
    if cache is not None and key in cache:
        return cache[key]

    if not img_path or not os.path.exists(img_path):
        reader = create_white_placeholder_image(width_points, height_points)

        if cache is not None:
            cache[key] = reader

        return reader

    try:
        with Image.open(img_path) as img:
            try:
                img.draft("RGB", (target_w_px, target_h_px))
            except Exception:
                pass

            img = img.convert("RGB")
            if img.size != (target_w_px, target_h_px):
                img = img.resize((target_w_px, target_h_px), Image.LANCZOS)

            reader = ImageReader(img)
    except Exception:
        reader = create_white_placeholder_image(width_points, height_points)

    if cache is not None:
        cache[key] = reader
    return reader


# ---------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------------

def delete_folder(folder_path: str):
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)


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


def create_output_folder(base: str) -> str:
    today = datetime.now().strftime('%d.%m.%y')
    name = f"Наборы29 макеты от {today}"
    out_dir = os.path.join(base, name)
    delete_folder(out_dir)
    os.makedirs(out_dir)
    return out_dir


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


def get_image_files(folder: str) -> list[str]:
    """
    Быстрее старого os.listdir + os.path.isfile:
    os.scandir меньше дергает файловую систему.
    """
    files = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.is_file() and os.path.splitext(entry.name)[1].lower() in IMAGE_EXTS:
                    files.append(entry.path)
    except Exception:
        return []

    # Оставлено обычное строковое упорядочивание, как было в исходнике.
    files.sort()
    return files


def build_theme_index(root: str) -> dict[str, str]:
    """
    Старый код для каждой темы заново делал os.walk(root).
    Это было самое тяжелое место на больших папках.

    Теперь весь каталог индексируется один раз:
    имя папки -> путь к папке.
    Прямые подпапки root имеют приоритет, как и в исходной логике.
    """
    index: dict[str, str] = {}

    try:
        with os.scandir(root) as it:
            for entry in it:
                if entry.is_dir():
                    index.setdefault(entry.name, entry.path)
    except Exception:
        return index

    for dirpath, dirnames, _ in os.walk(root):
        for dirname in dirnames:
            index.setdefault(dirname, os.path.join(dirpath, dirname))

    return index


def collect_images_strict_order(
    theme_index: dict[str, str],
    theme_names: list[str],
    subfolder: str,
    notepad_dup: bool = False,
    log_fn=None,
    err_fn=None,
) -> tuple[list[str], list[str], list[int], list[bool], bool, list[tuple[int, str, list[str]]]]:
    blocks: list[tuple[int, str, list[str]]] = []

    count_per_theme = 1
    if subfolder in ("Наклейки Love is", "Фото наклейки"):
        count_per_theme = 6
    elif subfolder == "Открытки":
        count_per_theme = 8
    elif subfolder == "Мини-постеры":
        count_per_theme = 6
    elif subfolder.lower().startswith("постеры"):
        count_per_theme = 1
    elif subfolder in ("Брелок", "Блокнот"):
        count_per_theme = 1

    for theme_idx, theme in enumerate(theme_names):
        theme = theme.strip()
        theme_folder = theme_index.get(theme)

        files = []
        if theme_folder and os.path.isdir(theme_folder):
            sub_path = os.path.join(theme_folder, subfolder)
            if os.path.isdir(sub_path):
                files = get_image_files(sub_path)

                if len(files) == 0:
                    if err_fn:
                        err_fn(f"Папка {theme}: нет фото для папки {subfolder}")
                elif len(files) < count_per_theme:
                    if err_fn:
                        err_fn(
                            f"Папка {theme}: не достаточно файлов для папки {subfolder} "
                            f"(нужно {count_per_theme}, найдено {len(files)})"
                        )

                if len(files) < count_per_theme:
                    files = files[:count_per_theme] + [""] * (count_per_theme - len(files))
                else:
                    files = files[:count_per_theme]
            else:
                if err_fn:
                    err_fn(f"Папка {theme}: нет фото для папки {subfolder}")
                files = [""] * count_per_theme
        else:
            if err_fn:
                err_fn(f"Папка {theme}: нет фото для папки {subfolder}")
            files = [""] * count_per_theme

        blocks.append((theme_idx, theme, files))

    result_files: list[str] = []
    result_themes: list[str] = []
    result_theme_indices: list[int] = []
    result_last_flags: list[bool] = []

    # Строго сохраняем порядок тем для всех категорий:
    # Брелок, Love is, Фото-наклейки, Постер А3, Мини-постеры, Открытки, Блокнот.
    # Никаких перестановок тем здесь нет: как вставлены наименования, так они и идут в PDF.
    ordered_blocks = blocks

    for theme_idx, theme, block in ordered_blocks:
        if subfolder == "Блокнот" and notepad_dup and block:
            files_for_theme = [block[0], block[0]]
        else:
            files_for_theme = block

        for i, file_path in enumerate(files_for_theme):
            result_files.append(file_path)
            result_themes.append(theme)
            result_theme_indices.append(theme_idx)
            result_last_flags.append(i == len(files_for_theme) - 1)

    align_top = subfolder in ("Блокнот", "Наклейки Love is", "Фото наклейки")

    return result_files, result_themes, result_theme_indices, result_last_flags, align_top, blocks


def draw_img_to_pdf(c, img_path: str, x, y, width, height, log_fn=None, err_fn=None, image_cache=None):
    try:
        img_reader = prepare_image_reader(img_path, width, height, cache=image_cache)
        c.drawImage(img_reader, x, y, width=width, height=height)
    except Exception as e:
        msg = f"Ошибка вставки {img_path}: {e}"
        if err_fn:
            err_fn(msg)
        elif log_fn:
            log_fn(msg)


def mark_category_theme_done(job_name: str, theme: str, theme_idx: int, log_fn, theme_done_fn):
    """Лог по теме сразу после фактической вставки ее фото в текущий PDF."""
    if log_fn:
        log_fn(f"✅ {job_name}: {theme} — выполнена")
    if theme_done_fn:
        theme_done_fn(theme_idx)


def process_job(
    theme_index: dict[str, str],
    theme_names: list[str],
    out_folder: str,
    job: dict,
    log_fn,
    err_fn,
    pause_event: threading.Event,
    stop_event: threading.Event,
    theme_done_fn=None,
):
    """
    Одна PDF-задача.
    Важно: внутри нет прямой работы с Tkinter. Только callbacks log_fn/err_fn,
    которые кладут сообщения в очередь интерфейса.
    """
    if stop_event.is_set():
        return

    try:
        log_fn(f'Генерируется {job["name"]}...')

        page_w, page_h = job["page_size"]

        img_files, img_themes, img_theme_indices, img_last_flags, align_top, theme_blocks = collect_images_strict_order(
            theme_index=theme_index,
            theme_names=theme_names,
            subfolder=job["subfolder"],
            notepad_dup=job.get("notepad_dup", False),
            log_fn=log_fn,
            err_fn=err_fn,
        )

        # Темы без фото тоже считаем обработанными для прогресса, иначе прогресс может не дойти до конца.
        for theme_idx, theme, files in theme_blocks:
            if not files and theme_done_fn:
                theme_done_fn(theme_idx)

        if not img_files:
            log_fn(f'Не найдено ни одной фотки для {job["name"]}')
            return

        img_w, img_h = job["photo_size"] if job["photo_size"] else (page_w, page_h)
        per_page = job["per_page"]
        cols = job["cols"]
        image_cache = {}

        # Специальную логику названия "Постеры А3" оставляем как в исходнике.
        # Чтобы не менять поведение без отдельной команды.
        if job["subfolder"] == "Постеры А3":
            c = None
            out_path = os.path.join(out_folder, job["filename"])
            for img_path, theme, theme_idx, is_last in zip(img_files, img_themes, img_theme_indices, img_last_flags):
                if stop_event.is_set():
                    break
                pause_event.wait()

                if img_path:
                    with Image.open(img_path) as img:
                        iw, ih = img.size
                else:
                    iw, ih = page_w * mm, page_h * mm

                if c is None:
                    c = canvas.Canvas(out_path, pagesize=(iw, ih))
                else:
                    c.setPageSize((iw, ih))

                img_reader = prepare_image_reader(img_path, iw, ih, cache=image_cache)
                c.drawImage(img_reader, 0, 0, width=iw, height=ih)
                c.showPage()

                if is_last:
                    mark_category_theme_done(job["name"], theme, theme_idx, log_fn, theme_done_fn)

            if c is not None:
                c.save()
                log_fn(f'{job["filename"]} — Сохранено!')
            return

        # Брелок — зеркальная вторая страница
        if job["subfolder"] == "Брелок" and job.get("mirror", False):
            c = canvas.Canvas(os.path.join(out_folder, job["filename"]), pagesize=(page_w * mm, page_h * mm))
            pages = [
                (
                    img_files[i:i + per_page],
                    img_themes[i:i + per_page],
                    img_theme_indices[i:i + per_page],
                    img_last_flags[i:i + per_page],
                )
                for i in range(0, len(img_files), per_page)
            ]

            for page_imgs, page_themes, page_theme_indices, page_last_flags in pages:
                if stop_event.is_set():
                    break
                pause_event.wait()

                count = len(page_imgs)
                rows = (count + cols - 1) // cols
                total_h = rows * img_h * mm
                y0 = (page_h * mm - total_h) / 2
                pos = []

                for r in range(rows):
                    c_in_row = min(cols, count - r * cols)
                    total_w = c_in_row * img_w * mm
                    x0 = (page_w * mm - total_w) / 2
                    for col in range(c_in_row):
                        x = x0 + col * img_w * mm
                        # PDF считает Y снизу, поэтому для визуального порядка
                        # сверху-вниз используем обратный индекс строки.
                        # Теперь брелоки идут как список тем: верхний ряд слева-направо,
                        # потом следующий ряд и т.д.
                        y = y0 + (rows - 1 - r) * img_h * mm
                        pos.append((x, y))

                page_items = list(zip(page_imgs, page_themes, page_theme_indices, page_last_flags))

                for (img_path, theme, theme_idx, is_last), (x, y) in zip(page_items, pos):
                    if stop_event.is_set():
                        break
                    pause_event.wait()
                    draw_img_to_pdf(c, img_path, x, y, img_w * mm, img_h * mm, log_fn, err_fn, image_cache)

                c.showPage()

                mirrored_items = []
                for r in range(rows):
                    start = r * cols
                    end = min(count, (r + 1) * cols)
                    row = page_items[start:end]
                    mirrored_items.extend(row[::-1])

                for (img_path, theme, theme_idx, is_last), (x, y) in zip(mirrored_items, pos):
                    if stop_event.is_set():
                        break
                    pause_event.wait()
                    draw_img_to_pdf(c, img_path, x, y, img_w * mm, img_h * mm, log_fn, err_fn, image_cache)
                    if is_last:
                        mark_category_theme_done(job["name"], theme, theme_idx, log_fn, theme_done_fn)

                c.showPage()

            c.save()
            log_fn(f'{job["filename"]} — Сохранено!')
            return

        # Общий случай
        c = canvas.Canvas(os.path.join(out_folder, job["filename"]), pagesize=(page_w * mm, page_h * mm))
        pages = [
            (
                img_files[i:i + per_page],
                img_themes[i:i + per_page],
                img_theme_indices[i:i + per_page],
                img_last_flags[i:i + per_page],
            )
            for i in range(0, len(img_files), per_page)
        ]

        for page_imgs, page_themes, page_theme_indices, page_last_flags in pages:
            if stop_event.is_set():
                break
            pause_event.wait()

            count = len(page_imgs)
            total_slots = job["per_page"]

            if align_top and count < total_slots:
                missing = total_slots - count
                page_imgs = page_imgs + [None] * missing
                page_themes = page_themes + [None] * missing
                page_theme_indices = page_theme_indices + [None] * missing
                page_last_flags = page_last_flags + [False] * missing

            rows = (total_slots + cols - 1) // cols if align_top else (count + cols - 1) // cols
            total_h = rows * img_h * mm
            y0 = (page_h * mm - total_h) / 2
            pos = []

            for r in range(rows):
                c_in_row = min(cols, total_slots - r * cols) if align_top else min(cols, count - r * cols)
                total_w = c_in_row * img_w * mm
                x0 = (page_w * mm - total_w) / 2

                for col in range(c_in_row):
                    x = x0 + col * img_w * mm
                    # Визуальный порядок для остальных категорий:
                    # сверху-вниз, слева-направо, строго по списку наименований тем.
                    y = y0 + (rows - 1 - r) * img_h * mm
                    pos.append((x, y))

            for img_path, theme, theme_idx, is_last, (x, y) in zip(page_imgs, page_themes, page_theme_indices, page_last_flags, pos):
                if stop_event.is_set():
                    break
                pause_event.wait()

                draw_img_to_pdf(c, img_path, x, y, img_w * mm, img_h * mm, log_fn, err_fn, image_cache)
                if is_last and theme is not None:
                    mark_category_theme_done(job["name"], theme, theme_idx, log_fn, theme_done_fn)

            c.showPage()

        c.save()
        log_fn(f'{job["filename"]} — Сохранено!')

    except Exception as e:
        err_fn(f'Ошибка генерации {job.get("name", "без имени")}: {e}')


# ---------------- GUI ----------------

class App:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title("Наборы 29 предметов. Макеты для печати")
        self.root.geometry("1250x1000")
        self.root.minsize(1100, 650)

        self.style = tb.Style("darkly")

        self.folder_var = tk.StringVar()
        self.save_folder_var = tk.StringVar(value=get_desktop_folder())
        self.threads_var = tk.IntVar(value=min(7, max(2, (os.cpu_count() or 8) - 1)))

        self.pause_event = threading.Event()
        self.pause_event.set()

        self.stop_event = threading.Event()
        self.ui_queue = queue.Queue()
        self.worker_thread = None

        self.total_themes = 0
        self.done_themes = 0
        self.run_start_ts = None
        self.out_folder = None

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
        tb.Entry(row1, textvariable=self.folder_var).pack(side=LEFT, fill=X, expand=YES, padx=8)
        tb.Button(row1, text="Выбрать", bootstyle=PRIMARY, command=self.choose_folder).pack(side=LEFT)

        row2 = tb.Frame(paths)
        row2.pack(fill=X, pady=4)
        tb.Label(row2, text="Куда сохранить макеты:", width=24, anchor=W).pack(side=LEFT)
        tb.Entry(row2, textvariable=self.save_folder_var).pack(side=LEFT, fill=X, expand=YES, padx=8)
        tb.Button(row2, text="Выбрать", bootstyle=PRIMARY, command=self.choose_save_folder).pack(side=LEFT)

        row3 = tb.Frame(paths)
        row3.pack(fill=X, pady=4)
        tb.Label(row3, text="Потоки:", width=24, anchor=W).pack(side=LEFT)
        tb.Spinbox(row3, from_=1, to=16, textvariable=self.threads_var, width=6).pack(side=LEFT, padx=(0, 8))
        tb.Label(
            row3,
            text="Больше 7 потоков почти не дает прироста",
            foreground="#c9c9c9"
        ).pack(side=LEFT)

        names_frame = tb.Labelframe(outer, text="Артикулы/Наименования тем", padding=10)
        names_frame.pack(fill=X, pady=(10, 0))

        self.names_box = ScrolledText(names_frame, height=7, autohide=True)
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

        self.log_box = ScrolledText(left, height=17, autohide=True)
        self.log_box.pack(fill=BOTH, expand=YES)

        self.err_box = ScrolledText(right, height=17, autohide=True)
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

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Выбери главную папку с темами")
        if folder:
            self.folder_var.set(folder)

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

    def start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        root_folder = self.folder_var.get().strip()
        if not root_folder or not os.path.exists(root_folder):
            messagebox.showerror("Ошибка", "Укажите корректную с материалами.")
            return

        save_base = self.save_folder_var.get().strip()
        if not save_base or not os.path.exists(save_base):
            messagebox.showerror("Ошибка", "Выбери папку, куда сохранить готовую папку с макетами!")
            return

        names = self.names_box.get("1.0", "end").strip().splitlines()
        names = [n.strip() for n in names if n.strip()]
        if not names:
            messagebox.showerror("Ошибка", "Вставь наименования!")
            return

        self._clear_text(self.log_box)
        self._clear_text(self.err_box)

        self.stop_event.clear()
        self.pause_event.set()

        self.b_start.config(state=DISABLED)
        self.b_paste.config(state=DISABLED)
        self.b_pause.config(state=NORMAL)
        self.b_resume.config(state=DISABLED)
        self.b_stop.config(state=NORMAL)

        self.done_themes = 0
        self.total_themes = len(names)
        self.run_start_ts = time.time()
        self._set_progress(0, self.total_themes)

        try:
            self.out_folder = create_output_folder(save_base)
        except Exception as e:
            self._err(f"Не удалось создать папку результата: {e}")
            self._finish_ui()
            return

        threads_count = max(1, int(self.threads_var.get()))
        self.worker_thread = threading.Thread(
            target=self.process_all,
            args=(root_folder, names, threads_count),
            daemon=True
        )
        self.worker_thread.start()

    # ---------- безопасные обновления GUI через очередь ----------

    def _log(self, msg: str):
        self.ui_queue.put(("log", msg))

    def _err(self, msg: str):
        self.ui_queue.put(("err", msg))

    def _set_progress(self, done: int, total: int):
        self.ui_queue.put(("prog", done, total))

    def _finish_ui(self):
        self.ui_queue.put(("finish",))

    def _poll_ui_queue(self):
        max_items_per_tick = 12
        processed = 0
        try:
            while processed < max_items_per_tick:
                item = self.ui_queue.get_nowait()
                processed += 1
                t = item[0]

                if t == "log":
                    self._append_text(self.log_box, item[1])

                elif t == "err":
                    self._append_text(self.err_box, item[1])

                elif t == "prog":
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

                elif t == "finish":
                    self.b_start.config(state=NORMAL)
                    self.b_paste.config(state=NORMAL)
                    self.b_pause.config(state=DISABLED)
                    self.b_resume.config(state=DISABLED)
                    self.b_stop.config(state=DISABLED)

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

    # ---------- рабочая логика ----------

    def process_all(self, root_folder: str, theme_names: list[str], threads_count: int):
        t0 = time.time()

        try:
            self._log("Индексирую папки один раз...")
            theme_index = build_theme_index(root_folder)
            self._log(f"Найдено папок в индексе: {len(theme_index)}")

            workers = max(1, min(threads_count, len(JOBS)))
            self._log(f"Потоки: {workers}")
            self._log(f"Папка результата: {self.out_folder}")

            self.done_themes = 0
            self.total_themes = len(theme_names)
            self._set_progress(0, self.total_themes)

            category_total = len(JOBS)
            theme_category_counts = [0] * len(theme_names)
            fully_done_theme_indices = set()
            progress_lock = threading.Lock()

            def mark_theme_category_done(theme_idx: int):
                if theme_idx is None:
                    return
                if theme_idx < 0 or theme_idx >= len(theme_category_counts):
                    return
                with progress_lock:
                    if theme_idx in fully_done_theme_indices:
                        return
                    theme_category_counts[theme_idx] += 1
                    if theme_category_counts[theme_idx] >= category_total:
                        fully_done_theme_indices.add(theme_idx)
                        self.done_themes = len(fully_done_theme_indices)
                        self._set_progress(self.done_themes, self.total_themes)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        process_job,
                        theme_index,
                        theme_names,
                        self.out_folder,
                        job,
                        self._log,
                        self._err,
                        self.pause_event,
                        self.stop_event,
                        mark_theme_category_done,
                    ): job
                    for job in JOBS
                }

                for future in as_completed(futures):
                    job = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        self._err(f'Ошибка задания {job["name"]}: {e}')

                    if self.stop_event.is_set():
                        # Все задания уже получили stop_event и сами быстро завершатся.
                        pass

            dt = time.time() - t0
            done = self.done_themes
            total = self.total_themes
            if self.stop_event.is_set():
                self._log(f"Остановлено. Готово {done}/{total} тем. Время: {fmt_time(dt)}")
            else:
                self._log(f"Готово! {done}/{total} тем. Время: {fmt_time(dt)}")

        except Exception as e:
            self._err(f"Критическая ошибка: {e}")

        finally:
            self._finish_ui()


def main():
    root = tb.Window(themename="darkly")
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()