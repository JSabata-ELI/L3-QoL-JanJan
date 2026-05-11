# _internal_builder.py
# Jak buildit?
# py -m PyInstaller --onedir --windowed --name "_internal_builder" --collect-all PIL --collect-all matplotlib --collect-all pyparsing --collect-all cycler --collect-all kiwisolver --collect-all contourpy --collect-all fonttools --collect-all packaging --collect-all python-dateutil --distpath "C:\Dev\dist" --noconfirm "_internal_builder.py"

# _internal_builder.py
# Účel: Buildí se přes PyInstaller a slouží jako zdroj sdílené _internal složky
# pro všechny programy v Image Tools suite.
# Tento soubor se NESPOUŠTÍ — pouze se buildí.

# ── stdlib ───────────────────────────────────────────────────────────────────
import argparse
import atexit
import bisect
import collections
import concurrent.futures
import configparser
import contextlib
from contextlib import contextmanager
import csv
import ctypes
from ctypes import wintypes
import dataclasses
import datetime
from datetime import datetime as _datetime, timedelta, timezone
import json
import math
import os
import pathlib
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import typing
import ssl
import urllib.parse
import urllib.request
import webbrowser
import zipfile
import zoneinfo
from zoneinfo import ZoneInfo
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── tkinter ───────────────────────────────────────────────────────────────────
import tkinter as tk
from tkinter import (
    ttk, messagebox, filedialog, simpledialog, scrolledtext,
    Tk, Toplevel, Button, Label, BooleanVar, Checkbutton,
    StringVar, Frame, Scrollbar, END, BOTH, RIGHT, Y, LEFT, RIDGE
)
import tkinter.font as tkfont

# ── PySide6 ───────────────────────────────────────────────────────────────────
from PySide6.QtCore import (
    Qt, QTimer, QRunnable, QThreadPool, QObject, Signal,
    QSize, QRect, QPointF, QDate, QModelIndex, QAbstractItemModel
)
from PySide6.QtGui import (
    QPixmap, QImageReader, QPainter, QFontMetrics, QImage,
    QColor, QPen, QGuiApplication, QTextCharFormat, QBrush, QFont
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QDoubleSpinBox, QScrollArea, QLabel, QSlider,
    QPushButton, QFileDialog, QMessageBox, QProgressBar, QComboBox,
    QCheckBox, QDialog, QCalendarWidget, QDialogButtonBox,
    QFileSystemModel, QSpinBox, QFrame, QSizePolicy,
    QStyledItemDelegate, QAbstractItemView, QTreeView, QLineEdit,
    QTabWidget, QStatusBar, QTableWidget, QTableWidgetItem,
    QGroupBox, QTextEdit, QInputDialog, QPlainTextEdit, QSplitter,
    QHeaderView, QAbstractScrollArea, QColorDialog
)

# ── screeninfo ────────────────────────────────────────────────────────────────
import screeninfo

# ── dateutil ──────────────────────────────────────────────────────────────────
import dateutil
import dateutil.parser
import dateutil.tz
import dateutil.relativedelta

# ── matplotlib ────────────────────────────────────────────────────────────────
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec
from matplotlib.collections import LineCollection
from matplotlib.ticker import FuncFormatter
from matplotlib.widgets import RectangleSelector

# ── numpy / pandas ────────────────────────────────────────────────────────────
import numpy as np
import pandas as pd

# ── Pillow ────────────────────────────────────────────────────────────────────
from PIL import Image, ImageTk, ImageDraw, ImageFont, ImageGrab
import PIL.Image
import PIL.ImageDraw  
import PIL.ImageFont
import PIL._imaging  # C extension — klíčové

# ── tkcalendar ────────────────────────────────────────────────────────────────
try:
    from tkcalendar import Calendar
except ImportError:
    pass

# ── xlwt ──────────────────────────────────────────────────────────────────────
try:
    import xlwt
except ImportError:
    pass

# ── pyepics ───────────────────────────────────────────────────────────────────
try:
    import epics
except ImportError:
    pass

# ── win32 ─────────────────────────────────────────────────────────────────────
try:
    import win32com.client as win32
    import pythoncom
except ImportError:
    pass

if __name__ == "__main__":
    import subprocess
    script = Path(__file__).resolve()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir", "--windowed",
        "--name", "_internal_builder",
        "--collect-all", "PIL",
        "--collect-all", "matplotlib",
        "--collect-all", "pyparsing",
        "--collect-all", "cycler",
        "--collect-all", "kiwisolver",
        "--collect-all", "contourpy",
        "--collect-all", "fonttools",
        "--collect-all", "packaging",
        "--collect-all", "python-dateutil",
        "--collect-all", "screeninfo",
        "--distpath", r"C:\Dev\dist",
        "--noconfirm",
        str(script),
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, cwd=str(script.parent))