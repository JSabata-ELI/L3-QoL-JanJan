Spectra – SPIDER Spectrometer Analysis Tool
============================================

A PySide6 desktop application for analysing SPIDER spectrometer data from the
CPVA archive or in real time.  Run it with:

    python sp_t.py


OVERVIEW
--------
Spectra has two operating modes that are switched with the radio buttons at the
top of the window:

  Archive mode  – Load spectral waveforms for a chosen day from the CPVA
                  archive, pick time regions on the graph, compute averages,
                  and compare or export them.

  Live mode     – Stream the most recent shots in real time and display a
                  rolling average of the last N spectra.


DEPENDENCIES
------------
  pip install PySide6 numpy matplotlib


QUICK START – ARCHIVE MODE
--------------------------
1. Select "Archive" at the top.
2. Click the date button (defaults to today) and choose the day you want.
3. Click "Load day" – the time axis populates with shots from that day.
4. Switch to "Select" mode (radio button in the toolbar) and drag a region
   on the graph to mark a time interval.  Repeat for more regions.
5. Click "Analyse" to compute the average spectrum for every marked region.
6. Adjust the averaging method (Mean / Median / Trimmed mean / Sigma-clip)
   and hit "Analyse" again if needed.
7. Use the colour controls to colour regions by selection order or by
   dispersion values (GDD / TOD).
8. Export the results with "Export CSV" or "Save image".


QUICK START – LIVE MODE
-----------------------
1. Select "Live" at the top.
2. Set "Last N shots" to how many recent spectra to average.
3. Click "Start" – the graph updates automatically as new shots arrive.
4. Click "Stop" to freeze the display.


PV CONFIGURATION
----------------
The tool reads two waveform PVs for the spectrometer:

  X axis (wavelength)  – L3-SBDP-SPIDER:SpecDomain_Int_X
  Y axis (intensity)   – L3-SBDP-SPIDER:SpecDomain_Int_Y

These can be changed in the "PV settings" panel.  Use the search field to
look up available channels in the CPVA archive.  You can save frequently used
PV sets as named presets (Presets → Save / Load).

The dispersion-order PVs (GDD, TOD, FOD) used for region colouring are fixed
constants defined at the top of sp_t.py.


AVERAGING METHODS
-----------------
  Mean           – Simple arithmetic average of all spectra in the region.
  Median         – Median per wavelength bin (robust to outliers).
  Trimmed mean   – Discards a configurable fraction of extreme values before
                   averaging.
  Sigma-clip     – Iteratively removes samples that deviate by more than N σ
                   from the current mean, then averages the remainder.

The trimming fraction and sigma threshold are set in the controls next to the
method selector.


INDIVIDUAL SPECTRA OVERLAY
---------------------------
Tick "Show individual" to overlay raw spectra under the averaged curve.  A
"Subsample" control limits how many individual spectra are drawn when the
region contains many shots (keeps the display responsive).


AXIS AND LAYOUT
---------------
  - Click the axis-label fields to rename axes.
  - Type values into the limit fields (X min/max, Y min/max) and press Enter
    to fix the view; leave blank to use automatic limits.
  - Drag the subplot-adjust toolbar button to change margins; the new layout
    is saved automatically.


EXPORT
------
CSV export writes one file per region.  The file contains:

  - A header line with the region metadata (date, time range, method, etc.)
  - Two columns: Wavelength and Intensity
  - Decimal separator:  period  (compatible with the lab Excel locale)
  - Column separator:   semicolon  (set by a "sep=;" line at the top so
    Excel opens the file correctly without an import wizard)

Image export supports PNG, PDF, and SVG.


CONFIGURATION FILES
-------------------
User settings (presets, saved PV lists, window layout) are stored in:

  %APPDATA%\ELI_Spectra\

Deleting or renaming that folder resets the application to its defaults.


ARCHITECTURE NOTE
-----------------
The entire application is a single file (sp_t.py, ~3 000 lines).  The main
class is SpectraWidget (QWidget); it can be embedded in a parent application
or run standalone via the __main__ block at the bottom.

For a detailed description of every class, method, and internal data structure
see STRUCTURE.md in the same folder.


CONTACT
-------
Jan Moučka – jan.moucka@eli-beams.eu
ELI Laser, L3 Quality-of-Life tools
