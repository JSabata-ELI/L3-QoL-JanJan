# Extractor — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `e.py` | Jediný soubor. CLI skript bez UI. |

---

## e.py

### Záměr
Rozbalí jeden `.zip` soubor ze `Z:\Software` do `_internal` složky každého programu v `TARGET_DIRS`. Slouží k hromadnému nasazení nové sdílené `_internal` složky (PyInstaller runtime) do všech programů najednou.

### Konstanty
- `SOFTWARE_ROOT` — `Z:\Software` — kořen s zip souborem a cílovými složkami
- `TARGET_DIRS` — seznam složek programů, do kterých se rozbalí `_internal`

### Funkce
| Funkce | Popis |
|--------|-------|
| `find_zip(root)` | Najde jediný `.zip` v root složce; varuje při více souborech, vrátí první |
| `extract_to(zip_path, dst_dir)` | Odstraní starou `_internal/`, rozbalí zip s oříznutím path prefixu |
| `main()` | CLI flow: ověří root → najde zip → zobrazí přehled složek → potvrzení → rozbalení |

### Chování
- Prefix cesty v zipu se automaticky detekuje — hledá část `_internal/` v entry jménech
- Staré `_internal/` je smazáno před rozbalením (bez archivace)
- Kompatibilní se strukturou PyInstaller `--onedir` buildu
- Výsledek: `_internal/` přímo ve složce programu, např. `Z:\Software\Image Tools\_internal\`
- Interaktivní CLI — vyžaduje spuštění v konzoli (potvrzení `y/N`)

### Závislosti
- `zipfile`, `shutil`, `pathlib` — stdlib, žádné externí závislosti
