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
  - 16-bit PNGs are stored scaled onto 65535 (the archiver multiplies
    raw counts by 65535/(2^bits - 1), with the bit count taken from
    each frame's own peak). The tabs display every frame of a camera
    on one fixed range, so brightness is comparable between frames and
    between cameras — see "Pixel normalization" under Image Slider.
  - All background operations (scanning, loading, analysis) run in
    thread pools; the red "Stop All" button in the status bar halts
    everything.
  - Temp files created during viewing are cleaned up on exit.
  - A busy day can hold more samples than the archiver will
    answer for in one go (it refuses somewhere above 110 000 and
    the request simply fails). The programme notices this and asks
    for the day in halves until each piece fits, so a search over a
    busy day returns its shots instead of an empty table. It is
    only slower, never incomplete.

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
  - Pixel normalization identical to Image Slider — the camera's own
    fixed range, so one palette colour always means one intensity, in
    every frame and on every camera.
  - "Auto stretch" (off by default): spreads the frame over its own
    p0.5..p99.5 window instead. It makes a dim frame readable and gives
    up the comparability above, so leave it off unless you need it. It
    applies to the preview AND to what Save / Send to Workshop write, so
    an exported frame looks like the one you saw.
    The Binary and False Colors palettes always map per frame, whatever
    this box says — that is how those two are meant to work.
  - "Gamma" slider (0.30-1.50, default 1.00) with its own Auto box and
    reset — the same control the Image Slider has, so a frame looks the
    same in both tabs at the same setting. Below 1.00 lifts the dark end
    WITHOUT giving up comparability: the curve depends only on the pixel
    value, so one colour still means one intensity. 0.50 is the working
    point on these cameras. Auto picks the curve per frame (median to
    45 % of the range), which does give up comparability, and parks the
    slider on the value it used. The row is greyed out while Auto
    stretch is on, because that already sets both ends of the frame.
  - Under the preview: the frame's peak in raw counts, how much of the
    camera's full scale that is, the sensor bit depth, and which of the
    two mappings produced the picture. A frame reading "3 % FS" is a
    weak frame, not a broken display.



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
  - EVERY camera updates on every shot. The camera whose radio button
    is ticked (the main one) sets the moment the grid is showing, and
    the others are put on that same moment. A camera that receives a
    picture a moment later than the main one now shows it straight
    away instead of waiting for the next shot.
  - A camera that saves pictures far less often than the main one is
    kept at ITS own newest picture rather than being left frozen. Its
    clock is then red, because it is not on the same moment as the
    rest of the grid.
  - A camera that really received nothing new keeps its last picture.
    That is the only case a tile stands still.
  - Switching live mode on or off reloads every tile: all cameras are
    put on the current moment and redrawn at the resolution that mode
    uses.

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
                     the frame it was asked for yet, or (in live mode)
                     it is showing its own newest picture because the
                     main camera has run more than 3 s ahead of it.
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
    title bar included. The window is also cut down to the cameras
    themselves: the space around and below the frames is not painted
    at all, so you see through it and clicks there reach the program
    behind. Drag any frame to move the focus window, drag the edge of
    the camera block to resize it. Ctrl+F11 — watcher view:
    edge-to-edge, for a wall display. ESC or the same key returns.

DISPLAY OPTIONS
  - WHICH CAMERAS A DISPLAY SETTING HITS (several cameras on screen):
    Palette, Auto-stretch, Brightness, Contrast and Gamma go to the
    cameras selected in the layout, or to every camera when nothing is
    selected. They are applied AT THE MOMENT YOU MOVE THE CONTROL, and
    only then. Selecting another camera afterwards does NOT copy the
    current settings onto it — each camera keeps what it was last
    given, so you can put one camera on Binary and leave the rest on
    Gradient. Each control also travels on its own: changing the
    brightness of a camera does not repaint its palette.
    Saving works the same way — each camera is written out with the
    palette it is shown with.
  - Color palette: Default, Grayscale, Gradient, Binary, False Colors,
    Rainbow, Hot, Black & White, Viridis, Plasma, Inferno, Jet, Turbo.
    Binary and Rainbow are the NI Vision palettes of those names.
    Binary was MEASURED against the real NI viewer (August 2026) and now
    matches it: 15 colours — red, green, blue, yellow, magenta, cyan,
    orange, rose, chartreuse, violet, azure, spring green, light red,
    light green, light blue — repeating, with black below the first
    band. One colour covers a fixed 1024 counts of the 16-bit scale, so
    a colour boundary is always the same pixel value: the same colour
    means the same intensity in every frame and on every camera, and you
    can read a level off the picture. That also means Binary ignores
    Brightness, Contrast and both Auto boxes — they are greyed out, the
    same as on Default — because any stretch would move every pixel to a
    different colour. A dim frame therefore shows only two or three
    colours; that is not a fault, it is what a dim frame IS.
    Because its colour changes with the value, Binary is also drawn
    without any smoothing: fitting the frame into the view picks whole
    pixels instead of averaging them, since the average of two cycle
    colours is a third, unrelated one and averaging turned the picture
    into grey mush. Expect it to look grainy — that grain is the data.
    Rainbow goes blue to red with a wide green
    middle, black at 0 and white at 255.
    False Colors is the dark blue → violet → pink → white ramp, crowded
    at the bottom so faint detail keeps most of the range; it is spread
    over the frame's own range, so it shows the signal even without Auto.
  - Brightness: additive offset, -255 to +255. Auto is a full auto
    level of the frame (p0.5..p99.5 onto 0..255), the same window
    ImageJ's auto display range uses.
  - Gamma: slider 0.30-1.50 with its own Auto box and reset, next to
    Brightness and Contrast. This is the control for "the picture is
    dark but I want to keep the numbers honest": below 1.00 it lifts the
    dark end of the range, and unlike Auto contrast it does NOT rescale
    the frame to its own content, so the same pixel value still gives
    the same colour in every frame and on every camera. 0.50 is the
    working point on these cameras (a PASF1 frame goes from mean code 21
    to 73). What you give up is that equal count differences stop
    looking equally big - the dark end is stretched and the bright end
    compressed - so the readout always names the gamma in use.
    Auto picks the curve that puts THAT frame's median at 45 % of the
    range; being per-frame it gives up comparability the same way Auto
    contrast does, and it parks the greyed-out slider on the value it
    used so you can always read what you are looking at.
    Auto contrast already sets both ends of the frame, so the whole
    gamma row is greyed out while it is on - two corrections fighting
    over the same pixels is not a setting worth having.
  - Contrast: multiplicative gain around the frame's own black level,
    so raising it spreads the signal instead of crushing the picture.
    Auto is the same auto level as Auto brightness — either box gives
    the picture an auto-scaling viewer (ImageJ, Windows Photos) shows,
    and ticking both does not level the frame twice.
    While an "Auto" box is ticked its slider is greyed out but still
    moves to the value the auto pass actually applied; unticking it
    puts your own value back.
  - The three rows are labelled "Con:", "Bri:" and "Gam:" and each shows
    the number in use to the right of its slider (contrast -127..+127,
    brightness -255..+255, gamma 0.30-1.50) — the setting on screen is
    always readable, including the value an "Auto" box picked. The names
    are abbreviated to make room for that number; hover the name, the
    slider or the number for the full name and what it does.
  - All four controls work on every palette EXCEPT "Default".
    "Default" is view-only: the file exactly as it is stored in the
    folder, no palette and no brightness/contrast, so those four
    controls are greyed out while it is selected. Pick "Grayscale" for
    the same picture with the controls live. Subtraction still works
    on "Default".
  - Subtraction mode: load a reference frame; subsequent frames show
    the pixel-wise difference. Threshold slider suppresses noise, and
    the difference statistics are shown next to it. The reference is
    named in a green "Ref:" strip under the camera name, and a
    warning appears if it no longer belongs to the loaded set.
  - Pixel normalization: the 16-bit value against the camera's own
    range. No per-frame auto-scaling, so one palette colour always
    means one intensity, and low-signal cameras (the PDxM1 diodes)
    show their real intensity without needing Auto contrast.
    Why the archive does NOT destroy the intensity: it stores
    value = counts x 65535/(2^bits-1), a FIXED factor within a frame,
    not a per-frame stretch. Measured on the PD reference frames — a
    warmup frame whose peak is 3378 counts stores a maximum of 54061
    (= 3378 x 16.0037), not 65535, and dividing back gives exactly 3378.
    A dark frame therefore stays dark in the file.
    The PNG "MaxValue" field is the PEAK OF THAT FRAME in counts, not
    the sensor range (it reads 4095 only on a saturated 12-bit frame),
    so nothing here divides by it.

  - Why the range is per camera and not simply 65535: the archiver
    picks that factor from EACH FRAME's own peak, rounded up to the
    next power of two. A camera whose peak sits right on one of those
    steps therefore gets a factor that changes from frame to frame,
    and dividing everything by 65535 made its picture jump between two
    brightnesses with nothing physical behind it — C03-081-PCW3NF
    alternated between dark orange and green every few seconds at the
    same shot energy. The app now shows every frame of a camera on the
    LARGEST range that camera has been seen to use, so the picture
    holds one appearance. A camera that never sits on such a step is
    displayed exactly as before.
    The learned range per camera is remembered in
    %APPDATA%\ELI_ImageTools\cam_depths.json. Delete the file to make
    every camera learn again from the next frames it reads.

OVERLAYS
  - Cross reticle (centre of image).
  - Circle, square, and cross overlays — drag to position/resize.
    The cross follows the mouse while the button is held: click to drop
    it, hold to drag it, click somewhere else to move it there.
  - Overlay settings: configure size, opacity, and limits.
  - Overlays can be burned into exported images ("Save with overlay").
    With it ticked, the PV values are added as a white bar under the
    picture. Two things decide what appears there: the tick box itself,
    and the eye column in the PV values list — a PV switched to the
    crossed-out eye is left out. The bar goes into the extra file whose
    name ends with "_annotated", not into the file you name in the save
    dialog. A value the archiver cannot deliver is written out as a
    marker ("ERR" / "n/a"), so the bar is never quietly left off; if the
    bar itself cannot be drawn, the "Saved" box says so and the reason
    is written to image_tools_diag.log.
  - PDxM1 / PDxM2 diode cameras have a configurable measuring grid:
    set the number of rows and columns and drag individual lines.
    Every line is stored as an absolute position in the image, so
    moving one does not shift the others, and cameras of the same
    type share one configuration.

MULTI-CAMERA LAYOUT
  - Cameras can be arranged automatically, or placed by hand in the
    layout editor. Auto-arrange searches split layouts (rows, columns
    and any nesting of the two) and keeps the one where the SMALLEST
    frame comes out as large as possible, so a portrait diode array
    next to square far-field cameras no longer forces everything into
    one short row with half the screen left empty. Diode arrays are
    asked for twice the area of a normal camera, because they are the
    ones read for per-diode detail. The tiles always reach every edge
    instead of sitting as a small block in the middle of a large grey
    rectangle.
  - After OK in the camera selection the cameras come up in exactly
    the automatic arrangement, unless a layout was placed by hand for
    that same set of cameras. Only a layout you actually dragged is
    remembered; leaving the editor on Auto-arrange keeps the
    arrangement automatic, so it is recomputed for the real camera
    area instead of being frozen at the editor's proportions.
  - The editor previews the arrangement on a board shaped like the
    real camera area, so what it shows is what the cameras do.
  - Each camera has its own slider; one camera can be the master
    that the others follow in time.

SPATIAL CONTRAST
  - With several cameras on screen it measures the one camera you have
    clicked in the layout. With no camera, or more than one, selected
    the Measure button says so and does nothing.
  - Computes the spatial contrast of the current frame, with an
    automatic or manual threshold, a histogram of the values, and
    exclusion regions you can draw to ignore parts of the image.
  - The strongest N points can be marked in the image.

POINTING ANALYSIS
  - Run on the loaded image set. With several cameras on screen it
    analyses the one camera you have clicked in the layout; with no
    camera, or more than one, selected Run Analysis says so and does
    nothing.
  - Fits a Gaussian to each frame to extract beam centroid (cx, cy)
    and beam waist (w0 x h0).
  - Thr: % of the peak a pixel must reach to count towards the
    centroid. Default 90 % — the bright core only. Lower it if the
    beam gets cut off, raise it if noise pulls the centroid.
  - Results shown as scatter + histogram panel with zoom/pan.
  - Click any point in the scatter plot to jump to that frame.
  - Show Path draws the beam trajectory coloured by time (blue =
    start, red = end) with a colour bar beside it.
  - The colour bar has a draggable time cursor: drag the white dot
    along the bar and a green dot walks the beam path to where the
    beam was at that moment, the time (HH:MM) is shown next to the
    bar, and the image viewer follows to the matching frame. The
    cursor snaps to real shots, so it never lands in a gap between
    measurements.
  - If the analysis spans more than one day, a line crosses the
    colour bar at every midnight and the date (MM-DD) is written next
    to each day's part of the bar — the HH:MM readout alone would not
    say which day it is.
  - Delete mode: click a point to delete just that one, or drag a
    rectangle to delete everything inside it. Undo delete (or Ctrl+Z
    over the graph) takes back the last deletion, one step at a time;
    Restore All brings back everything.
  - Save Plot exports the current zoom state to PNG. The day markers
    are part of the figure; the draggable cursor and its time readout
    are not.
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
  - "◀ 📌 Pin" / "📌 Pin ▶" (or Alt+← / Alt+→) step to the previous /
    next SAVED TIMESTAMP, in time order. These are the amber buttons in
    the Timestamps group — not the grey ◀ ▶ arrows in Playback, which
    move ONE IMAGE at a time.
    Stepping is relative to where the timeline currently sits, so it
    still works after scrubbing or playback, and it does not wrap around
    at the first/last pin (the status line says when you are there).
    The status line shows "📌 2/5", the frame it landed on, and the
    residual Δt to the saved moment.

PV VALUES
  - Configure which CPVA channels to display (PTM1, PCM2, PCM4,
    PAP1, SBW4, Back Ref, Waveplate, etc.).
  - Own PVs: the search box under the presets browses ALL archiver
    channels. Type fragments — they are AND-matched with wildcards
    between them, so "hapls sbw4" finds every channel holding
    "hapls" and, later, "sbw4" (i.e. *hapls*sbw4*). Click a result
    (or press Enter) to add it; a bare name typed in full is added
    literally, even when the archiver list is unavailable.
  - Added PVs get a checkbox of their own: untick one to keep it in
    the list without reading it. They are named after their channel,
    are shown in the PV panel and the overlay, and are burned into
    saved images exactly like the presets.
  - GIVE A PV YOUR OWN NAME. Every row in "Selected PVs" has a
    "show as" box. Type a short name there and that is what the
    image overlay, the PV table and the burn-in on saved images
    show, instead of a channel name like
    "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy" which is far too long to
    read over a picture. Presets can be renamed the same way.
      · Leave the box empty to keep the PV's own name.
      · The name is only what is DISPLAYED. Behind it the PV keeps
        its own name, so a name cannot move a channel letter,
        repoint a formula, or hide which channel is being read —
        the full channel is still in the tooltip of the PV table
        and of the row in the picker.
      · Names survive a restart, and unticking a PV does not throw
        the name away.
  - Own formulas (derived PVs), the same idea as in the CSS Logger:
    every PV carries a CHANNEL LETTER shown on its tick box (A, B, …),
    and a formula row at the bottom of the picker computes a new PV
    from those letters as a Python expression — B/D, A*0.749,
    round(A-B, 2). Give it a name and, if it has one, a unit.
      · The letters are positional, so they move when the PV list
        changes. Each formula stores which PV every letter stood for
        and is re-lettered from that, so adding or removing a PV can
        never repoint a formula at a different one. The presets come
        first, so A…H never move.
      · A formula may use an earlier formula's letter. Using one
        defined BELOW it, or itself, reads n/a — they are evaluated in
        row order. The row flags it with ⚠ and the tooltip says why.
      · A source PV is read even when it is not ticked itself, so a
        formula never quietly reads n/a because its input is hidden.
      · A missing source, a division by zero or a deleted source PV
        gives n/a, never a number built on the wrong PV. An
        approximate or held source passes its ~ / (older shot)
        marker on to whatever is computed from it.
  - The eye column in the PV table switches a PV off in the IMAGE
    OVERLAY only (👁 shown, 🚫 hidden). The value keeps being read and
    stays listed in the table with its number — hiding is about the
    picture, not about the reading. The burn-in follows the eye, so a
    saved file shows what the screen showed.
  - The selection, the added PVs, their names, the formulas and the
    eye state survive a restart
    (%APPDATA%\ELI_ImageTools\slider_ui_state.json).
  - Values update in real time alongside image playback. A frame
    change refreshes the panel immediately; changes arriving faster
    than 0.5 s are coalesced into one archiver query. At 3.3 Hz that
    is about two refreshes a second, and asking any faster cannot
    produce a fresher number: the archiver needs about a second to
    publish a shot either way. Requests never queue up behind the
    shots - only one is ever in flight, and the next one always asks
    about the newest frame, so a long run cannot build a backlog.
  - THE PANEL WAITS FOR THE ARCHIVER. An image is on the share about
    0.02 s after the shot, but its archived sample only becomes
    readable roughly a second later (measured 0.2-2.5 s). So at the
    moment a new frame appears, the values for it do not exist yet.
    The panel now says so ("no data yet") and keeps asking until they
    land, which normally takes one retry; it gives up after 20 s,
    when "nothing was archived for this frame" is the true answer.
    Until then it goes on showing the last number it did read, and
    labels it with how far back that shot was — "12.3 J (-28 s)".
    Before this, that older number was shown with a quiet "(old)"
    and nothing ever re-asked, so with one frame per shot the panel
    stayed ONE SHOT BEHIND the picture for as long as you kept
    shooting.
  - FASTER THAN ~1 SHOT/S the newest frame is always younger than
    that delay, so the panel deliberately steps back to the newest
    shot the archiver HAS published and shows THAT shot's real
    numbers, labelled with the gap — "12.3 J (-0.6 s)". The numbers
    skip a shot or two; the IMAGES never do. It only reaches 3 s
    back (the scale of the publication delay), so a value from a
    quiet stretch or an earlier run is never dragged in: those
    frames wait and then read "n/a".
  - IT KEEPS READING ALL DAY. The panel only ever has one archiver
    request open at a time. If that one request never came back
    (a connection that died quietly, the archiver going away for a
    moment), the panel used to stay silent for the rest of the
    session and only a restart brought the values back. Two things
    now prevent that: a request that has not answered in 2 minutes is
    abandoned and a fresh one is sent, and even with nothing moving
    on screen the panel re-asks at least once a minute. Nothing to
    switch on, and nothing to click when it happens.
  - WHEN THE LASER STOPS the panel catches up on its own: it keeps
    re-asking until the last frame's own sample is published, then
    shows that number with no offset label. So the value you are
    left looking at always belongs to the last picture on screen.
  - Nothing of this applies away from the live edge. A frame older
    than the archiver's published point is answered exactly, so
    scrubbing, browsing the archive and the burn-in of saved images
    still resolve EVERY shot's own energy.
  - How to read a value (side panel):
      12.3 J          the shot's own archived value.
      ~12.3 J         approximate. Only the waveplate uses this now:
                      the motor was caught mid-move and the position
                      was snapped onto its 1000-count grid.
      12.3 J (-0.6 s) this frame's own sample is not available (yet),
      12.3 J (-28 s)  so the value of an earlier shot is shown — the
                      number in brackets is how much earlier. Greyed;
                      the tooltip names that shot's exact time.
      12.3 J          the same, when the age of the older reading is
        (older shot)  not known (e.g. right after a restart).
      no data yet     the archiver has not published this frame yet
                      and the PV has no earlier value to show. It
                      fills in by itself.
      n/a             no sample near this frame and the PV has never
                      reported one, so there is nothing to hold.
      ERR             the archiver request failed; the next refresh
                      retries it.
      ...             first read, still in flight.
  - The floating OVERLAY shows the numbers only. The status marks
    sit on ONE LINE along the bottom edge instead, separated by "·".
    They used to be stacked down the right-hand side, and that strip
    — as wide as "no data yet", reserved whether or not anything was
    lit — left a third of the panel empty next to every number:
      ⟳               the numbers lag the displayed frame by more
                      than 1 s — a read is on its way.
      no data yet     the archiver has not published the displayed
                      frame yet — the panel is waiting for it and
                      the values fill themselves in.
      older shot      at least one value was read for an EARLIER
                      shot than the picture. Which one is in the
                      side panel — the line only says that one is.
    Hover the overlay and it explains every mark that is lit. The
    marks are painted outside the layout and their line is reserved
    whether or not they are lit, so they cannot resize or move the
    overlay. As text in the rows they changed the line width every
    time they toggled and the panel hopped once per refresh. On a
    narrow overlay the marks are shortened with "…" rather than
    wrapped onto a second line, which would grow the panel the
    moment one lights up.
  - Tooltips that explain a value (the ones above, and the greyed
    numbers in the side panel) now stay up for two minutes. Windows
    takes an ordinary tooltip away after about ten seconds and then
    refuses to show it again until the pointer has left and come
    back, which is why they used to vanish mid-sentence.
  - The overlay never shrinks while the same PVs are selected, so a
    value that renders narrower (4.5 after 12.34, or "n/a") cannot
    make it jump either. Changing the selection or the font size
    re-measures it.
  - A value carried over as "(older shot)" / "(-28 s)" keeps that
    flag when it is burned into a saved image, so the file cannot
    claim a reading the panel did not. A frame saved sooner than the
    archiver publishes its sample burns "no data yet" for that PV —
    save it again later to get the number.

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
  - That list carries its own preview: clicking a shot in it shows
    that shot's image with the same value line under the picture.
    Every shot is looked up in the hour folder of its own time, so
    shots outside the hour of the day's best shot show up too.
  - Pixel normalization: same value / 65535 pipeline as the Slider —
    the camera's absolute full scale, so a colour means the same
    intensity in every result row.
  - "Auto stretch" (off by default) spreads each frame over its own
    p0.5..p99.5 window instead: dim frames become readable, rows stop
    being comparable. It applies to the preview and to Save Images and
    Send to Workshop alike. Binary and False Colors always map per
    frame regardless.
  - "Gamma" slider (0.30-1.50, default 1.00) with Auto and reset, the
    same control as in the other two tabs. Below 1.00 brightens the dark
    end and result rows STAY comparable, because the curve depends only
    on the pixel value. Auto is per frame (median to 45 % of the range)
    and therefore does not. Greyed out while Auto stretch is on.
  - Under the palette selector: the previewed frame's peak in raw
    counts, % of the camera's full scale, bit depth, and which mapping
    was used.


=================================================================
TAB 4 — WORKSHOP
=================================================================

Look at, measure and mark up images handed over from the other tabs
("➤ Workshop"). You can also open a file with "Open file…" or drop
one straight onto the picture.

THE ONE THING TO KNOW
  Everything in the Display section only changes how the picture
  LOOKS. It never changes the picture itself, so you can always put
  a slider back and get exactly what you started with. Only the
  Edit picture section and the Crop tool change the picture, and
  each of those steps can be undone. Saving always writes a new
  file — the file the image came from is never overwritten, and the
  program refuses if you aim at it.

THE TOP ROW OF TOOLS
  ✋ Hand      drag to move the picture. This also works in any
               other tool with the middle mouse button, with Ctrl
               held down, or with the space bar held down.
  🔍 Magnify   left click zooms IN on the spot you clicked, right
               click zooms OUT. Drag a box and that box is enlarged
               to fill the whole window. The mouse wheel zooms
               wherever the pointer is, in every tool.
  ↖ Select     click something you have drawn, then move it or drag
               its handles. Delete removes it.
  🖌 ╱ ➜ ▭ ◯ ⬠ T   freehand, line, arrow, rectangle, ellipse,
               polygon and text. Hold Shift for a perfect circle or
               square. For the polygon, click each corner and finish
               with a right click or a double click. For text,
               click first and then type; Enter confirms.
  📏 Ruler     measures a length. With a scale set it reads in
               millimetres, otherwise in pixels.
  ▦ ◎ Region   a rectangle or an ellipse; the Measure section then
               reports the numbers for that area.
  〰 Profile   draw a line, then double-click it (or press "Plot
               profile") to see the values along it, with buttons
               to copy them or save them as CSV.
  ✚ Marker     reads out the value at one pixel.
  ⧉ Crop       drag the part you want to keep.
  💧 Pick      takes a colour out of the picture to draw with.
  ⌫ Delete     click a drawn item to remove it.

  Below the tools: the drawing colour, line and brush thickness,
  text size, a Fill box, the magnification in percent with ＋ − Fit
  and 1:1, and Undo / Redo / Clear drawing / ↺ Original.

THE BAR AT THE BOTTOM
  Where the pointer is, the value under it, the size of the picture
  and the magnification. When the original file could be read back,
  the value is the real sensor count and its share of full scale;
  otherwise it is the 0-255 display value and the Measure section
  says so.

IMAGES
  Every received image is a slot — switch between them, remove one,
  or clear them all. The reference image chosen here is what the
  subtraction and the comparison views work against.

DISPLAY (never changes the picture)
  - Palette — the same set and the same names as the other tabs.
  - Histogram — shows where the values are. Drag the black line and
    the red line to choose which values are shown; double-click to
    reset. The Contrast "Auto" box puts them on the data for you
    (0.5 % … 99.5 %) and parks them there so you can see where they
    landed.
  - Contrast, Brightness and Gamma, each with a live number, a ↺
    back to neutral and an Auto box. Contrast spreads the values
    above the background, Brightness adds a constant to all of
    them, Gamma bends the scale so faint detail comes up without
    clipping the bright parts.
  - "Reset all display settings" puts everything back.

MEASURE
  Minimum, maximum, mean, standard deviation, sum, number of
  pixels, centre of mass and area — for the selected region, or for
  the whole picture with "Whole image". Double-click any number to
  copy it.
  Scale: draw a ruler across something whose size you know, select
  it, press "Set scale…" and type that size in millimetres. Lengths
  and areas then read in millimetres until you press Clear.

EDIT PICTURE (changes the picture, undoable)
  Rotate left / right / 180°, flip across or down, Resize…, and
  Subtract or Difference against the reference image. Anything you
  have drawn moves with the picture.

COMPARE
  Off, Side by side, Blend (with a mixing slider) or Difference,
  against the reference image. While a comparison is shown the
  picture on screen is a combination of two images, so drawing and
  measuring are switched off.

SAVE
  Save PNG… or Save TIFF… writes a copy of exactly what you see,
  including the drawing and the measurements unless you untick the
  box. "Save all…" writes every image in the Workshop into one
  folder, and "Copy" puts the picture on the clipboard.


=================================================================
FOR WHOEVER WORKS ON THE CODE
=================================================================

  STRUCTURE.md              what each module does and why
  image_tools_structure.md  line-by-line map of every file
  HANDOFF_02072026.md       older hand-over note, kept for context

  The bench_*.py and test_*.py files next to the sources are
  headless checks — they open no window and need no network. Run
  them before changing the viewer or the archiver client:

    test_day_split.py        the archiver refusing a whole day
    test_pv_resilience.py    the PV panel freezing
    test_scale_invariance.py the brightness scale (needs the lab)
    bench_live_rollover.py   live mode following the next hour
    bench_live_dot.py        the live indicator turning red
    bench_pv_wait.py         the PV panel waiting for the archiver
    bench_pv_live_multi.py   the same with several cameras
    bench_master_sync.py     the sliders following the master
    bench_live_slave_refresh.py  every camera repainting live
    bench_live_pv_load.py    PV reads not starving the pictures
    bench_drag.py            dragging the slider
    bench_play.py            playback

  sort_by_camera.py is a separate little utility: it takes the
  per-shot folders the camera screenshot tool writes and re-files
  them into one folder per camera.

-----------------------------------------------------------------
