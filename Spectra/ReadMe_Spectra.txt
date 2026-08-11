Spectra – Spectrometer Analysis Tool
============================================

A PySide6 desktop application for analysing spectrometer data (SPIDER by
default) from the CPVA archive or in real time.  Run it with:

    python sp_t.py


OVERVIEW
--------
Everything is controlled from the sidebar on the left.  Two modes, switched in
the "Mode" box:

  Archive mode  – Load a day from the CPVA archive, mark time regions in the
                  search graph, average the spectra inside them, compare and
                  export them.

  Live mode     – Poll the newest shots every 3 s and show a rolling average
                  of the last N spectra.


DEPENDENCIES
------------
  pip install PySide6 numpy matplotlib


QUICK START – ARCHIVE MODE
--------------------------
1. Click "Load day…" and pick a day.  Plain click toggles a day, Ctrl+click
   adds the whole range from your previous click (multi-day is allowed).
2. The top graph shows the search signal (SBW4 energy by default) for that
   day.  Click a row in the PV table to plot a different signal.
3. Enable "Select" in the top toolbar and drag over the graph to mark a time
   region.  Repeat for as many regions as you need.
4. Click "Analyze".  Every not-yet-analysed region is fetched and averaged in
   the background; the progress bar tracks it.
5. Switch the averaging method, normalisation, colouring, smoothing or the
   variation band at any time — no refetch is needed, all methods are computed
   up front.
6. Export with "Export results".


QUICK START – LIVE MODE
-----------------------
1. Click "Live" in the Mode box.
2. Set "Average last N".
3. Click "Start Live".  The last 10 minutes are preloaded, then the newest
   shots are polled every 3 s.  The graph is only redrawn when a new shot
   actually arrives, so the UI stays smooth.
4. "Stop Live" freezes the display.  The buffer keeps up to 2000 spectra.


SEARCH PVs AND PRESETS
----------------------
The "Search data by" box holds the list of signals you can search in:

  - The table lists them as Label | Channel.  Click a row to plot it,
    double-click the label to rename it.
  - "+ Add PV…" opens a channel browser (the full CPVA channel list is loaded
    once and then filtered locally).  The inline search field above the table
    does the same thing faster.
  - "✕ Remove" removes the selected PV.
  - Presets: pick one from the combo box, or use + / ✎ / 🗑 next to it to
    create, rename and delete presets.  "Edit…" opens the full preset editor.

The dispersion-order PVs (GDD, TOD, FOD) used for region colouring and for the
details block are fixed constants at the top of sp_t.py.


SPECTRUM PV AND THE WAVELENGTH AXIS
-----------------------------------
"Change…" in the active card picks the intensity (Y) waveform.

  - A channel ending in _X or _Y is treated as one half of a pair, so the
    wavelength axis is taken from the matching _X channel automatically.
  - Any other waveform (e.g. …:FundY) usually has no _X twin, so the tool
    asks how to build the wavelength axis:
        * copy it from another PV
        * copy it from a PV and apply a linear transform  x' = a·x + b
        * load it from a CSV / text file
        * use the plain sample index (0, 1, 2 …)

The resolved pair is always shown under the card as "→ X: … / Y: …".


DISPLAY OPTIONS
---------------
  Average        – Mean / Median / Trimmed mean 10% / Sigma-clipped mean
                   (the trim fraction 10 % and the 3σ threshold are fixed).
  Colour by      – Selection order, or GDD / TOD value (adds a colorbar).
  Normalize      – None / Peak / Area.
  Variation band – ±1σ or the 10–90 percentile band around the average.
  Smooth         – moving average with an adjustable window.
  Show search graph – hides or shows the upper graph.
  Spectrum range [nm] – From / To, plus "Auto-fit range to data on Analyze".

Per region (in the region list): eye icon = show/hide, expand = details with
energy, GDD/TOD/FOD and the spectral metrics (peak wavelength, peak intensity,
centroid, FWHM, RMS bandwidth, area), and a switch to overlay the individual
spectra of that region (capped at 400 traces).

"Compare regions" plots the difference (A − B) or ratio (A ÷ B) of two
analysed regions as an extra curve.


GRAPH INTERACTION
-----------------
  - Both graphs have a crosshair with floating X/Y value labels.
  - Pan/zoom is remembered across redraws; "Home" resets it.
  - "Select" and Pan/Zoom are mutually exclusive — turning on Pan or Zoom
    switches Select off.
  - Right-click inside a graph for: Axis limits…, Axis labels…, major /
    minor grid, Y scale linear / logarithmic, Reset view.
  - Subplot margins changed in the toolbar's Subplots dialog are saved.


EXPORT
------
"Export results" writes a CSV of the data and/or a graph image (PNG, PDF,
SVG).  One CSV holds everything:

  - "# Spectrum details" — one row per region: date, time range, number of
    spectra, method, SBW4 energy, GDD/TOD/FOD and all spectral metrics.
  - a blank line
  - "# Curve data" — a wavelength column, then the averaged curve + std for
    every region, one column per live shot, and the comparison curve if it is
    enabled.
  - Decimal separator: period.  Column separator: semicolon, announced by a
    "sep=;" first line so Excel opens the file without an import wizard.


CONFIGURATION FILES
-------------------
User settings are stored in  %APPDATA%\ELI_Spectra\ :

  search_pvs.json       current search-PV list
  search_presets.json   named presets
  spec_pvs.json         spectrum Y channel + wavelength-axis configuration
  layout.json           splitter sizes + subplot margins

Deleting that folder resets the application to its defaults.


ARCHITECTURE NOTE
-----------------
The whole application is a single file (sp_t.py).  The main class is
SpectraWidget (QWidget); it can be embedded in a parent application (it exposes
cancel_scan() for a global "Stop All") or run standalone via the __main__ block
at the bottom.

For a detailed description of every class, method and internal data structure
see STRUCTURE.md in the same folder.


CONTACT
-------
Jan Moučka – jan.moucka@eli-beams.eu
ELI Laser, L3 Quality-of-Life tools
