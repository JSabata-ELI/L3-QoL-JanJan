# Calibrations — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `cal.py` | Jediný soubor. Kalibrační tabulka v PySide6. |

---

## cal.py

### Záměr
Python replika logiky `Calibrations2.xlsx`. Počítá kalibrační faktory (`QE95 / Device`) z naměřených dat a provádí lineární fit pro výpočet nových kalibračních konstant (multiplicator, offset, int_multiplicator).

### Konstanty / konfigurace
- Zařízení: `PAP1`, `PTM1`, `PCM2`, `PCM4`
- `DEFAULT_PROFILES` — výchozí rozsahy waveplate hodnot per zařízení (start, end, step, from_, to)
- `TIMINGS` — `["Off-A SS", "Off-A", "Off-B SS", "Off-B"]`

### Pomocné funkce (module-level)
| Funkce | Popis |
|--------|-------|
| `to_float(s)` | String → float; prázdný/None → None |
| `linreg_slope_intercept(x, y)` | Metoda nejmenších čtverců → `(slope, intercept)` nebo `(None, None)` |
| `micro_to_base(v_micro)` | `v * 1e-6` |
| `parse_date_any(s)` | Parsuje datum v mnoha formátech (dd.mm.yyyy, yyyy-mm-dd, dd-mm-yy…) |
| `format_date(d)` | Kanonický výstupní formát `dd.mm.yyyy` |

### Dialogy
- `CalendarDialog(QDialog)` — výběr data přes `QCalendarWidget`
- `InfoDialog(QDialog)` — zobrazení nápovědy (read-only text)

### CalibrationTable(QMainWindow)
Hlavní okno.

**Sloupce tabulky (5):**
`Waveplate | QE95 [mJ/J] | <Device> [mJ/J] | Cal Factor (read-only) | Note`

**Ovládací panel (nahoře):**
- `device_combo` — výběr zařízení; změna generuje výchozí waveplate tabulku (potvrzení při dirty)
- `convert_combo` / `useint_combo` — YES/NO přepínače (replika Excel Settings logiky)
- `date_combo` + `btn_date_cal` — datum kalibrace (editovatelné nebo výběr z kalendáře)
- `timing_combo` — Off-A SS / Off-A / Off-B SS / Off-B
- `from_combo` / `to_combo` — rozsah waveplate hodnot pro fit a průměrný faktor
- `avg_cal_factor` — read-only; průměr `Cal Factor` v rozsahu from–to

**Kalibrační panel (vpravo nahoře):**
- `mult1 / off1 / intmult1_micro` — staré kalibrační data (vstup uživatele; intmult zobrazen v ×10⁶)
- `mult2 / off2 / intmult2_micro` — nové kalibrační data (read-only výpočet)

**Klíčové metody:**
| Metoda | Popis |
|--------|-------|
| `recompute_cal_factor_for_row(r)` | `QE95 / Device` → Cal Factor pro jeden řádek |
| `recompute_all_cal_factors()` | Přepočítá Cal Factor pro všechny řádky |
| `recompute_new_calibration()` | Výpočet nových mult/off/intmult dle Convert?/Use Int? |
| `apply_highlight()` | Zvýrazní rozsah from–to + aktivní řádek + aktivní sloupec |
| `save_to_file()` | CSV (vždy dostupné) nebo .xls (vyžaduje `xlwt`) |

**Výpočet nové kalibrace (replika Excel logiky):**
- Fit: `QE = slope * Device + intercept` na rozsahu from–to
- PAP1: exponent `p=1`, ostatní zařízení `p=2`
- 4 kombinace Convert?/Use Int? → různé vzorce pro `new_mult`, `new_off`, `new_int`

### Závislosti
- `PySide6` — UI
- `csv` — export CSV
- `xlwt` (volitelné) — export .xls; pokud chybí, fallback na CSV s upozorněním
