# Spectra — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `sp_t.py` | Jediný soubor. SPIDER spektrometr analýza v PySide6. (~2840 řádků) |

---

## sp_t.py

### Záměr
Analýza dat ze SPIDER spektrometru z CPVA archivu. Dva režimy: **Archive** (výběr časových oblastí v search grafu → průměrování spekter) a **Live** (stream nejnovějších shotů + rolling průměr posledních N shotů).

### Konstanty

| Konstanta | Hodnota / popis |
|-----------|-----------------|
| `PV_ENERGY` | `HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy` — SBW4 energie (výchozí search signál + per-region scalar) |
| `PV_ALPHA_ENERGY` | `HAPLS-ENER_IN_GPL_LT2_DIAG2:Energy` — Alpha output energie (volitelný search signál) |
| `PV_SPEC_X` | `L3-SBDP-SPIDER:SpecDomain_Int_X` — vlnová délka (osa X) |
| `PV_SPEC_Y` | `L3-SBDP-SPIDER:SpecDomain_Int_Y` — intenzita (osa Y, waveform) |
| `CPVA_URL` | `https://10.78.0.57:8443/api/1.0/cpva` |
| `ORDER_PVS` | Seznam `(label, channel)` pro Order 2/3/4 = **GDD, TOD, FOD** — průměrují se per-region a zobrazí v detailech |
| `DEFAULT_SEARCH_PVS` | Výchozí PV seznam pro search graf (SBW4, Alpha, GDD, TOD) — uživatel může editovat |
| `_CHUNK_NS` | `3_600_000_000_000` (1 h) — CPVA timeoutuje na delší jednotlivý request |
| `LIVE_INTERVAL_S` | `3` s — interval pollingu v live módu |
| `LIVE_BUF_MAX` | `2000` — max spekter v rolling bufferu (`deque`) |
| `DEFAULT_LIVE_N` | `100` — výchozí "průměruj posledních N" |
| `LIVE_HISTORY_S` | `600` s — preload při startu live módu |
| `MAX_INDIVIDUAL_LINES` | `400` — max jednotlivých spekter při "show all" zobrazení |
| `_REGION_COLORS` | 8 výchozích barev pro výběry (#C62828, #2E7D32, …) |

### Config soubory (`%APPDATA%\ELI_Spectra\`)

| Cesta (funkce) | Obsah |
|----------------|-------|
| `_search_pv_config_path()` → `search_pvs.json` | Aktuální uživatelský seznam search PV |
| `_preset_config_path()` → `search_presets.json` | Pojmenované presety PV seznamů |
| `_layout_config_path()` → `layout.json` | Velikosti splitteru + ručně upravené subplot marginy |

### Pomocné funkce (module-level)

| Funkce | Popis |
|--------|-------|
| `_ssl_ctx()` | SSL kontext bez ověření certifikátu (self-signed) |
| `_cpva_fetch(channel, start_ns, end_ns)` | Jeden HTTP GET na `/samples` → list dicts |
| `_cpva_load_all_channels()` | GET na `/channels` → setříděný list jmen kanálů (`[]` při chybě); module-level cache `_cpva_channel_cache` |
| `_fetch_waveforms(channel, start_ns, end_ns)` | Waveform data → `list[(ts_ns, np.ndarray)]` |
| `_fetch_scalars(channel, start_ns, end_ns)` | Skalární data → `list[(ts_ns, float)]` |
| `_fetch_scalars_chunked(channel, start, end, progress_cb)` | Jako `_fetch_scalars`, ale dělí na 1h chunky (anti-timeout); volá `progress_cb(done, total)` |
| `_trimmed_mean(stack, frac=0.1)` | Průměr po ořezu dolních/horních `frac` hodnot |
| `_sigma_clipped_mean(stack, sigma=3.0)` | Průměr po maskování bodů mimo ±3σ |
| `_compute_stats(arrs)` | Z listu waveformů (nejčastější délka) vypočítá **všechny metody najednou** → dict `mean/median/trimmed/sigma/std/stack/n` |
| `_load_search_presets()` / `_save_search_presets(presets)` | Read/write `search_presets.json` |
| `_ns_to_dt` / `_fmt_hms` / `_fmt_date` / `_fmt_dur` | ns timestamp → Prague datetime / `HH:MM:SS` / `YYYY-MM-DD` / `Xm YYs` |
| `_day_range_ns(qdate)` | `QDate` → `(start_ns, end_ns)` celý den v Prague tz |
| `_bg(fn)` | Spustí `fn` v daemon `threading.Thread` |

> **Pozn.:** `_fmt_dur()` je momentálně dead code (definováno, nikde nevoláno).

### Průřezové QSS / hint konstanty

- `_CAL_STYLE` — styl `QCalendarWidget` (šedý header, modré navigační prvky)
- `_CHK_STYLE` — styl `QCheckBox` (pouze `::indicator`, nikdy border na root)
- `_GROUP_STYLE` — styl `QGroupBox`
- `_BTN_PRIMARY` / `_BTN_SUCCESS` / `_BTN_DANGER` — barevné styly tlačítek
- `_TB_STYLE` — styl matplotlib toolbaru
- `_TB_HINTS` — `dict` text akce → tooltip pro toolbar tlačítka

### `_CustomToolbar(NavigationToolbar2QT)`

Vlastní toolbar: trvale odstraňuje tlačítko **"Export values"** ze Subplots dialogu a emituje `subplot_params_changed` při zavření tohoto dialogu (→ uloží layout).

### Signálová třída

```python
class _Sig(QObject):
    done       = Signal(object)
    error      = Signal(str)
    progress   = Signal(str)
    progress_n = Signal(int, int)  # (hotovo, celkem)
```

Použití: `sig = _Sig(self)` → spojit sloty → spustit background přes `_bg(fn)`. CPVA volání **nikdy** na main threadu.

### Dialogy

| Dialog | Popis |
|--------|-------|
| `_WeekendDelegate(QStyledItemDelegate)` | Vykresluje buňky kalendáře: vybrané = modré pozadí, So/Ne = červený text (detekce **podle `index.column()`**, sloupec 5=So, 6=Ne — funguje i pro spillover dny). `initStyleOption` strhává Qt selection highlight u nevybraných buněk. |
| `_make_calendar(initial)` | Factory: `QCalendarWidget` (Monday-first, anglické locale) + vlastní šedý navigační řádek (◀ měsíc▾ rok ▶) + vlastní šedý day-name header. Vrací `(wrapper_frame, cal)`. |
| `DatePickerDialog(QDialog)` | Výběr dne/dnů. **Plain click** = toggle dne; **Ctrl+click** = přidá rozsah od posledního kliku. `selected_dates()` → `list[QDate]`. Žádný separátní "Multi-day" checkbox. |
| `PvSearchDialog(QDialog)` | Načte jednou všechny CPVA kanály (`_cpva_load_all_channels`), pak filtruje lokálně podle psaného textu. Multi-select → `added_pvs()` → `list[(label, channel)]`. |
| `PresetEditDialog(QDialog)` | Správa pojmenovaných presetů: vlevo seznam presetů (Load), vpravo search kanálů + zelené vybrané PV. Save/Delete preset, `preset_loaded` signal → aplikuje výběr. |
| `ExportDialog(QDialog)` | Volba exportu: CSV data + graph image (PNG/PDF/SVG). |

### SpectraWidget(QWidget)

#### Stav (výběr)

| Atribut | Typ / popis |
|---------|-------------|
| `_selected_day` / `_selected_days` | `QDate` / `list[QDate]` — vybrané dny (jeden nebo rozsah) |
| `_day_start_ns` / `_day_end_ns` | `int` — hranice načteného rozsahu |
| `_search_pvs` | `list[(label, channel)]` — uživatelsky editovatelný seznam search PV |
| `_color_mode` | `"order"` \| `"gdd"` \| `"tod"` — způsob barvení spekter |
| `_energy_data` | `list[(ts_ns, float)]` — data search signálu pro aktuální den(y) |
| `_x_data` | `np.ndarray \| None` — vlnová délka; cache z prvního `PV_SPEC_X` fetch |
| `_regions` | `list[dict]` — single source of truth; každý region nese vše (id, t_start, t_end, color, visible, expanded, show_individual, analyzed, mean/median/trimmed/sigma/std/stack, orders, energy_avg, energy_n, n) |
| `_region_seq` | `int` — monotónní ID zdroj |
| `_row_widgets` | `dict[id → {name, eye, details}]` — widget reference pro update bez rebuild |
| `_span` | `SpanSelector \| None` — na top grafu |
| `_top_user_xlim/ylim`, `_bot_user_xlim/ylim` | `tuple \| None` — uložený pan/zoom stav (přežije redraw) |
| `_live`, `_live_buf`, `_live_start_ns`, `_live_last_ns` | live mód stav + rolling buffer `deque(maxlen=LIVE_BUF_MAX)` |
| `_busy` | `bool` — analýza probíhá (lock) |
| `_colorbar_bot` / `_colorbar_info` | objekt colorbaru + jeho `{cmap, vmin, vmax, label}` pro GDD/TOD barvení |
| `_last_saved_layout` | `dict` — poslední uložený layout (dedup zápisů) |

#### UI layout

```
QHBoxLayout
├── Sidebar (fixed width 276 px)
│   ├── Status block (blikající live indikátor + lbl_day + lbl_status)
│   ├── "Load day…" button
│   ├── GroupBox "Search data by"
│   │     ├── Preset combo + "Edit…" (PresetEditDialog)
│   │     ├── QTableWidget [Label | Channel] (klik = vykreslit; dvojklik label = přejmenovat)
│   │     └── "+ Add PV" (PvSearchDialog) / "✕ Remove"
│   ├── GroupBox "Mode" (Archive | Live checkable buttons)
│   ├── GroupBox "Live" (Average last N + Start/Stop) — skrytý v archive
│   ├── GroupBox "Display" (Average method, Colour by, Normalize, Std band, Show search graph)
│   ├── GroupBox "Spectrum range [nm]" (From/To spinboxy)
│   ├── Region panel (Expand all + collapsible seznam výběrů + Clear/Analyze + progress bar)
│   └── "Export results" button
└── QSplitter (Vertical)
    ├── Top canvas (_CustomToolbar + "Select" mode + search signal + SpanSelector)
    └── Bottom canvas (averaged/live spectra + volitelný GDD/TOD colorbar)
```

#### Klíčové metody

| Metoda | Popis |
|--------|-------|
| `_pick_day()` | Otevře `DatePickerDialog`, přepne na Archive, `_load_day_energy()` |
| `_load_day_energy()` | Background **chunked** fetch aktuálního search PV pro vybrané dny |
| `_on_energy_loaded(data)` | Ořeže samply mimo den → `_draw_energy()` + `_install_span()` |
| `_load_search_pvs` / `_save_search_pvs` | Persist `search_pvs.json` |
| `_refresh_pv_table(select_row)` | Přestaví PV tabulku + vybere řádek |
| `_open_add_pv_dialog` / `_remove_selected_pv` / `_on_pv_label_edited` | Add/Remove/přejmenování PV |
| `_update_preset_combo` / `_on_preset_combo_changed` / `_open_edit_presets_dialog` / `_apply_preset` | Presety |
| `_save_layout` / `_load_layout` | Persist/restore splitter + subplot marginy (`layout.json`) |
| `_install_span()` / `_on_span(xmin, xmax)` | SpanSelector → přidá region do `_regions` |
| `_rebuild_regions_ui` / `_make_region_row` / `_build_region_details` | Region panel |
| `_toggle_region_expanded` / `_toggle_all_expanded` / `_toggle_region_visible` / `_toggle_region_individual` / `_delete_region` / `_clear_regions` | Akce nad regiony |
| `_run_analysis()` | Background: per neanalyzovaný region fetch `PV_SPEC_Y` + `PV_ENERGY` + `ORDER_PVS` → `_compute_stats()`. **Analýza běží přímo, bez potvrzovacího dialogu.** |
| `_on_analysis_done(payload)` | `r.update(res)`, `analyzed=True`, rebuild UI, `_redraw_spectra()` |
| `_redraw_spectra()` | Bottom graf: viditelné regiony, metoda, color mode, normalize, std band, live overlay, colorbar |
| `_compute_region_colors()` | `"order"` → pevná paleta; `"gdd"/"tod"` → rainbow podle hodnoty Order 2/3 + nastaví `_colorbar_info` |
| `_plot_spectrum` / `_plot_individual` / `_plot_live_spectra` | Vykreslení křivek (s normalizací, X rozsahem, std band) |
| `_set_live_mode(live)` / `_toggle_live` / `_start_live` / `_stop_live` / `_live_tick` / `_on_live_y` | Live mód |
| `_export()` / `_export_csv()` | Export CSV + graph image |
| `cancel_scan()` | Veřejné API pro parent "Stop All" → `_stop_live()` |

#### Pan/zoom + "Select" mód (top toolbar)

- `_connect_zoom_tracking()` ukládá uživatelský xlim/ylim do `_*_user_*lim`, takže redraw neresetuje view; "Home" je vynuluje.
- Vlastní akce **"Select"** (jen top toolbar): zaškrtnuto = drag vybírá časový region; zapnutí Pan/Zoom Select automaticky vypne a span pozastaví.

#### Export CSV formát

Jeden CSV: details blok (datum, čas, # spekter, metoda, SBW4 energie, GDD/TOD/FOD), prázdný řádek, pak curve table (wavelength + mean + std pro každý region + každý live shot).
Vždy: `sep=;` první řádek + desetinná tečka (viz [[feedback_csv_excel_format]]).

---

## Závislosti

- `PySide6` — Qt widgets, signals, threading
- `numpy` — spektrální výpočty, průměrování
- `matplotlib` — grafy, `SpanSelector`, `FigureCanvasQTAgg`, `NavigationToolbar2QT`, colorbar
- `ssl`, `urllib` — CPVA archiver API (bez ověření certifikátu)
- `zoneinfo` — Prague timezone
- `json`, `csv` — config persist + export
