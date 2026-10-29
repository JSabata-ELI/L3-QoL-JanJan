# ssq_core.py
"""Screenshots — everything that is not a window.

The camera tables, the Windows capture layer, the archive lookup, the auto-copy
time grid and the preview's tone/palette rendering were taken over unchanged
from the Tk program this replaced (s.py, until 16.10.2026), so the frames
picked and the pixels painted stayed the same. The preset file, the output
naming, the camera copier and the live capture are plain classes, so the Qt
window only drives them.
"""
from __future__ import annotations

import os
import sys
import re
import json
import shutil
import socket
import threading
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import subprocess
from pathlib import Path
try:
    from PIL import Image
    _PIL_OK = True
except Exception:
    _PIL_OK = False
import time
from statistics import median
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
_NORM_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_NORM_UPPER_RE = re.compile(r"[^A-Z0-9]+")

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

# Auto copy: how far back from a cycle's own moment a frame may be taken, and
# how late a cycle may still be processed. Beyond this the archive can no longer
# answer for that moment, so the cycle is dropped instead of guessed.
AUTO_BACK_WINDOW_NS = 10_000_000_000   # 10 s
AUTO_MIN_INTERVAL_S = 5

DEFAULT_DEST = r"\\hapls-share.lcs.local\scratch"
# Office/dev box: the lab scratch share is mapped to Z: here but has no such
# mapping in the lab, so the two stations need different defaults. Built from
# the real profile folder instead of a literal user name.
DEFAULT_DEST_CZOW = str(Path.home() / "Downloads" / "test screenshot")

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
        1:(2,0), 2:(3,0), 3:(0,0), 4:(1,0), 5:(0,1), 6:(1,1), 7:(3,1), 8:(2,1),
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


def _station_layout_name(station_id: str) -> str | None:
    """Layout name for a hostname, tolerant of spelling: 'L3-OPR3', 'L3-OPR03',
    'L3OPR3', 'opr3.domain' all give 'OPR-03'. None if no layout fits."""
    m = re.search(r"(VIS|OPR)\D{0,2}0*(\d+)", (station_id or "").upper())
    if not m:
        return None
    name = f"{m.group(1)}-{int(m.group(2)):02d}"
    return name if name in MONITOR_LAYOUTS else None

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

    if station not in STATION_CAMERAS:
        # Hostname spelled differently from the keys (e.g. 'L3-OPR03') → canonical key
        lay = _station_layout_name(station)
        if lay:
            kind, num = lay.split("-")
            station = f"L3-{kind}{int(num)}"
    return station in assigned_to

# ---------------- konec STATION CONFIG ----------------

IDENTIFY_MS = 2000
MODE_TOKENS = {"NF", "FF", "DF"}
TS_IN_NAME_RE = re.compile(r"_(\d{12,})\.(png|jpg|jpeg|tif|tiff|bmp)$", re.IGNORECASE)

# Matches the "YYYY-MM-DD__HH-MM-SS" timestamp produced by TS_FMT / RUN_FOLDER_FMT,
# wherever it lands in a saved filename (prefix, middle, or suffix).
_TIMESTAMP_IN_FILENAME_RE = re.compile(r"\d{4}-\d{2}-\d{2}__\d{2}-\d{2}-\d{2}")


def display_timestamp_only(name: str) -> str:
    """Reduce a filename to just its timestamp for UI display only — the camera
    name is already obvious from the tab/selection, so the preview grid caption
    shows the time and nothing else. The saved file keeps its full name.
    Falls back to the plain filename when there is no timestamp to show."""
    m = _TIMESTAMP_IN_FILENAME_RE.search(name)
    if m:
        date, _, clock = m.group(0).partition("__")
        return f"{date} {clock.replace('-', ':')}"
    m = TS_IN_NAME_RE.search(name)
    if m:  # raw CPVA name: epoch nanoseconds, same reading as the copy step
        try:
            from zoneinfo import ZoneInfo
            return datetime.fromtimestamp(int(m.group(1)) / 1_000_000_000,
                                          tz=ZoneInfo("Europe/Prague")
                                          ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return m.group(1)
    return Path(name).stem


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


def _cluster_1d(values: list[int], tol: float) -> dict[int, int]:
    """Greedy 1-D banding anchored on each band's start (prevents tolerance creep).
    Returns {value: rank} with rank 0 = smallest."""
    uniq = sorted(set(values))
    if not uniq:
        return {}
    bands = {uniq[0]: 0}
    rank = 0
    anchor = uniq[0]
    for v in uniq[1:]:
        if v - anchor > tol:
            rank += 1
            anchor = v
        bands[v] = rank
    return bands


def geometric_monitor_grid(rects: list[tuple[int, int, int, int]]) -> dict[tuple[int, int], int]:
    """Cluster monitor rects into a physical (row, col) grid.
    Returns {(row, col): os_index}. row 0 = topmost, col 0 = leftmost.

    Positions never change, so this mapping is stable even when Windows renumbers
    the monitors (the OS enumeration index / EnumDisplayMonitors order changes)."""
    if not rects:
        return {}
    med_h = median(b - t for l, t, r, b in rects)
    med_w = median(r - l for l, t, r, b in rects)
    rows = _cluster_1d([t for l, t, r, b in rects], 0.5 * med_h)
    cols = _cluster_1d([l for l, t, r, b in rects], 0.5 * med_w)
    cell_to_osidx: dict[tuple[int, int], int] = {}
    for i, (l, t, r, b) in enumerate(rects):
        cell = (rows[t], cols[l])
        while cell in cell_to_osidx:  # collision → push right, never hide a monitor
            cell = (cell[0], cell[1] + 1)
        cell_to_osidx[cell] = i
    return cell_to_osidx


def monitor_index_for_hwnd(hwnd: int) -> int | None:
    """Return the 0-based index (matching list_monitors_rects order) of the monitor
    that contains the given window, or None if not found."""
    MONITOR_DEFAULTTONEAREST = 0x00000002
    _u32 = ctypes.windll.user32
    h = ctypes.c_void_p(int(hwnd) & 0xFFFFFFFFFFFFFFFF)
    hmon = _u32.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST)
    if not hmon:
        return None

    # Collect (hmon, rect) pairs via EnumDisplayMonitors
    MONITORENUMPROC2 = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(RECT), wintypes.LPARAM,
    )
    pairs: list[tuple[int, tuple[int, int, int, int]]] = []

    def _cb2(hM, hdc, lprc, lparam):
        r = lprc.contents
        pairs.append((int(hM), (r.left, r.top, r.right, r.bottom)))
        return True

    cb2 = MONITORENUMPROC2(_cb2)
    _u32.EnumDisplayMonitors(0, 0, cb2, 0)

    target = int(hmon)
    for i, (hm, _rect) in enumerate(pairs):
        if hm == target:
            return i
    return None


def monitors_for_cameras(cam_names: list[str]) -> list[int]:
    """Return sorted list of unique 0-based monitor indices where the CSS windows
    for the given cameras currently reside. Cameras with no visible window are skipped."""
    result: set[int] = set()
    if not cam_names:
        return []
    windows = snapshot_windows()   # one desktop walk for every camera and needle
    for cam in cam_names:
        needles = _build_window_needles_static(cam)
        for needle in needles:
            hwnd = find_window_by_title_substring(needle, snapshot=windows)
            if hwnd:
                idx = monitor_index_for_hwnd(hwnd)
                if idx is not None:
                    result.add(idx)
                break
    return sorted(result)


def _build_window_needles_static(cam: str) -> list[str]:
    """Standalone version of build_window_needles (no self) for use outside the UI class."""
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
    out: list[str] = []
    seen: set[str] = set()
    for n in needles:
        n = (n or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


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


def take_screenshots_monitors_png(targets: list[tuple[Path, int | None]]) -> list[Path]:
    """Save several screenshots from ONE grab of the whole desktop.

    `ImageGrab.grab()` always captures every screen, so grabbing once per
    selected monitor captured (and PNG-encoded) the same desktop several times —
    seconds of a cycle spent on the same picture. One grab, one monitor
    enumeration, then a crop per target.
    """
    if not targets:
        return []
    try:
        from PIL import ImageGrab
    except Exception as e:
        raise RuntimeError("Pillow missing.\nInstall:\n\n  py -m pip install pillow\n") from e

    full = ImageGrab.grab(all_screens=True)
    rects = None
    written: list[Path] = []
    for dst_path, monitor_index in targets:
        img = full
        if monitor_index is not None:
            if rects is None:
                rects = list_monitors_rects()
            if not rects:
                raise RuntimeError("Failed to enumerate monitors.")
            if monitor_index < 0 or monitor_index >= len(rects):
                raise RuntimeError(f"Invalid monitor index {monitor_index+1}. Available: 1..{len(rects)}")
            l, t, r, b = rects[monitor_index]
            min_left = min(x[0] for x in rects)
            min_top = min(x[1] for x in rects)
            img = full.crop((l - min_left, t - min_top, r - min_left, b - min_top))
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst_path, "PNG")
        written.append(dst_path)
    return written


# ---------------- Window capture ----------------
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
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
    return _NORM_ALNUM_RE.sub("", (s or "").lower())

def _get_window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value or ""

# Window classes that belong to everyday desktop apps, never to the CSS camera
# viewer — a title match against one of these is almost always a false positive
# (e.g. a browser tab or Explorer window whose title happens to mention a
# camera name because a previously saved screenshot file was opened/browsed).
_EXCLUDED_WINDOW_CLASSES = {
    "chrome_widgetwin_1",       # Edge / Chrome / most Electron apps
    "cabinetwclass",            # File Explorer
    "notepad",
    "applicationframewindow",   # UWP apps (Settings, Photos, ...)
    "windows.ui.core.corewindow",
    "framework::cframe",        # Office (Word/Excel/OneNote)
    "rctrl_renwnd32",           # Outlook
    "tktoplevel",               # our own tkinter tools
    "tk",
}

def snapshot_windows() -> list[tuple[int, str, str]]:
    """Every visible, non-minimised, titled top-level window as
    (hwnd, title, normalised title) — one EnumWindows pass that several title
    searches can share instead of each walking the desktop again."""
    out: list[tuple[int, str, str]] = []

    def _cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return 1
        if user32.IsIconic(hwnd):
            return 1  # minimized — not usable as a capture target, and often a stale leftover
        title = _get_window_text(hwnd)
        if title:
            out.append((int(hwnd) & 0xFFFFFFFFFFFFFFFF, title, _norm_win_title(title)))
        return 1

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return out


def find_window_by_title_substring(substr: str, log=None,
                                   snapshot: list[tuple[int, str, str]] | None = None) -> int | None:
    if not substr:
        return None
    needle = _norm_win_title(substr)
    if not needle:
        return None

    exact_matches: list[tuple[int, str]] = []
    loose_matches: list[tuple[int, str]] = []

    for hwnd, title, norm_title in (snapshot if snapshot is not None else snapshot_windows()):
        if norm_title == needle:
            # keep scanning in case of duplicates; exact match always wins anyway
            exact_matches.append((hwnd, title))
            continue
        if needle in norm_title:
            cls = _get_window_class(hwnd).lower()
            if cls in _EXCLUDED_WINDOW_CLASSES:
                if log:
                    log(f"[WIN] ignored '{title}' (class={cls}) — not a camera window")
                continue
            loose_matches.append((hwnd, title))

    if exact_matches:
        return exact_matches[0][0]
    if loose_matches:
        # A real CSS window's title is (almost) exactly the needle. A title that
        # merely *contains* the needle (e.g. a saved screenshot filename opened in
        # another app) is longer — so the shortest candidate is the safest bet.
        loose_matches.sort(key=lambda hw: len(hw[1]))
        if log and len(loose_matches) > 1:
            log(f"[WIN] multiple candidates for '{substr}': "
                f"{[t for _, t in loose_matches]} -> picked '{loose_matches[0][1]}'")
        return loose_matches[0][0]
    return None

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

def cpva_hour_dir_for_ns(ts_ns: int) -> Path:
    """Hour folder that holds the frames of a given moment (folders are UTC)."""
    d = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
    return NETWORK_ROOT / str(d.year) / str(d.month) / str(d.day) / str(d.hour)

"""def target_cpva_hour_dir(_day_root: Path) -> Path:
    # TESTOVACÍ OVERRIDE — smaž pro produkci
    return NETWORK_ROOT / "2026" / "4" / "7" / "14" """


def norm_folder(s: str) -> str:
    return _NORM_UPPER_RE.sub("", s.upper())


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

def find_camera_folders_bulk(day_root: Path, cams: list[str], log,
                             hour_dir: Path | None = None) -> dict[str, Path]:
    if hour_dir is None:
        hour_dir = target_cpva_hour_dir(day_root)
    log(f"[CPVA] hour_dir = {hour_dir}")

    if not hour_dir.exists():
        log("[CPVA] hour_dir DOES NOT EXIST - searching previous hour...")
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
                    log(f"  [OK] {cam} -> {p.name}")
                    if not needs:
                        log(f"[CPVA] bulk scan checked={checked}, found={len(found)} (EARLY STOP)")
                        return found

        log(f"[CPVA] bulk scan checked={checked}, found={len(found)} (END)")
        return found

    except Exception as e:
        log(f"[CPVA] bulk scan ERROR: {e}")
        return found
    
def auto_tick_target_ns(t0_ns: int, n: int, interval_s: int) -> int:
    """The moment cycle n belongs to. Cycles sit on a fixed grid from the start
    of the run, so they are always exactly `interval_s` apart no matter how long
    the copying of any one of them took."""
    return t0_ns + n * interval_s * 1_000_000_000


def auto_tick_delay_ms(t0_mono: float, n: int, interval_s: int, now_mono: float) -> int:
    """Milliseconds until tick n is due — measured from the start of the run,
    never 'one interval after the previous cycle finished'."""
    due = t0_mono + n * interval_s
    return max(0, int(round((due - now_mono) * 1000)))


def auto_should_skip(age_ns: int | None, src: Path | None,
                     prev_frame: Path | None) -> bool:
    """Whether a camera is left out of a cycle.

    A frame older than the backward window no longer belongs to this cycle's
    moment. It is still kept when it is the very frame the previous cycle used —
    a camera that is standing still has to keep showing up — but a *different*
    old frame belongs to some other moment, so it is skipped.
    """
    if src is None or age_ns is None:
        return True
    return age_ns > AUTO_BACK_WINDOW_NS and src != prev_frame


def fmt_span_ms(ms: float) -> str:
    """Human readable time span: ms below a second, then s / m s / h m s."""
    ms = abs(float(ms))
    if ms < 1000:
        return f"{ms:.1f}ms"
    total_s = ms / 1000.0
    h, rem = divmod(int(total_s), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{int(total_s * 10) / 10:.1f}s"


def scan_camera_dir(cam_dir: Path) -> list[tuple[int, str]]:
    """Listing of one camera folder as (ts_ns, filename), sorted by timestamp."""
    entries: list[tuple[int, str]] = []
    with os.scandir(cam_dir) as it:
        for entry in it:
            if not entry.is_file(follow_symlinks=False):
                continue
            m = TS_IN_NAME_RE.search(entry.name)
            if not m:
                continue
            entries.append((int(m.group(1)), entry.name))
    entries.sort()
    return entries


def find_frame_at_or_before(cam_dir: Path, t_target_ns: int, log=None,
                            _cache: dict | None = None, _cache_time: dict | None = None,
                            _cache_ttl: float = 5.0,
                            _cache_lock: "threading.Lock | None" = None,
                            newest: bool = False
                            ) -> tuple[Path | None, int | None]:
    """Newest archived frame whose own timestamp is at or before `t_target_ns`.

    A cycle asks for the picture that existed at its recorded moment, so a frame
    from after that moment is never an answer — only the newest one at or before
    it. Returns (path, age_ns), age = how far the frame sits behind the target;
    (None, None) when the folder holds nothing that old.

    `newest=True` ignores the target and takes whatever is newest in the folder
    (the age is still reported against the target). That is what the manual Copy
    and the first cycle of an auto run do: they have nothing to compare against
    yet, and the first cycle is what measures how far the archive's clock runs
    behind this computer's.

    A cached listing whose newest entry is already older than the target cannot
    answer the question (the frame we want may have been written after the scan),
    so it is rescanned even while the TTL still holds.
    """
    t0 = time.perf_counter()
    cam_key = str(cam_dir)
    now = time.time()

    entries: list[tuple[int, str]] | None = None
    if _cache is not None and _cache_time is not None:
        if _cache_lock is not None:
            _cache_lock.acquire()
        try:
            cached = _cache.get(cam_key)
            fresh = (now - _cache_time.get(cam_key, 0)) < _cache_ttl
            # Usable only if it is fresh AND already reaches past the target.
            if cached and fresh and (newest or cached[-1][0] >= t_target_ns):
                entries = cached
        finally:
            if _cache_lock is not None:
                _cache_lock.release()

    if entries is None:
        try:
            entries = scan_camera_dir(cam_dir)
        except Exception as e:
            if log:
                log(f"[PICK] {cam_dir.name}: listing failed: {e}")
            return None, None
        if _cache is not None and _cache_time is not None:
            if _cache_lock is not None:
                _cache_lock.acquire()
            try:
                _cache[cam_key] = entries
                _cache_time[cam_key] = now
            finally:
                if _cache_lock is not None:
                    _cache_lock.release()

    if not entries:
        return None, None

    idx = len(entries) - 1 if newest else bisect_right(entries, (t_target_ns, "￿")) - 1
    if idx < 0:
        if log:
            oldest = datetime.fromtimestamp(entries[0][0] / 1_000_000_000).strftime("%H:%M:%S")
            log(f"[PICK] {cam_dir.name}: nothing at or before target (oldest {oldest})")
        return None, None

    best_ts, best_name = entries[idx]
    if log:
        log(f"[PICK] {cam_dir.name}: {best_name} "
            f"(age {fmt_span_ms((t_target_ns - best_ts) / 1_000_000)}, "
            f"lookup {time.perf_counter() - t0:.2f}s)")
    return cam_dir / best_name, t_target_ns - best_ts


# ---------------- README helpers ----------------
def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

def norm_query(s: str) -> str:
    return _NORM_ALNUM_RE.sub("", (s or "").lower())

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


# ---------------- Preview rendering (one path for grid, popup, zoom, Save) ----------------
class Tone:
    """The preview's tone curve and palettes — s.py's PreviewWindow
    methods without the window, so the Qt preview renders the same pixels.
    """

    _THUMB = 180        # px thumbnail at zoom = 1.0
    _MIN_THUMB = 60     # never render a thumbnail smaller than this
    _CELL_PAD = 24      # px consumed per cell by padding + border
    _FILL_SLACK = 1.3   # how far a thumbnail may grow past the slider size to fill a row

    # "Original" keeps the picture in the colours it arrived in (a colour camera
    # stays in colour); "Grayscale" deliberately drops them. Everything after
    # that is a false-colour map over the grey values.
    # The same list, in the same order, as Image Tools (is_t.GRADIENTS), with
    # "Original" in the place of its "Default".
    _PALETTES = ["Original", "Grayscale", "Gradient", "Binary", "False Colors",
                 "Rainbow", "Hot", "Black and White", "Viridis", "Plasma",
                 "Inferno", "Jet", "Turbo"]
    # Binary and False Colors are copies of the NI viewer's palettes, measured in
    # Image Tools (see its is_t.py / STRUCTURE.md). Their colours are pinned to the
    # camera's raw counts, so — as in Image Tools — they ignore Contrast, Brightness,
    # Gamma and both Autos: any stretch would paint a colour that means a different
    # value than it does in the NI window.
    _NI_PALETTES = frozenset({"Binary", "False Colors"})
    # Binary changes colour every 16 counts, so the picture may only be shrunk by
    # picking whole pixels: the average of two cycle colours is a third, unrelated one.
    _PIXEL_EXACT_PALETTES = frozenset({"Binary"})

    _LUTS: dict = {}  # lazy-built

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
            # NI Vision "Rainbow": blue to red with a wide green middle, 0 black,
            # 255 white (same stops as Image Tools).
            "Rainbow":  [(0.00,(0,0,0)),(0.04,(0,0,200)),(0.14,(0,40,255)),(0.26,(0,150,255)),
                         (0.36,(0,230,180)),(0.46,(0,255,80)),(0.56,(90,255,0)),(0.66,(190,255,0)),
                         (0.76,(255,220,0)),(0.86,(255,120,0)),(0.94,(255,0,0)),(1.00,(255,255,255))],
            # Red / yellow lowered, pale yellow added, white kept at the top — as in
            # Image Tools.
            "Hot":      [(0,(0,0,0)),(0.27,(255,0,0)),(0.53,(255,255,0)),(0.78,(255,255,190)),(1,(255,255,255))],
            "Viridis":  [(0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),(0.75,(94,201,98)),(1,(253,231,37))],
            "Plasma":   [(0,(13,8,135)),(0.25,(126,3,168)),(0.5,(204,71,120)),(0.75,(248,149,64)),(1,(240,249,33))],
            "Inferno":  [(0,(0,0,4)),(0.25,(87,16,110)),(0.5,(188,55,84)),(0.75,(249,142,9)),(1,(252,255,164))],
            "Jet":      [(0,(0,0,128)),(0.125,(0,0,255)),(0.375,(0,255,255)),(0.625,(255,255,0)),(0.875,(255,0,0)),(1,(128,0,0))],
            "Turbo":    [(0,(48,18,59)),(0.2,(70,131,193)),(0.4,(48,210,142)),(0.6,(194,228,59)),(0.8,(244,117,22)),(1,(122,4,3))],
        }
        if name in tables:
            cls._LUTS[name] = lut(tables[name])
        elif name == "Binary":
            cls._LUTS[name] = cls._ni_binary_lut()
        elif name == "False Colors":
            cls._LUTS[name] = cls._ni_false_colors_lut()
        elif name == "Black and White":
            a = np.zeros((256, 3), dtype=np.uint8)
            a[128:] = 255
            cls._LUTS[name] = a
        else:
            cls._LUTS[name] = None  # Grayscale = None

    # NI Vision "Binary" (Image Tools is_t._NI_BINARY_CYCLE): 256 entries indexed by
    # raw counts >> 4 — entry 0 black, 255 (saturated) white, 1..254 this 15-colour
    # cycle, so a new colour every 16 counts.
    _NI_BINARY_CYCLE = [
        (255,0,0), (0,255,0), (0,0,255), (255,255,0), (255,0,255), (0,255,255),
        (255,127,0), (255,0,127), (127,255,0), (127,0,255), (0,127,255), (0,255,127),
        (255,127,127), (127,255,127), (127,127,255),
    ]

    @classmethod
    def _ni_binary_lut(cls):
        import numpy as np
        i = np.arange(256)
        table = np.array([(0, 0, 0)] + cls._NI_BINARY_CYCLE, dtype=np.uint8)
        a = table[np.where(i == 0, 0, (i - 1) % 15 + 1)]
        a[255] = (255, 255, 255)
        return a

    @staticmethod
    def _ni_false_colors_lut():
        """NI Vision "False Colors" — the formula measured in Image Tools
        (is_t._make_ni_false_colors_lut): hue one full turn backwards, a 12-entry
        lightness triangle (the contour bands), fades at both ends, entry 0 grey,
        252..254 greys, 255 white."""
        import numpy as np
        i = np.arange(256, dtype=np.float64)
        hue = (321.0 - 1.3967 * i) % 360.0
        light = 0.425 + 0.025 * (6.0 - np.abs(i % 12.0 - 6.0))
        light -= 0.006 * np.clip(50.0 - i, 0.0, None)
        light += (1.0 / 120.0) * np.clip(i - 220.0, 0.0, None)
        light = np.clip(light, 0.0, 1.0)
        chroma = 1.0 - np.abs(2.0 * light - 1.0)
        hp = hue / 60.0
        x = chroma * (1.0 - np.abs(hp % 2.0 - 1.0))
        z = np.zeros_like(i)
        seg = hp.astype(np.int64) % 6
        r = np.choose(seg, [chroma, x, z, z, x, chroma])
        g = np.choose(seg, [x, chroma, chroma, x, z, z])
        b = np.choose(seg, [z, z, x, chroma, chroma, x])
        m = light - chroma / 2.0
        a = np.rint(np.stack([r + m, g + m, b + m], 1) * 255.0).clip(0, 255).astype(np.uint8)
        a[0] = (58, 58, 58)
        for k in (252, 253, 254):
            a[k] = (k, k, k)
        a[255] = (255, 255, 255)
        return a

    @staticmethod
    def _ni_entry(img):
        """The NI palette entry (0..255) of every pixel: raw camera counts >> 4.

        An archive frame is 16-bit, stored as raw_counts * 65535 / (2**bits - 1)
        with `bits` the bracket of the frame's own peak (`MaxValue` tEXt), and the
        cameras are 12-bit, so raw counts come back exactly and the entry is the
        same for the same intensity on every frame. A 16-bit file without
        `MaxValue` is taken as already on the full 16-bit scale (stored >> 8). An
        8-bit picture (a screen capture) has no counts left; its grey level is
        the best stand-in."""
        import math
        import numpy as np
        if img.mode in ("I", "I;16", "I;16B", "F"):
            arr = np.asarray(img, dtype=np.float64)
            try:
                mv = float(img.info.get("MaxValue", ""))
            except (TypeError, ValueError):
                mv = 0.0
            if mv > 0:
                top = 2 ** max(1, math.ceil(math.log2(mv + 1))) - 1
                raw = np.rint(arr * (top / 65535.0))
                return (np.clip(raw, 0, 4095) / 16).astype(np.uint8)
            return (np.clip(arr, 0, 65535) / 256).astype(np.uint8)
        return np.array(img.convert("L"))

    @classmethod
    def _resample_for(cls, palette: str):
        """How to shrink or enlarge a picture before `palette` is applied."""
        return Image.NEAREST if palette in cls._PIXEL_EXACT_PALETTES else Image.LANCZOS

    # ── image processing ──────────────────────────────────────────
    # Terminology used consistently everywhere below — never mix these two up:
    #   CONTRAST   = multiplicative gain (spreads values apart / squeezes them
    #                together). "Auto" = percentile auto-levels stretch.
    #   BRIGHTNESS = additive offset (shifts every value up or down, gain
    #                untouched). "Auto" = shift so the top percentile hits 255.
    #   GAMMA      = midtone curve (the ends stay put). Above 1 lifts the
    #                midtones, below 1 deepens them.
    # All three are built into ONE 256-entry tone curve by `_tone_lut`, which is
    # what lets the same settings apply to a grayscale image and to each channel
    # of a colour one.
    @staticmethod
    def _contrast_from_gain(gain: float) -> int:
        """Inverse of the Contrast gain curve: gain → slider value in
        [-127, 127]. Used to park the Contrast slider where auto contrast landed."""
        if gain <= 0:
            return 0
        c = 32893.0 * (gain - 1.0) / (259.0 + 127.0 * gain)
        return int(round(max(-127.0, min(127.0, c))))

    @staticmethod
    def _gamma_curve(lut, gamma: float):
        """Midtone curve on an already-built LUT. Above 1 lifts the midtones,
        below 1 deepens them; the black and white ends stay put."""
        import numpy as np
        if gamma is None or abs(float(gamma) - 1.0) < 1e-3:
            return lut
        g = max(0.05, float(gamma))
        return 255.0 * np.power(np.clip(lut, 0, 255) / 255.0, 1.0 / g)

    @classmethod
    def _tone_lut(cls, stats_arr, auto_contrast: bool, contrast: int,
                  auto_bright: bool, brightness: int, gamma: float = 1.0):
        """Build the 256-entry tone curve for the current settings.

        A curve rather than a pixel-by-pixel transform, because the same curve
        then applies to a grayscale image AND to each channel of a colour one —
        which is what lets a colour camera keep its colours while its contrast,
        brightness and gamma are adjusted. `stats_arr` is the 8-bit grey (or
        luma) array the auto modes measure.

        Order: contrast (gain) → gamma (midtones) → brightness (offset).
        Auto contrast overrides the manual Contrast slider; Auto brightness
        overrides the manual Brightness slider (same rule as Image Tools).

        Returns (lut, applied) where applied = {"contrast", "brightness"} are the
        slider-equivalent values actually used, so the UI sliders can follow the
        auto modes.
        """
        import numpy as np
        applied = {"contrast": int(contrast), "brightness": int(brightness)}
        lut = np.arange(256, dtype=np.float32)

        if auto_contrast:
            lo, hi = np.percentile(stats_arr, [0.1, 99.9])
            if hi > lo + 2:
                lut = (lut - float(lo)) / (float(hi) - float(lo)) * 255.0
                applied["contrast"] = cls._contrast_from_gain(255.0 / float(hi - lo))
            else:
                applied["contrast"] = 0
        elif contrast:
            c = float(contrast)
            factor = (259.0 * (c + 127.0)) / (127.0 * (259.0 - c))
            lut = factor * (lut - 128.0) + 128.0

        lut = cls._gamma_curve(lut, gamma)

        if auto_bright:
            # Measured on the image as the curve so far leaves it.
            so_far = np.clip(lut, 0, 255).astype(np.uint8)[stats_arr]
            offset = int(round(255 - float(np.percentile(so_far, 99.5))))
            offset = max(-255, min(255, offset))
            lut = lut + offset
            applied["brightness"] = offset
        elif brightness:
            lut = lut + float(brightness)

        return np.clip(lut, 0, 255).astype(np.uint8), applied

    @staticmethod
    def _stats_gray(img):
        """The 8-bit grey the auto modes measure, whatever the source mode is."""
        import numpy as np
        if img.mode in ("I", "I;16", "I;16B", "F"):
            arr = np.array(img)
            mn, mx = float(arr.min()), float(arr.max())
            if mx > mn:
                return ((arr - mn) / (mx - mn) * 255).astype(np.uint8)
            return np.zeros(arr.shape, dtype=np.uint8)
        return np.array(img.convert("L"))

    @classmethod
    def _apply_bc(cls, arr, auto_contrast: bool, contrast: int,
                  auto_bright: bool, brightness: int, gamma: float = 1.0):
        """Grayscale-only shorthand for `_tone_lut` (kept for tests)."""
        lut, applied = cls._tone_lut(arr, auto_contrast, contrast,
                                     auto_bright, brightness, gamma)
        return lut[arr], applied

    @staticmethod
    def _fit_to_box(img, size: int, resample=None):
        """Scale the WHOLE frame so its long side is exactly `size` — never a crop,
        and unlike Image.thumbnail() this also enlarges sources smaller than the
        box, so the Zoom slider grows every thumbnail by the same factor."""
        long_side = max(img.width, img.height)
        if long_side == size or long_side == 0:
            return img
        scale = size / long_side
        box = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        try:
            return img.resize(box, Image.LANCZOS if resample is None else resample)
        except Exception:
            # 1-bit / palette / I;16 sources reject the reducing filters
            return img.resize(box, Image.NEAREST)

    @classmethod
    def _render_tuned(cls, img, palette: str, auto_contrast: bool, contrast: int,
                      auto_bright: bool, brightness: int, gamma: float = 1.0):
        """Apply the viewing settings to one image → (RGB image, applied).

        The single path used by the grid, the hover popup, the zoom window and
        Save, so all four agree. With the "Original" palette a colour picture
        stays in its own colours — the tone curve is applied to each channel —
        instead of being flattened to grey the moment a slider or Auto is
        touched, which is what used to make Auto contrast look like a palette
        change.
        """
        import numpy as np
        if palette in cls._NI_PALETTES:
            # View-only, as in Image Tools: no tone curve, the colour is the value.
            if palette not in cls._LUTS:
                cls._build_lut(palette)
            applied = {"contrast": int(contrast), "brightness": int(brightness)}
            return Image.fromarray(cls._LUTS[palette][cls._ni_entry(img)], "RGB"), applied
        stats = cls._stats_gray(img)
        lut, applied = cls._tone_lut(stats, auto_contrast, contrast,
                                     auto_bright, brightness, gamma)

        if palette == "Original" and img.mode in ("RGB", "RGBA", "P", "LA"):
            arr_rgb = np.array(img.convert("RGB"))
            return Image.fromarray(lut[arr_rgb], "RGB"), applied

        gray = lut[stats]
        if palette not in ("Original", "Grayscale"):
            if palette not in cls._LUTS:
                cls._build_lut(palette)
            cmap = cls._LUTS.get(palette)
            if cmap is not None:
                return Image.fromarray(cmap[gray], "RGB"), applied
        return Image.fromarray(gray, "L").convert("RGB"), applied


# ---------------- Monitor device names ----------------
class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


def list_monitor_devices() -> list[str]:
    """Device name of every monitor ('\\\\.\\DISPLAY1', …) in the order of
    list_monitors_rects. Qt names its screens the same way, which is how a
    monitor number shown here is matched to the Qt screen it belongs to."""
    user32_ = ctypes.windll.user32
    names: list[str] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(RECT), wintypes.LPARAM)

    def _cb(hmon, hdc, lprc, lparam):
        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        try:
            ok = user32_.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi))
        except Exception:
            ok = False
        names.append(mi.szDevice if ok else "")
        return True

    user32_.EnumDisplayMonitors(0, 0, proc(_cb), 0)
    return names


# ---------------- Station ----------------
CZOW_STATION = "CZOW-NB2DLRL24"   # office box: one monitor, emulates the walls
LAYOUT_TO_STATION = {
    "VIS-01": "L3-VIS01",
    "VIS-02": "L3-VIS02",
    "OPR-01": "L3-OPR1",
    "OPR-02": "L3-OPR2",
    "OPR-03": "L3-OPR3",
}


def default_dest(station: str) -> str:
    return DEFAULT_DEST_CZOW if station == CZOW_STATION else DEFAULT_DEST


def cam_available(cam: str, source_mode: str, station: str, layout: str) -> bool:
    """Whether a camera can be picked: every camera through the archive; in
    window mode only the ones whose CSS window lives on this station."""
    if source_mode != "window":
        return True
    if cam in _L3BT_CAMS:
        return False
    effective = station
    if station == CZOW_STATION:
        effective = LAYOUT_TO_STATION.get(layout, station)
    return _cam_available_here(cam, effective)


def cam_tooltip(cam: str, available: bool) -> str:
    if not available:
        stations = _cam_available_on_stations(cam)
        where = ", ".join(stations) if stations else "other station"
        return f"Available on: {where}"
    v = CAM_INFO.get(cam) or CAM_INFO.get(cpva_label(cam))
    return str(v) if v else ""


# ---------------- Output naming ----------------
def sanitize_folder_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if name in {"", ".", ".."}:
        return ""
    return name


def is_unc_path(s: str) -> bool:
    s = (s or "").strip()
    return s.startswith("\\\\") or s.startswith("//")


def planned_output_count(monitors_sel: list[int] | None, all_screens: bool,
                         cams_sel: list[str]) -> int:
    """Monitors + cameras. One output means the name field is a FILE name,
    more than one a FOLDER name."""
    if monitors_sel is None and not all_screens:
        shots = 0
    elif monitors_sel is None:
        shots = 1
    else:
        shots = len(monitors_sel)
    return shots + len(cams_sel)


def ensure_output_path(dest: Path, run_name: str, total_outputs: int) -> Path:
    """dest/<name> for a multi-output run with a name; dest itself otherwise."""
    if total_outputs <= 1:
        return dest
    user_name = sanitize_folder_name(run_name)
    if user_name:
        run_dir = dest / user_name
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
    return dest


def monitor_targets(out_dir: Path, run_ts: str, monitors: list[int] | None,
                    all_screens: bool, custom_name: str,
                    total: int) -> list[tuple[Path, int | None]]:
    """Where each monitor shot of one run goes (None = all screens in one)."""
    targets: list[tuple[Path, int | None]] = []
    if monitors is None and all_screens:
        fname = f"{custom_name}.png" if (total == 1 and custom_name) else f"{run_ts}_all.png"
        targets.append((out_dir / fname, None))
    elif monitors is not None:
        for mi in monitors:
            fname = (f"{custom_name}.png" if (total == 1 and custom_name)
                     else f"{run_ts}_monitor{mi+1}.png")
            targets.append((out_dir / fname, mi))
    return targets


def prague_file_ts(src: Path) -> str:
    """A frame's own timestamp in Prague time, for its file name."""
    try:
        m = TS_IN_NAME_RE.search(src.name)
        if m:
            from zoneinfo import ZoneInfo
            return datetime.fromtimestamp(
                int(m.group(1)) / 1_000_000_000, tz=ZoneInfo("Europe/Prague")
            ).strftime(TS_FMT)
    except Exception:
        pass
    return datetime.fromtimestamp(safe_mtime(src)).strftime(TS_FMT)


def camera_file_name(cam: str, src: Path, custom_name: str, total: int,
                     label: dict | None) -> str:
    """`<cam>__<time>.<ext>` (or the user's name for a single output), with
    the camera's label token joined by `__` in front or behind."""
    lbl = label or {}
    text = lbl.get("text", "").strip()
    idx = f"{lbl.get('index', 1):02d}" if lbl.get("use_index", False) else ""
    token = f"{text}_{idx}" if (text and idx) else (text or idx)
    base = (f"{custom_name}{src.suffix.lower()}" if (total == 1 and custom_name)
            else f"{cam}__{prague_file_ts(src)}{src.suffix.lower()}")
    if lbl.get("enabled", False) and token:
        stem, ext = Path(base).stem, Path(base).suffix
        if lbl.get("mode", "prefix") == "prefix":
            base = f"{token}__{stem}{ext}"
        else:
            base = f"{stem}__{token}{ext}"
    return base


# ---------------- Presets ----------------
def presets_path() -> Path:
    """custom_presets.json beside the exe / this script — the same file and
    format (v2) the Tk program wrote, so an installation keeps its list."""
    return get_app_dir() / "custom_presets.json"


def _clean_preset(data: dict) -> dict:
    return {"cams": list(data.get("cams") or []),
            "mons": list(data["mons"]) if data.get("mons") else None}


def factory_presets() -> dict[str, dict]:
    """A private copy of the built-in presets (PRESETS shares its lists with CAM_CATEGORIES)."""
    return {name: _clean_preset(data) for name, data in PRESETS.items()}


def load_presets(path: Path | None = None) -> dict[str, dict]:
    """The whole preset list from disk; the built-ins when there is none.

    v2 file: {"version": 2, "presets": {name: {...}}} — the key order is the list order.
    v1 file: a flat {name: {...}} of custom presets only; it is merged over the built-ins.
    """
    path = path or presets_path()
    if not path.exists():
        return factory_presets()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return factory_presets()
    if not isinstance(raw, dict):
        return factory_presets()
    if isinstance(raw.get("presets"), dict):          # v2
        out = {name: _clean_preset(data) for name, data in raw["presets"].items()
               if isinstance(data, dict)}
        return out or factory_presets()
    out = factory_presets()                            # v1 → migrate
    for name, data in raw.items():
        if isinstance(data, dict):
            out[name] = _clean_preset(data)
    return out


def save_presets(presets: dict[str, dict], path: Path | None = None) -> str | None:
    """Write the list; returns the error text, or None when it was written."""
    try:
        (path or presets_path()).write_text(
            json.dumps({"version": 2, "presets": presets}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return None
    except Exception as e:
        return str(e)


# ---------------- Program icon ----------------
def icon_file() -> Path | None:
    """icon.ico: beside the exe, inside the bundle, or beside this script."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "icon.ico")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "icon.ico")
    cands.append(Path(__file__).resolve().parent / "icon.ico")
    for p in cands:
        if p.exists():
            return p
    return None


# The taskbar identity (_icon_app_id, _nudge_taskbar_icon) lives in main.py,
# where Dev Tools/testing/test_icon_app_id.py looks for it.


# ---------------- Camera copying (Copy button and every auto cycle) ----------------
class ArchiveCopier:
    """Copies camera frames out of the archive (or captures their windows).

    Holds what lives longer than one copy: the listing cache of every camera
    folder in use (re-read every 3 s in the background, so a Copy never pays
    for a cold share listing), the camera → folder mapping of the current hour,
    the per-camera label settings and the frame each camera delivered last
    (the auto skip rule compares against it). Every method that copies runs on
    a worker thread; `log` must be callable from any thread.
    """

    CACHE_TTL_S = 5.0

    def __init__(self, log):
        self.log = log
        self.cam_dir_cache: dict[str, list[tuple[int, str]]] = {}
        self.cam_dir_cache_time: dict[str, float] = {}
        self.cam_dir_cache_used: dict[str, float] = {}
        self.cam_dir_cache_lock = threading.Lock()
        self._cam_folder_cache: tuple | None = None
        # Session state of the Labels dialog: cam -> {enabled, mode, text,
        # use_index, index}.
        self.label_settings: dict[str, dict] = {}
        self.last_frame: dict[str, Path] = {}
        self._label_lock = threading.Lock()

    # ── listing cache ────────────────────────────────────────────
    def start_cache_refresh_worker(self):
        def _worker():
            while True:
                time.sleep(3)
                try:
                    # Only folders a copy has used in the last hour: the rest
                    # are past hours nobody reads any more, and re-listing them
                    # on the share every 3 s was pure load.
                    now = time.time()
                    with self.cam_dir_cache_lock:
                        folders = list(self.cam_dir_cache.keys())
                        stale = [k for k in folders
                                 if now - self.cam_dir_cache_used.setdefault(k, now) > 3600]
                        for k in stale:
                            self.cam_dir_cache.pop(k, None)
                            self.cam_dir_cache_time.pop(k, None)
                            self.cam_dir_cache_used.pop(k, None)
                    for cam_key in [k for k in folders if k not in stale]:
                        try:
                            entries = scan_camera_dir(Path(cam_key))
                            with self.cam_dir_cache_lock:
                                self.cam_dir_cache[cam_key] = entries
                                self.cam_dir_cache_time[cam_key] = time.time()
                        except Exception:
                            pass
                except Exception:
                    pass
        threading.Thread(target=_worker, daemon=True, name="cam-dir-refresh").start()

    def resolve_cam_folders(self, target_ns: int, cams: list[str]) -> dict[str, Path]:
        """Camera folder per camera for the moment `target_ns`, cached per hour.

        The mapping only changes when the archive rolls over to a new hour
        folder, so listing the whole hour once per cycle was pure waiting.
        """
        hour_dir = cpva_hour_dir_for_ns(target_ns)
        key = (str(hour_dir), tuple(cams))
        cached = self._cam_folder_cache
        if cached and cached[0] == key:
            return cached[1]
        found = find_camera_folders_bulk(today_day_root(), cams, self.log, hour_dir=hour_dir)
        if not found:
            # Just after the hour rolls over the new folder may not exist yet.
            prev = cpva_hour_dir_for_ns(target_ns - 3_600_000_000_000)
            found = find_camera_folders_bulk(today_day_root(), cams, self.log, hour_dir=prev)
        if found:
            self._cam_folder_cache = (key, found)
        return found

    def prime(self, cams: list[str]):
        """Read every selected camera folder once, before the first cycle.

        The 3 s refresher only re-reads folders the cache already knows, so
        without this the first cycle pays one cold share listing per camera.
        """
        try:
            folders = self.resolve_cam_folders(time.time_ns(), cams)
            if not folders:
                return

            def _one(item):
                _cam, folder = item
                try:
                    entries = scan_camera_dir(folder)
                except Exception:
                    return
                with self.cam_dir_cache_lock:
                    self.cam_dir_cache[str(folder)] = entries
                    self.cam_dir_cache_time[str(folder)] = time.time()
            with ThreadPoolExecutor(max_workers=min(8, max(1, len(folders)))) as pool:
                list(pool.map(_one, folders.items()))
            self.log(f"[AUTO] Camera listings primed ({len(folders)}).")
        except Exception as e:
            self.log(f"[AUTO] Priming failed: {e}")

    # ── labels ───────────────────────────────────────────────────
    def label_for(self, cam: str) -> dict:
        with self._label_lock:
            return dict(self.label_settings.get(cam, {}))

    def bump_label_index(self, cam: str):
        with self._label_lock:
            lbl = self.label_settings.setdefault(cam, {})
            lbl["index"] = lbl.get("index", 1) + 1

    # ── copying ──────────────────────────────────────────────────
    def copy_one_camera(self, cam: str, cam_folder: Path, target_ns: int,
                        out_dir: Path, custom_name: str, total: int,
                        prev_frame: Path | None, cycle: int | None,
                        newest: bool = False) -> dict:
        """Resolve and copy one camera's frame. Runs on a worker thread."""
        out: dict = {"cam": cam, "dst": None, "age_ns": None,
                     "problem": None, "src": None, "label_bump": False}
        with self.cam_dir_cache_lock:
            self.cam_dir_cache_used[str(cam_folder)] = time.time()   # keeps it refreshed
        src, age_ns = find_frame_at_or_before(
            cam_folder, target_ns, None,
            _cache=self.cam_dir_cache,
            _cache_time=self.cam_dir_cache_time,
            _cache_ttl=self.CACHE_TTL_S,
            _cache_lock=self.cam_dir_cache_lock,
            newest=newest,
        )
        if src is None:
            out["problem"] = f"{cam}: no frame at or before the requested moment."
            return out
        out["src"] = src
        out["age_ns"] = age_ns

        if cycle is not None and not newest and auto_should_skip(age_ns, src, prev_frame):
            out["problem"] = (f"{cam}: skipped — newest frame is "
                              f"{fmt_span_ms(age_ns / 1_000_000)} old.")
            return out

        label = self.label_for(cam)
        dst = out_dir / camera_file_name(cam, src, custom_name, total, label)
        try:
            shutil.copy2(str(src), str(dst))
        except Exception as e:
            out["problem"] = f"{cam}: copy failed ({e})"
            return out
        if not dst.exists():
            out["problem"] = f"{cam}: copy reported OK but file is missing at {dst}"
            return out
        out["dst"] = dst
        out["label_bump"] = bool(label.get("enabled", False))
        return out

    def copy_cameras(self, cams: list[str], source_mode: str, target_ns: int,
                     out_dir: Path, custom_name: str, total: int,
                     on_file=None, cycle: int | None = None,
                     newest: bool = False, keep_going=None) -> dict:
        """Copy every selected camera for one moment.

        CPVA cameras are done in parallel: one share listing and one file copy
        per camera, done one after the other, is what spread the frames of a
        single cycle over ten seconds. `on_file(path, age_ns)` fires per copied
        file, in camera order. `keep_going()` (window mode) stops a run that
        was switched off half way.
        """
        copied: list[Path] = []
        problems: list[str] = []
        ages: dict[str, int | None] = {}

        if source_mode == "window":
            snapshot = snapshot_windows()
            for cam in cams:
                if keep_going is not None and not keep_going():
                    break
                hwnd = None
                needles = _build_window_needles_static(cam)
                for needle in needles:
                    hwnd = find_window_by_title_substring(needle, log=self.log,
                                                          snapshot=snapshot)
                    if hwnd:
                        break
                stamp = datetime.fromtimestamp(target_ns / 1_000_000_000).strftime(RUN_FOLDER_FMT)
                dst = out_dir / (f"{custom_name}.png" if (total == 1 and custom_name)
                                 else f"{stamp}_{cam}__window.png")
                try:
                    if not hwnd:
                        raise RuntimeError(f"Window not found. Tried: {needles}")
                    take_screenshot_window_png(dst, hwnd)
                    copied.append(dst)
                    ages[cam] = 0
                    if on_file:
                        on_file(dst, None)
                except Exception as e:
                    problems.append(f"{cam}: window capture failed ({e})")
            return {"copied": copied, "problems": problems, "ages": ages}

        if source_mode != "cpva":
            return {"copied": copied, "problems": [f"Unknown source mode: {source_mode}"],
                    "ages": ages}

        known = [c for c in cams if is_known_camera(c)]
        for cam in cams:
            if cam not in known:
                problems.append(f"{cam}: I don't know this camera.")
        cam_to_folder = self.resolve_cam_folders(target_ns, known) if known else {}

        jobs = []
        for cam in known:
            folder = cam_to_folder.get(cam)
            if not folder:
                problems.append(f"{cam}: not found in the archive hour folder.")
                continue
            jobs.append((cam, folder))
        if not jobs:
            return {"copied": copied, "problems": problems, "ages": ages}

        def _one(job):
            cam, folder = job
            try:
                return self.copy_one_camera(
                    cam, folder, target_ns, out_dir, custom_name, total,
                    self.last_frame.get(cam), cycle, newest)
            except Exception as e:
                return {"cam": cam, "dst": None, "age_ns": None, "src": None,
                        "label_bump": False, "problem": f"{cam}: {e!r}"}

        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            results = list(pool.map(_one, jobs))

        # Merged back in camera order, so the file list, the problems and the
        # label indices do not depend on which thread finished first.
        by_cam = {r["cam"]: r for r in results}
        for cam, _folder in jobs:
            r = by_cam.get(cam)
            if r is None:
                continue
            ages[cam] = r["age_ns"]
            if r["src"] is not None and cycle is not None:
                self.last_frame[cam] = r["src"]
            if r["problem"]:
                problems.append(r["problem"])
            if r["dst"] is not None:
                copied.append(r["dst"])
                if r["label_bump"]:
                    self.bump_label_index(cam)
                if on_file:
                    on_file(r["dst"], r["age_ns"])
        return {"copied": copied, "problems": problems, "ages": ages}


# ---------------- Live capture ----------------
class LiveCapture:
    """Every new frame of the chosen cameras, copied as it appears.

    One thread polls the camera folders every 80 ms, a second one copies the
    queue, so a slow copy never makes the poll miss a frame. `on_count(n)` is
    called from the copy thread after each saved file.
    """

    POLL_S = 0.08

    def __init__(self, log, on_count=None):
        self.log = log
        self.on_count = on_count
        self.active = False
        self.files: list[Path] = []
        self._files_lock = threading.Lock()

    def start(self, cams: list[str], live_dir: Path):
        self.active = True
        with self._files_lock:
            self.files = []
        last_seen: dict[str, int] = {}
        copy_queue: list[tuple[Path, Path]] = []
        copy_lock = threading.Lock()

        def _copy_worker():
            while self.active or copy_queue:
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
                    with self._files_lock:
                        self.files.append(dst)
                        count = len(self.files)
                    if self.on_count:
                        self.on_count(count)
                    self.log(f"[LIVE] saved {dst.name}")
                except Exception as e:
                    self.log(f"[LIVE] copy error {src.name}: {e}")

        def _poll_worker():
            cam_to_folder = find_camera_folders_bulk(today_day_root(), cams, self.log)
            # Start from what is there now: only frames newer than this count.
            for cam in cams:
                folder = cam_to_folder.get(cam)
                if not folder:
                    self.log(f"[LIVE] init: no folder for {cam} — this camera is skipped")
                    continue
                try:
                    entries = scan_camera_dir(folder)
                    last_seen[cam] = entries[-1][0] if entries else 0
                    self.log(f"[LIVE] init {cam}: last_seen={last_seen[cam]}, {len(entries)} files")
                except Exception as e:
                    self.log(f"[LIVE] init {cam} error: {e}")

            while self.active:
                for cam in cams:
                    folder = cam_to_folder.get(cam)
                    if not folder:
                        continue   # already reported once at init
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
                                    # Not initialised: take the current newest, copy nothing.
                                    last_seen[cam] = ts_ns
                                    continue
                                if ts_ns > prev:
                                    last_seen[cam] = ts_ns
                                    src = folder / entry.name
                                    dst = live_dir / f"{cam}__{prague_file_ts(src)}{src.suffix.lower()}"
                                    with copy_lock:
                                        copy_queue.append((src, dst))
                    except Exception as e:
                        self.log(f"[LIVE] poll error {cam}: {e}")
                time.sleep(self.POLL_S)

        threading.Thread(target=_copy_worker, daemon=True, name="live-copy").start()
        threading.Thread(target=_poll_worker, daemon=True, name="live-poll").start()

    def stop(self) -> list[Path]:
        self.active = False
        with self._files_lock:
            return list(self.files)
