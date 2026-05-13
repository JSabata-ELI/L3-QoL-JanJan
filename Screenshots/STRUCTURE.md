# Screenshots — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `s.py` | Jediný soubor. Celý nástroj v tkinter. |

---

## s.py

### Záměr
Nástroj pro pořizování, správu a odesílání screenshotů z CSS (Control System Studio) oken na síťovou sdílenou složku. Zvládá jak capture celé obrazovky, tak přesný capture konkrétního okna s ořezem Win11 DWM stínu.

### Konstanty
- `DEFAULT_DEST` — `\\hapls-share.lcs.local\scratch` — výchozí cílová síťová složka
- `CAM_INFO` — dict `{cam_name: cam_id_str}` — mapování kamer na CPVA ID (~80 kamer)
- `CAM_WINDOW_TITLES` — dict `{cam_name: window_title_prefix}` — prefix titulku CSS okna pro každou kameru

### Parsování CSS titulků
- `parse_cpva_header(header)` → `(cam_name, cam_id)` — normalizuje různé formáty (`C03-`, `L3-`, `L3BT-`)
- `_norm_suffix_mode(raw)` — přidá podtržítko před NF/FF/DF suffix

### DPI a window capture (Windows API)
- `_set_dpi_awareness()` — nastaví Per-Monitor DPI awareness v2 (fix ořezu na HiDPI)
- `_dwmapi` / `DWMWA_EXTENDED_FRAME_BOUNDS = 9` — pro přesné ořezání DWM stínu
- `take_screenshot_window_png(hwnd)`:
  1. `GetWindowRect` — celý bitmap včetně stínu
  2. `PrintWindow` — capture do bitmapy
  3. `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)` — skutečné viditelné hranice
  4. PIL crop na přesnou viditelnou oblast → PNG bytes

### Sdílení a upload
- `copy_to_dest(png_bytes, filename, dest_folder)` — uloží PNG do cílové složky
- Upload do cyklů (`cycle_A`, `cycle_B`, ...) — každý cyklus je pojmenovaná složka pro sérii snímků

### UI (tkinter)
- Hlavní okno: seznam aktivních CSS oken / kamer
- Tlačítka: Screenshot, Send to Scratch, Open destination
- Výběr cílové složky (síť / lokální)
- Preview posledního screenshotu (PIL → ImageTk)
- Automatické pojmenovávání souboru: `<cam_name>_<timestamp>.png`

### Závislosti
- `PIL` (Pillow) — PNG encode, preview
- `ctypes` / `wintypes` — Windows API (DWM, PrintWindow)
- `tkinter` — UI
