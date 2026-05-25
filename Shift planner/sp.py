import os
import re
import sys
import webbrowser
from pathlib import Path

FOLDER = Path(r"C:\Users\jan.moucka\OneDrive - ELI Beamlines\L3-HAPLS\General")
PATTERN = re.compile(r"^Shift_plan_.*\.xlsx$", re.IGNORECASE)

# SharePoint site URL — použito pro sestavení online odkazu na soubor
SHAREPOINT_BASE = "https://elibeamlines.sharepoint.com/sites/L3-HAPLS/Sdilene%20dokumenty/General"
SHAREPOINT_FOLDER_URL = "https://elibeamlines.sharepoint.com/sites/L3-HAPLS/Sdilene%20dokumenty/Forms/AllItems.aspx?id=%2Fsites%2FL3%2DHAPLS%2FSdilene%20dokumenty%2FGeneral&viewid=8d6152c8%2Dd136%2D4760%2Db143%2D99eae8fed4f7"

def find_latest() -> Path | None:
    candidates = [f for f in FOLDER.iterdir() if PATTERN.match(f.name)]
    if not candidates:
        return None
    def sort_key(p):
        nums = [int(n) for n in re.findall(r"\d+", p.name)]
        return nums
    candidates.sort(key=sort_key, reverse=True)
    return candidates[0]

def open_online(local_path: Path):
    """Otevře soubor přímo online v Excelu přes ms-excel protokol (AutoSave funguje)."""
    filename = local_path.name
    # Sestavíme SharePoint URL souboru a otevřeme přes ms-excel:ofe protokol
    from urllib.parse import quote
    sp_url = f"{SHAREPOINT_BASE}/{quote(filename)}"
    excel_url = f"ms-excel:ofe|u|{sp_url}"
    os.startfile(excel_url)

if __name__ == "__main__":
    latest = find_latest()
    if latest:
        open_online(latest)
    else:
        # když nic nenajde, otevři složku na SharePointu
        webbrowser.open(SHAREPOINT_FOLDER_URL)
        sys.exit(1)