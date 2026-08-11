Image Tools — Information
Created by Jan Moučka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------

Image Tools is a multi-tab application for browsing, analysing and
exporting camera images from ELI Laser experiments.
It consists of four integrated tools accessible via tabs at the top:
Image Finder, Image Slider, Shot Finder and Workshop.


=================================================================
GENERAL NOTES
=================================================================

  - Network roots: //users-L3.tier0.lcs.local  (Lab)
                   Z:\  (Office)
  - CPVA archiver: https://10.78.0.57:8443 (SSL cert not verified)
  - Supported image formats: PNG, TIFF, JPG, BMP
  - 16-bit PNGs are stored scaled so the camera's full scale lands on
    65535 (the archiver multiplies raw counts by 65535/(2^bits - 1)),
    so the Image Slider displays value/65535 — an absolute scale that
    is the same for every camera depth and every frame.
  - All background operations (scanning, loading, analysis) run in
    thread pools; the red "Stop All" button in the status bar halts
    everything.
  - Temp files created during viewing are cleaned up on exit.

-----------------------------------------------------------------


=================================================================
TAB 1 — IMAGE FINDER
=================================================================

Discover camera images by date/hour and correlate them with energy
and PV data from CSV files or the CPVA archiver.

SEARCH
  - Pick a date from the calendar and an hour (Lab time / UTC).
  - Choose data source: Lab (network share) or Office (Z: drive).
  - Results table lists found images with matching energy data.

ENERGY COLUMNS
  - Click ⚙ to choose which CSV columns to annotate:
    Waveplate, PTM1, PCM2, PCM4, PAP1, SBW4, Camp ON,
    E2–E5 Open, Back Ref.
  - Values come from the CPVA archiver per column, with the daily
    CSV files (dataof{YYYY}{Mon}_{DD}.csv) as a fallback. A frame
    is matched to a sample within 30 s (archiver) or 120 s (CSV).

FIND BY PV CONDITIONS
  - "Search by PV" browses the archiver channels, lets you set a
    condition per PV (min / max), and finds the time regions of
    the day where all of them hold. Each region gets a colour, and
    the frame closest to it is picked for every selected camera.

MULTI-DAY SEARCH
  - Pick a date range, the cameras and the hour; the result opens
    in a preview grid with Day and Camera tabs.
  - Per image: palette, brightness, overlays (circle / square /
    cross) — an overlay edit can be mirrored to every selected
    thumbnail, keeping each shape centred and scaled.
  - "Search again" re-searches the selected cells from a chosen
    hour in the background: it prefers energy-anchored candidates,
    rejects empty frames, logs each attempt and can be cancelled.

ACTIONS ON RESULTS
  - View: send selected images to Image Slider.
  - Save: export selected images to disk (with energy annotation).
  - Compare: side-by-side A/B pixel difference view.
  - Send to Workshop: hand the image over for editing.
  - Open Folder: open the source folder in Explorer.
  - Info: show detailed image metadata.

DISPLAY
  - Same palette selector as Image Slider.
  - Pixel normalization identical to Image Slider (value / 65535).



=================================================================
TAB 2 — IMAGE SLIDER
=================================================================

Browse and play back sequences of camera images frame by frame.
Supports both offline (recorded) and online (live) modes.

LOADING IMAGES
  - Click "Settings" to open the time-window dialog: pick the day
    (or several), the From/To time to the minute, and the cameras.
      * The dialog opens on "Now" — today, the current hour.
      * To is the EXCLUSIVE end of the window, so 12:00–13:00 is
        exactly one hour.
      * Two multi-day modes: a day → day range (click first and
        last day), or one time window per day ("Add day"). Both
        preset every day to 07:00–21:00, and the ⚙ column gives a
        single day its own window (marked with *).
      * The camera list is the union over every selected window,
        so an empty day or hour cannot blank it out.
  - Single camera or multi-camera mode.
  - Live mode is opt-in: tick "Live mode" in the time-window dialog
    (or press "Now"). Without it the window is loaded once and
    nothing follows the newest frame.
  - In live mode new files are picked up by a Windows directory
    watcher (plus a 0.5–5 s adaptive poll as a safety net) and the
    view auto-advances to the latest frame. A silently dead watcher
    is detected and recreated.
  - Refresh (⟳) reloads new frames without resetting position.
  - Live mode (⇢) jumps to the newest frame automatically; green =
    on, red = off. Turns off when you move the slider manually.

PREVIEW PRELOAD
  - With live mode off the whole loaded window is preloaded at low
    resolution in the background, so dragging the slider repaints
    from memory instead of reading the share frame by frame. The
    frame you stop on is then re-rendered at full resolution.
    Progress is shown next to the frame info and hides when done.
  - A typical window (4 cameras, a few hours) is held completely, so
    every frame you drag across is shown. On much longer windows the
    preload samples evenly instead, and the sampling is visible: see
    the timestamp colours below.
  - The preview is deliberately low resolution while you are moving.
    It sharpens about 0.2 s after you stop.

TIMESTAMP COLOURS (multi-camera tiles)
  Each tile's clock shows the frame ACTUALLY on screen, never the one
  requested, and its colour says how the two relate:
    amber            showing exactly the frame the slider asked for
    blue, with "~"   showing the nearest PRELOADED frame instead of
                     the exact one. Normal while the window is still
                     preloading; the time shown is the frame you are
                     really looking at.
    red              this tile is genuinely behind — it has not got
                     the frame it was asked for yet.
  In single-camera mode the same "~" appears in front of the Prague
  time when a preloaded neighbour is being shown.

PLAYBACK
  - Scrub with the slider or use Play/Stop.
  - Play speed: 0.10 to 5 %/s, as a % of the loaded frames per second
    of real time. So 1 %/s over 6000 loaded frames plays 60 frames a
    second. Speeds above 5 %/s were removed: on a window of any size
    they need to skip most of what they play, so the file names scroll
    but almost nothing is actually shown.
  - While the preview carries playback the tick runs at 60 Hz rather
    than 30, which halves how many frames have to be skipped at the
    same playback speed.
  - Two play modes: discrete (frame-by-frame) or continuous
    (time-based interpolation).
  - F11 — focus view: everything but the image is hidden, window
    title bar included. Ctrl+F11 — watcher view: edge-to-edge, for
    a wall display. ESC or the same key returns.

DISPLAY OPTIONS
  - Color palette: Grayscale, Gradient, Hot, Binary, Black & White,
    Viridis, Plasma, Inferno, Jet, Turbo.
  - Brightness: additive offset, -255 to +255. Auto puts the frame's
    median at mid-grey without clipping the highlights.
  - Contrast: multiplicative gain around the frame's own black level,
    so raising it spreads the signal instead of crushing the picture.
    Auto is a percentile auto-stretch — that is the box to tick to get
    the picture an auto-scaling viewer (ImageJ, Windows Photos) shows.
    While an "Auto" box is ticked its slider is greyed out but still
    moves to the value the auto pass actually applied; unticking it
    puts your own value back.
  - All four controls work on every palette, "Default" included.
  - Subtraction mode: load a reference frame; subsequent frames show
    the pixel-wise difference. Threshold slider suppresses noise, and
    the difference statistics are shown next to it. The reference is
    named in a green "Ref:" strip under the camera name, and a
    warning appears if it no longer belongs to the loaded set.
  - Pixel normalization: 16-bit value / 65535 — the camera's absolute
    full scale, identical for every camera bit depth. No PNG metadata
    is involved, so low-signal cameras (the PDxM1 diodes) show their
    real intensity without needing Auto contrast.

OVERLAYS
  - Cross reticle (centre of image).
  - Circle, square, and cross overlays — drag to position/resize.
  - Overlay settings: configure size, opacity, and limits.
  - Overlays can be burned into exported images ("Save with overlay").
  - PDxM1 / PDxM2 diode cameras have a configurable measuring grid:
    set the number of rows and columns and drag individual lines.
    Every line is stored as an absolute position in the image, so
    moving one does not shift the others, and cameras of the same
    type share one configuration.

MULTI-CAMERA LAYOUT
  - Cameras can be arranged automatically in justified rows that
    maximise the total image area (no grey letterbox around the
    frames), or placed by hand in the layout editor.
  - Each camera has its own slider; one camera can be the master
    that the others follow in time.

SPATIAL CONTRAST
  - Computes the spatial contrast of the current frame, with an
    automatic or manual threshold, a histogram of the values, and
    exclusion regions you can draw to ignore parts of the image.
  - The strongest N points can be marked in the image.

POINTING ANALYSIS
  - Run on the loaded image set (single or multi-cam).
  - Fits a Gaussian to each frame to extract beam centroid (cx, cy)
    and beam waist (w0 x h0).
  - Results shown as scatter + histogram panel with zoom/pan.
  - Click any point in the scatter plot to jump to that frame.
  - Save Plot exports the current zoom state to PNG.
  - Calibration shapes: circle, square, cross for spatial reference.

SAVING
  - Save Image: current frame.
  - Save Range: all frames between Set From / Set To marks.
  - ±N frames: include N frames before and after each saved frame.
  - Optional: save metadata sidecar .txt file (PNG tEXt chunks).
  - The save dialog opens in a LOCAL folder the first time, then
    follows wherever you last saved. It used to open on the scratch
    share, which the dialog has to enumerate before it can appear —
    from the office network that name is unreachable and every Save
    Image click waited out the full SMB timeout (~48 s) first.
    Saving to the share still works, it is just not the first thing
    the dialog has to reach.

TIMESTAMPS
  - Save Timestamp stores the current frame time.
  - Go to Saved jumps to the nearest matching frame in the current
    camera (useful for syncing across camera folders).

PV VALUES
  - Configure which CPVA channels to display (PTM1, PCM2, PCM4,
    PAP1, SBW4, Back Ref, Waveplate, etc.).
  - Values update in real time alongside image playback.

CAMERA PRESETS (multi-cam mode)
  - Save/load named sets of cameras via the Presets panel in the
    camera picker dialog (stored in %APPDATA%\ELI_ImageTools).


=================================================================
TAB 3 — SHOT FINDER
=================================================================

Find specific shots by PV target value across a date range.
Useful for locating shots at a given energy, waveplate angle, etc.

SEARCH CRITERIA
  - Select a date range (calendar + hour).
  - Choose one or more PV columns as search criteria:
    SBW4, PTM1, PCM2, PCM4, PAP1, Back Ref, Waveplate.
  - For each criterion: set target value, unit, and ±tolerance.
  - Also show: select extra PV columns to annotate results without
    using them as a search filter.

CAMERA SELECTION
  - Filter by camera name (text search).
  - Select one or multiple cameras for the results table.

RESULTS
  - Table shows the best-matching image path for each
    camera × day combination, with annotated PV values.
  - Click a row to preview the image with an energy overlay bar.
  - Open Slider: send the selected row to Image Slider.
  - Save Results: export table to CSV.
  - Save Images: export annotated images to a chosen folder.

TECHNICAL NOTES
  - Queries the CPVA archiver (HTTPS) per PV and per day; a daily
    CSV is used only as a fallback when a channel is missing, never
    to paper over a failed request (mixing the two sources used to
    make the waveplate alternate between two unrelated values).
  - A value is shown as the number itself, "n/a" (no sample in
    range) or "ERR" (the fetch failed and can be retried).
  - Step PVs such as the waveplate are archived only when they
    change, so they are resolved as "last value at or before this
    frame", not as the nearest sample.
  - Extra columns are matched within 30 s; the frame itself must sit
    within 30 s of the matched shot.
  - SBW4 values are corrected for transmission (×0.749).
  - Back Ref and PAP1 are displayed in mJ when < 1 J.
  - Clicking the Folder cell reveals the matched file in Explorer;
    double-clicking a row lists every shot inside the tolerance.
  - Pixel normalization: same value / 65535 pipeline as the Slider.


=================================================================
TAB 4 — WORKSHOP
=================================================================

Editor for images handed over from the other tabs
("Send to Workshop").

  - Every received image is a slot; switch between them in the
    combo box, remove one or clear them all.
  - Tools: Pan, Brush, Eraser, Line, Rectangle, Text, Crop,
    Eyedropper — with a draw colour and brush / line width.
  - Brightness and Contrast sliders with Reset, Auto B/C and Apply.
  - Palettes: Default, Grayscale, Inferno, Plasma, Viridis, Hot,
    Binary …
  - Reference diff: load a reference from file, then subtract it or
    show the absolute difference.
  - Undo and Reset to Source.
  - Save as PNG or TIFF.

