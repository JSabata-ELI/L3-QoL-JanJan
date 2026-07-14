# Announcer — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `a.py` | Jediný soubor. Celý nástroj v tkinter. |

---

## a.py

### Záměr
Sledování změn ve vybrané oblasti obrazovky (Screen Region Change Tracker). Monitoruje pixely v zadané obdélníkové oblasti a alertuje (vizuální flash + zvuk) při detekci změny.

### Konstanty
- `POLL_INTERVAL_MS` — `500` ms — interval poll smyčky
- `CHANGE_THRESHOLD` — `2.0` — výchozí práh průměrné pixelové odchylky (0–255)
- `FLASH_DURATION_MS` — `3000` ms — jak dlouho blikat po detekci
- `FLASH_INTERVAL_MS` — `300` ms — rychlost blikání

### Třídy

#### `RegionSelector(tk.Toplevel)`
Overlay okno pro výběr oblasti na obrazovce (drag na monitoru).
- Dva-okno přístup: polo-průhledný overlay + plně neprůhledné hranice (červený rámeček)
- Callback `(x1, y1, x2, y2)` → předán `ScreenTracker._region_selected()`

#### `ScreenTracker(tk.Tk)`
Hlavní okno aplikace.

**Stav:**
- `region` — `(x1, y1, x2, y2)` nebo `None`
- `reference` — numpy float32 array referenčního snímku
- `tracking` — zda probíhá polling
- `changed` — zda byla detekována změna

**Kolečko (barevný indikátor stavu):**
- šedá — žádná reference
- oranžová — reference nastavena, připraven ke spuštění
- zelená — aktivní sledování
- červená — změna detekována

**Klíčové metody:**
| Metoda | Popis |
|--------|-------|
| `_select_region()` | Otevře `RegionSelector`, schová hlavní okno |
| `_save_reference()` | Uloží aktuální screenshot jako referenci |
| `_toggle_tracking()` | Start / stop / reset podle aktuálního stavu |
| `_poll()` | Pravidelná smyčka: grab → diff → detekce |
| `_on_change_detected(diff)` | Flash + zvuk při překročení prahu |
| `_start_flash()` | Animované blikání overlay framu přes celé okno |
| `_play_sound()` | `winsound.Beep` nebo WAV ze složky `sounds/` v background vlákně |

**Presets:** region uložen jako JSON do `presets.json` (vedle exe). Geometrie oken: `window_geometry` (ovládací okno) a `image_geometry` (obrázkové okno) — každá globálně (top-level klíč) nebo per-preset (uvnitř dictu presetu).

**Dvě nezávislá okna:**
- *Ovládací okno* (`ScreenTracker` sám) — kolečko, presety, PV alerty, log. Za trackingu HUD (borderless + chroma-key). Geometrie `window_geometry`.
- *Obrázkové okno* (`_image_win`, samostatný `Toplevel`) — sem blikne alarm obrázek/barva. Vlastní geometrie `image_geometry`, jinak fallback na geometrii ovládacího okna. Za tripu jsou obě okna viditelná zároveň. Vytvořeno v `_ensure_image_win()`.

**Settings popup:** práh detekce, barva flashe, trvání flashe, flash mode, obrázek, zvuk (freq / duration / WAV soubor), dva recordery geometrie ("Set control window" / "Set image window" → `_start_control_window_recording` / `_start_image_window_recording`, sdílejí `_open_geometry_recorder`)

**Preview popup:** hover nebo klik na "Preview region" — miniatura aktuálního screenshotu oblasti

**Multi-monitor:** `screeninfo.get_monitors()`, combo výběr monitoru + tlačítko Identify

### Závislosti
- `tkinter` — UI
- `PIL` (Pillow) — `ImageGrab`, `ImageTk` pro preview
- `numpy` — porovnání pixelů
- `screeninfo` — seznam monitorů
- `winsound` — Windows zvuk (stdlib)
