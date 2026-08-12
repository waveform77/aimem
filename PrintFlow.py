# -*- coding: utf-8 -*-
import sys
import traceback
import importlib.util
from pathlib import Path

import tkinter as tk
from PIL import Image, ImageTk

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.scrolled import ScrolledText


# Эти импорты нужны, чтобы PyInstaller видел библиотеки,
# которые используются внутри скриптов из папки put.
try:
    import PIL.Image
    import PIL.ImageOps
    import PIL.ImageDraw
    import PIL.ImageFont
    import PIL.ImageTk
except Exception:
    pass

try:
    import reportlab
    import reportlab.pdfgen.canvas
    import reportlab.lib.utils
    import reportlab.lib.units
except Exception:
    pass

try:
    import fitz
except Exception:
    pass


TITLE = "PrintFlow"

# Все скрипты-модули должны лежать в папке put рядом с этим скриптом.
# При сборке EXE эта папка будет зашиваться внутрь exe.
MODULES_FOLDER_NAME = "put"

# Иконка приложения.
# Лучше всего использовать icon.ico.
# Можно положить рядом со скриптом/EXE файл icon.ico.
ICON_FILENAMES = (
    "icon.ico",
    "icon.png",
    "icon.gif",
    "icon",
)


PROGRAMS = [
    {
        "tab": "Блокноты и скетчбуки",
        "file": "Блокноты и скетчбуки.py",
        "class": "App",
    },
    {
        "tab": "Брелоки",
        "file": "Брелоки.py",
        "class": "App",
    },
    {
        "tab": "Мини-постеры",
        "file": "Мини-постеры.py",
        "class": "App",
    },
    {
        "tab": "Наборы 29",
        "file": "Наборы29.py",
        "class": "App",
    },
    {
        "tab": "Наборы 54",
        "file": "Наборы54.py",
        "class": "App",
    },
    {
        "tab": "Наклейки",
        "file": "Наклейки.py",
        "class": "App",
    },
    {
        "tab": "Открытки",
        "file": "Открытки.py",
        "class": "App",
    },
    {
        "tab": "Постеры и календари",
        "file": "Постеры и календари.py",
        "class": "App",
    },
    {
        "tab": "Значки",
        "file": "Значки.py",
        "class": "EllipseLayoutApp",
    },
    {
        "tab": "Кружки",
        "file": "Кружки.py",
        "class": "PhotoLayoutApp",
    },
]


def get_app_folder() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


def get_resource_folder() -> Path:
    """
    Папка ресурсов.
    В обычном запуске это папка рядом со скриптом.
    В EXE onefile это временная папка PyInstaller sys._MEIPASS.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)

    return get_app_folder()


def get_modules_folder() -> Path:
    """
    В обычном запуске берет put рядом со скриптом.
    В собранном EXE берет put изнутри EXE.
    """
    bundled_put = get_resource_folder() / MODULES_FOLDER_NAME
    external_put = get_app_folder() / MODULES_FOLDER_NAME

    if getattr(sys, "frozen", False):
        if bundled_put.exists():
            return bundled_put
        return external_put

    return external_put


def find_icon_path() -> Path | None:
    """
    Ищет иконку:
    1) рядом со скриптом/EXE;
    2) внутри EXE, если icon.ico добавили через --add-data.
    """
    folders = [
        get_app_folder(),
        get_resource_folder(),
    ]

    checked = set()

    for folder in folders:
        folder = Path(folder)

        if folder in checked:
            continue

        checked.add(folder)

        for filename in ICON_FILENAMES:
            icon_path = folder / filename

            if icon_path.exists() and icon_path.is_file():
                return icon_path

    return None


def set_windows_app_id():
    """
    Для Windows: помогает нормально показывать иконку на панели задач.
    """
    if sys.platform != "win32":
        return

    try:
        import ctypes

        app_id = "PrintFlow.MaketyDlyaPechati"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def set_window_icon(root):
    """
    Ставит иконку в левом верхнем углу окна и на панели задач.

    Работает:
    - при обычном запуске .py;
    - при запуске собранного .exe;
    - с icon.ico рядом со скриптом/EXE;
    - с icon.ico, зашитой внутрь EXE через --add-data.
    """
    icon_path = find_icon_path()

    if not icon_path:
        print("Иконка не найдена")
        return

    print(f"Найдена иконка: {icon_path}")

    # 1) Стандартный способ для Windows .ico.
    # Иногда он не срабатывает в обычном .py, поэтому ниже есть второй способ.
    try:
        if icon_path.suffix.lower() == ".ico" or icon_path.suffix == "":
            root.iconbitmap(str(icon_path))
            root.iconbitmap(default=str(icon_path))
            print("Иконка установлена через iconbitmap")
    except Exception as error:
        print(f"iconbitmap не сработал: {error}")

    # 2) Более надежный способ через Pillow/ImageTk.
    # Он помогает, когда iconbitmap не применяет иконку в окне .py.
    try:
        image = Image.open(icon_path).convert("RGBA")
        image.thumbnail((256, 256), Image.Resampling.LANCZOS)

        icon_image = ImageTk.PhotoImage(image)
        root.iconphoto(True, icon_image)

        # Важно сохранить ссылку, иначе Tkinter может очистить изображение.
        root._app_icon_image = icon_image

        print("Иконка установлена через iconphoto")
    except Exception as error:
        print(f"iconphoto не сработал: {error}")


def patch_frame_as_root(frame: tb.Frame):
    """
    Старые программы ждут root как отдельное окно tb.Window.
    Во вкладке root — это Frame, поэтому добавляем методы окна,
    чтобы внутренний код модулей не ломался.
    """
    frame.title = lambda *args, **kwargs: None
    frame.geometry = lambda *args, **kwargs: None
    frame.minsize = lambda *args, **kwargs: None
    frame.maxsize = lambda *args, **kwargs: None
    frame.resizable = lambda *args, **kwargs: None
    frame.protocol = lambda *args, **kwargs: None
    frame.iconbitmap = lambda *args, **kwargs: None
    frame.state = lambda *args, **kwargs: None
    frame.deiconify = lambda *args, **kwargs: None
    frame.lift = lambda *args, **kwargs: None
    frame.focus_force = lambda *args, **kwargs: None

    return frame


def load_module_from_file(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(
        module_name,
        str(file_path),
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Не удалось создать spec для файла: {file_path}"
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    return module


class CombinedApp:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title(TITLE)
        self.root.geometry("1850x1200")
        self.root.minsize(1200, 760)

        self.style = tb.Style("darkly")

        self.app_folder = get_app_folder()
        self.modules_folder = get_modules_folder()

        # Чтобы модули могли импортировать файлы рядом с собой, если это понадобится.
        modules_folder_str = str(self.modules_folder)
        if modules_folder_str not in sys.path:
            sys.path.insert(0, modules_folder_str)

        self.loaded_apps = []
        self.loaded_modules = []

        self._build_ui()
        self._load_all_tabs()

    def _build_ui(self):
        outer = tb.Frame(self.root, padding=8)
        outer.pack(fill=BOTH, expand=YES)

        top = tb.Frame(outer)
        top.pack(fill=X, pady=(0, 8))

        tb.Label(
            top,
            text="PrintFlow",
            font=("Segoe UI", 14, "bold"),
        ).pack(side=LEFT)

        tb.Label(
            top,
            text=(
                ""
                ""
            ),
            foreground="#c9c9c9",
        ).pack(side=LEFT, padx=(18, 0))

        self.notebook = tb.Notebook(
            outer,
            bootstyle="dark",
        )
        self.notebook.pack(fill=BOTH, expand=YES)

    def _load_all_tabs(self):
        if not self.modules_folder.exists():
            self._create_folder_error_tab()
            return

        for index, program in enumerate(PROGRAMS, start=1):
            self._create_program_tab(index, program)

    def _create_folder_error_tab(self):
        tab = tb.Frame(self.notebook)
        self.notebook.add(tab, text="Ошибка")

        files_list = "\n".join(
            f"- {program['file']}"
            for program in PROGRAMS
        )

        self._show_error_in_tab(
            tab,
            "Папка put не найдена",
            (
                f"Не найдена папка с модулями:\n"
                f"{self.modules_folder}\n\n"
                f"При обычном запуске создай рядом со скриптом папку put.\n"
                f"При сборке EXE добавь папку put через --add-data.\n\n"
                f"Внутри put должны лежать файлы:\n\n{files_list}"
            ),
        )

    def _create_program_tab(self, index: int, program: dict):
        tab_title = program["tab"]

        tab = tb.Frame(self.notebook)
        self.notebook.add(tab, text=tab_title)

        patch_frame_as_root(tab)

        file_path = self.modules_folder / program["file"]

        if not file_path.exists():
            self._show_error_in_tab(
                tab,
                f"{tab_title} — файл не найден",
                (
                    f"Файл не найден:\n{file_path}\n\n"
                    f"Проверь, что в папке put лежит файл:\n"
                    f"{program['file']}"
                ),
            )
            return

        try:
            module_name = f"_combined_program_{index}"
            module = load_module_from_file(module_name, file_path)

            app_class_name = program["class"]

            if not hasattr(module, app_class_name):
                raise RuntimeError(
                    f"В файле {program['file']} "
                    f"не найден класс {app_class_name}"
                )

            app_class = getattr(module, app_class_name)
            app_instance = app_class(tab)

            self.loaded_modules.append(module)
            self.loaded_apps.append(app_instance)

        except Exception:
            error_text = traceback.format_exc()

            self._show_error_in_tab(
                tab,
                f"{tab_title} — ошибка загрузки",
                (
                    f"Не удалось загрузить программу:\n"
                    f"{program['file']}\n\n{error_text}"
                ),
            )

    def _show_error_in_tab(
        self,
        tab: tb.Frame,
        title: str,
        error_text: str,
    ):
        wrapper = tb.Frame(tab, padding=20)
        wrapper.pack(fill=BOTH, expand=YES)

        tb.Label(
            wrapper,
            text=title,
            font=("Segoe UI", 14, "bold"),
            bootstyle=DANGER,
        ).pack(anchor=W, pady=(0, 10))

        box = ScrolledText(wrapper, height=20, autohide=True)
        box.pack(fill=BOTH, expand=YES)

        box.insert("end", error_text)

        try:
            inner_text = getattr(box, "text", box)
            inner_text.configure(state="disabled")
        except Exception:
            pass


def main():
    set_windows_app_id()

    try:
        root = tb.Window(themename="darkly")
    except Exception:
        root = tk.Tk()

    app = CombinedApp(root)

    set_window_icon(root)

    root.mainloop()


if __name__ == "__main__":
    main()