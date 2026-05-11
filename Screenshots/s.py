# s.py
import os
import sys
import re
import shutil
import socket
import threading
from datetime import datetime, timedelta, timezone
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
try:
    from PIL import Image, ImageTk
    _PIL_OK = True
except Exception:
    _PIL_OK = False
import tkinter.font as tkfont
import time
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
# ---------------- DPI awareness (fix cropped window captures on HiDPI) ----------------
def _set_dpi_awareness():
    try:
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

HEADER_SUFFIX_RE = re.compile(r"-_-IMG$", re.IGNORECASE)

def _norm_suffix_mode(raw: str) -> str:
    raw = raw.strip()
    return re.sub(r"(NF|FF|DF)$", r"_\1", raw)

def parse_cpva_header(header: str) -> tuple[str, str | None]:
    h = header.strip()
    h = HEADER_SUFFIX_RE.sub("", h)

    if h.startswith("C03-"):
        parts = h.split("-")
        if len(parts) >= 3:
            cam_id = parts[1]
            raw = parts[2]
            return _norm_suffix_mode(raw), cam_id

    if h.startswith("L3BT-"):
        rest = h[len("L3BT-"):]
        parts = rest.split("-")
        cam_id = parts[-1] if parts else None
        return rest, cam_id

    if h.startswith("L3-"):
        rest = h[len("L3-"):]
        parts = rest.split("-")
        if len(parts) >= 2:
            cam_id = parts[-1]
            raw = "-".join(parts[:-1])
            return _norm_suffix_mode(raw), cam_id

    return _norm_suffix_mode(h), None

# ---------------- CONFIG ----------------
CAM_INFO = {
    "PFM1_NF": "013",
    "PFM4_NF": "014",
    "PD1M1_DF": "015",
    "PD1M2_NF": "016",
    "PFM10_NF": "017",
    "PFM12_NF": "018",
    "PD2M1_DF": "019",
    "PD2M2_NF": "020",
    "PFM8_FF": "021",
    "PFM8_NF": "022",
    "PD3M1_DF": "023",
    "PD3M2_NF": "024",
    "PFM5_NF": "025",
    "PFM11_NF": "026",
    "PD4M1_DF": "027",
    "PD4M2_NF": "028",
    "PAP1_NF": "029",
    "PAP1_FF": "030",
    "PFM11_FF": "031",
    "PAP1_DF": "032",
    "PAM1_FF": "033",
    "PFM17_NF": "034",
    "PTM11w_NF": "035",
    "PTM11w_FF": "036",
    "PAM5_NF": "037",
    "PAM5_FF": "038",
    "PAM10_NF": "039",
    "PFM13_NF": "040",
    "PASF1_NF": "041",
    "PASF2_NF": "042",
    "PAM11_FF": "043",
    "PAM9_NF": "045",
    "PAM9_FF": "046",
    "PTM52w_NF": "048",
    "PAM12_NF": "049",
    "PAM12_FF": "050",
    "WRT2DP_NF": "051",
    "PTM52w_FF": "052",
    "SFM6_NF": "053",
    "SFM6_FF": "054",
    "SFM2_FF": "055",
    "SAM2_FF": "056",
    "SAW1_NF": "057",
    "SAM4_NF": "058",
    "SAM11BR_NF": "059",
    "SPM10_NF": "060",
    "SAM7A_NF": "061",
    "SAM7A_FF": "062",
    "SPM3_NF": "063",
    "SPW1_NF": "064",
    "SPM3_FF": "065",
    "SPM10_FF": "066",
    "CEO_NF": "067",
    "SBM5_DF": "068",
    "SAM18_NF": "069",
    "SAM18_FF": "070",
    "SBM2_FF": "071",
    "SBM4_FF": "072",
    "SBM6_FF": "073",
    "SBM5_NF": "075",
    "SBM5_FF": "076",
    "PCM4_FF": "077",
    "PCM4_NF": "078",
    "PCM2_NF": "079",
    "PCM2_FF": "080",
    "PCW3_NF": "081",
    "SBW1_NF": "082",
    "PCH2_NF": "083",
    "PCW3_DF": "084",
    "PCC1_DF": "085",
    "SAM13_NF": "089",
    "SAM10_FF": "090",
    "PCC12w_NF": "097",
    "PCC12w_FF": "098",
    "PTM12w_NF": "099",
    "PTM12w_FF": "100",
    "BRF_F1": "394",
    "BRF_F2": "395",
    "BRF_F3": "396",
    "BR_NF": "393",
    "CPTM9_NF": "C305",
    "GR1PAD_FF": "C306",
    "OM1_DF": "C309",
    "OM1_FF": "C310",
    "OM1_NF": "C311",
    "PTM4_FF": "C303",
    "PTM9_FF": "C301",
    "PTM9_NF": "C304",
    "SCG1SDB_FF": "C307",
    "SCG1TDB_FF": "C308",
    "SCG4_DF": "C312",
}

CAM_WINDOW_TITLES: dict[str, str] = {
    "PFM1_NF": "C03-013-PFM1NF",
    "PFM4_NF": "C03-014-PFM4NF",
    "PD1M1_DF": "C03-015-PD1M1DF",
    "PD1M2_NF": "C03-016-PD1M2NF",
    "PFM10_NF": "C03-017-PFM10NF",
    "PFM12_NF": "C03-018-PFM12NF",
    "PD2M1_DF": "C03-019-PD2M1DF",
    "PD2M2_NF": "C03-020-PD2M2NF",
    "PFM8_FF": "C03-021-PFM8FF",
    "PFM8_NF": "C03-022-PFM8NF",
    "PD3M1_DF": "C03-023-PD3M1DF",
    "PD3M2_NF": "C03-024-PD3M2NF",
    "PFM5_NF": "C03-025-PFM5NF",
    "PFM11_NF": "C03-026-PFM11NF",
    "PD4M1_DF": "C03-027-PD4M1DF",
    "PD4M2_NF": "C03-028-PD4M2NF",
    "PAP1_NF": "C03-029-PAP1NF",
    "PAP1_FF": "C03-030-PAP1FF",
    "PFM11_FF": "C03-031-PFM11FF",
    "PAP1_DF": "C03-032-PAP1DF",
    "PAM1_FF": "C03-033-PAM1FF",
    "PFM17_NF": "C03-034-PFM17NF",
    "PTM11w_NF": "C03-035-PTM11wNF",
    "PTM11w_FF": "C03-036-PTM11wFF",
    "PAM5_NF": "C03-037-PAM5NF",
    "PAM5_FF": "C03-038-PAM5FF",
    "PAM10_NF": "C03-039-PAM10NF",
    "PFM13_NF": "C03-040-PFM13NF",
    "PASF1_NF": "C03-041-PASF1NF",
    "PASF2_NF": "C03-042-PASF2NF",
    "PAM11_FF": "C03-043-PAM11FF",
    "PAM9_NF": "C03-045-PAM9NF",
    "PAM9_FF": "C03-046-PAM9FF",
    "PTM52w_NF": "C03-048-PTM52wNF",
    "PAM12_NF": "C03-049-PAM12NF",
    "PAM12_FF": "C03-050-PAM12FF",
    "WRT2DP_NF": "C03-051-WRT2DPNF",
    "PTM52w_FF": "C03-052-PTM52wFF",
    "SFM6_NF": "C03-053-SFM6NF",
    "SFM6_FF": "C03-054-SFM6FF",
    "SFM2_FF": "C03-055-SFM2FF",
    "SAM2_FF": "C03-056-SAM2FF",
    "SAW1_NF": "C03-057-SAW1NF",
    "SAM4_NF": "C03-058-SAM4NF",
    "SAM11BR_NF": "C03-059-SAM11BRNF",
    "SPM10_NF": "C03-060-SPM10NF",
    "SAM7A_NF": "C03-061-SAM7ANF",
    "SAM7A_FF": "C03-062-SAM7AFF",
    "SPM3_NF": "C03-063-SPM3NF",
    "SPW1_NF": "C03-064-SPW1NF",
    "SPM3_FF": "C03-065-SPM3FF",
    "SPM10_FF": "C03-066-SPM10FF",
    "CEO_NF": "C03-067-CEONF",
    "SBM5_DF": "C03-068-SBM5DF",
    "SAM18_NF": "C03-069-SAM18NF",
    "SAM18_FF": "C03-070-SAM18FF",
    "SBM2_FF": "C03-071-SBM2FF",
    "SBM4_FF": "C03-072-SBM4FF",
    "SBM6_FF": "C03-073-SBM6FF",
    "SBM5_NF": "C03-075-SBM5NF",
    "SBM5_FF": "C03-076-SBM5FF",
    "PCM4_FF": "C03-077-PCM4FF",
    "PCM4_NF": "C03-078-PCM4NF",
    "PCM2_NF": "C03-079-PCM2NF",
    "PCM2_FF": "C03-080-PCM2FF",
    "PCW3_NF": "C03-081-PCW3NF",
    "SBW1_NF": "C03-082-SBW1NF",
    "PCH2_NF": "C03-083-PCH2NF",
    "PCW3_DF": "C03-084-PCW3DF",
    "PCC1_DF": "C03-085-PCC1DF",
    "SAM13_NF": "C03-089-SAM13NF",
    "SAM10_FF": "C03-090-SAM10FF",
    "PCC1_2w_NF": "C03-097-PCC12wNF",
    "PCC1_2w_FF": "C03-098-PCC12wFF",
    "PTM1_2w_NF": "C03-099-PTM12wNF",
    "PTM1_2w_FF": "C03-100-PTM12wFF",
    "BR_FF1": "L3-BRFF1-C394",
    "BR_FF2": "L3-BRFF2-C395",
    "BR_FF3": "L3-BRFF3-C396",
    "BR_NF": "L3-BRNF-C393",
    "E2-C05_034": "L3BT-E2-C05-034",
    "E2-C05_036": "L3BT-E2-C05-036",
    "E2-C05_038": "L3BT-E2-C05-038",
    "E2-C533": "L3BT-E2-C533",
    "E2-C534": "L3BT-E2-C534",
    "E2-C535": "L3BT-E2-C535",
    "S1-C514": "L3BT-S1-C514",
    "S3-C511": "L3BT-S3-C511",
    "S4-C506": "L3BT-S4-C506",
    "S4-C507": "L3BT-S4-C507",
    "S4-C508": "L3BT-S4-C508",
    "S4-C509": "L3BT-S4-C509",
    "S4-C516": "L3BT-S4-C516",
    "S4-C517": "L3BT-S4-C517",
    "S4-C518": "L3BT-S4-C518",
    "S4-C519": "L3BT-S4-C519",
    "S5-C526": "L3BT-S5-C526",
    "S5-C528": "L3BT-S5-C528",
    "S5-C530": "L3BT-S5-C530",
    "S5-C532": "L3BT-S5-C532",
    "S6-C528": "L3BT-S6-C528",
    "CPTM9_NF": "L3-CPTM9NF-C305",
    "GR1PAD_FF": "L3-GR1PADFF-C306",
    "OM1_DF": "L3-OM1DF-C309",
    "OM1_FF": "L3-OM1FF-C310",
    "OM1_NF": "L3-OM1NF-C311",
    "PTM4_FF": "L3-PTM4FF-C303",
    "PTM9_FF": "L3-PTM9FF-C301",
    "PTM9_NF": "L3-PTM9NF-C304",
    "SCG1SDB_FF": "L3-SCG1SDBFF-C307",
    "SCG1TDB_FF": "L3-SCG1TDBFF-C308",
    "SCG4_DF": "L3-SCG4DF-C312",
}

NETWORK_ROOT = Path(r"\\users-L3.tier0.lcs.local\cpva-image-2026")

RUN_FOLDER_FMT = "%Y-%m-%d__%H-%M-%S"
TS_FMT = "%Y-%m-%d__%H-%M-%S"

DEFAULT_DEST = r"\\hapls-share.lcs.local\scratch"
DEFAULT_DEST_CZOW = r"C:\Users\jan.moucka\Downloads\TEST"

CAM_CATEGORIES: dict[str, list[str]] = {
    "LT1": [
        "PFM1_NF", "PFM4_NF", "PFM10_NF", "PFM12_NF", "PFM8_FF", "PFM8_NF",
        "PFM5_NF", "PFM11_NF", "PFM11_FF", "PFM13_NF"
    ],
    "LT2": [
        "SFM6_NF", "SFM6_FF", "SFM2_FF", "SAM2_FF", "SAW1_NF", "SAM4_NF", "SPM10_NF", "SAM7A_NF",
        "SAM7A_FF", "SPM3_NF", "SPW1_NF", "SPM3_FF", "SPM10_FF", "CEO_NF"
    ],
    "LT4 + PAD": ["CPTM9_NF", "OM1_DF", "OM1_FF", "OM1_NF", "PTM4_FF", "PTM9_FF", "PTM9_NF", "SCG1SDB_FF"],
    "LT5": [
        "SBM5_DF", "SAM18_NF", "SAM18_FF", "SBM2_FF", "SBM4_FF", "SBM6_FF",
        "SBM5_NF", "SBM5_FF", "SBW1_NF", "PCH2_NF", "PCW3_DF", "SAM10_FF"
    ],
    "LT6": ["PTM5_2w_NF", "PTM5_2w_FF", "PCM4_FF", "PCM4_NF", "PCM2_NF", "PCM2_FF",
            "PCW3_NF", "SAM13_NF"],
    "LT7": [
        "PD1M1_DF", "PD1M2_NF", "PD2M1_DF", "PD2M2_NF", "PD3M1_DF", "PD3M2_NF",
        "PD4M1_DF", "PD4M2_NF", "PAP1_NF", "PAP1_FF", "PAP1_DF", "PAM1_FF",
        "PFM17_NF", "PTM1_1w_NF", "PTM1_1w_FF", "PAM5_NF", "PAM5_FF", "PAM10_NF",
        "PASF1_NF", "PASF2_NF", "PAM11_FF", "PAM9_NF", "PAM9_FF",
        "PAM12_NF", "PAM12_FF", "WRT2DP_NF", "PCC1_DF", "PCC1_2w_NF", "PCC1_2w_FF",
        "PTM1_2w_NF", "PTM1_2w_FF",
    ],
    "Compressor": ["GR1PAD_FF", "SCG1SDB_FF", "SCG1TDB_FF", "SCG4_DF"],
    "L3BT": ["E2-C05-034","E2-C05-036","E2-C05-038","E2-C533","E2-C534",
        "E2-C535","S1-C514","S3-C511","S4-C506","S4-C507","S4-C508","S4-C509","S4-C516",
        "S4-C517","S4-C518","S4-C519","S5-C526","S5-C528","S5-C530","S5-C532","S6-C528"]
}

L3BT_ALIAS_RE = re.compile(r"^L3BT-(.+?)-_-IMG$", re.IGNORECASE)

def add_l3bt_aliases_from_foldernames(folder_names: list[str], cam_aliases: dict[str, str]):
    for fn in folder_names:
        m = L3BT_ALIAS_RE.match(fn.strip())
        if not m:
            continue
        cam_aliases[fn] = m.group(1).strip()

PRESETS: dict[str, dict[str, object]] = {
    "All cameras": {"cams": [cam for cams in CAM_CATEGORIES.values() for cam in cams], "mons": None},
    "LT1": {"cams": CAM_CATEGORIES["LT1"], "mons": None},
    "LT2": {"cams": CAM_CATEGORIES["LT2"], "mons": None},
    "LT4 + PAD": {"cams": CAM_CATEGORIES["LT4 + PAD"], "mons": None},
    "LT5": {"cams": CAM_CATEGORIES["LT5"], "mons": None},
    "LT6": {"cams": CAM_CATEGORIES["LT6"], "mons": None},
    "LT7": {"cams": CAM_CATEGORIES["LT7"], "mons": None},
    "PLFE": {
        "cams": ["PFM1_NF", "PFM4_NF", "PFM5_NF", "PFM8_FF", "PFM8_NF", "PFM10_NF", "PFM11_NF", "PFM11_FF", "PFM12_NF", "PFM13_NF"],
        "mons": [6],
    },
    "PL Crosses": {
        "cams": ["PAP1_NF", "PAP1_DF", "PAM5_NF", "PAM9_NF", "PAM10_NF", "PAM12_NF", "PTM11w_NF"],
        "mons": [6, 4],
    },
    "Diodes": {
        "cams": ["PD1M1_DF", "PD1M2_NF", "PD2M1_DF", "PD2M2_NF", "PD3M1_DF", "PD3M2_NF", "PD4M1_DF", "PD4M2_NF"],
        "mons": [5],
    },
    "Slits + Depol": {
        "cams": ["PASF1_NF", "PASF2_NF", "WRT2DP_NF", "PAM11_FF"],
        "mons": [7, 3],
    },
    "PL - High Power": {
        "cams": ["PD1M1_DF", "PD1M2_NF", "PD2M1_DF", "PD2M2_NF", "PD3M1_DF", "PD3M2_NF", "PD4M1_DF", "PD4M2_NF",
                 "PFM1_NF", "PFM4_NF", "PFM5_NF", "PFM8_NF", "PFM10_NF",
                 "PFM11_NF", "PFM12_NF", "PAP1_NF", "PAP1_DF", "PAM5_NF", "PAM9_NF",
                 "PAM10_NF", "PAM12_NF", "PTM11w_NF", "PAP1_FF", "PAM1_FF", "PAM5_FF", "PAM9_FF", "PAM11_FF", "PAM12_FF",
                 "PTM1_1w_FF", "PTM5_2w_FF", "PTM52w_NF","PTM5_2w_FF","PCM4_FF","PCM4_NF","PCM2_NF",
                 "PCM2_FF","PCW3_NF", "PCC1_2w_NF","PCC1_2w_FF","PTM1_2w_NF","PTM1_2w_FF"],
        "mons": [3, 4, 5, 6, 7],
    },
    "SPFE": {
        "cams": ["SAM18_NF", "SAM13_NF", "OM1_DF", "OM1_FF", "SBM5_NF", "SBM5_FF", "SBM2_FF",
                "SBM4_FF", "SBM6_FF", "SFM2_FF", "SAM7A_NF", "SFM6_NF", "SAW1_NF", "SPW1_NF"],
        "mons": [5, 1, 8],
    },
    "Alpha": {
        "cams": ["SAM18_NF", "SAM13_NF", "OM1_DF", "OM1_FF", "SBM5_NF", "CEO_NF", "SPM3_NF",
                "SFM2_FF", "SAM7A_NF", "SFM6_NF", "SAW1_NF", "SPW1_NF", "SPM10_NF", "SAM4_NF",
                "CPTM9_NF", "OM1_DF", "OM1_FF"],
        "mons": [5, 1, 8, 2],
    },
    "SP - High Power": {
        "cams": ["SAM18_NF", "SAM13_NF", "SBM5_NF", "SBM6_NF", "SBM5_NF", "OM1_DF", "OM1_FF",
                 "SCG4_DF", "BR_NF"],
        "mons": [5, 1, 8, 2],
    }
}

MONITOR_LAYOUTS = {
    "VIS-01": {
        8:(3,2), 4:(0,1), 3:(1,1),
        7:(2,2), 5:(1,0), 2:(0,2),
        1:(1,2), 6:(0,0),
    },
    "VIS-02": {
        7:(3,0), 6:(2,0), 5:(0,0), 8:(1,0),
        1:(0,1), 2:(1,1), 3:(0,2), 4:(1,2),
    },
    "OPR-01": {
        2:(0,0), 3:(0,1), 4:(1,0), 1:(1,1),
    },
    "OPR-02": {
        1:(1,1), 2:(0,1), 3:(1,0), 4:(0,0),
    },
    "OPR-03": {
        1:(0,0), 2:(0,1), 3:(1,1), 4:(1,0),
    },
}

CAM_ALIASES = {
    "PTM1_1w_NF": "PTM11w_NF",
    "PTM1_1w_FF": "PTM11w_FF",
    "PTM1_2w_FF": "PTM12w_FF",
    "PTM1_2w_NF": "PTM12w_NF",
    "PTM5_2w_FF": "PTM52w_FF",
    "PTM5_2w_NF": "PTM52w_NF",
    "PCC1_2w_FF": "PCC12w_FF",
    "PCC1_2w_NF": "PCC12w_NF",
}

L3BT_FOLDERS = [
    "L3BT-E2-C05-034-_-IMG", "L3BT-E2-C05-036-_-IMG", "L3BT-E2-C05-038-_-IMG",
    "L3BT-E2-C533-_-IMG", "L3BT-E2-C534-_-IMG", "L3BT-E2-C535-_-IMG",
    "L3BT-S1-C514-_-IMG", "L3BT-S3-C511-_-IMG",
    "L3BT-S4-C506-_-IMG", "L3BT-S4-C507-_-IMG", "L3BT-S4-C508-_-IMG", "L3BT-S4-C509-_-IMG",
    "L3BT-S4-C516-_-IMG", "L3BT-S4-C517-_-IMG", "L3BT-S4-C518-_-IMG", "L3BT-S4-C519-_-IMG",
    "L3BT-S5-C526-_-IMG", "L3BT-S5-C528-_-IMG", "L3BT-S5-C530-_-IMG", "L3BT-S5-C532-_-IMG",
    "L3BT-S6-C528-_-IMG",
]

add_l3bt_aliases_from_foldernames(L3BT_FOLDERS, CAM_ALIASES)

# ---------------- STATION / PC CONFIG ----------------
def _get_station_id() -> str:
    """Vrátí hostname velkými písmeny, např. 'L3-VIS01'."""
    try:
        return socket.gethostname().upper().strip()
    except Exception:
        return ""

# Kamery dostupné na konkrétních stanicích.
# Klíč = hostname uppercase. Hodnota = set UI názvů kamer.
# Kamery neuvedené pro danou stanici budou zašedlé + tooltip se seznamem stanic kde jsou dostupné.
# Pokud hostname není v mapě vůbec -> žádná kamera není zašedlá (fallback = vše povoleno).
STATION_CAMERAS: dict[str, set[str]] = {
    # ---- VIS01
    "L3-VIS01": {
        # monitor 1: C03-085_PCC1_DF, C03-084_PCW3_DMG, C03-097_PCC1_2w_NF, C03-098_PCC1_2w_FF,
        #            C03-041_PASF1_NF, C03-042_PASF2_NF, C03-099_PTM1_2w_NF, C03-100_PTM1_2w_FF
        "PCC1_DF", "PCW3_DF", "PCC1_2w_NF", "PCC1_2w_FF",
        "PASF1_NF", "PASF2_NF", "PTM1_2w_NF", "PTM1_2w_FF",
        # monitor 3: C03-082_SBW1_NF, C03-081_PCW3_NF
        "SBW1_NF", "PCW3_NF",
        # monitor 4: C03-037_PAM5_NF, C03-039_PAM10_NF, C03-045_PAM9_NF, C03-047_PAM9_DF,
        #            C03-049_PAM12_NF, C03-035_PTM1_1w_NF
        "PAM5_NF", "PAM10_NF", "PAM9_NF", "PAM9_DF", "PAM12_NF", "PTM1_1w_NF",
        # monitor 5: C03-034_PFM17_NF, C03-051_WRT2_DP_NF, C03-079_PCM2_NF, C03-080_PCM2_FF,
        #            C03-040_PFM13_NF, C03-043_PAM11_FF, C03-078_PCM4_NF, C03-077_PCM4_FF
        "PFM17_NF", "WRT2DP_NF", "PCM2_NF", "PCM2_FF",
        "PFM13_NF", "PAM11_FF", "PCM4_NF", "PCM4_FF",
        # monitor 7: C03-015..028 PD diodes
        "PD1M1_DF", "PD2M1_DF", "PD3M1_DF", "PD4M1_DF",
        "PD1M2_NF", "PD2M2_NF", "PD3M2_NF", "PD4M2_NF",
        # monitor 8: C03-032_PAM1_FF, C03-038_PAM5_FF, C03-050_PAM12_FF, C03-048_PTM5_2w_NF,
        #            C03-030_PAP1_FF, C03-046_PAM9_FF, C03-036_PTM1_1w_FF, C03-052_PTM5_2w_FF
        "PAM1_FF", "PAM5_FF", "PAM12_FF", "PTM52w_NF",
        "PAP1_FF", "PAM9_FF", "PTM1_1w_FF", "PTM52w_FF",
    },

    # ---- OPR1: C3xx kamery
    "L3-OPR1": {
        # C307_SCG1SDBFF, C308_SCG1TDBFF, C304_PTM9NF, C301_PTM9FF,
        # C312_SCG4DF, C310_OM1FF, C309_OM1DF, C305_CPTM9NF,
        # C393_BRNF, C311_OM1NF
        "SCG1SDB_FF", "SCG1TDB_FF", "PTM9_NF", "PTM9_FF",
        "SCG4_DF", "OM1_FF", "OM1_DF", "CPTM9_NF",
        "BR_NF", "OM1_NF",
    },

    # ---- OPR2: zatím žádné kamery ----
    "L3-OPR2": set(),

    # ---- OPR3: zatím žádné kamery ----
    "L3-OPR3": set(),

    # ---- VIS02
    # Z obrázku (mnoho monitorů): hlavně SP a SB sekce
    "L3-VIS02": {
        # LT2 / SP area
        "SFM6_NF", "SFM6_FF", "SFM2_FF", "SAM2_FF", "SAW1_NF",
        "SAM4_NF", "SAM11BR_NF", "SPM10_NF", "SAM7A_NF", "SAM7A_FF",
        "SPM3_NF", "SPW1_NF", "SPM3_FF", "SPM10_FF", "CEO_NF",
        # LT5 
        "SBM5_DF", "SAM18_NF", "SAM18_FF", "SBM2_FF", "SBM4_FF",
        "SBM6_FF", "SBM5_NF", "SBM5_FF", "PCH2_NF", "SAM13_NF", "SAM10_FF",
        # LT1
        "PFM1_NF", "PFM4_NF", "PFM10_NF", "PFM12_NF",
        "PFM8_FF", "PFM8_NF", "PFM5_NF", "PFM11_NF", "PFM11_FF",
        # PAP / PAM1 area
        "PAP1_NF", "PAP1_DF", "PAM1_FF",
    },
}

# L3BT kamery jsou dostupné pouze na VIS02 (nebo jen přes archiver, ne window mode)
_L3BT_CAMS: set[str] = set(CAM_CATEGORIES.get("L3BT", []))

def _cam_available_on_stations(cam: str) -> list[str]:
    """Vrátí seznam stanic, kde je daná kamera explicitně přiřazena."""
    result = []
    for s, cams in STATION_CAMERAS.items():
        if cam in cams:
            result.append(s)
    return result  # prázdný seznam = povoleno všem


def _cam_available_here(cam: str, station: str) -> bool:
    if not station:
        return True

    # Zkus i alias (PTM5_2w_NF → PTM52w_NF)
    resolved = CAM_ALIASES.get(cam, cam)

    assigned_to = _cam_available_on_stations(cam)
    if not assigned_to:
        # Zkus resolved alias
        assigned_to = _cam_available_on_stations(resolved)
    if not assigned_to:
        return True  # není nikde explicitně → povolena všem

    return station in assigned_to

# ---------------- konec STATION CONFIG ----------------

IDENTIFY_MS = 2000
MODE_TOKENS = {"NF", "FF", "DF"}
TS_IN_NAME_RE = re.compile(r"_(\d{12,})\.(png|jpg|jpeg|tif|tiff|bmp)$", re.IGNORECASE)


@contextmanager
def timed(log, label: str):
    t0 = time.perf_counter()
    log(f"[TIMER] START {label}")
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        log(f"[TIMER] END   {label}  ({dt:.3f} s)")


def cpva_label(cam_ui: str) -> str:
    return CAM_ALIASES.get(cam_ui, cam_ui)

def is_known_camera(cam_ui: str) -> bool:
    return (
        (cam_ui in CAM_INFO)
        or (cam_ui in CAM_ALIASES)
        or (cpva_label(cam_ui) in CAM_INFO)
        or any(cam_ui in cams for cams in CAM_CATEGORIES.values())
    )


# ---------------- Windows monitor enumeration ----------------
class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def list_monitors_rects() -> list[tuple[int, int, int, int]]:
    user32 = ctypes.windll.user32
    monitors: list[tuple[int, int, int, int]] = []

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(RECT), wintypes.LPARAM,
    )

    def _cb(hMon, hdc, lprc, lparam):
        r = lprc.contents
        monitors.append((r.left, r.top, r.right, r.bottom))
        return True

    cb = MONITORENUMPROC(_cb)
    user32.EnumDisplayMonitors(0, 0, cb, 0)
    return monitors


def take_screenshot_monitor_png(dst_path: Path, monitor_index: int | None):
    try:
        from PIL import ImageGrab
    except Exception as e:
        raise RuntimeError("Pillow missing.\nInstall:\n\n  py -m pip install pillow\n") from e

    img = ImageGrab.grab(all_screens=True)

    if monitor_index is not None:
        rects = list_monitors_rects()
        if not rects:
            raise RuntimeError("Failed to enumerate monitors.")
        if monitor_index < 0 or monitor_index >= len(rects):
            raise RuntimeError(f"Invalid monitor index {monitor_index+1}. Available: 1..{len(rects)}")

        l, t, r, b = rects[monitor_index]
        min_left = min(x[0] for x in rects)
        min_top = min(x[1] for x in rects)
        crop_box = (l - min_left, t - min_top, r - min_left, b - min_top)
        img = img.crop(crop_box)

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst_path, "PNG")


# ---------------- Window capture ----------------
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [ctypes.c_void_p, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int

PW_RENDERFULLCONTENT = 0x00000002
user32.PrintWindow.argtypes = [ctypes.c_void_p, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL

# DWM extended frame bounds — dá přesné viditelné hranice okna bez Win11 shadow
_dwmapi = ctypes.WinDLL("dwmapi")
DWMWA_EXTENDED_FRAME_BOUNDS = 9
_dwmapi.DwmGetWindowAttribute.argtypes = [
    ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(RECT), wintypes.DWORD,
]
_dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

BI_RGB = 0

def _get_window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value

def _norm_win_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

def find_window_by_title_substring(substr: str) -> int | None:
    if not substr:
        return None
    needle = _norm_win_title(substr)
    if not needle:
        return None
    found: list[int] = []

    def _cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return 1
        title = _get_window_text(hwnd)
        if not title:
            return 1
        if needle in _norm_win_title(title):
            found.append(int(hwnd) & 0xFFFFFFFFFFFFFFFF)
            return 0  # stop enum
        return 1

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return found[0] if found else None

def _get_window_bounds(hwnd: int) -> tuple[int, int, int, int]:
    h = ctypes.c_void_p(int(hwnd) & 0xFFFFFFFFFFFFFFFF)
    rect = RECT()
    if not user32.GetWindowRect(h, ctypes.byref(rect)):
        raise RuntimeError("GetWindowRect failed.")
    return rect.left, rect.top, rect.right, rect.bottom

def take_screenshot_window_png(dst_path: Path, hwnd: int):
    try:
        from PIL import Image
    except Exception as e:
        raise RuntimeError("Pillow missing.\nInstall:\n\n  py -m pip install pillow\n") from e

    if not hwnd:
        raise RuntimeError("HWND is 0 (window not found).")

    h_hwnd = ctypes.c_void_p(int(hwnd) & 0xFFFFFFFFFFFFFFFF)

    # GetWindowRect → celé okno včetně Win11 shadow (použijeme pro bitmap)
    win_rect = RECT()
    user32.GetWindowRect(h_hwnd, ctypes.byref(win_rect))
    bmp_w = max(1, win_rect.right  - win_rect.left)
    bmp_h = max(1, win_rect.bottom - win_rect.top)

    # DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) → přesné viditelné hranice
    # bez Win11 průhledného shadow okraje; souřadnice jsou absolutní screen coords
    frame_rect = RECT()
    hr = _dwmapi.DwmGetWindowAttribute(
        h_hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(frame_rect), ctypes.sizeof(RECT),
    )
    if hr == 0:
        # Ořez relativně vůči GetWindowRect (levý horní roh = 0,0 bitmapy)
        crop = (
            frame_rect.left  - win_rect.left,
            frame_rect.top   - win_rect.top,
            frame_rect.right - win_rect.left,
            frame_rect.bottom - win_rect.top,
        )
    else:
        crop = None  # DWM selhal — použijeme celý bitmap

    hdc_window = user32.GetDC(h_hwnd)
    if not hdc_window:
        raise RuntimeError("GetDC failed.")
    try:
        hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
        if not hdc_mem:
            raise RuntimeError("CreateCompatibleDC failed.")
        hbmp = gdi32.CreateCompatibleBitmap(hdc_window, bmp_w, bmp_h)
        if not hbmp:
            gdi32.DeleteDC(hdc_mem)
            raise RuntimeError("CreateCompatibleBitmap failed.")
        old = gdi32.SelectObject(hdc_mem, hbmp)
        try:
            ok = user32.PrintWindow(h_hwnd, hdc_mem, PW_RENDERFULLCONTENT)
            if not ok:
                ok2 = user32.PrintWindow(h_hwnd, hdc_mem, 0)
                if not ok2:
                    raise RuntimeError("PrintWindow failed (window may be protected/offscreen).")
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = bmp_w
            bmi.bmiHeader.biHeight = -bmp_h
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB
            buf = ctypes.create_string_buffer(bmp_w * bmp_h * 4)
            bits = gdi32.GetDIBits(hdc_mem, hbmp, 0, bmp_h, buf, ctypes.byref(bmi), 0)
            if bits == 0:
                raise RuntimeError("GetDIBits failed.")
            img = Image.frombuffer("RGBA", (bmp_w, bmp_h), buf, "raw", "BGRA", 0, 1)
            if crop:
                img = img.crop(crop)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(dst_path, "PNG")
        finally:
            gdi32.SelectObject(hdc_mem, old)
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
    finally:
        user32.ReleaseDC(h_hwnd, hdc_window)


# ---------------- CPVA helpers ----------------
def safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except Exception:
        return 0.0


def today_day_root() -> Path:
    now = datetime.now()
    return NETWORK_ROOT / str(now.year) / str(now.month) / str(now.day)

def target_cpva_hour_dir(_day_root: Path) -> Path:
    now_utc = datetime.now(timezone.utc)
    return NETWORK_ROOT / str(now_utc.year) / str(now_utc.month) / str(now_utc.day) / str(now_utc.hour)

"""def target_cpva_hour_dir(_day_root: Path) -> Path:
    # TESTOVACÍ OVERRIDE — smaž pro produkci
    return NETWORK_ROOT / "2026" / "4" / "7" / "14" """


def norm_folder(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", s.upper())


def tokenize_user(label: str) -> tuple[list[str], list[str], list[str]]:
    parts = re.findall(r"[A-Z0-9]+", label.upper())
    modes = [p for p in parts if p in MODE_TOKENS]
    non_modes = [p for p in parts if p not in MODE_TOKENS]
    numeric = [p for p in non_modes if p.isdigit()]
    glued = []
    for i in range(1, len(parts)):
        if parts[i] in MODE_TOKENS and parts[i - 1] not in MODE_TOKENS and not parts[i - 1].isdigit():
            glued.append(parts[i - 1] + parts[i])
    seen = set()
    required = []
    for t in non_modes + glued:
        if t and t not in seen:
            seen.add(t)
            required.append(t)
    return required, modes, numeric


def contains_token(folder_norm: str, tok: str) -> bool:
    i = folder_norm.find(tok)
    if i < 0:
        return False
    if tok[-1].isdigit():
        j = i + len(tok)
        if j < len(folder_norm) and folder_norm[j].isdigit():
            return False
    return True

def _find_latest_existing_hour_dir(log) -> Path | None:
    """Najde nejnovější existující hour_dir — jde zpět hodinu po hodině, až 48h."""
    now_utc = datetime.now(timezone.utc)
    for hours_back in range(1, 49):
        candidate = now_utc - timedelta(hours=hours_back)
        p = NETWORK_ROOT / str(candidate.year) / str(candidate.month) / str(candidate.day) / str(candidate.hour)
        if p.exists():
            log(f"[CPVA] Found hour {hours_back}h back: {p}")
            return p
    return None

def find_camera_folders_bulk(day_root: Path, cams: list[str], log) -> dict[str, Path]:
    hour_dir = target_cpva_hour_dir(day_root)
    log(f"[CPVA] hour_dir = {hour_dir}")

    if not hour_dir.exists():
        log("[CPVA] hour_dir DOES NOT EXIST — hledám předchozí hodinu...")
        hour_dir = _find_latest_existing_hour_dir(log)
        if hour_dir is None:
            log("[CPVA] No available hour found.")
            return {}
        log(f"[CPVA] Using: {hour_dir}")

    cams_unique = list(dict.fromkeys(cams))
    needs = set(cams_unique)
    found: dict[str, Path] = {}
    checked = 0
    want_label = {cam: cpva_label(cam) for cam in cams_unique}

    try:
        for p in hour_dir.iterdir():
            if not p.is_dir():
                continue
            checked += 1
            folder_label, _folder_id = parse_cpva_header(p.name)
            for cam in list(needs):
                if folder_label == want_label[cam]:
                    found[cam] = p
                    needs.remove(cam)
                    log(f"  ✔ {cam} -> {p.name}")
                    if not needs:
                        log(f"[CPVA] bulk scan checked={checked}, found={len(found)} (EARLY STOP)")
                        return found

        log(f"[CPVA] bulk scan checked={checked}, found={len(found)} (END)")
        return found

    except Exception as e:
        log(f"[CPVA] bulk scan ERROR: {e}")
        return found
    
def find_image_near_click_fast(cam_dir: Path, t_click_ns: int, log=None,
                                _cache: dict | None = None, _cache_time: dict | None = None,
                                _cache_ttl: float = 5.0) -> Path | None:
    t0 = time.perf_counter()
    cam_key = str(cam_dir)
    now = time.time()

    # Použij cache pokud je čerstvá
    entries: list[tuple[int, str]] | None = None
    if (_cache is not None and cam_key in _cache and
            _cache_time is not None and (now - _cache_time.get(cam_key, 0)) < _cache_ttl):
        entries = _cache[cam_key]
        if log:
            log(f"[FAST] cache hit ({len(entries)} entries)")
    
    if entries is None:
        # Načti adresář
        entries = []
        try:
            with os.scandir(cam_dir) as it:
                for entry in it:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    m = TS_IN_NAME_RE.search(entry.name)
                    if not m:
                        continue
                    entries.append((int(m.group(1)), entry.name))
        except Exception as e:
            if log:
                log(f"[FAST] ERROR: {e}")
            return None
        
        if _cache is not None:
            _cache[cam_key] = entries
        if _cache_time is not None:
            _cache_time[cam_key] = now

        dt_scan = time.perf_counter() - t0
        if log:
            log(f"[FAST] scanned {len(entries)} files in {dt_scan:.3f}s")

    if not entries:
        if log:
            log("[FAST] no timestamped images found")
        return None

    best = min(entries, key=lambda x: abs(x[0] - t_click_ns))
    best_ts, best_name = best

    dt = time.perf_counter() - t0
    if log:
        log(f"[FAST] dt={dt:.3f}s delta_ms={abs(best_ts - t_click_ns)/1_000_000:.1f}ms")
        log(f"[FAST] best_name={best_name}")
    return cam_dir / best_name
    
# ---------------- README helpers ----------------
def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

def norm_query(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

def open_in_explorer(path: Path):
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        os.startfile(str(path))
        return
    except Exception:
        pass
    try:
        subprocess.Popen(["explorer", str(path)])
    except Exception:
        raise RuntimeError(f"Cannot open folder in Explorer:\n{path}")


# ---------------- UI helpers ----------------
class CollapsibleSection(ttk.Frame):
    def __init__(self, parent, title: str, expanded: bool = True):
        super().__init__(parent)
        self._expanded = tk.BooleanVar(value=expanded)
        self.header = ttk.Frame(self)
        self.header.pack(fill="x")
        self.btn = ttk.Button(self.header, width=3, command=self.toggle)
        self.btn.pack(side="left")
        self.lbl = ttk.Label(self.header, text=title)
        self.lbl.pack(side="left", padx=(6, 0))
        self.right_slot = ttk.Frame(self.header)
        self.right_slot.pack(side="left", padx=(8, 0))
        self.content = ttk.Frame(self)
        self.content.pack(fill="x", pady=(4, 6))
        self._render()

    def toggle(self):
        self._expanded.set(not self._expanded.get())
        self._render()

    def set_expanded(self, expanded: bool):
        if self._expanded.get() != expanded:
            self._expanded.set(expanded)
            self._render()

    def _render(self):
        if self._expanded.get():
            self.btn.config(text="▼")
            self.content.pack(fill="x", pady=(4, 6))
        else:
            self.btn.config(text="▶")
            self.content.forget()


class ToolTip:
    def __init__(self, widget, text_func, delay_ms: int = 350):
        self.widget = widget
        self.text_func = text_func
        self.delay_ms = delay_ms
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._on_enter, add=True)
        widget.bind("<Leave>", self._on_leave, add=True)
        widget.bind("<Motion>", self._on_motion, add=True)

    def _on_enter(self, _e=None):
        self._schedule()

    def _on_leave(self, _e=None):
        self._cancel()
        self._hide()

    def _on_motion(self, _e=None):
        if self._tip is not None:
            self._position()

    def _schedule(self):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
        self._after_id = None

    def _show(self):
        txt = str(self.text_func() or "").strip()
        if not txt:
            return
        if self._tip is not None:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.overrideredirect(True)
        self._tip.attributes("-topmost", True)
        lbl = ttk.Label(self._tip, text=txt, padding=(8, 5))
        lbl.pack()
        self._position()

    def _position(self):
        if self._tip is None:
            return
        x = self.widget.winfo_pointerx() + 12
        y = self.widget.winfo_pointery() + 16
        self._tip.geometry(f"+{x}+{y}")

    def _hide(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
        self._tip = None

class PreviewWindow(tk.Toplevel):
    """Popup okno s gridem zkopírovaných obrázků."""

    _COLS = 4
    _THUMB = 180  # px thumbnail

    _PALETTES = ["Grayscale", "Gradient", "Hot", "Viridis", "Plasma", "Inferno", "Jet", "Turbo"]

    _LUTS: dict = {}  # lazy-built

    # ── action callbacks ─────────────────────────────────────────
    def _do_keep(self):
        self._save_all_annotated()
        if self._on_keep:
            self._on_keep()
        self.destroy()

    def _save_all_annotated(self):
        if not _PIL_OK:
            return
        for path, state in self._img_states.items():
            try:
                overlay = state.get("overlay")
                has_drawing = False
                if overlay is not None:
                    import numpy as np
                    arr = np.array(overlay)
                    has_drawing = bool(arr[:, :, 3].max() > 0)
                # Rebuild processed base at original resolution
                from PIL import Image as PILImage
                img = PILImage.open(path)
                img_l = img.convert("I") if img.mode in ("I", "I;16") else img.convert("L")
                import numpy as np
                arr_raw = np.array(img_l)
                if arr_raw.dtype != np.uint8:
                    mn, mx = int(arr_raw.min()), int(arr_raw.max())
                    if mx > mn:
                        arr8 = ((arr_raw.astype(np.float32) - mn) / (mx - mn) * 255).astype(np.uint8)
                    else:
                        arr8 = np.zeros(arr_raw.shape, dtype=np.uint8)
                else:
                    arr8 = arr_raw
                if state.get("auto", False):
                    arr8 = self._autostretch(arr8)
                offset = state.get("brightness", 0)
                if offset != 0:
                    arr8 = np.clip(arr8.astype(np.int16) + offset, 0, 255).astype(np.uint8)
                pal = state.get("palette", "Grayscale")
                if pal != "Grayscale":
                    if pal not in self._LUTS:
                        self._build_lut(pal)
                    lut = self._LUTS.get(pal)
                    if lut is not None:
                        base_img = PILImage.fromarray(lut[arr8], "RGB").convert("RGBA")
                    else:
                        base_img = PILImage.fromarray(arr8, "L").convert("RGBA")
                else:
                    base_img = PILImage.fromarray(arr8, "L").convert("RGBA")

                orig_w, orig_h = img.width, img.height
                base_full = base_img.resize((orig_w, orig_h), PILImage.LANCZOS)

                if has_drawing:
                    ov_scaled = overlay.resize((orig_w, orig_h), PILImage.LANCZOS)
                    result_full = PILImage.alpha_composite(base_full, ov_scaled).convert("RGB")
                else:
                    result_full = base_full.convert("RGB")

                # Aplikuj crop pokud existuje
                crop = state.get("crop_rect")
                if crop is not None:
                    x1, y1, x2, y2 = crop
                    x1 = max(0, min(x1, orig_w))
                    y1 = max(0, min(y1, orig_h))
                    x2 = max(0, min(x2, orig_w))
                    y2 = max(0, min(y2, orig_h))
                    if x2 > x1 and y2 > y1:
                        result_full = result_full.crop((x1, y1, x2, y2))

                out_path = path.parent / f"{path.stem}_annotated.png"
                result_full.save(out_path, "PNG")
            except Exception as e:
                print(f"[ANNOTATE] Failed {path.name}: {e}")

    def _do_delete(self):
        if not self._paths:
            self.destroy()
            return
        import tkinter.messagebox as mb
        if not mb.askyesno("Delete", f"Delete {len(self._paths)} file(s)?", parent=self):
            return
        for p in self._paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        if self._on_delete:
            self._on_delete()
        self.destroy()

    def _do_try_again(self):
        if not self._paths:
            self.destroy()
            return
        import tkinter.messagebox as mb
        if not mb.askyesno("Delete and Try Again",
                   f"Delete {len(self._paths)} file(s) and copy again?", parent=self):
            return
        for p in self._paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        self.destroy()
        if self._on_try_again:
            self._on_try_again()

    @classmethod
    def _build_lut(cls, name: str):
        import numpy as np
        def lut(stops):
            a = np.zeros((256, 3), dtype=np.uint8)
            for i in range(256):
                t = i / 255.0
                for j in range(len(stops) - 1):
                    t0, c0 = stops[j]; t1, c1 = stops[j+1]
                    if t0 <= t <= t1:
                        f = (t - t0) / (t1 - t0)
                        a[i] = [int(c0[k] + f*(c1[k]-c0[k])) for k in range(3)]
                        break
            return a
        tables = {
            "Gradient": [(0,(0,0,0)),(0.15,(255,0,0)),(0.30,(255,200,0)),(0.45,(255,255,0)),
                         (0.58,(0,255,0)),(0.68,(0,220,255)),(0.92,(255,255,255)),(1,(255,255,255))],
            "Hot":      [(0,(0,0,0)),(0.33,(255,0,0)),(0.66,(255,255,0)),(1,(255,255,255))],
            "Viridis":  [(0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),(0.75,(94,201,98)),(1,(253,231,37))],
            "Plasma":   [(0,(13,8,135)),(0.25,(126,3,168)),(0.5,(204,71,120)),(0.75,(248,149,64)),(1,(240,249,33))],
            "Inferno":  [(0,(0,0,4)),(0.25,(87,16,110)),(0.5,(188,55,84)),(0.75,(249,142,9)),(1,(252,255,164))],
            "Jet":      [(0,(0,0,128)),(0.125,(0,0,255)),(0.375,(0,255,255)),(0.625,(255,255,0)),(0.875,(255,0,0)),(1,(128,0,0))],
            "Turbo":    [(0,(48,18,59)),(0.2,(70,131,193)),(0.4,(48,210,142)),(0.6,(194,228,59)),(0.8,(244,117,22)),(1,(122,4,3))],
        }
        if name in tables:
            cls._LUTS[name] = lut(tables[name])
        else:
            cls._LUTS[name] = None  # Grayscale = None

    def __init__(self, master, paths: list[Path],
                 on_keep=None, on_delete=None, on_try_again=None):
        super().__init__(master)
        self.title("Preview — copied images")
        self.geometry("900x700")
        self.resizable(True, True)

        if not _PIL_OK:
            ttk.Label(self, text="Pillow not available.").pack(pady=20)
            return

        self._paths = list(paths)
        self._thumbs: list[ImageTk.PhotoImage] = []
        self._brightness = tk.IntVar(value=0)
        self._auto = tk.BooleanVar(value=False)
        self._zoom = tk.DoubleVar(value=1.0)
        self._popup: tk.Toplevel | None = None
        self._on_keep = on_keep
        self._on_delete = on_delete
        self._on_try_again = on_try_again
        # per-image persistent state: path -> {"overlay": PIL RGBA Image | None, "palette": str, "brightness": int, "auto": bool}
        self._img_states: dict[Path, dict] = {}

        # ── action toolbar (Keep / Delete / Try Again) ────────────
        action_bar = ttk.Frame(self, padding=(6, 6))
        action_bar.pack(fill="x")
        ttk.Button(action_bar, text="✔ Save",
                   command=self._do_keep).pack(side="left", padx=(0, 6))
        ttk.Button(action_bar, text="🗑 Delete",
                   command=self._do_delete).pack(side="left", padx=(0, 6))
        ttk.Button(action_bar, text="🔄 Delete and Try Again",
                   command=self._do_try_again).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(2, 0))

        # ── image toolbar ─────────────────────────────────────────
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(fill="x")

        ttk.Label(bar, text="Auto brightness").pack(side="left")
        ttk.Checkbutton(bar, variable=self._auto,
                        command=self._redraw).pack(side="left", padx=(2, 12))

        ttk.Label(bar, text="Brightness:").pack(side="left")
        ttk.Scale(bar, from_=-255, to=255, orient="horizontal",
                  variable=self._brightness, length=180,
                  command=lambda _: self._redraw()).pack(side="left", padx=(4, 12))
        ttk.Button(bar, text="↺", width=3,
                   command=lambda: (self._brightness.set(0), self._redraw())).pack(side="left", padx=(0, 12))

        ttk.Label(bar, text="Zoom:").pack(side="left")
        ttk.Scale(bar, from_=0.3, to=3.0, orient="horizontal",
                  variable=self._zoom, length=120,
                  command=lambda _: self._redraw()).pack(side="left", padx=(4, 12))

        ttk.Label(bar, text="Palette:").pack(side="left")
        self._palette = tk.StringVar(value="Grayscale")
        ttk.Combobox(bar, textvariable=self._palette, values=self._PALETTES,
                     state="readonly", width=10,
                     postcommand=lambda: None).pack(side="left", padx=(4, 0))
        self._palette.trace_add("write", lambda *_: self._redraw())

        # ── scrollable canvas ─────────────────────────────────────
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True)
        xsb = ttk.Scrollbar(wrap, orient="horizontal")
        xsb.pack(side="bottom", fill="x")
        ysb = ttk.Scrollbar(wrap, orient="vertical")
        ysb.pack(side="right", fill="y")
        self._canvas = tk.Canvas(wrap, xscrollcommand=xsb.set,
                                  yscrollcommand=ysb.set, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        xsb.config(command=self._canvas.xview)
        ysb.config(command=self._canvas.yview)

        self._inner = ttk.Frame(self._canvas)
        self._inner_id = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                         lambda _: self._canvas.configure(
                             scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfig(
                              self._inner_id, width=e.width))

        def _on_mousewheel(event):
            self._canvas.yview_scroll(int(-event.delta / 120), "units")

        self._canvas.bind("<MouseWheel>", _on_mousewheel)
        self._inner.bind("<MouseWheel>", _on_mousewheel)
        self.bind_all("<MouseWheel>", _on_mousewheel)

        self._resize_after_id = None
        self.bind("<Configure>", self._on_resize)
        self._redraw()

    # ── image processing ──────────────────────────────────────────
    @staticmethod
    def _autostretch(arr):
        import numpy as np
        lo, hi = np.percentile(arr, [0.1, 99.9])
        if hi <= lo + 2:
            return arr
        return np.clip((arr.astype(np.float32) - lo) / (hi - lo) * 255, 0, 255).astype("uint8")

    def _process(self, path: Path, size: int) -> ImageTk.PhotoImage | None:
        try:
            import numpy as np
            state = self._img_states.get(path)
            img = Image.open(path)
            img.thumbnail((size, size), Image.LANCZOS)

            is_rgb_source = img.mode in ("RGB", "RGBA")
            use_auto = state["auto"] if state else self._auto.get()
            use_brightness = state["brightness"] if state else self._brightness.get()
            use_palette = state["palette"] if state else self._palette.get()

            # RGB obrázky bez explicitní palety → zachovej barvy
            if is_rgb_source and use_palette == "Grayscale" and not use_auto and use_brightness == 0 and not state:
                base_rgb = img.convert("RGB")
                if state and state.get("overlay") is not None:
                    from PIL import Image as PILImage
                    base_rgba = base_rgb.convert("RGBA")
                    ov_thumb = state["overlay"].resize(base_rgba.size, PILImage.LANCZOS)
                    composite = PILImage.alpha_composite(base_rgba, ov_thumb)
                    return ImageTk.PhotoImage(composite.convert("RGB"))
                return ImageTk.PhotoImage(base_rgb)

            # Grayscale pipeline
            img_l = img.convert("I") if img.mode in ("I", "I;16") else img.convert("L")
            arr_raw = np.array(img_l)
            if arr_raw.dtype != np.uint8:
                mn, mx = int(arr_raw.min()), int(arr_raw.max())
                if mx > mn:
                    arr = ((arr_raw.astype(np.float32) - mn) / (mx - mn) * 255).astype(np.uint8)
                else:
                    arr = np.zeros(arr_raw.shape, dtype=np.uint8)
            else:
                arr = arr_raw
            if use_auto:
                arr = self._autostretch(arr)
            if use_brightness != 0:
                arr = np.clip(arr.astype(np.int16) + use_brightness, 0, 255).astype(np.uint8)
            if use_palette != "Grayscale":
                if use_palette not in self._LUTS:
                    self._build_lut(use_palette)
                lut = self._LUTS.get(use_palette)
                if lut is not None:
                    base_rgb = Image.fromarray(lut[arr], "RGB")
                else:
                    base_rgb = Image.fromarray(arr, "L").convert("RGB")
            else:
                base_rgb = Image.fromarray(arr, "L").convert("RGB")

            if state and state.get("overlay") is not None:
                from PIL import Image as PILImage
                overlay = state["overlay"]
                base_rgba = base_rgb.convert("RGBA")
                ov_thumb = overlay.resize(base_rgba.size, PILImage.LANCZOS)
                composite = PILImage.alpha_composite(base_rgba, ov_thumb)
                return ImageTk.PhotoImage(composite.convert("RGB"))
            return ImageTk.PhotoImage(base_rgb)
        except Exception:
            return None

    def _on_resize(self, event=None):
        if hasattr(self, "_last_win_w") and self._last_win_w == self.winfo_width():
            return
        self._last_win_w = self.winfo_width()
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.after(220, self._redraw)

    def _on_hover_enter(self, path: Path):
        self._show_popup(path)

    def _on_hover_leave(self, _event=None):
        self._hide_popup()

    def _on_click(self, path: Path):
        self._hide_popup()
        self._open_zoom_window(path)

    def _open_zoom_window(self, path: Path):
        # Load existing state for this image if any
        existing_state = self._img_states.get(path, {})

        win = tk.Toplevel(self)
        win.title(path.name)
        win.resizable(True, True)

        # ── state ─────────────────────────────────────────────────
        _zoom = tk.DoubleVar(value=1.0)
        _draw_tool = tk.StringVar(value="none")
        _draw_color = tk.StringVar(value="#ff0000")
        _line_width = tk.IntVar(value=2)
        _img_ref = [None]
        _base_pil = [None]
        _overlay_pil = [None]
        _scale = [1.0]
        _drag_start = [None]
        _current_shape_ids = []
        _free_points = [[]]
        _crop_rect = [existing_state.get("crop_rect", None)]  # (x1,y1,x2,y2) v img coords nebo None
        _crop_preview_ids = []  # canvas shape IDs pro crop preview

        # Init overlay from existing state
        if existing_state.get("overlay") is not None:
            _overlay_pil[0] = existing_state["overlay"].copy()

        # ── toolbar ───────────────────────────────────────────────
        bar = ttk.Frame(win, padding=(6, 4))
        bar.pack(fill="x")

        ttk.Label(bar, text="Zoom:").pack(side="left")
        ttk.Scale(bar, from_=0.1, to=5.0, orient="horizontal",
                  variable=_zoom, length=140).pack(side="left", padx=(4, 12))

        ttk.Label(bar, text="Brightness:").pack(side="left")
        _bright_zoom = tk.IntVar(value=existing_state.get("brightness", self._brightness.get()))
        ttk.Scale(bar, from_=-255, to=255, orient="horizontal",
                  variable=_bright_zoom, length=140).pack(side="left", padx=(4, 12))

        ttk.Label(bar, text="Palette:").pack(side="left")
        _pal_zoom = tk.StringVar(value=existing_state.get("palette", self._palette.get()))
        ttk.Combobox(bar, textvariable=_pal_zoom, values=self._PALETTES,
                     state="readonly", width=10).pack(side="left", padx=(4, 12))

        _auto_zoom = tk.BooleanVar(value=existing_state.get("auto", self._auto.get()))
        ttk.Checkbutton(bar, text="Auto", variable=_auto_zoom).pack(side="left", padx=(4, 0))

        # Apply / Revert přímo v toolbaru
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=(16, 8))

        def _apply_changes():
            self._img_states[path] = {
                "overlay": _overlay_pil[0].copy() if _overlay_pil[0] is not None else None,
                "palette": _pal_zoom.get(),
                "brightness": _bright_zoom.get(),
                "auto": _auto_zoom.get(),
            }
            win.destroy()
            self._redraw()

        def _revert_changes():
            self._img_states.pop(path, None)
            win.destroy()
            self._redraw()

        _crop_mode = tk.BooleanVar(value=False)
        ttk.Button(bar, text="✖ Clear crop",
                   command=lambda: _clear_crop()).pack(side="left", padx=(0, 8))

        def _apply_changes():
            self._img_states[path] = {
                "overlay": _overlay_pil[0].copy() if _overlay_pil[0] is not None else None,
                "palette": _pal_zoom.get(),
                "brightness": _bright_zoom.get(),
                "auto": _auto_zoom.get(),
                "crop_rect": _crop_rect[0],
                "zoom": _zoom.get(),
            }
            win.destroy()
            self._redraw()

        def _revert_changes():
            self._img_states.pop(path, None)
            win.destroy()
            self._redraw()

        ttk.Button(bar, text="✔ Apply", command=_apply_changes).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="↺ Revert", command=_revert_changes).pack(side="left")

        ttk.Separator(win, orient="horizontal").pack(fill="x")

        # ── drawing toolbar ───────────────────────────────────────
        draw_bar = ttk.Frame(win, padding=(6, 4))
        draw_bar.pack(fill="x")

        ttk.Label(draw_bar, text="Draw:").pack(side="left")
        for tool_name, tool_val in [("None","none"),("Rect","rect"),
                                     ("Circle","circle"),("Cross","cross"),
                                     ("Free","free"),("🔍 Zoom","zoom")]:
            ttk.Radiobutton(draw_bar, text=tool_name, variable=_draw_tool,
                            value=tool_val).pack(side="left", padx=(4,0))

        ttk.Label(draw_bar, text="  Width:").pack(side="left")
        ttk.Spinbox(draw_bar, from_=1, to=20, textvariable=_line_width,
                    width=4).pack(side="left", padx=(4, 0))

        _color_btn = tk.Button(draw_bar, text="  Color  ", bg=_draw_color.get(),
                               fg="white", relief="raised", bd=2)
        _color_btn.pack(side="left", padx=(10, 4))

        def _pick_color():
            from tkinter.colorchooser import askcolor
            result = askcolor(color=_draw_color.get(), parent=win, title="Pick draw color")
            if result and result[1]:
                _draw_color.set(result[1])
                _color_btn.configure(bg=result[1])

        _color_btn.configure(command=_pick_color)

        ttk.Button(draw_bar, text="Undo",
                   command=lambda: _undo()).pack(side="left", padx=(10, 0))
        ttk.Button(draw_bar, text="Clear drawing",
                   command=lambda: _clear_overlay()).pack(side="left", padx=(4, 0))

        ttk.Separator(win, orient="horizontal").pack(fill="x")

        # ── canvas ────────────────────────────────────────────────
        canvas_frame = ttk.Frame(win)
        canvas_frame.pack(fill="both", expand=True)
        xsb = ttk.Scrollbar(canvas_frame, orient="horizontal")
        xsb.pack(side="bottom", fill="x")
        ysb = ttk.Scrollbar(canvas_frame, orient="vertical")
        ysb.pack(side="right", fill="y")
        canvas = tk.Canvas(canvas_frame, highlightthickness=0,
                           xscrollcommand=xsb.set, yscrollcommand=ysb.set)
        canvas.pack(fill="both", expand=True)
        xsb.config(command=canvas.xview)
        ysb.config(command=canvas.yview)

        # ── overlay management ────────────────────────────────────
        _undo_stack = []

        def _new_overlay(w, h):
            from PIL import Image as PILImage
            _overlay_pil[0] = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))

        def _push_undo():
            if _overlay_pil[0] is not None:
                _undo_stack.append(_overlay_pil[0].copy())
                if len(_undo_stack) > 30:
                    _undo_stack.pop(0)

        def _undo():
            if _undo_stack:
                _overlay_pil[0] = _undo_stack.pop()
                _render()

        def _clear_overlay():
            if _overlay_pil[0] is not None:
                _push_undo()
                from PIL import Image as PILImage
                _overlay_pil[0] = PILImage.new("RGBA", _overlay_pil[0].size, (0, 0, 0, 0))
                _render()

        # ── render ────────────────────────────────────────────────
        def _render(*_):
            try:
                import numpy as np
                from PIL import Image as PILImage
                z = _zoom.get()
                img = PILImage.open(path)
                w = max(1, int(img.width * z))
                h = max(1, int(img.height * z))
                img_r = img.resize((w, h), PILImage.LANCZOS)

                is_rgb = img_r.mode in ("RGB", "RGBA")
                pal = _pal_zoom.get()
                use_auto = _auto_zoom.get()
                offset = _bright_zoom.get()

                if is_rgb and pal == "Grayscale" and not use_auto and offset == 0:
                    base_img = img_r.convert("RGBA")
                else:
                    img_l = img_r.convert("I") if img_r.mode in ("I", "I;16") else img_r.convert("L")
                    arr_raw = np.array(img_l)
                    if arr_raw.dtype != np.uint8:
                        mn, mx = int(arr_raw.min()), int(arr_raw.max())
                        if mx > mn:
                            arr = ((arr_raw.astype(np.float32)-mn)/(mx-mn)*255).astype(np.uint8)
                        else:
                            arr = np.zeros(arr_raw.shape, dtype=np.uint8)
                    else:
                        arr = arr_raw
                    if use_auto:
                        arr = self._autostretch(arr)
                    if offset != 0:
                        arr = np.clip(arr.astype(np.int16)+offset, 0, 255).astype("uint8")
                    if pal != "Grayscale":
                        if pal not in self._LUTS:
                            self._build_lut(pal)
                        lut = self._LUTS.get(pal)
                        if lut is not None:
                            base_img = PILImage.fromarray(lut[arr], "RGB").convert("RGBA")
                        else:
                            base_img = PILImage.fromarray(arr, "L").convert("RGBA")
                    else:
                        base_img = PILImage.fromarray(arr, "L").convert("RGBA")

                _base_pil[0] = base_img
                _scale[0] = z

                if _overlay_pil[0] is None or _overlay_pil[0].size != (w, h):
                    _new_overlay(w, h)

                composite = PILImage.alpha_composite(base_img, _overlay_pil[0])
                pm = ImageTk.PhotoImage(composite.convert("RGB"))
                _img_ref[0] = pm
                canvas.delete("all")
                canvas.create_image(0, 0, anchor="nw", image=pm, tags="img")
                canvas.configure(scrollregion=(0, 0, w, h))
            except Exception as ex:
                print(f"[ZOOM RENDER] {ex}")

        _zoom.trace_add("write", _render)
        _bright_zoom.trace_add("write", _render)
        _pal_zoom.trace_add("write", _render)
        _auto_zoom.trace_add("write", _render)

        # Set window size to fit image after first render
        DEFAULT_WIN_W = 900
        DEFAULT_WIN_H = 600
        TOOLBAR_H = 115  # toolbar + draw_bar + separátory

        def _fit_window():
            try:
                from PIL import Image as PILImage
                img_info = PILImage.open(path)
                sw = self.winfo_screenwidth()
                sh = self.winfo_screenheight()
                target_w = min(max(img_info.width + 20, DEFAULT_WIN_W), int(sw * 0.85))
                target_h = min(max(img_info.height + TOOLBAR_H + 40, DEFAULT_WIN_H), int(sh * 0.85))
                win.minsize(DEFAULT_WIN_W, DEFAULT_WIN_H)
                win.geometry(f"{target_w}x{target_h}")
            except Exception:
                win.minsize(DEFAULT_WIN_W, DEFAULT_WIN_H)
                win.geometry(f"{DEFAULT_WIN_W}x{DEFAULT_WIN_H}")
            _render()

        win.after(10, _fit_window)

        # ── drawing ───────────────────────────────────────────────
        def _canvas_to_img(cx, cy):
            return int(canvas.canvasx(cx)), int(canvas.canvasy(cy))

        def _hex_to_rgba(hex_color: str, alpha=200):
            h = hex_color.lstrip("#")
            return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16), alpha)

        def _clear_crop():
            _crop_rect[0] = None
            for sid in _crop_preview_ids:
                try:
                    canvas.delete(sid)
                except Exception:
                    pass
            _crop_preview_ids.clear()

        # Crop — pravé tlačítko
        _crop_drag_start = [None]

        def _on_right_press(event):
            _crop_drag_start[0] = _canvas_to_img(event.x, event.y)
            for sid in _crop_preview_ids:
                try:
                    canvas.delete(sid)
                except Exception:
                    pass
            _crop_preview_ids.clear()

        def _on_right_drag(event):
            if _crop_drag_start[0] is None:
                return
            ix, iy = _canvas_to_img(event.x, event.y)
            sx, sy = _crop_drag_start[0]
            for sid in _crop_preview_ids:
                try:
                    canvas.delete(sid)
                except Exception:
                    pass
            _crop_preview_ids.clear()
            _crop_preview_ids.append(
                canvas.create_rectangle(sx, sy, ix, iy,
                                        outline="#00ff00", width=2,
                                        dash=(6, 3), tags="crop_preview"))

        def _on_right_release(event):
            if _crop_drag_start[0] is None:
                return
            ix, iy = _canvas_to_img(event.x, event.y)
            sx, sy = _crop_drag_start[0]
            z = _zoom.get()
            # Převeď z zoom coords na originální coords
            x1 = int(min(sx, ix) / z)
            y1 = int(min(sy, iy) / z)
            x2 = int(max(sx, ix) / z)
            y2 = int(max(sy, iy) / z)
            if x2 > x1 and y2 > y1:
                _crop_rect[0] = (x1, y1, x2, y2)
            _crop_drag_start[0] = None

        canvas.bind("<ButtonPress-3>", _on_right_press)
        canvas.bind("<B3-Motion>", _on_right_drag)
        canvas.bind("<ButtonRelease-3>", _on_right_release)

        # Kreslení — levé tlačítko
        def _on_button_press(event):
            tool = _draw_tool.get()
            if tool == "none":
                return
            ix, iy = _canvas_to_img(event.x, event.y)
            _drag_start[0] = (ix, iy)
            for sid in _current_shape_ids:
                try:
                    canvas.delete(sid)
                except Exception:
                    pass
            _current_shape_ids.clear()
            if tool == "zoom":
                # zoom tool nepoužívá overlay undo
                for sid in _crop_preview_ids:
                    try:
                        canvas.delete(sid)
                    except Exception:
                        pass
                _crop_preview_ids.clear()
                return
            _push_undo()
            if tool == "free":
                _free_points[0] = [(ix, iy)]

        def _on_drag(event):
            tool = _draw_tool.get()
            if tool == "none" or _drag_start[0] is None:
                return
            ix, iy = _canvas_to_img(event.x, event.y)
            sx, sy = _drag_start[0]

            if tool == "free":
                _free_points[0].append((ix, iy))
                _draw_free_line_to_overlay(ix, iy)
                _render_overlay_only()
                return

            if tool == "zoom":
                for sid in _crop_preview_ids:
                    try:
                        canvas.delete(sid)
                    except Exception:
                        pass
                _crop_preview_ids.clear()
                _crop_preview_ids.append(
                    canvas.create_rectangle(sx, sy, ix, iy,
                                            outline="#ffff00", width=2,
                                            dash=(6, 3), tags="zoom_preview"))
                return

            for sid in _current_shape_ids:
                try:
                    canvas.delete(sid)
                except Exception:
                    pass
            _current_shape_ids.clear()

            color = _draw_color.get()
            lw = _line_width.get()
            if tool == "rect":
                _current_shape_ids.append(
                    canvas.create_rectangle(sx, sy, ix, iy, outline=color, width=lw, tags="preview"))
            elif tool == "circle":
                _current_shape_ids.append(
                    canvas.create_oval(sx, sy, ix, iy, outline=color, width=lw, tags="preview"))
            elif tool == "cross":
                cx2 = (sx+ix)//2; cy2 = (sy+iy)//2
                half = max(abs(ix-sx), abs(iy-sy))//2
                _current_shape_ids.append(
                    canvas.create_line(cx2-half, cy2, cx2+half, cy2, fill=color, width=lw, tags="preview"))
                _current_shape_ids.append(
                    canvas.create_line(cx2, cy2-half, cx2, cy2+half, fill=color, width=lw, tags="preview"))

        def _draw_free_line_to_overlay(ix, iy):
            from PIL import ImageDraw
            if _overlay_pil[0] is None:
                return
            pts = _free_points[0]
            if len(pts) < 2:
                return
            draw = ImageDraw.Draw(_overlay_pil[0])
            draw.line(pts[-2:], fill=_hex_to_rgba(_draw_color.get()), width=_line_width.get())

        def _render_overlay_only():
            if _base_pil[0] is None or _overlay_pil[0] is None:
                return
            try:
                from PIL import Image as PILImage
                composite = PILImage.alpha_composite(_base_pil[0], _overlay_pil[0])
                pm = ImageTk.PhotoImage(composite.convert("RGB"))
                _img_ref[0] = pm
                canvas.delete("preview")
                canvas.delete("img")
                canvas.create_image(0, 0, anchor="nw", image=pm, tags="img")
            except Exception:
                pass

        def _on_button_release(event):
            tool = _draw_tool.get()
            if tool == "none" or _drag_start[0] is None:
                _drag_start[0] = None
                return
            ix, iy = _canvas_to_img(event.x, event.y)
            sx, sy = _drag_start[0]

            # Clear preview shapes
            for sid in _current_shape_ids:
                try:
                    canvas.delete(sid)
                except Exception:
                    pass
            _current_shape_ids.clear()

            if tool == "zoom":
                for sid in _crop_preview_ids:
                    try:
                        canvas.delete(sid)
                    except Exception:
                        pass
                _crop_preview_ids.clear()
                z = _zoom.get()
                # Souřadnice výběru v originálním obrázku
                x1 = int(min(sx, ix) / z)
                y1 = int(min(sy, iy) / z)
                x2 = int(max(sx, ix) / z)
                y2 = int(max(sy, iy) / z)
                if x2 > x1 and y2 > y1:
                    _crop_rect[0] = (x1, y1, x2, y2)
                    # Vypočítej nový zoom aby výběr vyplnil canvas
                    try:
                        cw = canvas.winfo_width()
                        ch = canvas.winfo_height()
                        sel_w = x2 - x1
                        sel_h = y2 - y1
                        new_z = min(cw / sel_w, ch / sel_h) * 0.95
                        new_z = max(0.1, min(new_z, 5.0))
                        _zoom.set(round(new_z, 2))
                        # Po re-renderu scrolluj na výběr
                        def _scroll_to_sel(nz=new_z, nx1=x1, ny1=y1):
                            canvas.update_idletasks()
                            total_w = canvas.winfo_width()
                            total_h = canvas.winfo_height()
                            img_w = max(1, int(canvas.winfo_reqwidth()))
                            # Scrolluj na střed výběru
                            cx = nx1 * nz + (x2 - nx1) * nz / 2
                            cy = ny1 * nz + (y2 - ny1) * nz / 2
                            sr = canvas.cget("scrollregion")
                            if sr:
                                parts = str(sr).split()
                                if len(parts) == 4:
                                    sw = float(parts[2])
                                    sh = float(parts[3])
                                    if sw > 0:
                                        canvas.xview_moveto(max(0, (cx - total_w/2) / sw))
                                    if sh > 0:
                                        canvas.yview_moveto(max(0, (cy - total_h/2) / sh))
                        canvas.after(80, _scroll_to_sel)
                    except Exception:
                        pass
                _drag_start[0] = None
                return

            if tool == "free":
                _free_points[0] = []
                _drag_start[0] = None
                return

            from PIL import ImageDraw
            draw = ImageDraw.Draw(_overlay_pil[0])
            color = _hex_to_rgba(_draw_color.get())
            lw = _line_width.get()

            if tool == "rect":
                draw.rectangle([sx, sy, ix, iy], outline=color, width=lw)
            elif tool == "circle":
                draw.ellipse([sx, sy, ix, iy], outline=color, width=lw)
            elif tool == "cross":
                cx2 = (sx+ix)//2; cy2 = (sy+iy)//2
                half = max(abs(ix-sx), abs(iy-sy))//2
                draw.line([(cx2-half, cy2),(cx2+half, cy2)], fill=color, width=lw)
                draw.line([(cx2, cy2-half),(cx2, cy2+half)], fill=color, width=lw)

            _drag_start[0] = None
            _render_overlay_only()

        canvas.bind("<ButtonPress-1>", _on_button_press)
        canvas.bind("<B1-Motion>", _on_drag)
        canvas.bind("<ButtonRelease-1>", _on_button_release)

        def _wheel(event):
            canvas.yview_scroll(int(-event.delta / 120), "units")
        canvas.bind("<MouseWheel>", _wheel)

    # ── grid draw ─────────────────────────────────────────────────
    def _redraw(self):
        for w in self._inner.winfo_children():
            w.destroy()
        self._thumbs.clear()

        # Dynamická šířka podle šířky okna
        win_w = self.winfo_width()
        if win_w < 100:
            win_w = 900
        pad = 24  # padding kolem každého obrázku (2x padx=4 + border)
        min_thumb = 80
        max_cols = self._COLS  # max 4 sloupce
        # Zjisti kolik sloupců se vejde při aktuálním zoom
        zoom_size = max(min_thumb, int(self._THUMB * self._zoom.get()))
        # Kolik sloupců se vejde do šířky okna
        cols = min(max_cols, max(1, win_w // (zoom_size + pad)))
        # Přepočítej velikost thumbnailu aby vyplnil šířku
        size = max(min_thumb, (win_w - pad * cols - 20) // cols)

        placed = 0
        for col_idx in range(cols):
            self._inner.columnconfigure(col_idx, weight=1, uniform="col")
        for i, path in enumerate(self._paths):
            pm = self._process(path, size)
            if pm is None:
                continue
            self._thumbs.append(pm)
            r, c = divmod(placed, cols)
            placed += 1

            cell = ttk.Frame(self._inner, padding=4)
            cell.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")

            lbl = tk.Label(cell, image=pm, cursor="hand2",
                           relief="flat", bd=1)
            lbl.pack(fill="both", expand=True)
            lbl.bind("<Enter>", lambda e, p=path: self._on_hover_enter(p))
            lbl.bind("<Leave>", lambda e, p=path: self._on_hover_leave(e))
            lbl.bind("<Button-1>", lambda e, p=path: self._on_click(p))

            name = path.name
            if len(name) > 22:
                name = name[:10] + "…" + name[-10:]
            ttk.Label(cell, text=name, font=("Segoe UI", 7),
                      foreground="gray").pack()

    # ── large preview popup ───────────────────────────────────────
    def _show_popup(self, path: Path):
        self._hide_popup()
        try:
            import numpy as np
            state = self._img_states.get(path)
            img = Image.open(path)
            zoom_size = max(80, int(self._THUMB * self._zoom.get()))
            popup_size = zoom_size * 4
            img.thumbnail((popup_size, popup_size), Image.LANCZOS)

            is_rgb_source = img.mode in ("RGB", "RGBA")
            use_auto = state["auto"] if state else self._auto.get()
            use_brightness = state["brightness"] if state else self._brightness.get()
            use_palette = state["palette"] if state else self._palette.get()

            if is_rgb_source and use_palette == "Grayscale" and not use_auto and use_brightness == 0 and not state:
                pm = ImageTk.PhotoImage(img.convert("RGB"))
            else:
                img_l = img.convert("I") if img.mode in ("I", "I;16") else img.convert("L")
                arr_raw = np.array(img_l)
                if arr_raw.dtype != np.uint8:
                    mn, mx = arr_raw.min(), arr_raw.max()
                    if mx > mn:
                        arr = ((arr_raw.astype(np.float32) - mn) / (mx - mn) * 255).astype(np.uint8)
                    else:
                        arr = np.zeros_like(arr_raw, dtype=np.uint8)
                else:
                    arr = arr_raw
                if use_auto:
                    arr = self._autostretch(arr)
                if use_brightness != 0:
                    arr = np.clip(arr.astype(np.int16) + use_brightness, 0, 255).astype(np.uint8)
                if use_palette != "Grayscale":
                    if use_palette not in self._LUTS:
                        self._build_lut(use_palette)
                    lut = self._LUTS.get(use_palette)
                    if lut is not None:
                        pm = ImageTk.PhotoImage(Image.fromarray(lut[arr], "RGB"))
                    else:
                        pm = ImageTk.PhotoImage(Image.fromarray(arr, "L"))
                else:
                    pm = ImageTk.PhotoImage(Image.fromarray(arr, "L"))
        except Exception:
            return

        self._popup = tk.Toplevel(self)
        self._popup.overrideredirect(True)
        self._popup.attributes("-topmost", True)
        lbl = tk.Label(self._popup, image=pm, bd=2, relief="solid")
        lbl.image = pm
        lbl.pack()
        x = self.winfo_pointerx() + 16
        y = self.winfo_pointery() + 16
        self._popup.geometry(f"+{x}+{y}")

    def _hide_popup(self):
        if self._popup is not None:
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None

# ---------------- App ----------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        if getattr(sys, "frozen", False):
            self.title(Path(sys.executable).stem)
        else:
            self.title("Screenshots")
        try:
            self.iconbitmap(str(get_app_dir() / "icon.ico"))
        except Exception:
            pass
        self.geometry("1440x800")
        self.minsize(800, 450)

        # Detect current station — musí být před dest_dir
        self._station_id = _get_station_id()
        print(f"[STATION] hostname='{self._station_id}'", flush=True)

        _default_dest = DEFAULT_DEST_CZOW if self._station_id == "CZOW-NB2DLRL24" else DEFAULT_DEST
        self.dest_dir = tk.StringVar(value=_default_dest)
        self._last_browse_dir = str(Path.home())

        self.run_name_var = tk.StringVar(value="")
        self._name_label_var = tk.StringVar(value="Folder name:")
        self.cam_search_var = tk.StringVar(value="")
        self._busy = False
        self._last_out_dir: Path | None = None
        self._auto_copy_active = False
        self._auto_copy_after_id = None
        self._auto_copy_count = 0
        self._auto_total_steps = 0   # celkový počet kroků přes všechny cykly (roste dynamicky)
        self._auto_copy_params: dict | None = None

        # Live mód
        self._live_active = False
        self._live_thread: threading.Thread | None = None
        self._live_collected_files: list[Path] = []   # soubory zachycené v live session
        self._live_lock = threading.Lock()
        self._auto_cycle_history: list[dict] = []
        self._auto_preview_win = None
        self._preview_enabled = tk.BooleanVar(value=True)
        self._notes_name_var = tk.StringVar(value="notes")
        self._label_settings: dict[str, dict] = {}
        self._cam_dir_cache: dict[str, list[tuple[int, str]]] = {}  # cam_dir -> [(ts_ns, name)]
        self._cam_dir_cache_time: dict[str, float] = {}  # cam_dir -> time.time()
        self._cam_dir_cache_ttl: float = 5.0  # sekund
        self._cam_dir_cache_lock = threading.Lock()
        self._start_cache_refresh_worker()
        self.source_mode = tk.StringVar(value="cpva")

        self._monitors = list_monitors_rects()[:8]
        self.monitor_vars: list[tk.BooleanVar] = [tk.BooleanVar(value=False) for _ in self._monitors]
        self.all_screens_var = tk.BooleanVar(value=True)

        # NOVÉ
        _station_layout_map = {
            "L3-VIS01": "VIS-01",
            "L3-VIS02": "VIS-02",
            "L3-OPR1":  "OPR-01",
            "L3-OPR2":  "OPR-02",
            "L3-OPR3":  "OPR-03",
        }
        _default_layout = _station_layout_map.get(self._station_id, "VIS-01")
        self.monitor_layout_var = tk.StringVar(value=_default_layout)
        self._layout_grid = None

        self._manual_cams: set[str] = set()
        self._manual_all_screens: bool = True
        self._manual_monitors: set[int] = set()

        self._active_presets: set[str] = set()
        self._preset_mon_ref: dict[int, int] = {}
        self._preset_all_screens_ref: int = 0

        self.camera_vars: dict[str, tk.BooleanVar] = {}
        for _cat, cams in CAM_CATEGORIES.items():
            for cam in cams:
                self.camera_vars.setdefault(cam, tk.BooleanVar(value=False))

        self.cat_all_vars: dict[str, tk.BooleanVar] = {cat: tk.BooleanVar(value=False) for cat in CAM_CATEGORIES}
        self._updating_cat = False

        self.cam_to_cat: dict[str, str] = {}
        for cat, cams in CAM_CATEGORIES.items():
            for cam in cams:
                self.cam_to_cat[cam] = cat

        self.preset_buttons: dict[str, ttk.Button] = {}

        style = ttk.Style(self)
        style.configure("Preset.TButton", padding=(3, 1), font=("Segoe UI", 9))
        style.configure("PresetOn.TButton", padding=(3, 1), font=("Segoe UI", 9, "bold"))
        style.layout("Preset.TButton", style.layout("TButton"))
        style.layout("PresetOn.TButton", style.layout("TButton"))

        self._cam_sections: dict[str, CollapsibleSection] = {}
        self._cam_grids: dict[str, ttk.Frame] = {}
        self._cam_cols = 4

        self._programmatic_cam_update = False

        # Progress bar vars
        self._progress_var = tk.IntVar(value=0)
        self._progress_label_var = tk.StringVar(value="")

        for cam, var in self.camera_vars.items():
            var.trace_add("write", lambda *_args, cam_name=cam: self._on_cam_var_changed(cam_name))

        self._update_name_label()
        self._build_ui()

        for cat in CAM_CATEGORIES:
            self._update_category_check(cat)

        self._apply_monitor_effective()

        # Show station info in title
        if self._station_id:
            self.title(f"Screenshots  —  {self._station_id}")

    # ---- Progress bar helpers ----
    def _start_cache_refresh_worker(self):
            def _worker():
                while True:
                    time.sleep(3)
                    try:
                        folders = list(self._cam_dir_cache.keys())
                        for cam_key in folders:
                            try:
                                entries = []
                                with os.scandir(cam_key) as it:
                                    for entry in it:
                                        if not entry.is_file(follow_symlinks=False):
                                            continue
                                        m = TS_IN_NAME_RE.search(entry.name)
                                        if not m:
                                            continue
                                        entries.append((int(m.group(1)), entry.name))
                                with self._cam_dir_cache_lock:
                                    self._cam_dir_cache[cam_key] = entries
                                    self._cam_dir_cache_time[cam_key] = time.time()
                            except Exception:
                                pass
                    except Exception:
                        pass
            threading.Thread(target=_worker, daemon=True).start()

    def on_detail(self):
        out_dir = getattr(self, "_last_out_dir", None)
        if out_dir is None:
            s = self.dest_dir.get().strip()
            if not s:
                messagebox.showwarning("Detail", "Set destination folder first.")
                return
            out_dir = Path(s)

        win = tk.Toplevel(self)
        win.title("Detail notes")
        win.geometry("520x580")
        win.resizable(True, True)

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)

        name_row = ttk.Frame(frm)
        name_row.pack(fill="x", pady=(0, 4))
        ttk.Label(name_row, text="File name:").pack(side="left")
        ttk.Entry(name_row, textvariable=self._notes_name_var, width=20).pack(side="left", padx=(8, 0))
        ttk.Label(name_row, text=".txt", foreground="gray").pack(side="left")

        ttk.Label(frm, text=str(out_dir), foreground="gray", anchor="w").pack(fill="x", pady=(0, 6))

        txt = tk.Text(frm, wrap="word")
        txt.pack(fill="both", expand=True)
        txt.focus_set()

        _out_dir = out_dir

        def _get_dst() -> Path:
            fname = self.sanitize_folder_name(self._notes_name_var.get()) or "notes"
            return _out_dir / f"{fname}.txt"

        def _try_load():
            def _worker():
                try:
                    dst = _get_dst()
                    if dst.exists():
                        content = dst.read_text(encoding="utf-8")
                        win.after(0, lambda: (txt.delete("1.0", "end"), txt.insert("1.0", content)))
                    else:
                        win.after(0, lambda: txt.delete("1.0", "end"))
                except Exception:
                    pass
            threading.Thread(target=_worker, daemon=True).start()

        self._notes_name_var.trace_add("write", lambda *_: _try_load())
        threading.Thread(target=lambda: _try_load(), daemon=True).start()

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x", pady=(8, 0))

        def _save():
            content = txt.get("1.0", "end-1c")
            dst = _get_dst()
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(content, encoding="utf-8")
                self.log(f"[DETAIL] Saved -> {dst.name}")
            except Exception as e:
                messagebox.showerror("Save error", str(e))

        def _delete():
            dst = _get_dst()
            try:
                if not dst.exists():
                    messagebox.showinfo("Delete", "File does not exist.")
                    return
            except Exception:
                messagebox.showinfo("Delete", "File does not exist.")
                return
            if messagebox.askyesno("Delete", f"Delete {dst.name}?"):
                try:
                    dst.unlink()
                    txt.delete("1.0", "end")
                    self.log(f"[DETAIL] Deleted -> {dst.name}")
                except Exception as e:
                    messagebox.showerror("Delete error", str(e))

        ttk.Button(btn_row, text="Save", command=_save).pack(side="right")
        ttk.Button(btn_row, text="Delete file", command=_delete).pack(side="right", padx=(0, 8))
        ttk.Button(btn_row, text="Close", command=win.destroy).pack(side="left")

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for btn_text in ["Copy"]:
            for child in self.winfo_children():
                self._set_state_recursive(child, btn_text, state)

    def _set_state_recursive(self, widget, btn_text: str, state: str):
        try:
            if isinstance(widget, ttk.Button) and widget.cget("text") == btn_text:
                widget.configure(state=state)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._set_state_recursive(child, btn_text, state)

    def _clear_all_selections(self):
        self._active_presets.clear()
        self._preset_mon_ref.clear()
        self._preset_all_screens_ref = 0
        self._manual_monitors.clear()
        self._manual_all_screens = True
        self._programmatic_cam_update = True
        try:
            for var in self.camera_vars.values():
                var.set(False)
        finally:
            self._programmatic_cam_update = False
        self.all_screens_var.set(True)
        for v in self.monitor_vars:
            v.set(False)
        for cat in CAM_CATEGORIES:
            self._update_category_check(cat)
        self._refresh_preset_button_styles()
        self._sync_sections_to_selected_cams()
        self._update_name_label()
        self.log("[CLEAR] All selections cleared.")

    def _progress_show(self, total: int, keep_value: bool = False):
        self._prog_bar.configure(maximum=max(1, total))
        if not keep_value:
            self._progress_var.set(0)
            self._progress_label_var.set("")
        self._prog_bar.pack(fill="x", pady=(4, 0))
        self._prog_label_widget.pack(fill="x")

    def _progress_update(self, done: int, total: int, label: str = ""):
        self._progress_var.set(done)
        txt = f"{label}  ({done}/{total})" if label else f"{done}/{total}"
        self._progress_label_var.set(txt)

    def _progress_hide(self):
        self._prog_bar.pack_forget()
        self._prog_label_widget.pack_forget()
        self._progress_label_var.set("")

    def _render_monitor_layout(self, layout_name: str):
        if not self._layout_grid:
            return
        for w in self._layout_grid.winfo_children():
            w.destroy()
        layout = MONITOR_LAYOUTS.get(layout_name, {})
        for mon, (r, c) in layout.items():
            idx = int(mon) - 1
            if idx < 0 or idx >= len(self.monitor_vars):
                dummy = tk.BooleanVar(value=False)
                cb = ttk.Checkbutton(self._layout_grid, text=str(mon), variable=dummy, width=3)
                cb.state(["disabled"])
                cb.grid(row=r, column=c, padx=2, pady=2, sticky="nsew")
                continue
            ttk.Checkbutton(
                self._layout_grid, text=str(mon),
                variable=self.monitor_vars[idx],
                command=lambda i=idx: self._on_specific_monitor_toggle(i),
                width=3
            ).grid(row=r, column=c, padx=2, pady=2, sticky="nsew")

    def _expand_sections_with_selected_cams(self):
        for cat, cams in CAM_CATEGORIES.items():
            sec = self._cam_sections.get(cat)
            if not sec:
                continue
            has_any = any(self.camera_vars.get(cam) and self.camera_vars[cam].get() for cam in cams)
            if has_any:
                sec.set_expanded(True)

    def _on_layout_selected(self, _evt=None):
        name = self.monitor_layout_var.get()
        self._render_monitor_layout(name)
        self._apply_monitor_effective()
        self._render_cameras()

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        def _append():
            self.log_text.insert("end", line)
            self.log_text.see("end")
        self.after(0, _append)

    def clear_log(self):
        self.log_text.delete("1.0", "end")

    @staticmethod
    def _is_descendant(w: tk.Widget | None, ancestor: tk.Widget) -> bool:
        while w is not None:
            if w == ancestor:
                return True
            w = w.master
        return False

    @staticmethod
    def _is_unc_path(s: str) -> bool:
        s = (s or "").strip()
        return s.startswith("\\\\") or s.startswith("//")

    def _cam_cell_px(self) -> int:
        f = tkfont.nametofont("TkDefaultFont")
        max_label_px = 0
        for cams in CAM_CATEGORIES.values():
            for name in cams:
                max_label_px = max(max_label_px, f.measure(name))
        return 36 + max_label_px + 26

    def _right_min_width_for_cam_cols(self, cols: int = 4) -> int:
        cell = self._cam_cell_px()
        return cols * cell + 18 + 26 + 10

    def _sync_sections_to_selected_cams(self):
        for cat, cams in CAM_CATEGORIES.items():
            sec = self._cam_sections.get(cat)
            if not sec:
                continue
            has_any = any(
                (cam in self.camera_vars) and self.camera_vars[cam].get()
                for cam in cams
            )
            sec.set_expanded(has_any)

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        right_min = self._right_min_width_for_cam_cols(4)
        root.columnconfigure(0, weight=0, minsize=340)
        root.columnconfigure(1, weight=1, minsize=right_min)
        root.rowconfigure(0, weight=1)

        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")

        # LEFT: Destination + Source (vedle sebe)
        top_row = ttk.Frame(left)
        top_row.pack(fill="x")
        top_row.columnconfigure(0, weight=1)
        top_row.columnconfigure(1, weight=0)

        dest_box = ttk.LabelFrame(top_row, text="Destination", padding=(8,4))
        dest_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        dest_box.columnconfigure(0, weight=1)
        dest_box.grid_columnconfigure(0, weight=1)
        dest_box.grid_columnconfigure(1, weight=0)
        dest_box.grid_columnconfigure(2, weight=0)
        dest_box.grid_columnconfigure(3, weight=0)
        ttk.Entry(dest_box, textvariable=self.dest_dir, width=10).grid(row=0, column=0, columnspan=4, sticky="we")
        _dest_btn_frame = ttk.Frame(dest_box)
        _dest_btn_frame.grid(row=0, column=0, columnspan=4, sticky="e")
        ttk.Button(_dest_btn_frame, text="...", width=4, command=self.pick_dest).pack(side="left")
        ttk.Button(_dest_btn_frame, text="📂", width=4, command=self.open_dest).pack(side="left", padx=(4, 0))
        ttk.Label(dest_box, textvariable=self._name_label_var).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self._run_name_entry = ttk.Entry(dest_box, textvariable=self.run_name_var, width=10)
        self._run_name_entry.grid(row=2, column=0, sticky="we")
        self._run_name_entry.bind("<Control-BackSpace>", lambda e: self._ctrl_backspace(e))
        ttk.Button(dest_box, text="Copy", command=self.on_copy).grid(row=2, column=1, padx=(6, 0))
        ttk.Button(dest_box, text="Labels", command=self.on_labels).grid(row=2, column=2, padx=(4, 0))
        ttk.Button(dest_box, text="Detail", command=self.on_detail).grid(row=2, column=3, padx=(4, 0))
        ttk.Checkbutton(dest_box, text="Preview", variable=self._preview_enabled).grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # Auto-copy řádek
        auto_row = ttk.Frame(dest_box)
        auto_row.grid(row=4, column=0, columnspan=4, sticky="we", pady=(6, 0))
        ttk.Label(auto_row, text="Auto every").pack(side="left")
        self._auto_interval_var = tk.IntVar(value=60)
        ttk.Spinbox(auto_row, from_=5, to=3600, textvariable=self._auto_interval_var,
                    width=6).pack(side="left", padx=(4, 2))
        ttk.Label(auto_row, text="s  max").pack(side="left")
        self._auto_cycles_var = tk.IntVar(value=0)
        ttk.Spinbox(auto_row, from_=0, to=999, textvariable=self._auto_cycles_var,
                    width=5).pack(side="left", padx=(4, 2))
        ttk.Label(auto_row, text="cycles (0=∞)").pack(side="left", padx=(0, 8))
        self._auto_btn = ttk.Button(auto_row, text="▶ Start auto",
                                     command=self._toggle_auto_copy)
        self._auto_btn.pack(side="left")
        self._auto_status_var = tk.StringVar(value="")
        ttk.Label(auto_row, textvariable=self._auto_status_var,
                  foreground="gray", font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))

        # Live mód řádek
        live_row = ttk.Frame(dest_box)
        live_row.grid(row=5, column=0, columnspan=4, sticky="we", pady=(4, 0))
        ttk.Label(live_row, text="Live:").pack(side="left")
        self._live_btn = ttk.Button(live_row, text="⏺ Start live",
                                    command=self._toggle_live)
        self._live_btn.pack(side="left", padx=(6, 0))
        self._live_status_var = tk.StringVar(value="")
        ttk.Label(live_row, textvariable=self._live_status_var,
                  foreground="gray", font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))

        src_box = ttk.LabelFrame(top_row, text="Source", padding=6)
        src_box.grid(row=0, column=1, sticky="nsew")
        station_display = self._station_id if self._station_id else "unknown"
        ttk.Label(src_box, text=station_display,
                  font=("Segoe UI", 8, "bold"), foreground="#0055aa").pack(anchor="w", pady=(0, 4))
        ttk.Radiobutton(src_box, text="Archiver", variable=self.source_mode,
                value="cpva", command=self._render_cameras).pack(anchor="w")
        ttk.Radiobutton(src_box, text="Screenshot window", variable=self.source_mode,
                        value="window", command=self._render_cameras).pack(anchor="w")

        # LEFT: Monitors + Presets
        monpres = ttk.Frame(left)
        monpres.pack(fill="x", pady=(8, 0))
        monpres.columnconfigure(0, weight=0)
        monpres.columnconfigure(1, weight=1)

        mon_box = ttk.LabelFrame(monpres, text="Monitors", padding=6)
        mon_box.grid(row=0, column=0, sticky="new")

        top_row = ttk.Frame(mon_box)
        top_row.pack(fill="x")
        ttk.Checkbutton(top_row, text="All screens", variable=self.all_screens_var,
                        command=self._on_all_screens_toggle).pack(side="left")
        ttk.Button(top_row, text="Identify", command=self.on_identify_monitors).pack(side="right")

        lay_row = ttk.Frame(mon_box)
        lay_row.pack(fill="x", pady=(6, 0))
        ttk.Label(lay_row, text="Layout:").pack(side="left")
        lay_cb = ttk.Combobox(lay_row, textvariable=self.monitor_layout_var,
                              values=list(MONITOR_LAYOUTS.keys()), state="readonly", width=7)
        lay_cb.pack(side="left", padx=(6, 0))
        lay_cb.bind("<<ComboboxSelected>>", self._on_layout_selected)

        self._layout_grid = ttk.Frame(mon_box)
        self._layout_grid.pack(anchor="w", pady=(6, 0))
        self._render_monitor_layout(self.monitor_layout_var.get())

        _PRESET_BTN_W = 80   # px na tlačítko (přibližně)
        _PRESET_COLS  = 6
        _PRESET_FIXED_W = _PRESET_COLS * _PRESET_BTN_W + 20   # +padding

        presets_wrap = ttk.LabelFrame(monpres, text="Presets", padding=6)
        presets_wrap.grid(row=0, column=1, sticky="nw")
        presets_wrap.grid_propagate(False)
        presets_wrap.configure(width=_PRESET_FIXED_W)

        presets_inner = ttk.Frame(presets_wrap)
        presets_inner.pack(anchor="w")
        preset_names = list(PRESETS.keys())

        clear_row = ttk.Frame(presets_wrap)
        clear_row.pack(fill="x", pady=(0, 4))
        ttk.Button(clear_row, text="Clear all", command=self._clear_all_selections).pack(side="left")

        for name in preset_names:
            btn = ttk.Button(presets_inner, text=name, style="Preset.TButton",
                             command=lambda n=name: self.toggle_preset(n))
            btn.update_idletasks()
            btn_w = btn.winfo_reqwidth()
            btn.configure(width=0)
            btn.grid_propagate(False)
            self.preset_buttons[name] = btn

        self._preset_cols = 0

        def _place_presets(cols: int):
            for b in self.preset_buttons.values():
                b.grid_forget()
            cols = max(1, min(cols, 6))
            # Zjisti maximální šířku tlačítka v normálním fontu
            self.update_idletasks()
            max_w = max((b.winfo_reqwidth() for b in self.preset_buttons.values()), default=80)
            for c in range(cols):
                presets_inner.columnconfigure(c, minsize=max_w + 6, weight=0)
            for i, n in enumerate(preset_names):
                r = i // cols
                c = i % cols
                self.preset_buttons[n].grid(row=r, column=c, sticky="we", padx=(0, 6), pady=(0, 4))
            self._refresh_preset_button_styles()

        def _refresh_presets_layout():
            _place_presets(6)

        presets_wrap.bind("<Configure>", lambda _e: None)
        self.after(0, _refresh_presets_layout)

        # LEFT: Selected cameras table
        sel_cams_box = ttk.LabelFrame(left, text="Selected cameras", padding=6)
        sel_cams_box.pack(fill="x", pady=(8, 0))

        sel_cams_inner = ttk.Frame(sel_cams_box)
        sel_cams_inner.pack(fill="x")

        sel_hsb = ttk.Scrollbar(sel_cams_inner, orient="horizontal")
        sel_hsb.pack(side="bottom", fill="x")

        self._sel_cams_canvas = tk.Canvas(sel_cams_inner, height=95, highlightthickness=0,
                                           xscrollcommand=sel_hsb.set)
        self._sel_cams_canvas.pack(fill="x", expand=True)
        sel_hsb.config(command=self._sel_cams_canvas.xview)

        self._sel_cams_frame = ttk.Frame(self._sel_cams_canvas)
        self._sel_cams_frame_id = self._sel_cams_canvas.create_window((0, 0), window=self._sel_cams_frame, anchor="nw")

        def _on_sel_cams_inner_configure(_evt=None):
            self._sel_cams_canvas.configure(scrollregion=self._sel_cams_canvas.bbox("all"))

        self._sel_cams_frame.bind("<Configure>", _on_sel_cams_inner_configure)

        # LEFT: Progress bar (skrytý dokud nekopírujeme)
        prog_frame = ttk.Frame(left)
        prog_frame.pack(fill="x", pady=(4, 0))
        self._prog_bar = ttk.Progressbar(prog_frame, variable=self._progress_var, maximum=100)
        self._prog_label_widget = ttk.Label(prog_frame, textvariable=self._progress_label_var,
                                             anchor="w", font=("Segoe UI", 8))
        # skryté na startu
        self._prog_bar.pack_forget()
        self._prog_label_widget.pack_forget()

        # LEFT: Diagnostics
        diag = ttk.LabelFrame(left, text="Diagnostics (copyable)", padding=(8, 6))
        diag.pack(fill="both", expand=True, pady=(10, 0))
        xsb = ttk.Scrollbar(diag, orient="horizontal")
        xsb.pack(side="bottom", fill="x")
        ysb = ttk.Scrollbar(diag, orient="vertical")
        ysb.pack(side="right", fill="y")
        self.log_text = tk.Text(diag, wrap="none", height=14,
                                xscrollcommand=xsb.set, yscrollcommand=ysb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        xsb.configure(command=self.log_text.xview)
        ysb.configure(command=self.log_text.yview)

        def _block_typing(event):
            if (event.state & 0x4) and event.keysym.lower() in ("c", "a", "x"):
                return None
            return "break"
        self.log_text.bind("<Key>", _block_typing)

        # RIGHT: Cameras
        cam_box = ttk.LabelFrame(right, text="Cameras", padding=6)
        cam_box.pack(fill="both", expand=True)

        search_row = ttk.Frame(cam_box)
        search_row.pack(fill="x", pady=(0, 8))
        ttk.Label(search_row, text="Search:").pack(side="left")
        search_entry = ttk.Entry(search_row, textvariable=self.cam_search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        ttk.Button(search_row, text="X", width=3,
                   command=lambda: self.cam_search_var.set("")).pack(side="left", padx=(8, 0))

        self.cam_canvas = tk.Canvas(cam_box, highlightthickness=0)
        cam_vsb = ttk.Scrollbar(cam_box, orient="vertical", command=self.cam_canvas.yview)
        self.cam_canvas.configure(yscrollcommand=cam_vsb.set)
        cam_vsb.pack(side="right", fill="y")
        self.cam_canvas.pack(side="left", fill="both", expand=True)

        cam_inner = ttk.Frame(self.cam_canvas)
        cam_inner_id = self.cam_canvas.create_window((0, 0), window=cam_inner, anchor="nw")

        def _on_cam_inner_configure(_evt=None):
            self.cam_canvas.configure(scrollregion=self.cam_canvas.bbox("all"))

        def _on_cam_canvas_configure(_evt=None):
            self.cam_canvas.itemconfig(cam_inner_id, width=self.cam_canvas.winfo_width())
            self._update_cam_cols()

        cam_inner.bind("<Configure>", _on_cam_inner_configure)
        self.cam_canvas.bind("<Configure>", _on_cam_canvas_configure)

        for cat_name in CAM_CATEGORIES.keys():
            sec = CollapsibleSection(cam_inner, title=cat_name, expanded=False)
            sec.pack(fill="x", pady=(0, 8))
            self._cam_sections[cat_name] = sec

            chk = ttk.Checkbutton(sec.right_slot, text="All",
                                  variable=self.cat_all_vars[cat_name],
                                  command=lambda cn=cat_name: self._toggle_category_all(cn))
            chk.pack(side="left")

            grid = ttk.Frame(sec.content)
            grid.pack(fill="x")
            self._cam_grids[cat_name] = grid

        self.cam_search_var.trace_add("write", lambda *_: self._render_cameras())
        self._render_cameras()
        search_entry.focus_set()

        def _wheel_router(event):
            delta = getattr(event, "delta", 0)
            if not delta:
                return
            step = int(-delta / 120)
            x = self.winfo_pointerx()
            y = self.winfo_pointery()
            w = self.winfo_containing(x, y)

            def _scroll_canvas(canvas: tk.Canvas):
                first, last = canvas.yview()
                if step < 0 and first <= 0.0001:
                    return "break"
                if step > 0 and last >= 0.9999:
                    return "break"
                canvas.yview_scroll(step, "units")
                return "break"

            if hasattr(self, "cam_canvas") and self._is_descendant(w, self.cam_canvas):
                return _scroll_canvas(self.cam_canvas)

        self.bind_all("<MouseWheel>", _wheel_router, add=True)

    def _update_cam_cols(self):
        w = self.cam_canvas.winfo_width()
        if w <= 80:
            return
        f = tkfont.nametofont("TkDefaultFont")
        max_label_px = 0
        for cams in CAM_CATEGORIES.values():
            for name in cams:
                max_label_px = max(max_label_px, f.measure(name))
        cell = 36 + max_label_px + 26
        cols = max(2, min(int(w // cell), 7))
        if cols != getattr(self, "_cam_cols", 4):
            self._cam_cols = cols
            self._render_cameras()

    @staticmethod
    def sanitize_folder_name(name: str) -> str:
        name = (name or "").strip()
        name = re.sub(r'[\\/:*?"<>|]+', "_", name)
        name = re.sub(r"\s+", " ", name).strip()
        if name in {"", ".", ".."}:
            return ""
        return name
    
    def _ctrl_backspace(self, event):
        w = event.widget
        pos = w.index(tk.INSERT)
        val = w.get()
        if pos == 0:
            return "break"
        # Najdi začátek předchozího slova
        i = pos - 1
        while i > 0 and val[i - 1] == " ":
            i -= 1
        while i > 0 and val[i - 1] != " ":
            i -= 1
        w.delete(i, pos)
        return "break"

    def pick_dest(self):
        cur = self.dest_dir.get().strip()
        initial = None
        if cur and (not self._is_unc_path(cur)) and Path(cur).exists():
            initial = cur
        else:
            initial = self._last_browse_dir if (self._last_browse_dir and Path(self._last_browse_dir).exists()) else str(Path.home())
        p = filedialog.askdirectory(title="Select destination folder", initialdir=initial, parent=self)
        if p:
            self.dest_dir.set(p)
            self._last_browse_dir = p

    def _get_dest(self) -> Path | None:
        s = self.dest_dir.get().strip()
        if not s:
            messagebox.showwarning("Missing destination", "Select destination folder.")
            return None
        p = Path(s)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Destination error", f"Cannot create/access destination:\n{p}\n\n{e}")
            return None
        return p

    def open_dest(self):
        p = self._get_dest()
        if not p:
            return
        try:
            open_in_explorer(p)
        except Exception as e:
            messagebox.showerror("Open folder error", str(e))

    def _apply_monitor_effective(self):
        manual_all = bool(self._manual_all_screens)
        manual_set: set[int] = set() if manual_all else set(self._manual_monitors)

        if self._preset_all_screens_ref > 0:
            effective_all = True
            effective_set: set[int] = set()
        else:
            preset_set = set(self._preset_mon_ref.keys())
            if preset_set and not manual_all:
                self._manual_all_screens = False
                if self.all_screens_var.get():
                    self.all_screens_var.set(False)
            effective_set = set() if manual_all else (manual_set | preset_set)
            effective_all = manual_all

        if not effective_all:
            effective_set = {i for i in effective_set if 0 <= i < len(self.monitor_vars)}

        if effective_all:
            for v in self.monitor_vars:
                if v.get():
                    v.set(False)
            if not self.all_screens_var.get():
                self.all_screens_var.set(True)
            self._manual_all_screens = True
            self._manual_monitors.clear()
        else:
            if self.all_screens_var.get():
                self.all_screens_var.set(False)
            for i, v in enumerate(self.monitor_vars):
                want = i in effective_set
                if v.get() != want:
                    v.set(want)

    def _on_all_screens_toggle(self):
        self._manual_all_screens = bool(self.all_screens_var.get())
        if not self._manual_all_screens:
            self._preset_all_screens_ref = 0
        else:
            self._manual_monitors.clear()
            for v in self.monitor_vars:
                v.set(False)
        self._apply_monitor_effective()
        self._update_name_label()

    def _on_specific_monitor_toggle(self, idx: int):
        val = bool(self.monitor_vars[idx].get())
        if val:
            self._manual_monitors.add(idx)
        else:
            self._manual_monitors.discard(idx)
            self._preset_mon_ref.pop(idx, None)
        self._manual_all_screens = False
        self._preset_all_screens_ref = 0
        self.all_screens_var.set(False)
        self._apply_monitor_effective()
        self._update_name_label()

    def _selected_monitors(self) -> list[int] | None:
        if self.all_screens_var.get():
            return None
        sel = [i for i, v in enumerate(self.monitor_vars) if v.get()]
        return sel if sel else None

    def _refresh_preset_button_styles(self):
        for n, btn in self.preset_buttons.items():
            btn.configure(style="PresetOn.TButton" if n in self._active_presets else "Preset.TButton")

    def _preset_add(self, name: str):
        data = PRESETS.get(name, {})
        cams = list(data.get("cams", []))
        mons = data.get("mons", None)
        self._programmatic_cam_update = True
        try:
            for cam in cams:
                if cam in self.camera_vars:
                    self.camera_vars[cam].set(True)
        finally:
            self._programmatic_cam_update = False
        if mons is None:
            self._preset_all_screens_ref += 1
        else:
            self._manual_all_screens = False
            for m1 in list(mons):
                mi = int(m1) - 1
                if 0 <= mi < len(self.monitor_vars):
                    self._preset_mon_ref[mi] = self._preset_mon_ref.get(mi, 0) + 1
        for cat in CAM_CATEGORIES:
            self._update_category_check(cat)
        self._apply_monitor_effective()

    def _preset_remove(self, name: str):
        data = PRESETS.get(name, {})
        cams = list(data.get("cams", []))
        mons = data.get("mons", None)
        self._programmatic_cam_update = True
        try:
            for cam in cams:
                if cam in self.camera_vars:
                    self.camera_vars[cam].set(False)
        finally:
            self._programmatic_cam_update = False
        if mons is None:
            self._preset_all_screens_ref = max(0, self._preset_all_screens_ref - 1)
        else:
            for m1 in list(mons):
                mi = int(m1) - 1
                if mi in self._preset_mon_ref:
                    self._preset_mon_ref[mi] -= 1
                    if self._preset_mon_ref[mi] <= 0:
                        del self._preset_mon_ref[mi]
        for cat in CAM_CATEGORIES:
            self._update_category_check(cat)
        self._apply_monitor_effective()

    def toggle_preset(self, name: str):
        if name in self._active_presets:
            self._active_presets.remove(name)
            self._preset_remove(name)
        else:
            self._active_presets.add(name)
            self._preset_add(name)
        self._refresh_preset_button_styles()
        for cat in CAM_CATEGORIES:
            self._update_category_check(cat)
        self._sync_sections_to_selected_cams()
        self._update_name_label()

    def _selected_cameras(self) -> list[str]:
        return [name for name, var in self.camera_vars.items() if var.get()]

    def _toggle_category_all(self, cat: str):
        if self._updating_cat:
            return
        want = bool(self.cat_all_vars[cat].get())
        cams = CAM_CATEGORIES.get(cat, [])
        self._programmatic_cam_update = True
        try:
            for cam in cams:
                if cam in self.camera_vars:
                    self.camera_vars[cam].set(want)
        finally:
            self._programmatic_cam_update = False
        self._update_category_check(cat)
        self._update_name_label()

    def _on_cam_var_changed(self, cam_name: str):
        if self._programmatic_cam_update:
            cat = self.cam_to_cat.get(cam_name)
            if cat:
                self._update_category_check(cat)
            return
        self._update_name_label()

    def _update_category_check(self, cat: str):
        cams = CAM_CATEGORIES.get(cat, [])
        if not cams:
            return
        all_on = all(self.camera_vars.get(cam, tk.BooleanVar(value=False)).get() for cam in cams)
        if self.cat_all_vars[cat].get() != all_on:
            self._updating_cat = True
            try:
                self.cat_all_vars[cat].set(all_on)
            finally:
                self._updating_cat = False

    def on_identify_monitors(self):
        rects = list_monitors_rects()
        if not rects:
            messagebox.showinfo("Identify", "No monitors detected.")
            return
        overlays: list[tk.Toplevel] = []
        for i, (l, t, r, b) in enumerate(rects):
            w, h = (r - l), (b - t)
            win = tk.Toplevel(self)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-alpha", 0.55)
            except Exception:
                pass
            win.geometry(f"{w}x{h}+{l}+{t}")
            frame = tk.Frame(win, bg="black")
            frame.pack(fill="both", expand=True)
            lbl = tk.Label(frame, text=f"M{i+1}", fg="white", bg="black",
                           font=("Segoe UI", 96, "bold"))
            lbl.place(relx=0.5, rely=0.5, anchor="center")
            overlays.append(win)

        def close_all():
            for w in overlays:
                try:
                    w.destroy()
                except Exception:
                    pass
        self.after(IDENTIFY_MS, close_all)

    def _render_cameras(self):
        q = norm_query(self.cam_search_var.get())
        cols = getattr(self, "_cam_cols", 4)
        station = self._station_id

        for cat, cams in CAM_CATEGORIES.items():
            sec = self._cam_sections.get(cat)
            grid = self._cam_grids.get(cat)
            if not sec or not grid:
                continue

            for child in grid.winfo_children():
                child.destroy()

            visible = [c for c in cams if (not q or q in norm_query(c))]

            if q and visible:
                sec.set_expanded(True)

            if not visible:
                sec.pack_forget()
                continue
            else:
                if not sec.winfo_ismapped():
                    sec.pack(fill="x", pady=(0, 8))

            for c in range(cols):
                grid.columnconfigure(c, weight=1)

            for i, cam in enumerate(visible):
                r = i // cols
                c = i % cols

                if self.source_mode.get() != "window":
                    available = True
                else:
                    if cam in _L3BT_CAMS:
                        available = False
                    else:
                        effective_station = station
                        if station == "CZOW-NB2DLRL24":
                            layout = self.monitor_layout_var.get()
                            _LAYOUT_TO_STATION = {
                                "VIS-01": "L3-VIS01",
                                "VIS-02": "L3-VIS02",
                                "OPR-01": "L3-OPR1",
                                "OPR-02": "L3-OPR2",
                                "OPR-03": "L3-OPR3",
                            }
                            effective_station = _LAYOUT_TO_STATION.get(layout, station)
                        available = _cam_available_here(cam, effective_station)

                cb = ttk.Checkbutton(grid, text=cam, variable=self.camera_vars[cam])
                if not available:
                    cb.state(["disabled"])
                cb.grid(row=r, column=c, sticky="w", padx=(0, 18), pady=1)

                def _tip_text(cam_name=cam, av=available):
                    if not av:
                        stations = _cam_available_on_stations(cam_name)
                        where = ", ".join(stations) if stations else "other station"
                        return f"Available on: {where}"
                    v = CAM_INFO.get(cam_name) or CAM_INFO.get(cpva_label(cam_name))
                    return str(v) if v else ""

                ToolTip(cb, _tip_text)

    def _planned_output_count(self, monitors_sel: list[int] | None, cams_sel: list[str]) -> int:
        if monitors_sel is None and not self.all_screens_var.get():
            shots = 0
        elif monitors_sel is None:
            shots = 1
        else:
            shots = len(monitors_sel)
        return shots + len(cams_sel)
    
    def _update_name_label(self):
        total = self._planned_output_count(self._selected_monitors(), self._selected_cameras())
        if total <= 1:
            self._name_label_var.set("File name (if empty => timestamp only):")
        else:
            self._name_label_var.set("Folder name (if empty => timestamp only):")
        self._refresh_selected_cams_table()

    def _refresh_selected_cams_table(self):
        if not hasattr(self, "_sel_cams_frame"):
            return
        for w in self._sel_cams_frame.winfo_children():
            w.destroy()

        mon_sel = self._selected_monitors()
        if mon_sel is None and self.all_screens_var.get():
            mon_items = ["All screens"]
        elif mon_sel:
            mon_items = [f"M{m+1}" for m in mon_sel]
        else:
            mon_items = []

        cam_items = self._selected_cameras()

        rows_per_col = 5
        current_row = 0
        current_col = 0

        def _place(text, bold=False):
            nonlocal current_row, current_col
            font = ("Segoe UI", 9, "bold") if bold else ("Segoe UI", 9)
            ttk.Label(self._sel_cams_frame, text=text, font=font).grid(
                row=current_row, column=current_col, sticky="w", padx=(0, 16), pady=0
            )
            current_row += 1
            if current_row >= rows_per_col:
                current_row = 0
                current_col += 1

        if mon_items:
            _place("Monitors:", bold=True)
            for m in mon_items:
                _place(m)

        if cam_items:
            # Pokud jsme uprostřed sloupce, přejdi na nový
            if current_row != 0:
                current_row = 0
                current_col += 1
            _place("Cameras:", bold=True)
            for c in cam_items:
                _place(c)

        self._sel_cams_canvas.configure(scrollregion=self._sel_cams_canvas.bbox("all"))

    def _ensure_output_path(self, dest: Path, run_ts: str, total_outputs: int) -> Path:
        if total_outputs <= 1:
            return dest
        user_name = self.sanitize_folder_name(self.run_name_var.get())
        if user_name:
            run_dir = dest / user_name
            run_dir.mkdir(parents=True, exist_ok=True)
            return run_dir
        return dest

    @staticmethod
    def build_window_needles(cam: str) -> list[str]:
        needles: list[str] = []
        v = CAM_WINDOW_TITLES.get(cam)
        if v:
            needles.append(v)
        al = cpva_label(cam)
        if al != cam:
            v2 = CAM_WINDOW_TITLES.get(al)
            if v2:
                needles.append(v2)
            needles.append(al)
        needles.append(cam)
        cid = CAM_INFO.get(cam) or CAM_INFO.get(al)
        if cid and str(cid).isdigit():
            needles.insert(0, f"C03-{cid}")
        out = []
        seen = set()
        for n in needles:
            n = (n or "").strip()
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def on_labels(self):
        cams = self._selected_cameras()

        win = tk.Toplevel(self)
        win.title("Labels")
        win.resizable(True, True)

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)

        # Header
        header = ttk.Frame(frm)
        header.pack(fill="x", pady=(0, 2))
        header.columnconfigure(0, minsize=140)
        header.columnconfigure(1, minsize=36)
        header.columnconfigure(2, minsize=90)
        header.columnconfigure(3, minsize=110)
        header.columnconfigure(4, minsize=60)
        header.columnconfigure(5, minsize=40)
        header.columnconfigure(6, minsize=30)
        ttk.Label(header, text="Camera", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="On", font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Mode", font=("Segoe UI", 9, "bold")).grid(row=0, column=2, sticky="w")
        ttk.Label(header, text="Text", font=("Segoe UI", 9, "bold")).grid(row=0, column=3, sticky="w")
        ttk.Label(header, text="Use idx", font=("Segoe UI", 9, "bold")).grid(row=0, column=4, sticky="w")
        ttk.Label(header, text="Counter", font=("Segoe UI", 9, "bold")).grid(row=0, column=5, sticky="w")
        ttk.Label(header, text="↺", font=("Segoe UI", 9, "bold")).grid(row=0, column=6, sticky="w")

        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=(2, 0))

        # Buttons — pack PŘED scroll_wrap aby měly vždy místo
        ttk.Separator(frm, orient="horizontal").pack(side="bottom", fill="x", pady=(4, 0))
        btn_row = ttk.Frame(frm)
        btn_row.pack(side="bottom", fill="x", pady=(0, 4))

        # Scrollable area
        scroll_wrap = ttk.Frame(frm)
        scroll_wrap.pack(fill="both", expand=True, pady=(4, 0))

        canvas = tk.Canvas(scroll_wrap, highlightthickness=0)
        vsb = ttk.Scrollbar(scroll_wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda _e: canvas.itemconfig(inner_id, width=canvas.winfo_width()))

        if not cams:
            ttk.Label(inner, text="No cameras selected.", foreground="gray").pack(pady=20)
            win.geometry("500x130")
            return

        rows: dict[str, dict] = {}

        for cam in cams:
            s = self._label_settings.get(cam, {})
            enabled_var = tk.BooleanVar(value=s.get("enabled", False))
            mode_var = tk.StringVar(value=s.get("mode", "prefix"))
            text_var = tk.StringVar(value=s.get("text", ""))
            index_val = s.get("index", 1)
            index_label_var = tk.StringVar(value=f"{index_val:02d}")

            row_frm = ttk.Frame(inner)
            row_frm.pack(fill="x", pady=1)
            row_frm.columnconfigure(0, minsize=140)
            row_frm.columnconfigure(1, minsize=36)
            row_frm.columnconfigure(2, minsize=90)
            row_frm.columnconfigure(3, minsize=110)
            row_frm.columnconfigure(4, minsize=60)
            row_frm.columnconfigure(5, minsize=40)
            row_frm.columnconfigure(6, minsize=30)

            use_index_var = tk.BooleanVar(value=s.get("use_index", False))

            ttk.Label(row_frm, text=cam).grid(row=0, column=0, sticky="w")
            ttk.Checkbutton(row_frm, variable=enabled_var).grid(row=0, column=1, sticky="w")
            ttk.Combobox(row_frm, textvariable=mode_var, values=["prefix", "suffix"],
                        state="readonly", width=7).grid(row=0, column=2, sticky="w")
            ttk.Entry(row_frm, textvariable=text_var, width=14).grid(row=0, column=3, sticky="we")
            ttk.Checkbutton(row_frm, text="idx", variable=use_index_var).grid(row=0, column=4, sticky="w")
            ttk.Label(row_frm, textvariable=index_label_var, anchor="center").grid(row=0, column=5, sticky="w")

            def _reset(c=cam, ilv=index_label_var):
                self._label_settings.setdefault(c, {})["index"] = 1
                ilv.set("01")

            ttk.Button(row_frm, text="↺", width=3, command=_reset).grid(row=0, column=6, sticky="w")

            rows[cam] = {
                "enabled": enabled_var,
                "mode": mode_var,
                "text": text_var,
                "use_index": use_index_var,
                "index_label_var": index_label_var,
            }

        def _reset_all():
            for cam in cams:
                self._label_settings.setdefault(cam, {})["index"] = 1
                rows[cam]["index_label_var"].set("01")

        def _save_and_close():
            for cam in cams:
                r = rows[cam]
                cur = self._label_settings.get(cam, {})
                self._label_settings[cam] = {
                    "enabled": r["enabled"].get(),
                    "mode": r["mode"].get(),
                    "text": r["text"].get(),
                    "use_index": r["use_index"].get(),
                    "index": cur.get("index", 1),
                }
            win.destroy()

        ttk.Button(btn_row, text="Reset all", command=_reset_all).pack(side="left")
        ttk.Button(btn_row, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(btn_row, text="Save", command=_save_and_close).pack(side="right", padx=(0, 8))

        h = min(120 + len(cams) * 34 + 80, 700)
        win.geometry(f"560x{h}")

    def _open_preview_async(self, out_dir: Path):
        def _collect():
            try:
                paths = []
                for ext in ("*.png", "*.jpg", "*.tif", "*.tiff"):
                    paths.extend(out_dir.glob(ext))
                paths.sort(key=lambda p: p.name)
                if paths:
                    self.after(0, lambda pp=paths: PreviewWindow(self, pp))
            except Exception:
                pass
        threading.Thread(target=_collect, daemon=True).start()

    def _open_preview_with_callbacks(self, out_dir: Path,
                                      monitors_local, all_screens_local,
                                      cams_local, source_mode,
                                      copied_files: list | None = None):
        def _retry(mon=monitors_local, all_s=all_screens_local,
                   cams=cams_local, src=source_mode):
            self.after(0, lambda: self._run_copy_worker(mon, all_s, cams, src))

        paths = sorted(copied_files or [], key=lambda p: p.name)
        if not paths:
            return

        self.after(0, lambda pp=paths: PreviewWindow(
            self, pp,
            on_keep=None,
            on_delete=None,
            on_try_again=_retry,
        ))

    def _open_auto_preview(self, latest_entry: dict):
        if not self._preview_enabled.get():
            return
        if not self._auto_cycle_history:
            return

        # Pokud už je auto preview okno otevřené, jen ho aktualizuj
        existing = getattr(self, "_auto_preview_win", None)
        if existing is not None:
            try:
                existing._refresh_cycles(self._auto_cycle_history, latest_entry["cycle"])
                return
            except Exception:
                pass

        win = tk.Toplevel(self)
        win.title("Auto copy — cycle preview")
        win.geometry("960x720")
        win.resizable(True, True)
        self._auto_preview_win = win

        def _on_close():
            self._auto_preview_win = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

        # ── top bar: výběr cyklu ──────────────────────────────────
        top = ttk.Frame(win, padding=(8, 6))
        top.pack(fill="x")

        ttk.Label(top, text="Cycle:").pack(side="left")
        cycle_var = tk.IntVar(value=latest_entry["cycle"])
        cycle_cb = ttk.Combobox(top, textvariable=cycle_var, state="readonly", width=10)
        cycle_cb.pack(side="left", padx=(6, 0))

        ttk.Button(top, text="◀", width=3,
                   command=lambda: _step(-1)).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="▶", width=3,
                   command=lambda: _step(1)).pack(side="left", padx=(2, 0))

        ts_var = tk.StringVar(value=latest_entry["ts"])
        ttk.Label(top, textvariable=ts_var, foreground="gray",
                  font=("Segoe UI", 8)).pack(side="left", padx=(12, 0))

        ttk.Separator(win, orient="horizontal").pack(fill="x")

        # ── preview frame ─────────────────────────────────────────
        preview_frame = ttk.Frame(win)
        preview_frame.pack(fill="both", expand=True)

        _current_pw: list = [None]

        def _load_cycle(cycle_num: int):
            # Najdi entry pro daný cyklus
            entry = next((e for e in self._auto_cycle_history if e["cycle"] == cycle_num), None)
            if entry is None:
                return
            ts_var.set(entry["ts"])
            # Zruš předchozí PreviewWindow obsah
            if _current_pw[0] is not None:
                try:
                    _current_pw[0].destroy()
                except Exception:
                    pass
                _current_pw[0] = None
            # Vytvoř nové PreviewWindow jako frame uvnitř win
            paths = entry["files"]
            if not paths:
                return

            def _retry(files=paths):
                pass  # retry nedává smysl pro historické cykly

            pw = PreviewWindow.__new__(PreviewWindow)
            tk.Toplevel.__init__(pw, win)
            pw.withdraw()  # schováme ho jako samostatné okno

            # Místo toho zobrazíme obsah přímo — embedded preview
            inner_win = tk.Toplevel(win)
            inner_win.title(f"Cycle {cycle_num} — {entry['ts']}")
            inner_win.geometry("920x640")
            inner_win.transient(win)
            _current_pw[0] = inner_win

            # Jednodušší přístup: otevři standardní PreviewWindow
            pw.destroy()
            pwin = PreviewWindow(win, paths, on_keep=None, on_delete=None, on_try_again=_retry)
            pwin.title(f"Cycle {cycle_num} — {entry['ts']}")
            inner_win.destroy()
            _current_pw[0] = pwin

        def _refresh_cycles(history: list, select_cycle: int | None = None):
            values = [f"Cycle {e['cycle']}  ({e['ts']})" for e in history]
            cycle_cb["values"] = values
            # Nastav výběr
            target = select_cycle if select_cycle is not None else history[-1]["cycle"]
            idx = next((i for i, e in enumerate(history) if e["cycle"] == target), len(history) - 1)
            cycle_cb.current(idx)
            cycle_var.set(history[idx]["cycle"])

        def _on_cycle_selected(_evt=None):
            idx = cycle_cb.current()
            if idx < 0 or idx >= len(self._auto_cycle_history):
                return
            entry = self._auto_cycle_history[idx]
            cycle_var.set(entry["cycle"])
            _load_cycle(entry["cycle"])

        def _step(delta: int):
            idx = cycle_cb.current()
            new_idx = max(0, min(idx + delta, len(self._auto_cycle_history) - 1))
            cycle_cb.current(new_idx)
            _on_cycle_selected()

        cycle_cb.bind("<<ComboboxSelected>>", _on_cycle_selected)

        # Přiřaď metodu pro refresh zvenku
        win._refresh_cycles = _refresh_cycles

        # Inicializace
        _refresh_cycles(self._auto_cycle_history, latest_entry["cycle"])
        _load_cycle(latest_entry["cycle"])

    def _toggle_auto_copy(self):
        if self._auto_copy_active:
            self._stop_auto_copy()
        else:
            self._start_auto_copy()

    def _start_auto_copy(self):
        if getattr(self, "_busy", False):
            self.log("[AUTO] Cannot start: busy")
            return
        monitors_local = self._selected_monitors()
        all_screens_local = bool(self.all_screens_var.get())
        cams_local = list(self._selected_cameras())
        source_mode = self.source_mode.get().strip().lower()
        if not cams_local and monitors_local is None and not all_screens_local:
            messagebox.showwarning("Auto copy", "Select cameras or monitors.")
            return
        self._auto_copy_active = True
        self._auto_copy_count = 0
        self._auto_total_steps = 0
        self._auto_cycle_history = []
        self._auto_preview_win = None
        self._auto_copy_params = {
            "monitors": monitors_local,
            "all_screens": all_screens_local,
            "cams": cams_local,
            "source": source_mode,
        }
        self._auto_btn.configure(text="■ Stop auto")
        self.log(f"[AUTO] Started — interval={self._auto_interval_var.get()}s  max_cycles={self._auto_cycles_var.get()}")
        self._run_auto_cycle()

    def _stop_auto_copy(self):
        self._auto_copy_active = False
        if self._auto_copy_after_id is not None:
            try:
                self.after_cancel(self._auto_copy_after_id)
            except Exception:
                pass
            self._auto_copy_after_id = None
        self._auto_btn.configure(text="▶ Start auto")
        self._auto_status_var.set("")
        self.log("[AUTO] Stopped.")

    def _run_auto_cycle(self):
        if not self._auto_copy_active or self._auto_copy_params is None:
            return
        max_cycles = self._auto_cycles_var.get()
        self._auto_copy_count += 1
        if max_cycles > 0 and self._auto_copy_count > max_cycles:
            self.log(f"[AUTO] Finished after {max_cycles} cycles.")
            self._stop_auto_copy()
            return
        status = f"cycle {self._auto_copy_count}" + (f"/{max_cycles}" if max_cycles > 0 else "")
        self._auto_status_var.set(status)
        self.log(f"[AUTO] {status}")
        self._schedule_next_auto_cycle()
        p = self._auto_copy_params
        self._run_copy_worker(
            p["monitors"], p["all_screens"], p["cams"], p["source"],
            _cycle_number=self._auto_copy_count,
        )

    def _schedule_next_auto_cycle(self):
        if not self._auto_copy_active:
            return
        interval_ms = max(5, self._auto_interval_var.get()) * 1000
        self._auto_copy_after_id = self.after(interval_ms, self._run_auto_cycle)

    # ── Live mód ──────────────────────────────────────────────────
    def _toggle_live(self):
        if self._live_active:
            self._stop_live()
        else:
            self._start_live()

    def _start_live(self):
        cams_local = list(self._selected_cameras())
        if not cams_local:
            messagebox.showwarning("Live", "Vyber alespoň jednu kameru.")
            return
        dest = self._get_dest()
        if not dest:
            return
        live_dir = dest / "Live"
        try:
            live_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Live", f"Nelze vytvořit složku Live:\n{e}")
            return

        self._live_active = True
        self._live_collected_files = []
        self._live_btn.configure(text="⏹ Stop live")
        self._live_status_var.set("running…")
        self.log(f"[LIVE] Started → {live_dir}")

        # Pro každou kameru si pamatujeme poslední zachycený timestamp
        last_seen: dict[str, int] = {}

        # Fronta souborů ke zkopírování: (src_path, dst_path)
        copy_queue: list[tuple[Path, Path]] = []
        copy_lock = threading.Lock()

        def _copy_worker():
            """Vlákno které drží frontu a kopíruje soubory."""
            while self._live_active or copy_queue:
                item = None
                with copy_lock:
                    if copy_queue:
                        item = copy_queue.pop(0)
                if item is None:
                    time.sleep(0.05)
                    continue
                src, dst = item
                try:
                    shutil.copy2(str(src), str(dst))
                    with self._live_lock:
                        self._live_collected_files.append(dst)
                    count = len(self._live_collected_files)
                    self.after(0, lambda n=count: self._live_status_var.set(f"{n} files"))
                    self.log(f"[LIVE] saved {dst.name}")
                except Exception as e:
                    self.log(f"[LIVE] copy error {src.name}: {e}")

        def _poll_worker():
            """Vlákno které polluje cam složky a detekuje nové soubory."""
            # Nejprve najdi cam složky (jednorázově)
            day_root = today_day_root()
            cam_to_folder = find_camera_folders_bulk(day_root, cams_local, self.log)

            # Inicializuj last_seen na aktuálně nejnovější timestamp
            for cam in cams_local:
                folder = cam_to_folder.get(cam)
                if not folder:
                    self.log(f"[LIVE] init: složka pro {cam} nenalezena — kamera bude přeskočena")
                    continue
                try:
                    entries = []
                    with os.scandir(folder) as it:
                        for entry in it:
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            m = TS_IN_NAME_RE.search(entry.name)
                            if m:
                                entries.append(int(m.group(1)))
                    if entries:
                        last_seen[cam] = max(entries)
                        self.log(f"[LIVE] init {cam}: last_seen={last_seen[cam]}, {len(entries)} souborů")
                    else:
                        last_seen[cam] = 0
                        self.log(f"[LIVE] init {cam}: složka prázdná, last_seen=0")
                except Exception as e:
                    self.log(f"[LIVE] init {cam} chyba: {e}")

            while self._live_active:
                for cam in cams_local:
                    folder = cam_to_folder.get(cam)
                    if not folder:
                        self.log(f"[LIVE] poll: {cam} folder was not found — camera will be skipped")
                        continue
                    try:
                        with os.scandir(folder) as it:
                            for entry in it:
                                if not entry.is_file(follow_symlinks=False):
                                    continue
                                mm = TS_IN_NAME_RE.search(entry.name)
                                if not mm:
                                    continue
                                ts_ns = int(mm.group(1))
                                prev = last_seen.get(cam, -1)
                                if prev == -1:
                                    # Kamera nebyla inicializována — nastav last_seen na aktuální maximum a nezachytávej
                                    last_seen[cam] = ts_ns
                                    continue
                                if ts_ns > prev:
                                    last_seen[cam] = ts_ns
                                    src = folder / entry.name
                                    try:
                                        from zoneinfo import ZoneInfo
                                        _PRAGUE = ZoneInfo("Europe/Prague")
                                        file_ts = datetime.fromtimestamp(
                                            ts_ns / 1_000_000_000, tz=_PRAGUE
                                        ).strftime(TS_FMT)
                                    except Exception:
                                        file_ts = datetime.now().strftime(TS_FMT)
                                    dst = live_dir / f"{cam}__{file_ts}{src.suffix.lower()}"
                                    with copy_lock:
                                        copy_queue.append((src, dst))
                    except Exception as e:
                        self.log(f"[LIVE] poll error {cam}: {e}")
                time.sleep(0.08)

        copy_t = threading.Thread(target=_copy_worker, daemon=True)
        poll_t = threading.Thread(target=_poll_worker, daemon=True)
        copy_t.start()
        poll_t.start()
        self._live_thread = poll_t

    def _stop_live(self):
        self._live_active = False
        self._live_btn.configure(text="⏺ Start live")
        count = len(self._live_collected_files)
        self._live_status_var.set(f"stopped — {count} files")
        self.log(f"[LIVE] Stopped. Total files: {count}")

        # Otevři preview pokud je co ukázat
        files = list(self._live_collected_files)
        if files and self._preview_enabled.get():
            paths = sorted(files, key=lambda p: p.name)
            self.after(200, lambda pp=paths: PreviewWindow(self, pp))

    def on_copy(self):
        monitors_local = self._selected_monitors()
        all_screens_local = bool(self.all_screens_var.get())
        cams_local = list(self._selected_cameras())
        source_mode = self.source_mode.get().strip().lower()
        if not cams_local and monitors_local is None and not all_screens_local:
            messagebox.showwarning("Copy", "Select cameras or monitors.")
            return
        self._run_copy_worker(monitors_local, all_screens_local, cams_local, source_mode)

    def _run_copy_worker(self, monitors_local, all_screens_local, cams_local, source_mode,
                         _auto_callback=None, _cycle_number: int | None = None):
        if getattr(self, "_busy", False):
            self.log("[UI] Ignored: busy=True")
            return
        dest = self._get_dest()
        if not dest:
            return
        dest_local = dest
        self._busy = True

        def worker():
            t_click_ns = time.time_ns()
            self.log(f"[CLICK] t_click_ns={t_click_ns}")
            try:
                self.log("=== COPY START ===")
                self.log(f"Source mode: {source_mode}")
                self.log(f"Station: {self._station_id}")
                self.log(f"Active presets: {sorted(self._active_presets)}")
                self.log(f"Selected monitors: {'ALL' if monitors_local is None else [m+1 for m in monitors_local]}")
                self.log(f"Selected cameras: {cams_local}")
                self.log(f"Destination: {dest_local}")

                run_ts = datetime.now().strftime(RUN_FOLDER_FMT)
                total = self._planned_output_count(monitors_local, cams_local)
                custom_name = self.sanitize_folder_name(self.run_name_var.get())
                out_dir = self._ensure_output_path(dest_local, run_ts, total)

                # Show progress bar — při auto cyklech maximum roste dynamicky
                is_auto_cycle = _cycle_number is not None
                if is_auto_cycle:
                    self._auto_total_steps += total
                    _auto_max = self._auto_total_steps
                    _is_first_cycle = (_cycle_number == 1)
                    self.after(0, lambda m=_auto_max, first=_is_first_cycle: self._progress_show(m, keep_value=not first))
                    # Offset: kolik kroků bylo hotovo v předchozích cyklech
                    _prev_done = self._auto_total_steps - total
                else:
                    self.after(0, lambda: self._progress_show(total))
                    _prev_done = 0
                done_steps = 0
                copied = 0
                copied_files: list[Path] = []
                problems: list[str] = []

                with timed(self.log, "screenshots"):
                    if monitors_local is None and all_screens_local:
                        fname = f"{custom_name}.png" if (total == 1 and custom_name) else f"{run_ts}_all.png"
                        out = out_dir / fname
                        self.log(f"[SHOT] saving {out.name}")
                        take_screenshot_monitor_png(out, None)
                        copied_files.append(out)
                        done_steps += 1
                        self.after(0, lambda d=done_steps, pd=_prev_done, t=_auto_max if is_auto_cycle else total: self._progress_update(pd + d, t, "Screenshot (all)"))
                    elif monitors_local is not None:
                        for mi in monitors_local:
                            fname = f"{custom_name}.png" if (total == 1 and custom_name) else f"{run_ts}_monitor{mi+1}.png"
                            out = out_dir / fname
                            self.log(f"[SHOT] saving {out.name}")
                            take_screenshot_monitor_png(out, mi)
                            copied_files.append(out)
                            done_steps += 1
                            self.after(0, lambda d=done_steps, pd=_prev_done, t=_auto_max if is_auto_cycle else total, m=mi: self._progress_update(pd + d, t, f"Monitor {m+1}"))

                if cams_local:
                    if source_mode == "cpva":
                        with timed(self.log, "resolve CPVA day_root"):
                            day_root = today_day_root()
                            self.log(f"[CPVA] day_root = {day_root}")

                        with timed(self.log, "find camera folders (bulk)"):
                            cam_to_folder = find_camera_folders_bulk(day_root, cams_local, self.log)

                        for cam in cams_local:
                            self.log(f"\n[CAM] '{cam}' (CPVA match='{cpva_label(cam)}')")
                            if not self._auto_copy_active and _cycle_number is not None:
                                self.log("[AUTO] Stop requested — aborting current cycle.")
                                break

                            if not is_known_camera(cam):
                                problems.append(f"{cam}: I don't know this camera.")
                                self.log("[CAM] UNKNOWN CAMERA")
                                done_steps += 1
                                self.after(0, lambda d=done_steps, pd=_prev_done, t=_auto_max if is_auto_cycle else total, c=cam: self._progress_update(pd + d, t, c))
                                continue

                            cam_folder = cam_to_folder.get(cam)
                            if not cam_folder:
                                problems.append(f"{cam}: Not found in latest hour folder.")
                                self.log("[CAM] NOT FOUND (latest hour)")
                                done_steps += 1
                                self.after(0, lambda d=done_steps, pd=_prev_done, t=_auto_max if is_auto_cycle else total, c=cam: self._progress_update(pd + d, t, c))
                                continue

                            self.log(f"[CAM] folder = {cam_folder}")
                            with timed(self.log, f"find image near click ({cam})"):
                                latest = find_image_near_click_fast(
                                    cam_folder, t_click_ns, self.log,
                                    _cache=self._cam_dir_cache,
                                    _cache_time=self._cam_dir_cache_time,
                                    _cache_ttl=self._cam_dir_cache_ttl,
                                )

                            if not latest:
                                problems.append(f"{cam}: No timestamped images yet: {cam_folder.name}")
                                self.log("[CAM] NO TIMESTAMPED IMAGES")
                                done_steps += 1
                                self.after(0, lambda d=done_steps, pd=_prev_done, t=_auto_max if is_auto_cycle else total, c=cam: self._progress_update(pd + d, t, c))
                                continue

                            with timed(self.log, f"copy2 ({cam})"):
                                try:
                                    _m = TS_IN_NAME_RE.search(latest.name)
                                    if _m:
                                        _ns = int(_m.group(1))
                                        from zoneinfo import ZoneInfo
                                        _PRAGUE = ZoneInfo("Europe/Prague")
                                        file_ts = datetime.fromtimestamp(_ns / 1_000_000_000, tz=_PRAGUE).strftime(TS_FMT)
                                    else:
                                        file_ts = datetime.fromtimestamp(safe_mtime(latest)).strftime(TS_FMT)
                                except Exception:
                                    file_ts = datetime.fromtimestamp(safe_mtime(latest)).strftime(TS_FMT)
                                _lbl = self._label_settings.get(cam, {})
                                _lbl_enabled = _lbl.get("enabled", False)
                                _lbl_text = _lbl.get("text", "").strip()
                                _use_index = _lbl.get("use_index", False)
                                _lbl_idx = f"{_lbl.get('index', 1):02d}" if _use_index else ""
                                if _lbl_text and _lbl_idx:
                                    _lbl_token = f"{_lbl_text}_{_lbl_idx}"
                                elif _lbl_text:
                                    _lbl_token = _lbl_text
                                elif _lbl_idx:
                                    _lbl_token = _lbl_idx
                                else:
                                    _lbl_token = ""
                                _base = f"{custom_name}{latest.suffix.lower()}" if (total == 1 and custom_name) else f"{cam}__{file_ts}{latest.suffix.lower()}"
                                if _lbl_enabled and _lbl_token:
                                    _stem = Path(_base).stem
                                    _ext = Path(_base).suffix
                                    if _lbl.get("mode", "prefix") == "prefix":
                                        _base = f"{_lbl_token}__{_stem}{_ext}"
                                    else:
                                        _base = f"{_stem}__{_lbl_token}{_ext}"
                                dst = out_dir / _base
                                self.log(f"[COPY] {latest} -> {dst.name}")
                                shutil.copy2(str(latest), str(dst))
                                copied_files.append(dst)
                                copied += 1
                                if _lbl_enabled:
                                    self._label_settings.setdefault(cam, {})["index"] = _lbl.get("index", 1) + 1

                            done_steps += 1
                            self.after(0, lambda d=done_steps, pd=_prev_done, t=_auto_max if is_auto_cycle else total, c=cam: self._progress_update(pd + d, t, c))

                    elif source_mode == "window":
                        for cam in cams_local:
                            self.log(f"\n[WIN] '{cam}'")
                            if not self._auto_copy_active and _cycle_number is not None:
                                self.log("[AUTO] Stop requested — aborting current cycle.")
                                break
                            hwnd = None
                            needles = self.build_window_needles(cam)
                            for needle in needles:
                                hwnd = find_window_by_title_substring(needle)
                                if hwnd:
                                    self.log(f"[WIN] matched by: '{needle}'")
                                    break
                            try:
                                with timed(self.log, f"window screenshot ({cam})"):
                                    dst = out_dir / (f"{custom_name}.png" if (total == 1 and custom_name) else f"{run_ts}_{cam}__window.png")
                                    if not hwnd:
                                        raise RuntimeError(f"Window not found. Tried: {needles}")
                                    self.log(f"[WINSHOT] hwnd={hwnd} -> {dst.name}")
                                    take_screenshot_window_png(dst, hwnd)
                                    copied_files.append(dst)
                                    copied += 1
                            except Exception as e:
                                problems.append(f"{cam}: window capture failed ({e})")
                                self.log(f"[WIN] ERROR: {e}")

                            done_steps += 1
                            self.after(0, lambda d=done_steps, pd=_prev_done, t=_auto_max if is_auto_cycle else total, c=cam: self._progress_update(pd + d, t, c))
                    else:
                        problems.append(f"Unknown source mode: {source_mode}")

                self.log(f"\n=== COPY END | Camera Outputs = {copied} | Problems = {len(problems)} ===")
                self._last_out_dir = out_dir
                if problems:
                    self.log("=== COPY FINISHED WITH PROBLEMS ===")
                    for p in problems:
                        self.log(f" ⚠ {p}")
                else:
                    self.log(f"=== DONE — {out_dir} ===")
                # Otevři preview pokud byly zkopírovány soubory
                if copied > 0:
                    is_auto = _cycle_number is not None
                    if is_auto:
                        self.log(f"[AUTO] Cycle {_cycle_number} done — {out_dir}")
                        cycle_entry = {
                            "cycle": _cycle_number,
                            "ts": datetime.now().strftime("%H:%M:%S"),
                            "files": list(copied_files),
                        }
                        self._auto_cycle_history.append(cycle_entry)
                        self.after(0, lambda ce=cycle_entry: self._open_auto_preview(ce))
                    else:
                        def _open_preview(od=out_dir,
                                          mon=monitors_local,
                                          all_s=all_screens_local,
                                          cams=cams_local,
                                          src=source_mode,
                                          ff=list(copied_files)):
                            if not self._preview_enabled.get():
                                return
                            self._open_preview_with_callbacks(od, mon, all_s, cams, src, ff)
                        self.after(0, _open_preview)
            except Exception as e:
                _err = repr(e)
                self.log(f"[FATAL] {_err}")
                self.after(0, lambda msg=_err: messagebox.showerror("Copy error", msg))
            finally:
                self._busy = False
                self.after(0, self._progress_hide)
                self.after(0, lambda: self._set_busy(False))
                if _auto_callback is not None:
                    self.after(0, _auto_callback)

        self._set_busy(True)
        threading.Thread(target=worker, daemon=True).start()


def main():
    _set_dpi_awareness()
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()