# CSS Logger — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `main.py` | Hlavní aplikace — PySide6 "CPVA Suite" (CSS Logger + Spectra v jednom okně). Spouští se `python main.py`. |
| `cpva_core.py` | Ne-UI helpery: config/preset I/O, CPVA archiver HTTP, time/PV-name/image helpery. Bez GUI toolkitu — sdílí main.py i testy. |
| `test_smoke.py` | Offline smoke test — headless (QT_QPA_PLATFORM=offscreen), mock sítě + dialogů, projede všechna tlačítka/dialogy. |
| `cpva_explorer_config.json` | Konfigurace — uložená nastavení (časový rozsah, zobrazené kanály, conditions, master PV…). |
| `cpva_presets.json` | Presets — pojmenované skupiny PV kanálů. |
| `cpva_conditions_presets.json` | Presets podmínek (min/max filtry). |
| `custom_pvs.json` | Odvozené PV (matematické výrazy nad základními PV). |
| `RampingRepository/` | Lokální parquet data (ramping události) — funguje offline. |

> Pozn.: Starý tkinter `cssl.py` byl odstraněn; jeho ne-UI helpery žijí v `cpva_core.py`,
> UI plně nahradil `main.py` (PySide6).

---

## main.py — CSSLoggerWidget

### Záměr
Explorer a logger dat z CPVA archivu (Control System Studio). Zobrazuje hodnoty PV kanálů
v čase, prochází archivní data, exportuje do CSV, kreslí grafy (Graph / XY / PV Time) a
zobrazuje asociované snímky kamer.

### Záložky (notebook)
- **Graph** — multi-axis graf v čase (stacked / overlaid), crosshair, zoom, reference lines, conditions, custom PV, axis settings.
- **XY Plot** — scatter X vs Y nad načtenými PV.
- **PV Time Plot** — denní distribuce / raw průběh z RampingRepository.
- **Table** — `QTableWidget` s daty, context menu (Copy row / Open image), export CSV.
- **Log** — log běhu.

### Sidebar
Time window dialog, presety (load/save/save-as/delete), PV list (Browse/Remove/Clear),
**LOAD DATA**, **Live** toggle, Master PV / multiple filtr, progress bar.

### Dialogy
`TimeWindowDialog`, `DatePickerDialog`, `PVBrowserDialog`, `_ConditionsDialog`,
`_RefLinesDialog`, `_CustomPVDialog`.

---

## cpva_core.py — ne-UI helpery

### Konstanty
- `CPVA_BASE_URL` — `https://10.78.0.57:8443/api/1.0/cpva` — SSL bez ověření certifikátu
- `IMAGE_ROOT` — `\\users-L3.tier0.lcs.local\cpva-image-2026`
- `CHUNK_SIZE_NS` — 1 hodina v ns — maximální bezpečné okno pro jeden API dotaz
- `CONFIG_FILE`, `PRESETS_FILE`, `CONDITIONS_PRESETS_FILE`, `CUSTOM_PVS_FILE` — cesty vedle exe/skriptu
- `RAMPING_PV_MAP` / `PV_TO_RAMPING`, `MASTER_RAMP_PV`, `DATA_REPOSITORY_DIR`, `RAMPING_REPOSITORY_DIR`

### Pomocné funkce
| Funkce | Popis |
|--------|-------|
| `get_app_dir()` | Složka exe (frozen) nebo `__file__` |
| `load_config()` / `save_config()` | JSON konfigurace s fallback výchozími hodnotami |
| `load_presets()` / `save_presets()` | JSON presets |
| `load_condition_presets()` / `save_condition_presets()` | JSON presets podmínek |
| `load_custom_pvs()` / `save_custom_pvs()` | JSON odvozených PV |
| `load_ramping_repository()` | Načte `RampingRepository/index.json` (offline) |
| `cpva_fetch_samples(channel, start_ns, end_ns)` | Jeden HTTP GET na CPVA API → list dicts |
| `cpva_fetch_samples_chunked(...)` | Rozdělí rozsah na hodinové chunky; přeskočí noční hodiny |
| `cpva_decode_value(sample)` | Dekóduje hodnotu vzorku (numeric / string / enum) |
| `cpva_fetch_channels()` | Získá seznam všech dostupných CPVA kanálů |
| `now_ns()` / `dt_to_ns()` / `ns_to_local_str()` | Time helpery |
| `parse_user_datetime(s)` | Parsuje uživatelský vstup data/času (ISO + evropský formát) |
| `shorten_pv_name(full_name)` | Zkrátí plný název PV pro zobrazení v UI |
| `_matches_wildcard()` | Wildcard / space-AND filtr |
| `_open_path()` / `_looks_like_image_path()` / `_image_file_size()` / `_resolve_image_path()` | Image helpery |
| `safe_divide(a, b)` | Element-wise dělení s NaN pro nulový/nekonečný jmenovatel |

### Závislosti
- `requests`, `urllib3`, `orjson`, `ssl` — HTTPS dotazy na CPVA (bez ověření certifikátu)
- `numpy` — `safe_divide`
- Žádný GUI toolkit (bezpečné pro headless testy)
