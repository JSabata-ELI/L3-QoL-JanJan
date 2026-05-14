Image Tools v1.6.1 — Information
Created by Jan Moučka, ELI Beamlines

Bugs / suggestions: jan.moucka@eli-beams.eu
-----------------------------------------------------------------

Image Tools is a multi-tab application for browsing, analysing and
exporting camera images from ELI Beamlines experiments.
It consists of three integrated tools accessible via tabs at the top.


=================================================================
TAB 1 — IMAGE SLIDER
=================================================================

Browse and play back sequences of camera images frame by frame.
Supports both offline (recorded) and online (live) modes.

LOADING IMAGES
  - Click "Settings" to open the time-window dialog: pick a date,
    hour range, and camera folder(s).
  - Single camera or multi-camera mode (up to 4 cameras in a grid).
  - In online mode the app polls network folders every ~100 ms for
    new images and auto-advances to the latest frame.
  - Refresh (⟳) reloads new frames without resetting position.
  - Auto-follow (⇢) jumps to the newest frame automatically; turns
    off when you move the slider manually.

PLAYBACK
  - Scrub with the slider or use Play/Stop.
  - Play speed: adjustable as % of real recorded speed.
  - Two play modes: discrete (frame-by-frame) or continuous
    (time-based interpolation).

DISPLAY OPTIONS
  - Color palette: Grayscale, Gradient, Hot, Binary, Black & White,
    Viridis, Plasma, Inferno, Jet, Turbo.
  - Brightness offset: -255 to +255.
  - Auto-stretch: percentile-based contrast enhancement.
  - Subtraction mode: load a reference frame; subsequent frames show
    the pixel-wise difference. Threshold slider suppresses noise.
  - Pixel normalization: reads imgMaxValue from PNG metadata and
    applies a fixed [0, 4095] scale (matches Matlab imagesc output).

OVERLAYS
  - Cross reticle (centre of image).
  - Circle, square, and cross overlays — drag to position/resize.
  - Overlay settings: configure size, opacity, and limits.
  - Overlays can be burned into exported images ("Save with overlay").

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
TAB 2 — IMAGE FINDER
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
  - Values are read from daily CSV files
    (dataof{YYYY}{Mon}_{DD}.csv) and matched by timestamp (±2 s).

ACTIONS ON RESULTS
  - View: send selected images to Image Slider.
  - Save: export selected images to disk (with energy annotation).
  - Compare: side-by-side A/B pixel difference view.
  - Open Folder: open the source folder in Explorer.
  - Info: show detailed image metadata.

DISPLAY
  - Same 10-palette gradient selector as Image Slider.
  - Pixel normalization identical to Image Slider (imgMaxValue).


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
  - Queries CPVA archiver (HTTPS) for per-day highest-energy shots.
  - SBW4 values are corrected for transmission (×0.749).
  - Back Ref and PAP1 are displayed in mJ when < 1 J.
  - Pixel normalization: same imgMaxValue pipeline as Image Slider.


=================================================================
GENERAL NOTES
=================================================================

  - Network roots: //users-L3.tier0.lcs.local  (Lab)
                   Z:\  (Office)
  - CPVA archiver: https://10.78.0.57:8443 (SSL cert not verified)
  - Supported image formats: PNG, TIFF, JPG, BMP
  - 16-bit PNGs are normalized using imgMaxValue from PNG metadata
    to match Matlab's imagesc([0, 4095]) display.
  - All background operations (scanning, loading, analysis) run in
    thread pools; the red "Stop All" button in the status bar halts
    everything.
  - Temp files created during viewing are cleaned up on exit.

-----------------------------------------------------------------
