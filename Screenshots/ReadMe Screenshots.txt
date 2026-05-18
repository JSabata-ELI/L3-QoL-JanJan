Screenshots v2.0.3 — Information
Created by Jan Moučka, ELI Beamlines

Bugs / suggestions: jan.moucka@eli-beams.eu
-----------------------------------------------------------------

Screenshot capture utility for archiving camera images and monitor
screenshots from ELI Beamlines experiment sessions.


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
  - Captures the live screen content of a selected monitor.
  - Monitor selector shows available displays; click Identify
    to overlay numbered labels on each screen for 3 seconds.


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
  - All cameras
  - PLFE / PL Crosses / Diodes
  - Slits + Depol
  - PL - High Power / SPFE / Alpha / SP - High Power


=================================================================
AUTO CAPTURE
=================================================================

  - Auto every X seconds: captures automatically at a fixed
    interval for a set total duration.
  - Start live: continuous capture with live preview updates.
  - Both modes run in the background; a progress bar shows
    current status.


=================================================================
SAVING
=================================================================

  - Destination folder: choose where files are saved (browse
    button or type path directly).
  - Run folder: name a subfolder for the current session
    (default is a timestamp: YYYY-MM-DD__HH-MM-SS).
  - Detail notes: attach a short text note; it is saved as a
    sidecar .txt file alongside the images.
  - Files are named with the original UTC timestamp from the
    camera folder.


=================================================================
PREVIEW
=================================================================

  - "Preview region" button opens a separate preview window with
    basic image editing tools: crop, draw, colour picker,
    undo/redo.
  - Hover the Preview button to see a thumbnail popup of the
    last captured image.


=================================================================
GENERAL NOTES
=================================================================

  - Network root: //users-L3.tier0.lcs.local/cpva-image-2026
  - Camera directory entries are cached for 5 seconds to reduce
    network load.
  - DPI-awareness is enabled for accurate HiDPI screen capture.

-----------------------------------------------------------------
