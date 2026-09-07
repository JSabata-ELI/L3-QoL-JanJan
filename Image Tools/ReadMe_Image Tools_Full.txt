Image Tools — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19
  (main.py 283 lines, if_t.py 8821, is_t.py 24345, sf_t.py 4080,
   wk_t.py 7281, cpva_client.py 1335, img_scale.py 673)
-----------------------------------------------------------------
Short version: Readme Image Tools.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------

Image Tools is a multi-tab application for browsing, analysing and
exporting camera images from ELI Laser experiments.
It consists of five integrated tools accessible via tabs at the top:
Image Finder, Image Slider, Shot Finder and Workshop. (One Moment was
merged into the Image Finder on 04.09.2026 — picking a moment out of a
graph and seeing every camera at it is done there now, under PV Search.)


=================================================================
GENERAL NOTES
=================================================================

  - Images: //users-L3.tier0.lcs.local — one path, the same from the
    lab and from the office.
  - Daily CSV files (the fallback for older days): Z:\Salvation or
    the same folder on the scratch share.
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
TAB 1 — COMPARE DAYS   (this tab was called "Image Finder")
=================================================================

One camera across many days, side by side, so a change shows
itself. It is the mirror image of the Image Slider: the Slider puts
many cameras next to each other at ONE moment, this puts many DAYS
of one camera next to each other. Nothing else in the program does
that — the Shot Finder gives you a table and shows one row at a
time, so it never puts two days side by side.

It also still finds the frames: by date and hour, over a range of
days, by PV conditions, or with no target at all.

DAYS SIDE BY SIDE
  - The days come up as tiles filling the window, sized so that
    even the smallest picture is as large as the arrangement
    allows.
  - Every day is drawn on the SAME scale, and ONE set of
    brightness / contrast / gamma controls applies to all of them.
    This is deliberately unlike the Image Slider, where a control
    only affects the cameras that were selected when you moved it:
    here the whole point is that the same colour means the same
    intensity in every day. A per-day setting would make the
    comparison lie.
  - Why that matters: the archive does NOT store the camera's own
    numbers. It stretches every frame by its own factor to fill the
    16-bit file, so a weak day and a strong one can both sit near
    51 000 in the file. Shown as stored, three days of 400, 1600
    and 3200 counts all come out at the same brightness. This tab
    undoes that stretch, so those three days read as 24, 99 and 199
    — the real ratio.
  - "Auto" is worked out ONCE over all the days together, never per
    day. A per-day automatic stretch measures each day against
    itself, which is exactly what flattens them into looking the
    same.

REFERENCE DAY
  - Pick one day as the reference and every other tile shows how
    far it differs from that day. That is the quickest way to see
    when something drifted.
  - Right-click a day to make it the reference, right-click again
    to drop it. The reference day itself stays shown as it is.

SAVING THE VIEW
  - "Save view" writes the WHOLE view into one file — every row,
    including the ones below the fold, with the days labelled and a
    strip under the picture naming the tab, the cameras, the days,
    how the frames were picked and the reference day. Neither of the
    other tabs can do this; they save frames one at a time.
  - It asks two things first: PNG or PDF, and this tab or every tab.
    Every tab with PNG writes one file per tab, named after it; PDF
    puts one tab per page, each page as big as its own picture so
    nothing is scaled down or cropped. A tab you have never opened
    exports properly too.
  - The camera, the days, the reference day and the scale used are
    written into the PNG file itself as well, so the picture says
    where it came from.

THE DAY-BY-DAY TAB
  - A row is a DAY, and the next row down is the next day. A column
    is one camera, in the same place all the way down, sorted by
    name. When a day carries several picks — four marked regions, or
    five picked moments — they sit side by side inside that day's own
    row, each camera owning one column per pick, and the banner says
    how many. (Before, all four shared one slot and three of them
    were painted underneath the fourth.)
  - About three rows fill the pane and then it scrolls, and a plain
    mouse wheel steps a WHOLE DAY per notch. Ctrl+wheel still zooms.
  - Several moments picked at once give a tab per camera plus this
    one, exactly like a day search — the flat grid of every camera
    against every moment is what the exported file is for.

LOOKING AT ONE DAY CLOSELY
  - Click a day to open that frame in a close-up WINDOW, sized well
    above the stored picture, with the arrows under it stepping
    through the rest of the wall. The wall stays clickable behind it,
    so the next day can be opened without closing the window first.
  - This used to be a permanent "One frame" tab beside the walls: a
    half-size picture nobody switched to. The close-up is now as big
    as the screen allows, which is what looking at a frame closely
    was asking for.
  - "Open in Image Slider" hands over the frames that are on the
    wall — the ones actually picked, each with its own day caption.
    (It used to open the first camera's whole folder and ignore the
    rest, throwing away the selection it had just worked out.)

SEARCH
  - The Source group is laid out the way the Image Slider's Source
    group is: "Time window" and "Cameras" side by side,
    "PV Search..." under them, and "Load data" at the bottom — the
    button that actually goes and reads the frames. Actions (Save
    As..., Folder, Workshop and the list of picked cameras) sits
    directly below Source.
  - "Time window" opens the calendar — the SAME window the Image
    Slider and the Shot Finder open. A plain click takes
    one day. Ctrl+click adds or removes one day, weekends included.
    Ctrl+Shift+click takes the stretch back to the last day you
    clicked, and skips Saturdays and Sundays unless you tick them in
    the Mon-Sun row under the calendar; clicking the same stretch
    again takes it back out.
  - Pick a second day and a table appears with a row per day, each
    with its own From and To. There is nothing to switch on. Go back
    to one day and the table goes away again.
  - A day you have just added starts at 08:00-19:00, or 08:00 to the
    current hour if it is today. The From/To above the table moves
    every day at once; a row you type in keeps what you typed.
  - Nothing happens until you press OK. Cancel throws the whole
    change away and leaves the previous pick as it was.
  - There is no "Lab time" switch any more: the times are the real
    (Prague) clock, everywhere. There is no automatic "busiest hour"
    either — it leaned on a file that stopped being written in May
    and had been guessing 14:00 ever since, which was often the
    emptiest hour of the day. Pick the window yourself, and use
    "PV Search..." to find the moment inside it.
  - What is picked is written under the buttons — the day (or how
    many days) and the hours — and the dot next to that line turns
    green when the camera list for those days is ready.
  - The calendar is Monday first, weekends in red, with the month
    and the year on the bar above it.

CAMERAS
  - "Cameras" sits next to "Time window" — the day and the
    cameras are the one question of what to look at. The list is
    the cameras actually found in the days you picked; search by
    name or by number, and a click adds or removes one. "Select all"
    takes everything the search box is showing, "Clear" empties the
    choice.
  - Presets are on the right of the picker, and they are the SAME
    presets the Image Slider has: a set saved in one tab is offered
    in the other. Save stores the cameras picked at that moment,
    Rename and Delete work on the preset selected in the list.
    A preset can name a camera the chosen days have no recording of
    — loading it picks the ones that are there and says how many
    were missing.
  - The chosen cameras are listed under the Workshop button. Click a
    camera to look at its first frame, double-click it to take it
    out. The button itself carries no count any more: "0/92" said
    nothing useful, because nobody knows which 92 cameras a given
    day happens to hold.
  - There used to be a table of every camera taking up a whole
    column of the window, with a tick box, an image count and a
    "3.3+Hz?" column. It is this button now — the list is only
    interesting while you are choosing — and the space it took goes
    to the pictures.

PVs
  - Click "PVs" to open the picker. It is the SAME picker the Image
    Slider has, over the same list of PVs: the presets, any archiver
    channel you search for or type in full, and your own formulas.
    See "PV VALUES" under Image Slider for how it works.
  - What you add is shared with the Image Slider. What each tab
    remembers on its own is only WHICH of those PVs it shows.
  - The picked PVs are listed in the panel with their values for the
    frame in the preview. The eye in front of a PV takes it off the
    picture (the bar under the preview and the bar burned into a
    saved image) without stopping it being read.
  - Values come from the CPVA archiver per PV, with the daily CSV
    files (dataof{YYYY}{Mon}_{DD}.csv) as a fallback for the older
    days. A frame is matched to a sample within 30 s (archiver) or
    120 s (CSV).
  - Every PV is reported exactly as the archiver has it. SBW4 used
    to be multiplied by the compressor transmission (0.749) behind
    the scenes, so SBW4 here did not match SBW4 anywhere else; it no
    longer is. For the compressed energy tick "Compressed SBW4" in
    the picker — it adds SBW4, adds the formula SBW4 x 0.749 under
    that name and takes SBW4 off the picture, so only the compressed
    value is printed.
  - Back Ref and PAP1 are still shown in mJ, and the waveplate is
    still snapped onto its own 1000-count grid.
  - The CSV-only columns (Camp ON, E2-E5 Open) have no archiver
    channel. They are still reachable for older days: type the
    column name into the picker's search box.

PV SEARCH
  - "PV Search..." opens a window with a calendar, a graph and the
    list of machine values to plot. It starts on the days already
    picked in the Time window.

  - THE CAMERAS DO NOT HAVE TO BE PICKED FIRST. Which cameras to
    look at and which moments to look at are two separate halves of
    one question — the graph does not depend on a camera at all — so
    either may be set first. Press Search with no camera picked and
    the camera picker opens; the search then runs by itself. If you
    close the picker without choosing, the search is kept and starts
    the moment you do choose.

  - THE DAYS. Mark as many as you like, the same way as in the Time
    window: a plain click takes one day, Ctrl+click adds or removes
    one, Ctrl+Shift+click takes the range back to the last day you
    clicked. Under the calendar every marked day is listed with what
    is picked on it — moments and regions; click a day to show it.
    A day that carries something picked is never unmarked, so
    building the set up day by day cannot lose what came before.

  - PICKING MOMENTS. Click the graph: every click adds one more
    moment, on this day or on any other marked day, and they are all
    searched together. Each pick snaps to the nearest real sample of
    the primary value and carries a number on the graph — the same
    number the tiles wear. Ctrl+Z, or the "Undo" button beside the
    moment line, takes the last pick back (a marked region too);
    "Clear" forgets every moment. The result is one wall of
    every camera at every picked moment.

  - Moments and marked regions can both be on screen. When a moment
    is picked, the moments are what gets searched and the moment line
    says the regions are being ignored; "Clear" brings them back.
    Neither one deletes the other any more.

  - THE GRAPH, two ways. "One day" shows one marked day at a time —
    the arrows at the top right step through them and say which one
    you are on ("Thu 13.08.2026  2 / 3"). "All days" puts every
    marked day on one axis side by side, with a line and the day's
    name between them, so two days a week apart sit next to each
    other with no empty week in between.

  - MARKING A REGION. Drag on the graph. The region belongs to the
    day it was drawn on and stays there when you step to another
    day. In "All days" a drag that runs over midnight is split into
    one region per day rather than being quietly cut short. Each
    region gets a colour, and each one gets its own frame from every
    selected camera — three regions on a day give three pictures,
    not one.

  - BY A NUMBER INSTEAD. Tick "Find the first moment a PV was...",
    choose the value, the comparison (above / at or above / below /
    at or below / between) and the number. The search then looks for
    the FIRST moment the condition held and takes that one moment
    from every camera, so the pictures really are of the same shot.
    It only accepts a moment where the cameras were recording
    something, judged by each camera's own TotalPower; if no moment
    had all of them live it takes the first where some were, and
    says so. A frame that turns out to be empty is not passed off as
    a result — the next matching moment is tried.

  - IT SAYS WHICH WAY IT FAILED. "never happened on the marked days"
    is not the same answer as "happened, but no camera was recording
    then", and neither is the same as "the archiver did not answer
    for 13.08., so it cannot be said" — each is reported as itself.

  - SBW4 HAS TWO NAMES. It was re-archived under a new name on
    14.08.2026, but which name a given stretch of time was written
    to depends on the configuration that ran, so the date alone is
    only a guess. Whenever a day (or a marked region) comes back with
    nothing under one name, the other name is asked for before it is
    believed — and the status line under the graph says which value
    was read that way. A value that answers normally costs no extra
    request.

  - ADDING AND REMOVING VALUES. "Browse" searches every archiver
    channel; what is added is ticked, selected and scrolled to, and
    the status line names it. "Remove" takes the selected rows off
    and says which. Nothing on that list is silent any more.
    "Own axis" gives the selected value a scale of its own — two
    values in the same unit but a thousand times apart share a scale
    on which neither can be read. "Edit" changes the value's name and
    unit; it writes them to the shared list, so every tab shows the
    same name afterwards.

  - THE FORMULAS on the shared value list are drawn too, over the
    whole day, marked "(formula)". What they are built from is read
    even if you did not tick it, and the line BREAKS wherever one of
    those is missing rather than being drawn straight across the gap.
    A formula that cannot be worked out says why, on its row and on
    the status line, instead of just not appearing. A formula is not
    offered as the value to search BY: the peak inside a region and
    "the first moment X was above Y" are asked of the archiver, and a
    formula is not in the archive.

  - THE STRIP UNDER THE GRAPH is the graph's settings: Grid, Legend,
    Log Y, "Y range" (type the two numbers, or leave them empty for
    automatic), "Time range" (part of one day, as two clock times),
    "Ticks" (minutes between the time labels), "Whole day" (undo
    every zoom and hand-set range), "Copy" (the graph to the
    clipboard) and "Save" (the graph as a PNG).

  - THE MARKED RANGE, IN NUMBERS. Under the graph: how many samples
    each value has in the marked region, its mean, its spread and the
    two extremes — with the median, the peak-to-peak, the first and
    last value and the trend in the row's tooltip. Pick which region
    with the box above the table; a region just dragged takes the
    place by itself. A value with NO sample inside the range still
    gets a row: the value it was already sitting at, in amber, with
    "0" and the word "held", because a setting that did not move is
    not a broken sensor. Only when there is nothing before the range
    either does it read as dashes.

  - WALKING THE DAY, AND KEEPING WHAT YOU FIND. Back in the tab, the
    two arrows under "PV Search" step to the shot before or after the
    one on the wall — no need to reopen the graph. Stepping records
    NOTHING. "Save" puts the moment on the wall onto the list beside
    them and greys out once it is there; a click on a row goes back
    to that moment, and it comes back at once because the newest
    saved moments are looked for quietly in the background. "Forget"
    takes one off, "Clear" empties it. The list is NOT saved —
    closing the program empties it.

  - AND OVER TO THE SLIDER. "Send moment" opens the moment on the
    wall in the Image Slider and KEEPS the cameras picked there;
    "Send + cameras" carries this tab's cameras over as well. Either
    way the Slider lands on that moment, so the shots either side can
    be slid through.

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
  - Three rows — "Con:", "Bri:" and "Gam:" — exactly the block the Image
    Slider has: a slider, the number in use, a reset button and an Auto
    box on each. The rule is the same everywhere: ticking Auto takes
    that row over, greys out its slider and parks it on the value Auto
    actually used, and unticking gives you your own value back. All
    three apply to the preview AND to what Save / Send to Workshop
    write, so an exported frame looks like the one you saw.
  - Con (Contrast, -127..+127, default 0): a gain around the frame's own
    black level — it spreads the values apart or squeezes them together.
    Its Auto box is what used to be the separate "Auto stretch"
    checkbox: the frame is spread over its own p0.5..p99.5 window, which
    makes a dim frame readable and gives up the comparability above, so
    leave it off unless you need it.
    The Binary and False Colors palettes always map per frame, whatever
    this box says — that is how those two are meant to work.
  - Bri (Brightness, -255..+255, default 0): an offset added to every
    pixel — it lifts or darkens the whole frame, it does not spread it.
    Its Auto box is the auto LEVEL: the same p0.5..p99.5 window placed
    on the data, because no amount of shifting can spread a narrow
    range. Auto contrast and Auto brightness are one and the same pass,
    so ticking both does not level the frame twice.
  - Gam (Gamma, 0.30-1.50, default 1.00): below 1.00 lifts the dark end
    WITHOUT giving up comparability — the curve depends only on the
    pixel value, so one colour still means one intensity. 0.50 is the
    working point on these cameras. Auto picks the curve per frame
    (median to 45 % of the range), which does give up comparability.
    The whole row is greyed out while Auto contrast or Auto brightness
    is on, because either of those already sets both ends of the frame
    and gamma then has nothing left to bend.
  - Under the preview: the frame's peak in raw counts, how much of the
    camera's full scale that is, the sensor bit depth, which of the two
    mappings produced the picture, and any gamma, contrast or brightness
    on top of it. A frame reading "3 % FS" is a weak frame, not a broken
    display.



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
  - The watcher reports a file the moment it is CREATED, which on the
    share is before the picture inside it has finished arriving. The
    picture is now waited for (up to about a second) instead of being
    read half-written, failing, and being asked for again a second and
    a half later by the safety net. That wait is what used to make a
    new shot appear roughly a second after it was really there — one
    camera measured 1.5 s before, 0.06 s after.
  - A file older than two minutes is never waited for: a picture that
    was truncated in the archive hours ago is not going to finish, and
    browsing must not stall on it.
  - Refresh (⟳) reloads new frames without resetting position.
  - Live mode (⇢) jumps to the newest frame automatically; green =
    on, red = off. Turns off when you move the slider manually.
  - EVERY camera updates on every shot. The camera whose radio button
    is ticked (the main one) sets the moment the grid is showing, and
    the others are put on that same moment. A camera that receives a
    picture a moment later than the main one now shows it straight
    away instead of waiting for the next shot. That also covers the
    camera whose file lands while the main camera's own picture is
    still being read off the share: it used to be put on the previous
    moment and then left there for a whole shot, which is why one
    particular tile always looked slower than its neighbours.
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
    That number is printed two different ways, because it means two
    different things. Black = a setting you made. Grey and italic = the
    row's "Auto" box is on, so it is a value Auto MEASURED, and with
    several cameras on screen it is the MASTER camera's measurement (the
    one with the radio button). Every other camera is levelled from its
    own frame and has its own value, which is not shown anywhere — one
    slider row cannot print six numbers. Black on grey is the whole
    difference between "all cameras are set to this" and "this is what
    one camera came out at"; hover the number for the same note.
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

  - Why the range is not simply 65535: the archiver picks that factor
    from EACH FRAME's own peak, rounded up to the next power of two. A
    camera whose peak sits right on one of those steps therefore gets a
    factor that changes from frame to frame, and dividing everything by
    65535 made its picture jump between two brightnesses with nothing
    physical behind it — C03-081-PCW3NF alternated between dark orange
    and green every few seconds at the same shot energy.
    So every frame is shown against the CAMERA SENSOR's range instead:
    4095 counts. That is one number for the whole archive, worked out
    from each picture's own metadata, so two pictures — from the same
    camera or from different ones — can be compared by eye. Checked
    2026-08-20 over a whole day: all 88 cameras are the same model
    (Basler acA1600-20gm) and no picture anywhere reads above 4095.
    What this means in practice: a camera running at a tenth of its
    sensor range now LOOKS a tenth as bright, because it is. Use Auto
    contrast or Gamma to open up such a picture — the readout line
    always names what was applied.
    Earlier versions instead learned, per camera, the largest range
    that camera had been seen to use, and kept it in
    %APPDATA%\ELI_ImageTools\cam_depths.json. That went wrong in two
    ways, so it is gone: a camera the app had only ever seen dim was
    shown against its dim range, and a single picture with an unusually
    high peak darkened that camera for good — C03-081-PCW3NF ended up
    recorded at the 65535 range and came out black on screen while the
    file held a perfectly normal picture. The old file is no longer
    read; you can delete it.

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
  - The arrangement is made in the "Camera" window, in the Layout
    picture under the list of picked cameras. It is there the moment
    the window opens — there is no second button to find and no second
    window to open.
  - The Layout picture is a true SCALE MODEL of the camera area, not a
    sketch: the arrangement in it is worked out for the real area in
    real pixels and then drawn smaller by one single factor, so every
    window, every picture and every name bar has the proportion it will
    really have. Before, the arrangement was worked out on the small
    board itself while the name bar kept the real area's height in
    plain pixels — so the name bar came out several times too fat, and
    the arrangement shown could genuinely differ from the one the
    cameras ended up in.
  - The picture is shaped to the camera area exactly, so there are no
    hatched strips down its sides. Room left over in the window shows
    as ordinary window background above and below it, which is what it
    is. The strips were the first attempt and they misread badly: they
    looked like area the cameras had failed to use.
  - Because the name bar is drawn at its REAL proportion it is only a
    few pixels tall, far too thin for a name. So each camera's name is
    written across its picture instead, with its size underneath. The
    text shrinks to fit; when the full name will not fit even at the
    smallest readable size it drops to the short name (PAM10NF rather
    than C03-039-PAM10NF), and on a picture too small for two lines the
    name wins and the size is left out. Nothing is ever drawn too small
    to read.
  - Cameras can be arranged automatically, or placed by hand. Auto-
    arrange searches split layouts (rows, columns and any nesting of
    the two). It first works out how large the SMALLEST picture can
    possibly be — counted per share of the area that camera asked for
    — so a portrait diode array next to square far-field cameras is
    never squeezed down to a postage stamp. Among the arrangements
    that keep it within tolerance of that, it then takes the one
    showing the most picture altogether, and prefers the one whose
    windows come out most alike in size. Insisting on the very largest
    smallest picture was the earlier rule and would rather grow one
    picture a few percent than fill the area: three wide cameras came
    out as a single row along the top with two thirds of the area
    empty, where two columns show twice as much picture for a picture
    barely smaller.

CAMERA SIZES
  - Every camera has a size: Smallest, Small, Medium, Large or
    Largest. Click a camera in the Layout picture and the five buttons
    above it come alive, showing which one it is on now. The size is
    also written across the camera, so the whole arrangement can be
    read without clicking anything — unless every camera has the same
    size, when the word would say nothing and is left out.
  - The sizes are shares of PICTURE AREA: 1, 4, 8, 12 and 16. A
    Largest camera is asked for sixteen times the picture area of a
    Smallest one, which is four times the width and four times the
    height, and the program cuts the area up so that it really comes
    out that way. New cameras start at Medium, and portrait diode
    arrays at Largest — they are the ones read for per-diode detail.
    Set a diode array to Medium if you would rather have the room back
    (see the last point below).
  - The size belongs to the CAMERA, not to the set: set it once and it
    holds in every set that camera appears in, and after a restart. It
    is kept in cam_sizes.json next to the other settings.
  - Setting a size re-arranges the Layout picture at once — including
    over an arrangement you had dragged by hand or loaded from a
    preset. It has to: the picture opens on a remembered hand-made
    arrangement whenever there is one, so a size press that spared it
    was a button that did nothing at all.
  - Asking for very different sizes costs empty space, and that is
    normal. A small camera gets a small window, and its picture cannot
    fill a window of any other shape, so there is grey room left
    around it — that is the price of "make that one big and that one
    small". The program picks, out of all the arrangements that come
    out at the sizes you asked for, the one that shows the most
    picture. Move a camera up a size or two if a shift wants the area
    filled instead. With more than twelve cameras the program falls
    back to equal-length rows and the sizes are ignored.
  - The windows always reach every edge instead of sitting as a small
    block in the middle of a large grey rectangle. Whatever room is
    left over goes first to the windows where it actually makes the
    picture bigger; only what no camera can use is shared out by
    window size. That leftover shows as a grey margin around the
    picture, and the margin is now EVEN — the picture sits in the
    middle of its window. Before, it hung from the label bar and the
    whole leftover height piled up underneath it, which is what made
    the cameras look pushed to the top edge with the bottom stretched
    to the floor.
  - After OK the cameras come up in exactly the arrangement the Layout
    picture showed. Only an arrangement you actually dragged is
    remembered as fixed; one left automatic stays automatic, so it is
    recomputed for the real camera area — and follows any size you
    change later — instead of being frozen as it looked that day.
  - Clicking a camera in the Layout picture only selects it, for the
    size buttons. It takes a real drag to hand the arrangement over to
    you.
  - The cameras can also be arranged where they are playing: drag a
    camera by its name bar to move it, drag the
    border or a corner of its window to resize it. Edges stick to the
    neighbouring window and to the middle and sides of the area, so
    windows meet exactly instead of leaving ragged gaps; hold Alt while
    dragging to place an edge freely. A short click on the name bar
    still just selects the camera.
  - Moving or resizing a camera this way switches that set of cameras
    to a hand-made arrangement and remembers it, exactly as a drag in
    the Layout picture does. Right-click a camera's name bar and choose
    "Auto-arrange cameras" to throw the hand-made arrangement away —
    for the open window and for the remembered one — and let the
    program size the cameras again from their sizes.
  - "Reset layout" in the Display group is the gentler way back: it
    puts the cameras where they were when you opened this set — the
    arrangement the preset carries, or the one remembered for these
    cameras — and when nothing was ever saved for them, back to the
    automatic arrangement. It only works with several cameras on
    screen.
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
    analyses the cameras you have clicked in the layout, or ALL of
    them when you have clicked none — one graph per camera, computed
    one camera after another.
  - With more than one graph, a strip above the graph carries ◀ ▶
    arrows, the camera's name and "2 / 4"; the arrows step between the
    cameras and stop at the first and last one. Each camera keeps its
    own graph: deleted points, zoom and the Show Path switch are still
    there when you come back to it.
  - A camera with no frames inside the Set From / Set To range is
    skipped and named in the status line under the buttons.
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
    are not. With graphs for several cameras it first asks whether to
    save the one on screen or one file per camera — then each file
    gets the camera name added to its name, and the camera name is
    written above the picture.
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
  - "Configure" opens the PV picker. It is one dialog for this tab
    and for the Image Finder, over one list of PVs.
  - THE PICKER READS AS TWO TABLES, ruled like a table with the
    column names once at the top of each: "On the picture" and
    "Read, not on the picture". A PV being in the list at all is what makes it
    read; the Show column in front of it is the eye, i.e. whether
    its value is printed over the frame and burned into a saved
    image. Ticking or unticking Show moves the row between the two
    tables, so what you show and what you only read is the split you
    are looking at. The X at the end of a row is what stops a PV
    being read.
  - Each row says: Show, the channel LETTER (for formulas), the PV
    (the archiver channel it reads, or the formula), the name you
    want to see, the unit, its Min and Max alarm limits (see ALARM
    LIMITS AND TRIPS below), and what kind of PV it is.
  - Presets are TICK BOXES under the search box. Ticked means the PV
    is in the list and read; unticking is the same as pressing that
    row's X. The tick always shows what is in the list, so it can
    never disagree with the tables below it.
  - "Compressed SBW4" is not a channel of its own but a RECIPE.
    Ticking it does three things at once: it adds SBW4, it adds the
    formula SBW4 x 0.749 (the compressor transmission) under the
    name "Compressed SBW4", and it takes SBW4 off the picture — so
    only the compressed energy is printed while the raw channel is
    still read, which the formula needs. Unticking removes the
    formula and puts SBW4 back on the picture. The factor is in the
    formula row for anyone to read and change; it used to be applied
    invisibly inside the programme, which meant the picker showed a
    PV whose number matched no archiver reading.
  - Own PVs: the search box browses ALL archiver channels. Type
    fragments — they are AND-matched with wildcards between them, so
    "hapls sbw4" finds every channel holding "hapls" and, later,
    "sbw4" (i.e. *hapls*sbw4*). Click a result (or press Enter) to
    add it; a bare name typed in full is added literally, even when
    the archiver list is unavailable. Typing the channel a preset
    already reads adds THE PRESET, not a second row for the same
    number.
  - Added PVs are named after their channel, are shown in the PV
    panel and the overlay, and are burned into saved images exactly
    like the presets.
  - UNITS. A preset knows its own unit. For a PV of your own there
    is a Unit box: the archiver's channel list does not tell us the
    unit, so this is the only place it can come from. Left empty,
    the programme uses what the channel name gives away (a name
    ending in ":Energy" is J, ":RawPos" is counts) and prints
    nothing at all when even that is unknown — never a guess.
  - GIVE A PV YOUR OWN NAME. Every row has a "Displayed name" box.
    Type a short name there and that is what the
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
      · Names survive a restart, and taking a PV off the picture
        does not throw the name away.
  - Own formulas (derived PVs), the same idea as in the CSS Logger:
    every PV carries a CHANNEL LETTER shown in the Letter column
    (A, B, …),
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
      · A source PV is read as soon as it is in the list, whether it
        is on the picture or not, so a formula never quietly reads
        n/a because its input is not shown.
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
    land, which normally takes one or two retries; it gives up after
    20 s, when "nothing was archived for this frame" is the true
    answer. While the answer is still expected — the first 2.5 s —
    it asks again every 0.4 s at a steady pace, and only after that
    does it start slowing down. It used to double the gap from the
    first retry, so it stepped straight over the moment the sample
    became readable and the number appeared about 0.7 s later than it
    had to, on every single shot. Measured: 0.8 s before, 0.02 s
    after, for about half an extra request per shot.
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
  - A SETTING is not a measurement. A value that is written only
    when somebody CHANGES it — a GDD order, a motor position, any
    setpoint readback — has nothing archived next to a picture,
    because nothing was written at that moment. Such a PV is shown
    with the last value written before the picture, however many
    hours back that is: that is simply what the machine was set to
    when the shot was taken. The program works out which PVs behave
    that way from the archive itself, so a newly added setting needs
    nothing switched on for it. Energy detectors keep the strict rule
    above — no reading of their own means "n/a", never the number
    from a neighbouring shot.
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

ALARM LIMITS AND TRIPS
  - LIVE MODE ONLY. This watches the shots as they arrive; it is not a
    search of the archive, and browsing is not shooting. Turning the
    Slider on in archive mode and picking a whole day does NOT walk
    the day looking for a value over its limit — nothing flashes and
    nothing is recorded while you scrub, however bad the numbers on
    screen are. Two reasons, and the second is the real one:
      · a trip dated by when the operator happened to drag past a
        frame is not a record of anything;
      · only the frames actually looked at are ever read, so there is
        no day to search. Answering "was the back reflection ever
        over 0.3 today" would be a different feature — a sweep of the
        day's archived samples, not a check of the frame on screen.
    Leaving live mode stops the flashing at once and closes whatever
    was still going; the trip LINES stay, because going back to look
    at one is exactly what takes the program out of live mode. A new
    live run therefore opens a new trip rather than counting shots
    onto one dated in the previous run.
  - SETTING A LIMIT. Every row of the PV picker has a Min and a Max
    box. Fill in either, or both, and that value is watched — a back
    reflection kept under 0.3, a temperature kept between 10 and 40.
    An empty box is never watched: an empty box is NOT a limit of
    zero.
      · A comma is accepted as the decimal mark as well as a dot, and
        is normalised when you leave the box, so what the box shows
        is what is stored. Something that is not a number at all
        turns the box pink and says so — a limit you believe is set
        but is not is the one failure this must not have.
      · Formulas can be given limits too: a ratio or a difference is
        exactly the sort of number worth watching.
      · Limits live with the rest of the PV setup
        (%APPDATA%\ELI_ImageTools\pv_registry.json), so they survive
        a restart and the Image Finder's picker shows the same ones.
      · Removing a PV with X removes its limit with it. Merely taking
        it off the picture does not.
  - WHAT HAPPENS. A value past its limit is printed RED and flashing
    over the picture, and red in the side panel. Only that value —
    the others are untouched.
      · The flashing stops by itself the moment the next shot comes
        in. It is about the shot on screen, and a value shouting
        about a moment that has passed is noise.
      · Only a real reading for THIS shot can trip. A number carried
        over from an earlier shot, "no data yet", "n/a" and "ERR"
        never do — a held value next to a live picture would
        otherwise raise an alarm about a shot nobody is looking at.
      · The comparison is the raw threshold you typed. No deadband,
        no smoothing.
  - THE TRIP LIST, at the very top of the Info panel, and invisible
    until something trips. One line per trip:
        14:32:07  Back reflection 0.42 J — above 0.30  ×12   [See]
    the time of the shot it happened on, what happened, and how many
    shots it went on for. Hover it for the whole story.
      · ONE line per problem, not one per shot. A value that stays
        high for ten seconds is one trip with a count on it, and the
        time is the FIRST of those shots — the one worth looking at.
        A value that goes back inside its limits closes the trip; the
        line stays, and the next crossing starts a new one.
      · "See" puts the picture back on that shot: every camera at
        that moment, and the machine values read again for it. Live
        mode switches off first, or the next arriving frame would
        drag the slider straight back off it.
      · "Clear trips" empties the list. It changes nothing about the
        limits — the next value past one starts a new trip.
      · The list holds the last 50 and scrolls; it never grows past
        about four rows of the panel.
      · Every trip also writes a line to image_tools_diag.log.
  - A CAMERA THAT STOPS DELIVERING leaves the same kind of line —
    live mode only, for the same reason plus one of its own: outside
    live mode nothing is supposed to be refreshing, so "not
    refreshing" is not a fault. It leaves a line so a
    gap in the record can be gone back to as well: "the image cannot
    be read", "the camera folder cannot be read", "the folder is not
    answering", "a newer image is not on screen", "the program was
    busy". These are the same five faults the red refresh dot names,
    and the trip uses the dot's own verdict — already latched over
    8 s — so a frame caught mid-write cannot fill the list. The row
    is one short line; the seconds and the filename are in the
    tooltip.
      · A read fault counts the frames that would not decode. The
        other four are one event, however long it lasts, so nothing
        counts 600 ms ticks.
      · A PV the archiver will not answer for is NOT a trip. Only a
        number that passes Min or Max is. "ERR" says the read
        failed, not that the beam did anything, and the row and the
        corner badge already say so.
      · No new retrying was added. The program carries on; the frame
        is read from the share again when you press "See".
  - HOW LOUD, in "Overlay settings":
      · "Flash the value" — just the number flashes.
      · "Flash the value, then the whole panel" — the panel goes red
        with white text between the red-value steps. The text turns
        white because the values are black by default and black on
        red is the one thing this must never produce.
    The value flash always dies with the next shot. The PANEL flash
    keeps going until every trip has been looked at, or "Clear trips"
    is pressed — so a trip cannot pass unnoticed while you are
    looking away. This choice survives a restart, along with the
    overlay's font, size, opacity and colours, which used to be
    forgotten every time.
  - The flash is only a colour. It never changes the overlay's size,
    weight or row count, so it cannot make the panel hop or move
    under the cursor.
  - Saved images are not affected: the burned-in values bar stays
    plain black on white. A limit trip is a live warning, not part of
    the record.

CAMERA PRESETS (multi-cam mode)
  - Save/load named sets of cameras via the Presets panel in the
    camera picker dialog (stored in %APPDATA%\ELI_ImageTools).
  - A preset remembers the ARRANGEMENT as well as the camera list:
    where each camera sits, and the size each one is set to. Arrange
    the cameras the way you want them — by dragging them where they are
    playing, or in the Layout picture — then open "Camera", type a name
    and press Save. Loading that preset later brings the same cameras
    back in the same places, at the same sizes.
  - The preset's sizes WIN when it is loaded: they are written in as
    those cameras' sizes, so the Layout picture, the live cameras and
    the next session all agree. Presets saved before sizes existed
    carry none and change nothing.
  - If the cameras were left arranged automatically when you pressed
    Save, the preset says "arrange these automatically" instead of
    freezing the window rectangles. That is deliberate: frozen
    rectangles belong to the window they were measured in, and the
    preset will be opened in a window of another shape. The sizes are
    saved either way — they are what the automatic arrangement is
    steered by.
  - "Auto-arrange cameras" (right-click a camera's name bar) does not
    touch the preset's copy. It rearranges what is on screen; load the
    preset again to get your arrangement back.
  - Presets saved before this carry the camera list only, and open
    arranged automatically.


=================================================================
TAB 3 — SHOT FINDER
=================================================================

Find specific shots by PV target value across a date range.
Useful for locating shots at a given energy, waveplate angle, etc.

WHAT IS SEARCHED
  - "Time window" and "Cameras" sit side by side, the picked days are
    listed under them, and "Load data" is at the bottom of the same
    group — the action next to everything it acts on.
  - "Time window" opens the one picker every tab opens: a single
    calendar (Monday first, weekends in red, the month and the year
    on the bar above the days) and the times From and To to the
    minute. Ctrl+click adds a day, Ctrl+Shift+click a whole stretch
    of them. Pick a second day and a table appears with a row per
    day, each with its own From and To — there is no tick to find,
    and no separate window for one day's times. So the days need NOT
    follow one another: Monday and Thursday alone is a perfectly good
    search. The From/To above the table moves every day at once; a
    row you type in keeps what you typed. "Now" moves the calendar to
    today. Nothing happens until OK; Cancel throws the change away.
    There is no Live mode here; this tab searches what is already
    archived.
    What this replaced was a pair of calendars of its own, Start
    point and End point, which could only pick whole hours and only a
    run of days from one date to another.
  - "Cameras" — the camera list. It is the Image Slider's own
    picker, with the same saved sets ("presets"), so a set of
    cameras saved in one tab is offered in the other. The cameras
    you picked stay listed on the panel below the button.
  - "Load data" reads the PV data for those days and finds the
    matching frames. While it runs, the bar under it says what it is
    reading, how far along it is, how many matches are in and roughly
    how long is left.
  - There used to be a Lab / Office switch next to the Time window
    button. Both of its settings pointed at the SAME network path
    for the pictures, the archiver answers the same from the lab and
    from the office, and the daily CSV files it also switched are no
    longer written. It could only ever be set wrong, so it is gone.

SEARCH CRITERIA
  - Choose one or more PV columns as search criteria:
    SBW4, PTM1, PCM2, PCM4, PAP1, Back Ref, Waveplate.
  - For each criterion: set target value, unit, and ±tolerance.
  - Also show: select extra PV columns to annotate results without
    using them as a search filter.

CAMERA SELECTION
  - "Cameras" opens the picker; search by name or number and
    click to add or remove. Every camera you pick is searched and
    gets its own results tab.
  - "✕ Remove selected camera" takes the highlighted one out of the
    panel list without opening the picker.

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
  - Step PVs such as the waveplate or a GDD order are archived only
    when they change, so they are resolved as "last value at or
    before this frame", not as the nearest sample. Which PVs those
    are is worked out from the archive itself - nothing has to be
    listed for a newly added setting.
  - Extra columns are matched within 30 s; the frame itself must sit
    within 30 s of the matched shot.
  - Every PV is reported exactly as the archiver has it. SBW4 used
    to be multiplied by the compressor transmission (0.749) behind
    the scenes, so the SBW4 number here did not match the SBW4
    number anywhere else; it no longer is. Nothing in this tab
    rewrites a reading - only Back Ref / PAP1 are shown in mJ, and
    the waveplate is snapped onto its own 1000-count grid.
  - Any archiver PV can be added, not only the presets: type
    fragments to search the channel list, or type a channel name in
    full and take the "+ add ... as an archiver channel name" row
    (Enter does the same). That also works when the channel list
    cannot be downloaded.
  - Right-click a PV row to rename it (a long channel name does not
    fit the panel or the picture), to copy its archiver channel
    name, or to remove it. The picked PVs and their names survive a
    restart.
  - Back Ref and PAP1 are displayed in mJ when < 1 J.
  - Clicking the Folder cell reveals the matched file in Explorer;
    double-clicking a row opens every shot of that day inside the
    tolerance in a second, shorter table under the results table.
  - Clicking a shot in that list shows its image in the picture area
    on the right — the same place the day's own picture appears, so
    the results stay visible. The value line under the picture is the
    same one. Every shot is looked up in the hour folder of its own
    time, so shots outside the hour of the day's best shot show up too.
  - The divider between the two tables can be dragged. "✕" closes the
    shot list; it also closes itself when another day row is clicked
    or when a new search is started.
  - Switching to another camera's tab keeps your place: the same day
    stays selected, the shot list stays open and the shot with the
    same time stays picked, so you see that exact moment through the
    other camera.
  - The last column of the shot list is the picture belonging to that
    shot. Click it to open the folder with the file selected. It is
    filled in only for the shot you look at or the one you click - a
    day can hold tens of thousands of shots, and finding every file
    would mean tens of thousands of trips to the share.
  - Next to the shot list is the day's own graph: every PV you
    searched by over the whole day, the target and its tolerance, a
    red dot on each shot in range and a black line on the shot you
    are looking at. Clicking in the graph picks the nearest shot, so
    you can see at a glance where in the day this frame sits.
  - Sending and saving live in "Save & Send" in the left panel. "Act
    on" chooses what they mean: the whole selected day(s), or only the
    shot picked in the shot list. The shot choice is available while
    the shot list is open.
  - Pixel normalization: same value / 65535 pipeline as the Slider —
    the camera's absolute full scale, so a colour means the same
    intensity in every result row.
  - Three rows — "Con:", "Bri:" and "Gam:" — the same block as in the
    Image Slider and the Image Finder: a slider, the number in use, a
    reset button and an Auto box on each. Ticking Auto takes that row
    over, greys out its slider and parks it on the value Auto used;
    unticking gives you your own value back. All three apply to the
    preview and to Save Images alike.
  - Con (Contrast, -127..+127, default 0): a gain around the frame's own
    black level. Its Auto box is what used to be the separate "Auto
    stretch" checkbox — each frame spread over its own p0.5..p99.5
    window: dim frames become readable, result rows stop being
    comparable. Binary and False Colors always map per frame regardless.
  - Bri (Brightness, -255..+255, default 0): an offset added to every
    pixel. Its Auto box is the auto level — the same p0.5..p99.5 window
    placed on the data, one pass shared with Auto contrast.
  - Gam (Gamma, 0.30-1.50, default 1.00): below 1.00 brightens the dark
    end and result rows STAY comparable, because the curve depends only
    on the pixel value. Auto is per frame (median to 45 % of the range)
    and therefore does not. The row is greyed out while Auto contrast or
    Auto brightness is on.
  - Under the palette selector: the previewed frame's peak in raw
    counts, % of the camera's full scale, bit depth, which mapping was
    used, and any gamma, contrast or brightness on top of it — an
    adjustment that changes the picture is never left unnamed.


=================================================================
TAB 4 — ONE MOMENT
=================================================================

The other way round from every other tab in this program. They all
answer "one thing over time"; this one answers "everything at one
time". Pick a moment out of the graph and see what every camera saw
at that moment, with the machine values that go with it.

WHERE THE CONTROLS ARE

  Everything you set is in the panel down the LEFT-HAND side, the
  same place and the same look as in the Image Slider. The sections
  fold away by clicking their coloured headers:

    Source            time window, cameras, and Load
    PV channels       the value picker and the list of picked
                      values with their number at the moment
    The moment        which moment is picked, prev / next, the two
                      ways out to the Image Slider ("Send moment"
                      and "Send + cameras"), and the list of saved
                      moments with Save / Forget / Clear
    Range statistics  the numbers for a marked stretch
    Image / Display   how the frames look

  The order is the order of the work: the window and the cameras,
  then the values that are about to be read over them, then the
  moment picked out of the graph they draw.

  The graph is at the top right, the frames underneath it. The bar
  between them can be dragged to give either one more room. Progress
  and status are always at the bottom of the left panel, below every
  section, so a folded section can never hide the line that says why
  nothing is happening.

HOW IT IS USED

  1. "Time window" — the day and the From/To times. This is the
     Image Slider's own picker, the same calendar, so a window picked
     there is picked here the same way, multiple days included. Only
     the readings inside the window are read and drawn. There is no
     "Live mode" tick in this picker, because this tab does not
     follow new images — the Image Slider is the tab that does.
  2. "Camera" beside it — which cameras a moment should show. Again
     the Slider's own picker, saved camera sets included. It does not
     offer the arrangement board the Slider's does: this tab has no
     grid to arrange, and an arrangement picked here would overwrite
     the Slider's own.
  3. "Search / select PVs" — which machine values to draw. The
     Slider's own picker over the same shared list, with the same
     search, so a value added or named here is added or named
     everywhere, and a set picked in the Slider is already picked
     here.
  4. "Load" — every picked value is read from the archiver for that
     window and drawn. The status line says how many were drawn,
     which came back with no readings, and how many the archiver
     refused.

ONE GRAPH, NOT ONE PER VALUE

  Everything picked goes into a SINGLE graph. Values measured in the
  same unit share one scale and can be read against each other;
  values in different units each get a scale of their own — the
  second on the right-hand side, further ones stepped outwards. No
  value is stretched or shifted to fit another one, so every number
  on every scale is a number that was really archived.

  There is no second arrangement to choose between. One graph per
  value, one under the other, was offered for a while and never used
  — and the eye (below) already answers "this one is in the way".

  The eye next to a value in the list takes it OFF the graph without
  unpicking it: the value stays read and stays listed with its
  number, it is just not drawn. Nothing is re-read when you switch it
  back on.

A VALUE STAYS PUT UNTIL IT CHANGES

  The archive writes a value down when it MOVES, not on a clock. So
  the graph draws it as a staircase: a waveplate that went to 15000
  at nine o'clock is drawn at 15000 all the way to the next time it
  moved, and then steps to the new number at that instant. It is
  never drawn as a slope from one to the other — a slope would show
  you angles the machine never stood at.

  A setting that did not move at all inside your window is still
  drawn, as a flat line at the value it was already sitting at, and a
  setting whose last move was days ago is drawn from the left-hand
  edge of the window to the right-hand edge.

  Values measured every shot are treated differently on purpose: if
  the shots stop at noon, the line stops at noon. Drawing it flat
  until the evening would say the laser kept firing. The program
  works out which kind each value is from the value's own history —
  there is no list to maintain.

  The little dots on a line mark the times something was really
  written into the archive. A line with no dots on it has too many
  samples to mark them.

  The time axis is labelled on the clock: every minute, quarter of an
  hour, hour or three hours, whichever fits — never at some round
  number of seconds like 02:46:40.

A VALUE BUILT FROM A FORMULA

  A formula (a value you built yourself in the value picker, for
  example one energy divided by another) is drawn like any other
  value, and it means exactly what it means everywhere else in the
  program — the same piece of the program works it out for the graph
  here, for the live overlay in the Image Slider and for the table in
  the Image Finder.

  Three things worth knowing about it:

  - The values a formula is built FROM are read even if you did not
    tick them yourself. Without that a formula built on an unticked
    value would silently come out empty.
  - It is worked out at the moments its own inputs were really
    recorded, and each input keeps its last reading until it publishes
    a new one — which is what the archive itself means for a value
    that is only written when it changes. Where an input has nothing
    to give (before its first reading, or across a long hole), the
    LINE BREAKS instead of being drawn straight across the gap.
  - A formula with no unit gets a scale of its own, because a ratio
    around 1.5 and a motor position around 20000 are both "no unit"
    and sharing one scale would flatten the ratio onto the bottom of
    the graph.

  If a formula cannot be worked out at all, its row says "n/a" and
  hovering it says why — for example that one of its letters no
  longer points at a value that exists. It is never left blank.

WHAT THE TAB REMEMBERS

  The window, the cameras, the picked values and their eye state, all
  the display sliders and their Auto ticks, the palette, the frame
  size, and which panels were left open. It comes back the way you
  left it the next time you start the program.

  The saved moments are the one thing it does NOT keep. They are
  yours for as long as the program is open: while it runs, a saved
  moment can be clicked with nothing loaded at all — a time needs no
  graph behind it for the frames to be found — but closing the
  program empties the list. Yesterday's times mean nothing against
  today's window, and they would only push out the ones you are
  working on.

  Restoring reads NOTHING — not the archive, not the image share. The
  tab comes up set the way you left it and waits for you to press
  "Load".

THEN, ON THE GRAPH

  The LEFT button READS the graph, the RIGHT button ZOOMS it and SETS
  IT UP. They never do the same thing.

  LEFT CLICK a moment
    Every picked camera's frame comes up on the right, and the value
    list in the left panel shows what each value was at that moment.
    Hover a number to see how far that value's nearest reading is
    from the moment. "prev" / "next" step from shot to shot without
    saving anything; "Save" keeps the one you stop on.

  LEFT DRAG across a stretch
    "Range statistics" then lists, for every value, how many readings
    the stretch holds, the average, the spread (± std) and the
    smallest and largest value in it. Hover a row for the middle
    value, the peak-to-peak, the first and last reading and the trend
    across the stretch. The marked stretch is shaded on the graph and
    "Clear the range" removes it. A drag works anywhere on the graph.

  RIGHT DRAG across a stretch
    The time axis shows only that stretch. The marked range and the
    picked moment are left exactly as they were — zooming is looking,
    not choosing.

  RIGHT CLICK
    The settings for whatever you clicked on. Four menus, and which
    one you get depends on where the mouse was:

      on a CURVE (or on its row in the legend)
        that value's own menu: hide it on the graph, make clicks snap
        to it instead of to the topmost curve, give it a scale of its
        own, open the value picker to rename it or set its unit and
        limits, take it off the list, or copy its channel name.

      on a SCALE down either side
        that scale's menu: fit it to the data, fit it to the marked
        stretch only, set the top and bottom by hand, or draw it
        logarithmically. A scale set by hand stays set until you
        choose "Fit to the data" again.

      UNDER the graph, where the times are
        the time axis: zoom back out one step or show the whole
        window, type the From/To times, or pick a fixed tick spacing
        instead of the automatic one.

      anywhere ELSE on the graph
        the graph itself: zoom back out, clear the marked stretch,
        switch the grid or the legend off, copy the graph to the
        clipboard or save it as a picture.

  ZOOMING BACK OUT is a STEP at a time — one right drag back per
  step — with "Show the whole window" for all of it at once. It used
  to be a plain right click that jumped straight to the whole loaded
  window; for a pick spanning many days that meant a whole month in
  one go, which is not what somebody looking closely at ten minutes
  wants. The toolbar's back arrow and house button do the same job.

  Click and drag are told apart by how far the mouse moved: below
  about five pixels it is a click, above it a drag — so finishing a
  right drag never opens a menu. The zoom and pan buttons in the
  toolbar take priority: while one of them is active, the mouse
  belongs to the toolbar.

WHY A CLICK SNAPS

  A click lands wherever the mouse was, and a time between two
  readings has no shot behind it — the frames shown for it would be
  an arbitrary pick. So the moment used is the nearest real reading
  of the FIRST value drawn, which is the topmost one in the value
  list that is not hidden by its eye. That is also what "prev" and
  "next" step along.

  If you would rather it followed a different value — a per-shot
  energy while a slow motor position sits at the top of the list, for
  example — right-click that curve and tick "Clicks snap to this PV".
  The choice is remembered, and it falls back to the ordinary rule if
  that value stops being drawn.

THE SAVED MOMENTS

  Finding an interesting shot in a day of readings is the slow part
  of this tab, so a moment you went looking for is kept. What goes on
  the list under "The moment", newest at the top:

    - a moment you CLICK in the graph,
    - a moment you go back to off the list itself,
    - whatever is on screen when you press "Save".

  What does NOT: "prev" and "next". Stepping through a stretch of the
  day shot by shot used to put every step on the list, which pushed
  the moments somebody had actually gone looking for off the end of
  it. When a step lands on one worth keeping, press "Save" — it greys
  out as soon as that moment is on the list, so the button also says
  whether it is.

  Click a row and that moment comes back: the frames and the value
  list follow. "Forget" takes the selected row off the list, "Clear"
  empties it. Neither touches the frames on screen. The list holds
  the last 60 moments and survives a restart.

  Changing the value list does NOT clear it: a time keeps its meaning
  whatever is being plotted.

GOING BACK IS INSTANT

  A moment costs two things: finding which file each camera answers
  with, and reading and drawing that file. Both are kept in memory,
  so a moment looked at a second time — off the list, with the
  arrows, or by clicking the same place again — goes back up without
  the share being touched at all. The same holds for a display
  setting you go back to: the picture for it is already there.

  What is kept is capped in size (about 190 MB of pictures), oldest
  first, because it is the commit charge and not the RAM that runs
  out on these PCs. "This camera has nothing near this moment" is
  only remembered once the moment is more than ten minutes old — for
  a moment that recent the answer can still change, so the share is
  asked again.

A SAVED MOMENT KEEPS ITS FRAMES

  Pressing "Save" is a promise to come back, so its frames get more
  than the ordinary memory:

  - They are HELD. Nothing you look at afterwards can push them out,
    however many other moments go through the cache in between, so
    flipping between two saved moments stays instant all day.
  - They are FETCHED AHEAD. The moment you press Save, that moment's
    frames are quietly found and drawn in the background, so the
    first click on its row already has them. It runs on fewer threads
    than the wall you are waiting for, so it can never be in the way.
  - Moving a display slider re-draws them too, in the background, so
    going back is instant after that as well.

  Two limits: the newest twelve saved moments are held (not all
  sixty), and what they hold has a ceiling of its own on top of the
  ordinary one. Both exist because it is the commit charge that runs
  out on these PCs, and sixty moments times twenty cameras would not
  fit in it.

  This is MEMORY only — nothing is copied onto the disk. Closing the
  program empties the list of moments along with their frames.

  Nothing is fetched while the tab is only restoring itself. The
  background fetch starts after the first time you ask for frames
  yourself, so a tab you have pressed nothing in reads no share at
  all.

WHY THE FRAMES USED TO TAKE A MINUTE

  Finding twenty cameras' frames for one moment could take about a
  minute, where it used to take seconds. None of that was in drawing
  the pictures; it was in reading folders on the share, and a folder
  read over the network takes about 150 ms.

  The old way read the same folders over and over. Each camera looked
  through the hour folder for ITSELF, and when its own folder was not
  in that hour — cameras do not all record every hour; one day held
  92 cameras with only 27 of them in every hour — it asked about
  every one of the ~90 folders in that hour separately. Then it did
  the same for the neighbouring hours, and then the next camera
  started again from scratch. Nothing was kept for the next moment
  either.

  Now each folder is read ONCE and the answer serves every camera and
  every moment in that hour. On a test tree of 20 cameras with 70
  others alongside them, that is 25 folder reads instead of 1816 —
  under four seconds' worth on the share instead of over four
  minutes — and a second moment in the same hour reads nothing at
  all.

  The line under the frames now says how long the search took and how
  many folders it had to read, and how long the drawing took, so a
  slow one can be explained rather than guessed at. This tab also has
  its own worker threads now, so nothing another tab is doing can
  make the frames wait.

THE TWO WAYS OUT TO THE IMAGE SLIDER

  The way out of this tab: the Image Slider is where a frame gets
  looked at full size, with the subtraction, the overlays, the
  profile tools and everything else that tab can do. Two buttons,
  side by side, differing only in where the CAMERAS come from:

    "Send moment"       opens the Slider on this moment and LEAVES
                        ITS CAMERAS ALONE. Use it when the Slider is
                        already set up on the cameras you are working
                        with and only the time needs to change.
                        Greyed out while the Slider has never picked
                        a camera — it would open on nothing, and the
                        tooltip says so.

    "Send + cameras"    also switches the Slider to the cameras
                        picked here. Greyed out while nothing is
                        picked here.

  Either way the Slider is given the quarter of an hour on either
  side of the moment as its window — so the shots around the
  interesting one can be slid through — and it opens ON the moment
  that was sent, not at the start of that window. Whatever the Slider
  was doing before is put down first: live mode, focus mode and the
  watcher, which would otherwise drag the view to the newest frame or
  hide the slider itself.

A STRETCH WITH NO READING IN IT

  Some values are written only when they CHANGE — a waveplate angle
  or a motor position can sit still for hours. Mark a stretch inside
  that silence and there is not one reading in it, but the value was
  perfectly well defined the whole time.

  So the statistics fall back to the last reading BEFORE the stretch
  and carry it forward: the average, smallest and largest columns all
  show that value (it never moved, so it is all three), the spread
  column says "held", the count says 0, and the whole row is amber so
  it can never be mistaken for an average of readings that are really
  there. Hover it for where that reading came from and how old it is.

  This also covers a value with no readings in the whole window: it
  is still listed, with whatever it was sitting at when the window
  opened. Only when the archive holds nothing at all that old are the
  dashes shown.

THE FRAMES

  Each frame is captioned with the camera and the frame's OWN time,
  and nothing else. Hover it for the rest: how far that time is from
  the moment you asked for, and which file it came from. A frame is
  stored only about every 35 seconds, so a few seconds' difference is
  normal. A camera with nothing within 30 seconds says so on its own
  tile instead of being dropped from the wall.

  Frames are found by exactly the same rule as in the Shot Finder
  (the hour folder the moment falls in plus its neighbours, and a
  30-second window), so a frame found here is the frame found there.
  What changed is only how OFTEN the share is read for it — see "Why
  the frames used to take a minute" above.

HOW THE FRAMES LOOK  ("Image / Display")

  The same controls, with the same meanings, as in the Image Slider —
  they are in fact the Slider's own, so a frame here and the same
  frame there look alike:

    Con   contrast, a gain on the picture. 0 is the plain absolute
          scale. "Auto" stretches each frame on its own and
          overrides the slider.
    Bri   brightness, an offset added to every pixel. "Auto" levels
          each frame and overrides the slider.
    Gam   gamma of the display curve. 1.00 is the plain absolute
          scale, below 1 lifts the dark end (0.50 is the working
          point on these cameras). Unlike Auto contrast it costs
          nothing in comparability: one pixel value still always
          gives one brightness.
    Palette  the colours. A new tab opens on "Gradient", because
          these frames are read as beams and grey hides everything
          the dark end of the scale is doing; a palette picked by
          hand is remembered and wins over that. "Default" is the
          untouched file as it sits in the folder — while it is
          selected, Con and Bri do nothing and are greyed out.
    Size  how large the frames are drawn. Holding Ctrl and rolling
          the mouse wheel OVER THE FRAMES does the same thing, and
          moves this slider with it. Only the pictures grow: the
          window does not move and neither does the graph above.

  Changing any of them redraws the frames from the files already
  found, WITHOUT reading the share again, so the picture follows the
  slider instead of waiting on the network. A setting you go back to
  is instant: that picture is still in memory.

  Click a frame to send it to Workshop; it goes there on the plain
  absolute scale, never with these display settings, because
  Workshop measures the picture it is given.

  The line between the graph and the frames can be dragged: it is a
  grey bar with a grip in the middle of it, and it lights up under
  the mouse. Neither half can be dragged away to nothing.

WHAT IS NOT DONE THIS WAY, ON PURPOSE

  - The frames are laid out in a plain grid, not in the Image
    Slider's fitted arrangement.
  - A window with more than 200000 readings behind a formula is
    refused, with the reason written where the number would be,
    rather than being thinned out: a thinned formula next to
    full-rate values is a different curve, and one drawn without
    saying so would be worse than one not drawn.


=================================================================
TAB 5 — WORKSHOP
=================================================================

Look at, measure and mark up images handed over from the other tabs
("➤ Workshop"). You can also open a file with "Open file…" or drop
one straight onto the picture.

THE ONE THING TO KNOW
  Everything in the Display section only changes how the picture
  LOOKS. It never changes the picture itself, so you can always put
  a slider back and get exactly what you started with. Only the
  Filters section, the Edit picture section and the Crop tool change
  the picture, and each of those steps can be undone. Saving always
  writes a new file — the file the image came from is never
  overwritten, and the program refuses if you aim at it.

  When a filter does change the picture, it changes the sensor
  counts behind it in the same way, so what the Measure section
  reports always belongs to the picture you are looking at.

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
  📏 Ruler     measures a length and writes it on the picture BOTH
               ways: "511.0 px = 2.3 mm". Hold Shift while drawing
               to keep it straight — across, down or at 45° — which
               also works when dragging one end of a finished one.
               (Straight to the picture, that is; a line parallel to
               a tilted beam is still to come.)
  ∠ Angle      click the end of one arm, then the corner, then the
               end of the other arm. The number next to the corner
               is the angle between them. Right click or Esc throws
               away a half-finished one.
  ▦ ◎ Region   a rectangle or an ellipse. The Measure section
               reports the numbers for that area, and the region
               itself carries them on the picture: minimum, maximum,
               mean and standard deviation, its size in pixels and
               in millimetres, how many pixels it holds, its area,
               the sum and the centre.
  〰 Profile   draw a line, then double-click it (or press "Plot
               profile") to see the values along it, with buttons
               to copy them or save them as CSV.
  ✚ Marker     reads out the value at one pixel.
  ⧉ Crop       drag the part you want to keep.
  💧 Pick      takes a colour out of the picture to draw with.
  ⌫ Delete     click a drawn item to remove it.

  Below the tools, on the first row: the drawing colour, then Line
  and Brush thickness as sliders. Each one shows its number and,
  beside it, a small sample of the stroke it will draw, in the
  colour that is set — so the weight can be seen before anything is
  drawn. Thin strokes are shown at their real thickness; from about
  14 upwards the sample no longer fits the little box, so the frame
  turns into an amber dashed one and the sample only shows that
  bigger is thicker. The number is always exact. While a thickness
  slider is being dragged, a large sample pops up above it and shows
  the stroke at its real thickness for the whole range, up to the
  widest brush; it disappears as soon as the slider is let go. Set
  with the arrow keys instead, it shows itself for a moment.

  Then comes the text size, with a sample of its own beside it: the
  letters "Aa" in the drawing colour, at the size the next piece of
  text will get. Up to about 20 they are at their real size; above
  that the box runs out of room, so the frame turns amber and dashed
  and the sample only shows that bigger is bigger. Every change of
  the number also pops a large sample up for a moment, and that one
  is at true size over the whole range, up to 200. Last on the row
  is the Fill box.

  The second row: the magnification as a slider with the percentage
  beside it and ＋ − Fit and 1:1 next to that, then Undo / Redo /
  Clear drawing / ↺ Original / Shortcuts. The zoom slider is spaced
  by ratio, so the same amount of travel doubles the magnification
  anywhere along it — dragging feels the same at 5 % as at 3000 %.

THE BAR AT THE BOTTOM
  Where the pointer is, the value under it, the size of the picture
  and the magnification. When the original file could be read back,
  the value is the real sensor count and its share of full scale;
  otherwise it is the 0-255 display value and the Measure section
  says so.

RIGHT CLICK ON THE PICTURE
  A short menu of the things you want most often: undo and redo,
  measure the whole picture, the results table, the beam report, the
  value under the pointer (it can be copied, or turned into a
  marker), delete the selected item, copy, "Save with overlay",
  Save PNG…, duplicate, fit and true size. It stays out of the way under Magnify and while
  you are clicking out a polygon or an angle, because there right
  click already means something else.

IMAGES
  Every received image is a slot — switch between them, remove one,
  or clear them all. "Open file…" takes several files at once. The
  reference image chosen here is what the subtraction, the merge and
  the comparison views work against.
  - Duplicate makes a copy as a new image. With a region selected it
    copies only that part, which is how you cut a detail out without
    touching the original. The copy keeps the sensor counts and the
    scale, so it can still be measured.
  - Paste brings in whatever image is on the clipboard.
  - "Show in Image Slider" opens the folder this frame came from in
    the Slider. It only goes that way round: the Slider browses
    files on the share, so an edited picture is not something it can
    take back.

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
  The values are called "pixel intensity" — the camera's own, when
  the source file could be read; "8-bit intensity" when only the
  picture itself is there. The line above the numbers always says
  which of the two you are looking at.
  - "Keep several regions" lets regions pile up instead of each new
    one replacing the last. The cells then show whichever region is
    selected, and the results table is where you read them all.
  - "Results table…" lists every region, ruler, angle, marker and
    profile line on this picture in one table, with the whole
    picture on the first row for comparison. Copy puts it on the
    clipboard, "Save CSV…" writes a file, and Refresh reads the
    numbers again after you have moved something.
  - "Histogram numbers…" is the histogram as a table: 256 rows with
    the value range, how many pixels, the share and the running
    total — again with Copy and Save CSV.
  Scale: one box, "One pixel is …", says how much of the object a
  single pixel covers. It starts at 4.4 µm, the pixel size of the
  Basler cameras here, and it is always in force — there is no
  switching it on. Type a different number and everything re-reads
  at once; the number stays for the next image and the next time
  the program is opened.
  - Every length is reported BOTH ways: a ruler reads
    "511.0 px = 2.3 mm", the results table has a pixel column and a
    millimetre column next to each other, and the beam report gives
    each width in both. Nothing has to be switched over, so the
    wrong one cannot be written down.
  - 4.4 µm is the size AT THE SENSOR. With a lens or a magnifier in
    front, one pixel covers something else on the object — put that
    size in the box, or measure it: draw a ruler across something
    whose size you know, select it, press "Set scale…" and type that
    size in millimetres. The pixel size is worked out from it, and
    the Scale line then says "measured with a ruler" instead of
    "camera pixel size", so you can always see which is in force.
    "Camera (4.4 µm)" puts it back.
  - 4.4 µm also assumes one stored pixel is one sensor pixel. Some
    cameras record with 2×2 binning — four sensor pixels joined into
    one, so that pixel covers 8.8 µm. OM1NF does this, OM1FF does
    not. On a binned frame, type the doubled value in; it is not read
    out of the picture yet.
  - "Add scale bar…" then draws a bar of a length you choose into
    the picture, so a saved copy carries its own scale. It suggests
    a round number, sits on a dark plate so it stays readable over
    a bright spot, and can be dragged anywhere with Select. Change
    the scale later and the bar follows — it can never disagree with
    it. "Remove bar" takes it off.

BEAM (spot size and shape)
  Measured on the selected region, or on the whole picture. The
  "Background" box is the level taken as background before anything
  is measured — 5 % of the area by default. That subtraction is not
  optional cosmetics: a width is computed from distances squared, so
  a background of a few counts spread over the whole frame outweighs
  the spot itself and the width comes out roughly the size of the
  frame. Set 0 % to measure the values exactly as they are.
  - "Beam report…" — centre of mass, peak and where it is, the D4σ
    width across and down, the same on the long and short axis with
    the tilt of the long axis, the roundness (1.0 = round), and the
    FWHM and 1/e² widths read off the row and column through the
    centre. "not reached" means the values never come back down to
    that level inside the area you measured — make the region
    bigger. Copy and Save CSV as everywhere else.
  - "Radial profile" — the average value against the distance from
    the centre. Every direction is averaged together, so a faint
    halo that a single profile line cannot separate from noise shows
    up clearly.
  - "Encircled energy" — how much of the signal sits inside a
    circle, against the radius of that circle. The half and 86.5 %
    radii are marked; 86.5 % is what a perfect Gaussian has inside
    its 1/e² radius, so comparing the two tells you how much is in
    the wings. If the circle runs off the edge of the area measured
    the window says so.
  - "Mark the centre" drops a marker on the centre of mass.

  The profile window (double-click a profile line) grew as well: it
  now prints FWHM, 1/e² width, D4σ, the centre of mass, the peak and
  the baseline, draws the half-maximum and 1/e² levels with both
  crossings marked, and has
  - "Average across the line" — average that many pixels either side
    of the line. On a noisy frame a one-pixel-wide profile is mostly
    noise; 9 pixels cuts that by about three without moving the
    peak, so the FWHM stops jumping about.
  - "Gaussian fit" — draws the fitted curve over the trace and gives
    its σ, its FWHM and r². A low r² is the answer to "is this spot
    really Gaussian": clipped or double-humped spots fit badly and
    say so.

FILTERS (changes the picture, undoable)
  - Median… — replaces each pixel by the middle value of its
    neighbours. The filter for hot pixels and speckle: it removes
    the outlier without smearing an edge, which a blur cannot do.
  - Blur… — Gaussian blur, for noise that is spread out rather than
    sitting in single pixels.
  - Sharpen… — adds back what a blur would remove.
  - Edges — bright where the picture changes fastest.
  - Remove background… — four kinds: a constant level (a percentile
    of the picture), a sloping plane, a curved surface (for
    vignetting or a glow), or a rolling ball of a size you give,
    which keeps everything smaller than the ball and treats
    everything larger as background. The same background is taken
    off the picture and off the counts, so the two stay in step.

EDIT PICTURE (changes the picture, undoable)
  Rotate left / right / 180°, flip across or down, Resize…, and
  Subtract or Difference against the reference image. Anything you
  have drawn moves with the picture.
  - "Rotate by angle…" turns by any angle you type. The picture
    grows so no corner is cut off, which means the corners it grows
    into are black — and they are real pixels, so keep a measuring
    region away from them.
  - "Straighten" turns the picture so that a line, arrow or ruler
    you drew along an edge becomes level. Draw it first, then press
    the button.
  - "Bin…" joins blocks of pixels into one — 2 × 2, 4 × 4 and so on.
    "Average" keeps the scale and just trades resolution for less
    noise; "Sum" adds the counts up, which is what a camera does
    when it is binned on the chip, and the full scale moves with
    them so the readings stay honest.

COMBINE IMAGES
  Makes a NEW image out of the ones already open — nothing existing
  is touched.
  - Across every open image: Average, Maximum, Sum or Minimum, over
    the area they all cover. Average of ten shots divides the noise
    by about three; Maximum is the envelope, everywhere the beam has
    been. If every image has the camera's own pixel intensity on the
    same scale the result keeps it, so it can still be measured that
    way; if not it says so.
  - "Merge as red and green" puts the active image in red and the
    reference in green. Where both have signal it is yellow, so a
    shift between them shows as a red edge on one side and a green
    edge on the other — far easier to see than a difference image.

COMPARE
  Off, Side by side, Blend (with a mixing slider) or Difference,
  against the reference image. While a comparison is shown the
  picture on screen is a combination of two images, so drawing,
  measuring and every operation that changes pixels are switched
  off — set Compare back to Off first.

SAVE
  "Save with overlay" decides what lands in the file. Ticked, and
  everything drawn on the picture goes in with it: text, lines,
  arrows, freehand strokes, regions, the scale bar and the numbers
  the measuring tools wrote by themselves. Unticked, only the
  picture is written, with nothing drawn on it. The same switch is
  in the right-click menu on the picture, the bar at the bottom says
  which way each file was written, and the choice is remembered for
  next time.
  Save PNG…, Save TIFF… or Save JPEG… then writes the copy. (Never
  keep data as JPEG — it throws detail away.) "Save all…" asks for a
  format and writes every image in the Workshop into one folder, and
  "Copy" puts the picture on the clipboard. All of them follow the
  same switch.
  - "Save the values as TIFF…" is the other kind of TIFF and both
    are needed. This one writes what was MEASURED: the camera's own
    pixel intensity as a plain 16-bit file, no palette, no display
    settings, no drawing. That is the file to open in ImageJ or from a
    script. The ordinary "Save TIFF…" writes what is on SCREEN,
    which is what a report or a presentation wants.
  - Animation from every open image: set the frame time, tick
    Repeat, then "Play…" to look at it first and "Save animation…"
    to write an animated GIF, PNG or WebP. Images of different sizes
    are centred on black rather than stretched, so what you are
    comparing is not changed on the way. Useful for showing a drift
    over a series of shots.
  - Session: "Save session…" writes down everything drawn on every
    open image, the regions, the scale and the display settings, so
    the work is not lost when the Workshop closes. It stores where
    each picture came from, not the picture itself — that keeps the
    file tiny, and "Load session…" re-opens them and puts the
    drawing back. An image that was handed over without a file
    behind it cannot come back, and the program says how many.

  Writing files runs in the background, so a folder full of images
  or a long animation no longer freezes the window.

KEYBOARD SHORTCUTS
  Press F1, or the "Shortcuts" button in the top row, for the whole
  list in a window. Every button that has a key also says so in its
  tooltip. The keys work only while the Workshop is the tab in
  front.

  Ctrl+Z            undo the last change
  Ctrl+Y            redo (Ctrl+Shift+Z does the same)
  Ctrl+Alt+Z        back to the image as it arrived
  Ctrl+Shift+D      remove every drawn and measured item

  Ctrl+O            open a file
  Ctrl+← / Ctrl+→   previous / next image
  Ctrl+D            duplicate (the selected region, if there is one)
  Ctrl+V            paste an image from the clipboard

  Ctrl+L / Ctrl+R   rotate left / right
  Ctrl+Shift+R      turn upside down
  Ctrl+H            mirror left to right
  Ctrl+Shift+H      mirror top to bottom
  Ctrl+E            resize
  Ctrl+Alt+R        rotate by any angle
  Ctrl+Shift+E      straighten along the selected line
  Ctrl+Alt+B        bin pixels
  Ctrl+M            subtract the reference image
  Ctrl+Shift+M      difference against the reference image

  Ctrl+Alt+M        median filter
  Ctrl+Alt+L        blur
  Ctrl+Alt+H        sharpen
  Ctrl+Alt+G        remove the background

  Ctrl+Alt+C        combine every open image into a new one
  Ctrl+K            next comparison view

  Ctrl+Shift+W      measure the whole picture
  Ctrl+P            plot the profile line
  Ctrl+T            results table
  Ctrl+J            histogram numbers
  Ctrl+B            beam report
  Ctrl+Shift+B      radial profile

  Ctrl+＋ / Ctrl+−  zoom in / out
  Ctrl+0 / Ctrl+1   fit in the window / true size

  Ctrl+S            save as PNG
  Ctrl+Shift+S      save as TIFF
  Ctrl+Alt+J        save as JPEG
  Ctrl+Alt+T        save the values as a 16-bit TIFF
  Ctrl+Shift+P      play the images
  Ctrl+Shift+A      save an animation
  Ctrl+Shift+C      copy to the clipboard

  With the picture itself clicked, single letters pick a tool:
  H hand, Z magnify, S select, B freehand, L line, A arrow,
  R rectangle, E ellipse, P polygon, T text, U ruler, G angle,
  Shift+R rectangle region, Shift+E ellipse region,
  Shift+P profile line, M marker, C crop, I pick a colour,
  D delete an item. While you are typing a text label the letters
  go into the text, not to the tools.

  Also on the picture: space bar and drag to move it, ＋ and − to
  zoom, 0 to fit, 1 for true size, the arrow keys to nudge the
  selected item (Shift for bigger steps), Delete to remove it and
  Esc to drop the selection.


=================================================================
FOR WHOEVER WORKS ON THE CODE
=================================================================

  STRUCTURE.md              what each module does and why
  image_tools_structure.md  line-by-line map of every file
  HANDOFF_02072026.md       older hand-over note, kept for context

  Readme Image Tools.txt    the short user description (the
                            Launcher's "ReadMe" button); this file
                            is what its "Details" button opens

  The testing folder holds the bench_*.py and test_*.py files:
  headless checks — they open no window and need no network. Run
  them before changing the viewer or the archiver client, from
  either folder (they find the sources themselves):

    python testing/test_day_split.py

    test_pv_stats_and_formula.py  the marked range's statistics, the
                             graph controls and a formula over time
    test_saved_moments.py    the session-only list of saved moments
    test_wall_regions.py     one row per day, a column per camera
    test_wall_export.py      Save view: PNG or PDF, one tab or all
    test_pv_regions_persist.py  a marked region never disappears
    test_pv_alias_and_order.py  SBW4 under either name; cameras or
                             moments picked in either order
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
