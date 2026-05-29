# CSS Logger — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `cssl.py` | Jediný soubor. Celý nástroj v tkinter. (~1750 řádků) |
| `cpva_explorer_config.json` | Konfigurace — uložená nastavení (časový rozsah, zobrazené kanály…) |
| `cpva_presets.json` | Presets — pojmenované skupiny PV kanálů |
| `cssl_structure.md` | Detailní řádkový map tříd a funkcí (Claude memory formát) |

---

## cssl.py

### Záměr
Explorer a logger dat z CPVA archivu (Control System Studio). Zobrazuje hodnoty PV kanálů v čase, umožňuje procházet archivní data, exportovat do CSV a zobrazovat asociované snímky kamer.

### Konstanty
- `CPVA_BASE_URL` — `https://10.78.0.57:8443/api/1.0/cpva` — SSL bez ověření certifikátu
- `IMAGE_ROOT` — `\\users-L3.tier0.lcs.local\cpva-image-2026`
- `CHUNK_SIZE_NS` — 1 hodina v ns — maximální bezpečné okno pro jeden API dotaz
- `MERGE_WINDOW_MS` — `80` ms — tolerance pro sloučení řádků z více kanálů
- `CONFIG_FILE` — `cpva_explorer_config.json` vedle exe

### Pomocné funkce (module-level)
| Funkce | Popis |
|--------|-------|
| `get_app_dir()` | Složka exe (frozen) nebo `__file__` |
| `load_config()` / `save_config()` | JSON konfigurace s fallback výchozími hodnotami |
| `load_presets()` / `save_presets()` | JSON presets |
| `cpva_fetch_samples(channel, start_ns, end_ns)` | Jeden HTTP GET na CPVA API → list dicts |
| `cpva_fetch_samples_chunked(...)` | Rozdělí rozsah na hodinové chunky; přeskočí noční hodiny |
| `cpva_decode_value(sample)` | Dekóduje hodnotu vzorku (numeric / string / enum) |
| `cpva_fetch_channels()` | Získá seznam všech dostupných CPVA kanálů |
| `ns_to_local_str(ts_ns)` | ns timestamp → lokální čas jako string |
| `parse_user_datetime(s)` | Parsuje uživatelský vstup data/času |
| `shorten_pv_name(full_name)` | Zkrátí plný název PV pro zobrazení v UI |

### Dialogy
- `DatePickerDialog(tk.Toplevel)` — výběr data a času (kalendář + časová pole)
- `PVBrowserDialog(tk.Toplevel)` — procházení a výběr PV kanálů (wildcard filtr, live search)

### CPVAExplorerApp
Hlavní aplikace (tkinter Tk nebo Toplevel).

**Sekce UI:**
- Levý panel: výběr PV kanálů, presets, časový rozsah (From/To), tlačítka Fetch/Export/Clear
- Pravý panel: tabulka dat (`ttk.Treeview`), log
- Volitelný náhled asociovaných obrázků kamer

**Klíčové metody:**
| Metoda | Popis |
|--------|-------|
| `fetch_data()` | Dotáže CPVA API pro vybrané kanály a časový rozsah → naplní tabulku |
| `export_csv()` | Exportuje zobrazená data do CSV souboru |
| `_open_image_for_row()` | Otevře snímek kamery asociovaný s vybraným řádkem |

### Závislosti
- `tkinter` — UI
- `ssl`, `urllib` — HTTPS dotazy na CPVA (bez ověření certifikátu)
- `matplotlib` (volitelné) — grafy hodnot v čase
