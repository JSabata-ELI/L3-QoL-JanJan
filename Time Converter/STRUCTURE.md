# Time Converter — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `tc.py` | Jediný soubor. Celý nástroj v tkinter. |

---

## tc.py

### Záměr
Kopíruje soubory kamer z archívu a přejmenovává je — UNIX nanosecond timestamp v názvu souboru nahradí čitelným formátem `YYYY_MM_DD--HH_MM_SS__ffffff` (UTC nebo Praha čas).

### Konstanty
- `FINAL_RE` — regex pro soubory již v cílovém formátu (budou přeskočeny)
- `SOURCE_RE` — regex pro detekci UNIX ns timestampu (trailing číslo v stem)
- `PRAGUE` — `ZoneInfo("Europe/Prague")`
- `TS_MIN_NS` / `TS_MAX_NS` — validační rozsah roku 2000–2100 v nanosekundách

### Pomocné funkce (module-level)
| Funkce | Popis |
|--------|-------|
| `split_stem(stem)` | Rozloží název souboru na `(prefix, raw_ts_str)`; ošetří `_-_` artefakty |
| `convert_timestamp(ns, use_prague_time)` | UNIX ns → formátovaný string (UTC nebo Praha) |
| `orig_ts_to_utc(orig_ts_str)` | UNIX ns string → čitelný UTC string (pro zobrazení v tabulce) |
| `build_new_name(stem, use_prague_time)` | Sestaví nový stem; vrátí `(new_stem, None)` nebo `(None, reason)` |

### UI komponenty
| Funkce | Popis |
|--------|-------|
| `show_intro_and_get_options(parent)` | Úvodní dialog — Prague time? + Show report? → dict nebo None (cancel) |
| `_make_file_table(parent, rows)` | `ttk.Treeview` s 5 sloupci a barevnými tagy (copy/overwrite/skip/warn/error) |
| `_make_dashboard(parent, counts)` | Řada barevných číselných boxů (summary statistika) |
| `show_preview(parent, plan_rows, skip_rows)` | Preview okno před kopírováním → Proceed / Cancel |
| `show_report(parent, done_rows, skip_rows, ...)` | Výsledkové okno po dokončení kopírování |
| `create_progress_window(parent, total)` | Progress okno s progress barem, EMA odhadem ETA a tlačítkem Cancel |

### Průběh (main loop)
1. Intro dialog (timezone + report volby)
2. Výběr souborů — `filedialog.askopenfilenames`, výchozí: síťový archív kamer
3. Výběr cílové složky
4. Plánování: každý soubor → `build_new_name` → copy / overwrite / skip row
5. Preview okno (pokud show_report=True)
6. Paralelní kopírování — `ThreadPoolExecutor(max_workers=4)` + polling progress v main thread
7. Report dialog nebo messagebox; pak se loop vrátí na krok 1

### Závislosti
- `tkinter` — UI
- `pathlib`, `shutil` — práce se soubory
- `concurrent.futures` — paralelní kopírování (max 4 workery)
- `zoneinfo` — Praha timezone
