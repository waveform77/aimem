@echo off
setlocal

python -m pip install --upgrade pip
python -m pip install pyinstaller pillow ttkbootstrap reportlab PyMuPDF

python -m PyInstaller --noconfirm --clean --onefile --windowed --name PrintFlow --icon icon.ico --add-data "put;put" --add-data "icon.ico;." --collect-all PIL --collect-all ttkbootstrap --collect-all reportlab --collect-all PyMuPDF --hidden-import fitz PrintFlow.py

endlocal