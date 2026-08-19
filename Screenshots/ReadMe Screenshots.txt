Screenshots — Information
Created by Jan Moučka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------

Screenshot capture utility for archiving camera images and monitor
screenshots from ELI Laser experiment sessions.


=================================================================
MODES
=================================================================

ARCHIVER MODE
  - Captures images directly from the CPVA network image store
    (//users-L3.tier0.lcs.local/cpva-image-2026).
  - Select individual cameras from the categorised grid or use
    preset buttons to select a predefined group at once.
  - Click Copy to fetch and save the latest image for each
    selected camera.

SCREENSHOT MODE
  - Captures the live screen content of a selected monitor, of
    all screens, or of the CSS window belonging to a camera.
  - Monitor selector shows available displays; click Identify
    to overlay numbered labels on each screen for 2 seconds.
  - Selecting cameras also preselects the monitors those cameras
    are shown on (per-station monitor layout).


=================================================================
CAMERA SELECTION
=================================================================

  - Cameras are grouped by beamline section:
    LT1-LT7, Compressor, L3BT (60+ cameras total).
  - Cameras unavailable at the current station are greyed out
    (hover shows which stations they belong to).
  - Station is auto-detected from hostname
    (L3-VIS01, L3-OPR1-3, L3-VIS02, etc.).

PRESETS (one-click multi-camera selection):
  - Built-in: All cameras + one preset per beamline section.
  - Config presets: create your own, rename or overwrite a
    built-in one. Custom presets are stored in
    custom_presets.json next to the program and survive
    restarts. Hover a preset button to list its cameras.
  - Clear all resets cameras, monitors and presets.


=================================================================
AUTO CAPTURE
=================================================================

  - Auto every X seconds / max Y cycles: captures automatically
    at a fixed interval. Y = 0 means run until stopped.
  - Start live: continuous capture with live preview updates.
  - Both run in the background; the progress bar keeps counting
    across auto cycles, and Stop aborts the running cycle.
  - With Preview on, auto capture reuses ONE preview window with
    a ◀ / ▶ cycle browser instead of opening a window per cycle.


=================================================================
SAVING
=================================================================

  - Destination folder: choose where files are saved (browse
    button or type path directly).
  - One output → the name field is a FILE name.
    More than one output → it is a FOLDER name; leave it empty
    to save straight into the destination folder.
  - Camera files are named  <camera>__YYYY-MM-DD__HH-MM-SS
    using the frame's own timestamp converted to Prague time.
    Monitor shots are named  <run time>_monitor<N>  /  _all.
  - Labels: per camera you can add a text prefix or suffix and
    an automatic 01, 02, 03 … counter to the file name. The
    counter advances after each successful copy; ↺ resets it.
  - Detail: attach a short text note; it is saved as a sidecar
    .txt file alongside the images.


=================================================================
PREVIEW
=================================================================

  - Tick Preview to open a grid of everything the run copied.
      Contrast / Brightness sliders, each with Auto (Auto parks
      the slider on the value it computed), Zoom and a Palette
      (Grayscale, Gradient, Hot, Viridis, Plasma, Inferno, Jet,
      Turbo).
      ✔ Save                — keep the files; images with a crop
                              or drawing are also written as
                              <name>_annotated.png
      🗑 Delete              — delete all files from the run
      🔄 Delete and Try Again — delete and copy again
  - Hover a thumbnail for a larger popup; click it to open the
    editor: zoom, contrast/brightness, palette, crop (with
    preview and Clear crop), freehand / line / rectangle drawing
    with colour and width, Undo, Clear drawing, Apply / Revert.


=================================================================
GENERAL NOTES
=================================================================

  - Network root: //users-L3.tier0.lcs.local/cpva-image-2026
  - Camera folder listings are cached and refreshed by a
    background worker every 3 seconds, so a copy does not wait
    for a cold network listing.
  - DPI-awareness is enabled for accurate HiDPI screen capture;
    window captures are cropped to the real visible bounds so no
    Windows shadow border is included.
  - The Diagnostics box logs every step of a run (folder
    resolution, chosen file, copy result, problems).

  - For whoever works on the code: STRUCTURE.md.

-----------------------------------------------------------------
