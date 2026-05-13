# Image Tools — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `main.py` | Entry point. Spouští `QApplication`, buildí hlavní okno s `QTabWidget` (záložky: Image Slider, Image Finder, Shot Finder). Detekuje verzi z názvu exe. |
| `is_t.py` | **Image Slider** — prohlížeč časových sérií snímků z kamer. Scrubbing, pointing, SC, multi-cam, online mode. |
| `if_t.py` | **Image Finder** — výběr kamer a snímků pro daný den, anotace energetickými daty z CPVA API + CSV. |
| `sf_t.py` | **Shot Finder** — hledání snímků podle hodnoty PV (energie, waveplate…) v CPVA archiveru. |

---

## Sdílené konstanty a konvence

- Timezone: `PRAGUE = ZoneInfo("Europe/Prague")` — ve všech třech modulech
- CPVA archiver: `https://10.78.0.57:8443/api/1.0/cpva` — SSL bez ověření certifikátu
- Timestamp v názvech souborů: UTC nanoseconds (`extract_ns_from_stem`)
- Síťové cesty: UNC `//server/share` (Lab) nebo `Z:\` (Office)
- Checkboxy: `_CHECKBOX_STYLE` / `_CHECKBOX_STYLE_SM` — jednotné QSS (`::indicator` only, nikdy border na `QCheckBox {}`)

---

## if_t.py — Image Finder

### Konstanty
| Konstanta | Hodnota / popis |
|-----------|-----------------|
| `IMAGES_ROOT_BASE` | `//users-L3.tier0.lcs.local` — kořen archívu kamer |
| `RAMPING_CANDIDATES` | `[("Lab", "//hapls-share…/2026_alldata"), ("Office", "Z:\…")]` |
| `DEFAULT_RAMPING_SOURCE` | `0` (Lab) |
| `ENERGY_CSV_ROOT` | `//hapls-share…/scratch/Salvation/2026_alldata` |
| `ENERGY_CSV_NAME_FMT` | `"dataof%Y%b_%d"` → `dataof2026Mar_24.csv` |
| `ENERGY_COLUMNS_AVAILABLE` | Všechny sloupce dostupné k výběru v dialogu |
| `ENERGY_COLUMNS_DEFAULT` | `[]` — defaultně žádné nevybrané |
| `ENERGY_COLUMNS_DISPLAY` | Dict `col → zobrazené jméno` v UI a na anotacích |
| `ENERGY_MATCH_TOL_S` | `0.55` s — tolerance shody timestamp obrázek ↔ PV |
| `CPVA_BASE_URL` | `https://10.78.0.57:8443/api/1.0/cpva` |
| `CPVA_HTTP_TIMEOUT` | `10.0` s |
| `CPVA_SHOT_CHANNEL` | `HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy` |
| `CPVA_CHANNEL_MAP` | Dict `col → channel`: ptm1, pcm2, pcm4, pap1, sbw4, Back_Ref (`L3-PM03-023:Energy`), waveplate (`L3-PFWP6-MTR03-1:RawPos`) |
| `MAX_SCAN_FILES` | `2000` — max stat() volání na síťové složce |
| `MIN_FULL_FILES` | `1` — min podsložek, aby byla hodina použita |

### Pomocné funkce (module-level)
| Funkce | Popis |
|--------|-------|
| `extract_ns_from_stem(stem)` | Parsuje UTC ns timestamp z názvu souboru |
| `extract_display_label(name)` | Zkrácený label pro kameru z názvu složky |
| `extract_folder_number(name)` | Číslo kamery z názvu složky |
| `is_valid_image_file(name)` | Kontrola přípony obrázku |
| `build_new_name(stem, use_prague_time)` | Přejmenuje soubor na čitelný timestamp |
| `_energy_csv_path(dt)` | Sestaví cestu k dennímu CSV souboru |
| `_load_energy_csv(csv_path)` | Načte CSV → `list[_EnergyRow]` |
| `_energy_api_for_day(dt, cols, csv_root, log)` | Dotáže CPVA API pro daný den a sloupce → `list[_EnergyRow]`; fallback na CSV per sloupec |
| `_find_energy_match(rows, img_ts_ns, tol_s)` | Binárním hledáním najde nejbližší řádek |
| `_cpva_ssl_ctx()` | SSL kontext bez ověření certifikátu |
| `_cpva_fetch_samples(channel, start_ns, end_ns)` | HTTP GET na CPVA archiver, vrátí list dicts |
| `_cpva_best_shot_ns(start_ns, end_ns)` | Najde timestamp nejsilnějšího shotu |
| `_annotate_image_with_energy(src, dst, match, before, after, ts_ns, cols)` | Přidá bílý proužek s PV hodnotami pod obrázek |

### Datové struktury
```python
class _EnergyRow:
    ts_dt: datetime   # Prague-naive
    values: dict[str, str]   # col → hodnota jako string

class _LoadSignals(QObject):
    done      = Signal(list, dict, int)   # subfolders, saved_sel, gen
    not_found = Signal(object)
    error     = Signal(str)
    log_msg   = Signal(str)              # thread-safe log z load workeru

class _CollectSignals(QObject):
    done = Signal(list)

class _AutoHourSignals(QObject):
    apply   = Signal(str, int, int, bool)  # msg, ui_hour, day_shift, use_lab
    log_msg = Signal(str)

class _LogSignals(QObject):
    msg = Signal(str)   # thread-safe log — veškeré background logy jdou přes tento signal
```

### ImageFinderWidget(QWidget)

**Inicializace:**
- `_user_has_selected_day` — False dokud uživatel (nebo `_auto_select_today`) nevybere den
- `_load_gen` — generace pro rušení starých load workerů
- `_load_sig` — aktuální `_LoadSignals` objekt; vždy se zachytí jako `_sig` před spawnem vlákna (GC ochrana)
- `_log_sig` — `_LogSignals` pro thread-safe logování z background vláken
- `_energy_cache` — `{date_str: list[_EnergyRow]}` — cache po dnech
- `_energy_selected_cols` — list vybraných sloupců (z `EnergyColumnDialog`)
- `RAMPING_ROOT` — aktuální cesta k CSV (Lab nebo Office)
- `_auto_hour_last_day` — zabrání opakovanému spuštění auto-hour pro stejný den
- `_DEFAULT_HOUR = 14` — výchozí hodina při startu nebo když CSV chybí

**Startup flow (výběr dne):**
1. `QTimer.singleShot(0, _auto_select_today)` — ihned po buildu UI nastaví `_user_has_selected_day = True` a zavolá `_apply_auto_hour_for_selected_day()`
2. `_apply_auto_hour_for_selected_day()` — okamžitě nastaví hodinu na `_DEFAULT_HOUR` (14) a spustí `load_folders()`; zároveň v background vlákně zkusí CSV auto-hour (timeout 1 s); pokud CSV vrátí jinou hodinu → reload
3. `load_folders()` — background worker scanuje síťovou složku přes `os.scandir`; výsledek doručen přes `_load_sig.done` na main thread

**Thread-safety pravidlo:**
- Všechny background vlákna volají `self._log_safe()` (ne `self._log()`)
- `_log_safe` emituje `_log_sig.msg` → main thread volá `_log()`
- Load worker logguje přes `_sig.log_msg.emit()`

**Klíčové metody:**
| Metoda | Popis |
|--------|-------|
| `_auto_select_today()` | Startup — nastaví `_user_has_selected_day`, spustí auto-hour |
| `_on_calendar_selected()` | Klik na den v kalendáři → auto-hour |
| `_apply_auto_hour_for_selected_day()` | Ihned: hodina 14 + load. Na pozadí: CSV auto-hour |
| `_apply_auto_hour_ui(msg, hour, shift, use_lab)` | Slot — aplikuje CSV výsledek; reload jen pokud se hodina změnila |
| `load_folders()` | Spustí background worker pro scan kamer |
| `_on_load_done(subfolders, saved_sel, gen)` | Vyplní tabulku kamer na main thread |
| `_get_energy_rows_for_dt(dt)` | API first → CSV fallback; cachuje per-den |
| `_lookup_energy_for_files(files)` | Pro každý soubor najde matching `_EnergyRow` |
| `_run_energy_lookup_async(files, on_done)` | Background energy lookup; `_sig` zachycen jako lokální proměnná |
| `view_primary_files()` | Collect → energy lookup → annotate → `os.startfile` |
| `_collect_primary_files_now(jobs)` | Paralelní sken složek (ThreadPoolExecutor, max 8 workerů) |
| `_collect_primary_files_async(on_done)` | Async obal `_collect_primary_files_now` |
| `select_images_from_folder(folder, how_many, ...)` | Vybere N representativních snímků (segmentace + rovnoměrný výběr) |
| `_get_items_cached(folder, ...)` | Scandir bez stat + sampled stat; cache v `_namecache` |
| `_pick_best_block_real_hour(day)` | Analyzuje ramping CSV, vrátí nejlepší hodinu; bez dat → hodina 14 |
| `_get_ramping_for_day_cached(day)` | Načte ramping CSV s timeoutem 1 s (probe na síti); cachuje |

**Energie — flow:**
1. Uživatel vybere sloupce v `EnergyColumnDialog` (waveplate, ptm1, Back_Ref…)
2. `view_primary_files()` → `_run_energy_lookup_async(files)` → background vlákno volá `_lookup_energy_for_files`
3. Pro každý soubor: `_get_energy_rows_for_dt(dt)` → `_energy_api_for_day(dt, cols)` (CPVA API) → fallback CSV
4. `_find_energy_match(rows, ts_ns)` → nejbližší `_EnergyRow` do 0.55 s
5. `_annotate_image_with_energy(src, dst, match, ...)` → přidá bílý proužek s hodnotami

---

## sf_t.py — Shot Finder

### Konstanty
| Konstanta | Hodnota / popis |
|-----------|-----------------|
| `IMAGES_ROOT_OPTIONS` | `{"Lab": Path("//users-L3…/cpva-image-2026"), "Office": Path("\\\\users-L3…")}` |
| `ENERGY_CSV_ROOT_OPTIONS` | `{"Lab": "//hapls-share…/2026_alldata", "Office": "Z:\\…"}` |
| `ENERGY_CSV_NAME_FMT` | `"dataof%Y%b_%d"` |
| `EXTRA_COL_MATCH_TOL_S` | `5.0` s — tolerance pro extra sloupce (waveplate…) |
| `CPVA_BASE_URL` | `https://10.78.0.57:8443/api/1.0/cpva` |
| `CPVA_HTTP_TIMEOUT` | `15.0` s |
| `CPVA_CHANNEL_MAP` | Dict `col → channel`: ptm1, pcm2, pcm4, pap1, sbw4, Back_Ref (`L3-PM03-023:Energy`), waveplate (`L3-PFWP6-MTR03-1:RawPos`) |
| `PV_COLUMNS` | Dict `col → label` pro UI |
| `MJ_COLUMNS` | `{"Back_Ref", "pap1"}` — hodnoty jsou v mJ (zobrazeny ×1000) |
| `SBW4_TRANSMISSION` | `0.749` |

### Pomocné funkce (module-level)
| Funkce | Popis |
|--------|-------|
| `_cpva_ssl_ctx()` | SSL kontext bez ověření |
| `_cpva_fetch_samples(channel, start_ns, end_ns)` | HTTP GET CPVA API → `(list[dict], url)` |
| `_load_csv_for_day(day, cols, csv_root)` | Načte denní CSV → `(merged_rows, per_col_rows)` |
| `_load_api_for_day(day, cols, log, csv_root)` | API first per-sloupec → fallback CSV → merge → `(merged_rows, per_col_rows)` |
| `_find_closest_col_value(per_col, col, target_ns, tol_s)` | Nearest-neighbour v čase pro extra sloupec |

### ShotFinderWidget(QWidget)

**UI sekce (levý panel):**
- **Search criteria** — PV checkboxy (sbw4, ptm1, pcm2, pcm4, pap1, Back_Ref, waveplate), target hodnota, tolerance ±
- **Also show** — extra PV sloupce zobrazené v tabulce výsledků (bez vlivu na vyhledávání)
- **Network source** — combo Images (Lab/Office) + combo Ramping (Lab/Office)
- **Date range** — kalendář, weekday filtry (Po–Ne)
- **Cameras** — scroll-list kamer z daného dne; filter textbox s dropdown

**UI sekce (pravý panel):**
- Tabulka výsledků (den, čas, hodnota PV, extra sloupce, náhled)
- Log box

**Klíčové metody:**
| Metoda | Popis |
|--------|-------|
| `_load_cameras()` | Scanuje všech 24 hodin vybraného dne, deduplicates kamer přes `seen: set` |
| `_find_hour_folder(day, images_root)` | Najde složku pro daný den/hodinu |
| `_load_api_for_day(day, cols, log, csv_root)` | Wrapper na module-level funkci |
| `_start_search()` | Zachytí `images_root` a `csv_root` z self před spawnem vlákna |
| `_on_source_changed()` / `_on_csv_source_changed()` | Aktualizuje `_images_root` / `_energy_csv_root` |
| `hideEvent()` | Vymaže cam filter text a skryje dropdown při přepnutí záložky |

**Vyhledávací worker:**
- Pro každý vybraný den: `_load_api_for_day(day, search_cols + extra_cols)` → najde shot s hodnotou nejblíže targetu → `_find_closest_col_value` pro extra sloupce
- Výsledky emitovány přes signal na main thread

---

## is_t.py — Image Slider

### Konstanty a globální data
- `IMG_EXT` — povolené přípony obrázků
- `_CHECKBOX_STYLE` — QSS pro checkboxy (border pouze na `::indicator`)
- `GRADIENTS` — barevné palety; `None` = Grayscale, jinak RGB LUT `np.array(256×3)`
- `DEFAULT_OPEN_DIR` / `DEFAULT_OPEN_ROOT` — síťová cesta k archívu kamer
- `DEFAULT_SAVE_DIR` — výchozí cesta pro ukládání
- `ONE_HOUR_NS`, `SLIDER_MAX`, `CACHE_SIZE`, `SCRUB_MAX_SIDE` — provozní konstanty
- `TZ_PRAGUE` — `ZoneInfo("Europe/Prague")`

### Datové struktury
```python
Item(path, ts_ns)     # frozen dataclass — jeden snímek
PixCache(max_items)   # LRU cache QPixmap
```

### Worker třídy (QRunnable)
| Třída | Signály | Účel |
|-------|---------|------|
| `LoadTask` | `LoaderSignals` | Načte a zdekóduje jeden snímek |
| `ScanTask` | `ScanSignals` | Projde složky, sestaví seznam `Item` |
| `RefreshScanTask` | `RefreshScanSignals` | Inkrementální refresh (nové snímky) |
| `SaveRangeTask` | `SaveRangeSignals` | Batch save A→B |
| `PointingAnalysisTask` | `PointingAnalysisSignals` | Centroid pro každý snímek |
| `_SCTask` | `_SCSignals` | Spatial Contrast pro jeden snímek |
| `_SCHistogramWidget` | — | matplotlib histogram SC hodnot (embedded v dialogu) |
| `_CamPollTask` | `_CamPollSignals` | Polling nových snímků (online multi-cam) |

### Pomocné funkce
- `load_image_scaled(path, max_side, brighten, gradient_id, ...)` — načte, zdekóduje, aplikuje paletu
- `_apply_lut(img, lut)` — RGB LUT na Grayscale8
- `_autostretch_gray(img)` — percentilový stretch
- `_apply_brightness_offset(img, offset)` — offset jasu
- `_copy_metadata_into_png(src, dst, save_txt)` — přenese PNG/TIFF metadata
- `fmt_prague_full_from_ns(ns)` / `fmt_hhmmss_ms_from_ns(ns)` — formátování ts
- `parse_unix_ns_from_name(path)` — timestamp z názvu souboru

### Widgety

#### `ImageView(QWidget)`
Zobrazuje snímek + overlaye v `paintEvent`:
- cross, circle, square (normalizované souřadnice)
- `sc_topn_points_norm` — top-N SC body (oranžové kroužky)
- energy bar (bílý proužek dole s PV hodnotami)
- cam label + timestamp

#### `CameraView(QWidget)`
Obal jedné kamery v multi-cam gridu. `img_view` + label.

#### `MultiCameraGrid(QWidget)`
Grid `CameraView`. Signal `camera_selected(int)`.
- `setup_cameras(names, folders)` — zachovává overlay stav přes `_overlay_store`
- `selected_cam_index()` / `selected_cam_indices()` / `selected_img_view()`

#### `PointingPanel(QWidget)`
Matplotlib scatter + histogram pointing. Signal `point_clicked(int)`.
- `plot(cx_urad, cy_urad, n_shots, ts_ns, ts_ns_int)`
- `set_replay_ts(ts_ns)` — live replay při scrubování

#### `TickBar(QWidget)`
Časová osa pod sliderem. Ticky, značky A/B, date labels při multi-day.
- `_draw_date_label` — `dd.mm` label s bílým pozadím na midnight crossings

#### Dialogy
- `DatePickerDialog` — kalendář + hodina + Now checkbox + Multi-day (end date, `adjustSize()` při toggle)
- `CameraPickerDialog` — výběr kamer pro multi-cam
- `FolderPickerDialog` — lazy tree síťové složky
- `_SCHistogramDialog` / `_SCExclusionEditor` — SC threshold + exkluzní regiony

### Hlavní widget: `Viewer(QWidget)`

**Stav:**
- `self.items` / `self.ts_list` — snímky + timestamps
- `self.current_idx`, `self._gen`
- `self._last_save_dir`

**Klíčové metody:**
| Metoda | Popis |
|--------|-------|
| `_start_scan(folders, ...)` | Spustí ScanTask |
| `_display_exact_index(idx, ts, ...)` | Zobrazí snímek |
| `_set_info_for(idx, axis_time_ns)` | Info panel + live replay pointing |
| `_on_auto_follow_toggled(checked)` | Online mode + skok na poslední |
| `_start_online_mode()` / `_stop_online_mode()` | Online polling |
| `_run_spatial_contrast()` | Spustí _SCTask |
| `save_current()` / `save_around_current()` / `save_range()` | Ukládání |

**Palety:** index 0 = Default (orig.), index 1 = Grayscale, 2+ = LUT

**Ukládání:** Default → `shutil.copy2`; ostatní → `load_image_scaled` + save; vždy `_copy_metadata_into_png`

---

## Závislosti
- `PySide6` — Qt widgets, signals, threading
- `numpy`, `Pillow` (PIL) — image processing
- `matplotlib` — PointingPanel, SC histogram
- `scipy` — SC hole-filling (volitelné)
- `zoneinfo` — Prague timezone
- `ssl`, `urllib` — CPVA archiver API (bez ověření certifikátu)
