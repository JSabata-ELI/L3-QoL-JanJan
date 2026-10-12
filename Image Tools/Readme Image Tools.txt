Image Tools — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Image Tools_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT IS

  Everything to do with the camera images, in one window with five
  tabs. Find them, watch them, search them by machine value, and
  measure and mark them up.

      Image Finder   one camera across many days, side by side, to
                     see what changed
      Image Slider   play a sequence like a film, one camera or
                     several side by side, recorded or live
      Shot Finder    find the shots where a value was what you want
      Workshop       look at one image properly: measure it, mark
                     it, export it

  The tabs hand images to each other. Each of the first three has a
  row of "send this on" buttons for the other tabs — Image Finder,
  Image Slider and Workshop, minus the tab you are already in.

  What crosses over to the Image Finder is the MOMENT, not the file:
  it picks the day itself, looks the frames up itself, and puts that
  one instant side by side for every camera it was given. So you can
  send a shot the Shot Finder found, or the moment the Image Slider
  is parked on — even one you scrubbed to between two pictures.

  "One Moment" is gone: it is part of the Image Finder now. Picking a
  moment out of a graph and seeing every camera at that moment is
  done there, under "PV Search" — and you can pick as many moments as
  you like, on as many days as you like, in one go.


THE FOUR TABS

  COMPARE DAYS
    Click "Time window" to pick the days in the calendar and
    "Cameras" to choose the cameras — the two buttons sit side by
    side, as in the Image Slider — and the days come up next to each
    other, so a change shows itself. Press OK in the calendar when
    you have finished picking; that is what sends it off to look. The camera picker keeps presets,
    the same ones the Image Slider offers. Every day is drawn on the SAME scale, and one set
    of brightness and contrast controls applies to all of them —
    otherwise the comparison would lie.

    CLICKING A PICTURE MARKS IT. Nothing pops up. Once a picture is
    marked, brightness, contrast, gamma, the colour scheme, turning
    and the drawn marks all apply to THAT picture only; with nothing
    marked they apply to everything, which is what keeps the days
    comparable. EVERY CLICK ADDS: click picture after picture and all
    of them stay marked, and click a marked one again to let just that
    one go. SHIFT+CLICK marks a whole CAMERA — every day of it at
    once — and adds it to what was already marked. A picture on its
    own settings says so under it, so nobody mistakes it for one of
    the comparable ones.

    To look at one closely: DOUBLE-CLICK it, or right-click it and
    choose "View", which opens a window much bigger than the picture
    itself. There is also a "Detailed view" tab at the end of the row
    of tabs — it shows the marked pictures one at a time, and the
    arrows under it (or the left and right arrow keys) step through
    them. Mark nothing and it steps through the whole wall.

    "Day by day" gives each camera a line of its own: that camera's
    days side by side, the next camera on the next line. Four days
    fill the window and the rest is to the right — scroll sideways for
    them, and down for more cameras. The grey strip over each line
    names the camera; the day and the time are written under each
    picture.

    Every click ADDS to what is marked, so you can click picture after
    picture and all of them stay marked. Click a marked one again to
    let just that one go. Esc, or right-click and "Unmark every
    picture", lets everything go.

    The cameras you picked are listed under the Workshop button.
    Click one and all its pictures on the wall get marked — nothing
    pops up. Click another and it is added; click a marked one again
    to take it back off. Double-click a camera to take it out of the
    search.

    Pick one day as the reference and the others show how far they
    differ from it. That is the quickest way to see when something
    drifted. Right-click a day to make it the reference, or drop it
    again.

    "Save view" writes the whole view into ONE file with the days
    labelled, ready to put in a report — every row you would have to
    scroll down to and every camera off to the right, each picture at
    its own full resolution, so everything in it can be read. It asks
    PNG or PDF, and this tab or every tab.

    You can also pick a day and an hour and see what was recorded,
    with the shot energy and any machine values you asked for, and
    send anything on to Workshop or the Image Slider.

    "PV Search" is the other way in, and it is where One Moment went.
    Mark as many days as you like in its calendar and plot a machine
    value over them — one day at a time, or all the marked days next
    to each other. Then either:

      CLICK the graph — press and let go without moving. That moment
      is picked, and every further click picks ANOTHER one, on this
      day or on any other marked day, so you build the set up as you
      go. Ctrl+Z takes the last one back. You get every picked camera
      at every picked moment.

      DRAG over a stretch. Move the mouse at all while the button is
      down and you get a stretch of time, however narrow it looks.
      Every stretch gets its own picture from every camera, taken
      from the peak of the value inside it.

    You can do BOTH at once and both are searched: four clicks and
    four drags give you eight pictures from every camera. The button
    under the day list says how many there are altogether ("Search 8
    selections"), and on the wall every picture simply wears its own
    number — the numbering runs through moments and stretches alike,
    so no two picks ever share a number.

    The table under the graph lists everything you have picked —
    moments and stretches together, in time order, with their
    numbers — and, on the same row, what each plotted value did
    inside a marked stretch: how many samples, the mean, the spread
    and the two extremes. The ✕ at the end of a row throws that one
    away; Ctrl+Z brings it back. THE NUMBERS DO NOT MOVE when you
    delete one: if you had 1 2 3 4 and throw 3 away, what is left is
    still 1 2 4, so the number you were talking about is still the
    same picture. Press "Renumber" when you want them counted again
    from one.

    Or say it as a number: "the first moment SBW4 was above 13 J"
    gives you that one moment on all the cameras at once, and it
    checks the cameras were actually recording something before it
    answers.

    The cameras do not have to be picked first — press Search and it
    asks for them, then goes.

  IMAGE SLIDER
    The one used most. Choose a period and one or more cameras, and
    then scrub through the frames with the slider or play them back.
    In live mode it follows the archive as new shots arrive, and a
    new picture appears as soon as the file is finished being
    written rather than about a second later. Every camera in the
    grid does, including the ones whose file happens to land while
    the selected camera's own picture is still being read.

    LAYOUT TABS
    The row of small tabs at the top of the Image Slider holds several
    set-ups at once — "Ghost tile" in one, "Laser running" in another —
    each with its own cameras, arrangement, time window and live mode.
    "+" adds a tab (named Panel 1, Panel 2, …), × closes one,
    double-click a tab's name to rename it.

    Only the tab you are looking at does any work. The others sleep:
    nothing is read from the share, nothing is drawn, no machine values
    are fetched. Click back and it carries on where you left it, with
    nothing reloaded — a live tab jumps straight to the newest pictures
    that arrived while it slept, a recorded one is still on the same
    picture. Going to another of the five main tabs puts the Image
    Slider to sleep the same way.

    The tabs are remembered when Image Tools closes: names, cameras,
    arrangement and time window. A live tab comes back live, on the
    current hour. A recorded one comes back on the same period and the
    same picture.

    Anything sent here from the Image Finder, the Shot Finder or the
    Workshop opens in a NEW tab, so the set-ups you already have are
    not touched.

    The ◀ ▶ arrows move one image per click; hold one down and it
    speeds up — two images a second, then three, four, five, eight
    after five seconds and ten after eight. With several cameras, the
    master camera is the one the others follow — move any other
    camera's slider and it stays on the moment you left it.

    Every setting — brightness, contrast, palette, zoom — applies to
    the cameras that were SELECTED WHEN YOU MOVED THE CONTROL, or to
    all of them if none is selected. Picking a camera afterwards
    never copies anything onto it.

    A panel of machine values sits beside the picture and always
    shows the values belonging to the frame you are looking at,
    waiting for the archive if it has to rather than showing the
    previous shot's numbers. While it is waiting it keeps asking at
    a steady pace instead of backing off, so the numbers land with
    the picture rather than a beat after it.

    A coloured dot next to a value says what state it is in — on the
    panel over the picture, in the PV table and in a saved picture:
    red = not loaded yet (it fills itself in), grey = no value
    (nothing archived near this frame, or it could not be read),
    orange = the number belongs to an earlier shot than the picture.
    No dot = this frame's own reading. Hover the value in the PV
    table for the details (how much earlier, and why).

    LIMITS AND TRIPS — LIVE MODE ONLY
    Any of those values can be given a limit. Open "Select PV
    channels" and fill in Min, Max or both next to the value — a
    back reflection you want kept under 0.3, say. An empty box means
    that value is not watched.

    This watches the shots as they come in. It is NOT a search of the
    archive: picking a whole day and scrubbing through it will not
    tell you whether the back reflection was ever over 0.3 that day,
    and nothing flashes or gets recorded while you are browsing.
    Only frames you actually look at are ever read, so there would be
    nothing to search anyway.

    When a value goes past its limit it flashes red over the picture,
    and a line appears at the top of the Info panel saying WHEN it
    happened: "14:32:07  Back reflection 0.42 J — above 0.30". The
    flashing stops by itself as soon as the next shot comes in, so it
    never sits there shouting about a moment that has passed. The
    line stays.

    Press "See" next to that line and the picture goes back to that
    shot: every camera at that moment, and the machine values read
    again for it. Live mode switches off so it stays on screen — and
    because the watching is live-mode only, nothing flashes once you
    are there; the line itself says which value it was and what it
    read. "Clear trips" empties the list.

    Leaving live mode stops the flashing straight away. The lines
    stay: they are the record of what happened while it was live, and
    going back to look at one is exactly what takes you out of live
    mode.

    A camera that stops delivering — an image that cannot be read, a
    folder that cannot be listed — leaves the same kind of line, so a
    gap in the record can be gone back to as well. It is one line per
    problem, not one per shot, with a count on it if it went on.

    "Overlay settings" chooses what the warning looks like: just the
    value flashing, or the whole panel flashing red after it. The
    panel flash keeps going until you have looked at every trip or
    pressed "Clear trips", so nothing can pass unnoticed while you
    are looking away.

    The same window sets how the panel of values over the picture
    looks: its font, its colours, and how big the panel itself is.
    "Panel size" is in pixels and reads "auto" at 0, which is the
    panel exactly as big as its values; type a width or a height and
    it stays that size whatever the numbers do. All of it is
    remembered for the next time.

    SHOT COUNTER. The last row of the PV table counts shots in live
    mode, and the panel over the picture shows it too. Each shot counts
    once, when the camera on screen, another open camera at the same
    rate, or a laser energy reading saw it — so a picture missing from
    the archive does not make it skip a shot. Changing the camera keeps
    the number; it only goes up with a new shot. After X seconds
    without a shot the count starts again from zero — the old number
    stays on screen until the next shot, which reads 1. Double-click
    the row to set X (10 s to start with).

    The PV panel always stays above the cameras, also in focus view
    (F11) and watcher view (Ctrl+F11). In focus view every camera
    window is cut to its picture, so no grey band is left around it;
    the picture keeps its size.

    With several cameras open, the program sizes their windows so
    that even the smallest picture comes out as large as the screen
    allows, and then, among the arrangements that are as good as that,
    picks the one showing the most picture altogether. Any room left
    over is an even grey margin around the picture.

    Press "Camera" and you see that arrangement straight away, under
    the list of picked cameras, in a Layout picture that is a true
    scale model of the camera area: each window and each picture has
    the proportions it will really have, name bar included. Each
    camera's name is written across its own picture, always at a size
    you can read.

    You can also say how important each camera is. Click a camera in
    the Layout picture and five buttons come alive above it —
    Smallest, Small, Medium, Large, Largest. A Largest camera is asked
    for sixteen times the picture area of a Smallest one, and the
    program then arranges them that way while still filling the whole
    area. The choice is written under the camera's name so you can read
    it without clicking, and it is remembered for that camera from then
    on, in every set you open it in. New cameras start at Medium.

    Wide differences cost empty space: a small camera gets a small
    window and its picture cannot fill a window of another shape, so
    grey room is left around it. Out of all the arrangements that come
    out at the sizes you asked for, the program picks the one showing
    the most picture.
    With more than twelve cameras open the sizes are ignored
    altogether and every camera is treated the same.

    To arrange them yourself, drag a camera by its name bar to move it
    and by its border to resize it — in the Layout picture, or on the
    live pictures while they keep playing. Right-click a name bar and
    choose "Auto-arrange cameras", or press "Auto-arrange" in the
    Layout picture, to hand the arrangement back to the program.

    Your arrangement can be saved: open "Camera", type a name and
    press Save next to the list of saved sets. The preset then holds
    the cameras, where each one sits AND how big each one is set to
    be, so it comes back exactly as you left it.

    MARKS ON THE PICTURE. Switch on Draw for a cross, a circle or a
    box and draw as many of them as you need — every click adds
    another one. Click a mark to pick it out and press Delete to take
    it away; Ctrl+click makes a new mark on top of one that is already
    there instead of picking that one up. You only ever click the
    shape you have switched on, so the others are never in the way.
    With several cameras marked, every mark lands on all of them at
    the same place in each picture, and Delete takes it off all of
    them. With no camera marked, nothing appears until you click a
    picture, and the mark then goes on that one camera only, where
    you clicked.

    A picked mark has a round knob with a turning arrow above it:
    drag the knob to turn the mark (hold Shift for steps of 15°).
    "Overlay settings" turns them by number too — pick one mark or
    "All …" from the list under each shape, or turn every mark at
    once with "All marks angle". Each shape can also have an
    outline: a thin rim (black unless you choose otherwise) along
    both sides of the line, so it stays visible on a bright beam
    and on a dark background alike. The look is remembered.

    PCW3_NF shows the permanent reference square that is set on the
    camera itself. The camera screen draws it, but it is not part of
    the stored picture, so the program draws it back on. Right-click
    the picture to switch it off or to nudge its edges. The diodes
    have their measuring grid behind the same right click; on every
    other camera a right click opens nothing, and right-click-drag
    still zooms everywhere. A MIDDLE click puts the camera you
    clicked back to the whole frame, that one camera only. The marks
    belong to the picture, not to the screen: zoom in and they stay
    on the same spot of the beam, growing with it, and keep their
    shape (a circle stays round); zoom out and they are back exactly
    as drawn.

    THE COUNTER at the top of INFO. With one camera: the frame on
    screen / frames loaded. With several: "12 / 340 merged · 1020 in
    total" — the shot on screen / shots (frames of the cameras taken
    at the same moment count as ONE shot, even when the cameras stamp
    it up to a second or two apart), then every frame of every camera.

    Units typed as mm^2 or cm^-1 are shown as mm² and cm⁻¹
    everywhere; the Unit box keeps what you typed.

    SAVING. "Save Image" writes the frame on screen. When something
    is burned in — the marks, or the PV values with "Save with
    overlay" ticked — you get TWO files: the untouched original and
    <name>_annotated.png beside it. With several cameras, "Save
    Layout" writes all of them into ONE picture, looking as on the
    screen: the tiles with their frames, camera names, times and
    marks. Tick "PV overlay" next to it to include the PV values
    panel. After every save the message has "Open" next to "OK";
    when several files were saved, pick the one to open from the
    list above the buttons ("Open folder" shows the folder).

    POINTING ANALYSIS measures where the beam centre is on every frame
    and plots how far it is from the MEDIAN of all shots, which sits
    at 0, 0. Frames with no beam on them (only the background, or a
    faint glow) are left out. Deleting points moves the median (Undo
    puts it back). The status line says how many were left out and how
    many were saturated. A progress bar shows how far the run has got
    (its tooltip counts the frames analysed so far).
    Choose px (sensor pixels), mm or µrad (angle at the experiment,
    Y up) under "Unit". The "Optics" button opens a window where each
    camera is set as Far field or Near field, with Pixel, M and f.
    Far field (the chip sits in the focus of a lens, like the C310):
    mm = px · pixel is the shift ON THE CHIP, and θ = x · M / f the
    angle. Near field (the chip sees an image of the beam): mm = px ·
    pixel / M is the real shift at the experiment; µrad is greyed out.
    Set per camera and remembered (a new camera starts at the C310
    values: far field, 4.5 µm, 1/66, 300 mm = 0.0045 mm and 0.227 µrad
    per pixel; "C310 defaults" puts them back). "Method" picks Peak
    core (this program's own: only the spot above Thr % of its peak
    is weighed, zero = the median shot) or Full-frame centroid — the
    MATLAB script exactly (whole frame, pixels above Thr counts at their
    full value, zero = the mean shot; same numbers as the script's file
    on the C310 run of 3 Sep 2026). "View" picks X–Y by time (with a
    time colour bar), X-Y drift (the X and Y deviation against the shot
    number, each graph with its shot axis, pale = every shot, dark
    line = the trend, a running median that shows a slow drift through
    the shot-to-shot noise; drag the round knob of the shot slider
    between them to walk through the shots — the frame opens and both
    points are ringed; a black line on the slider marks a pause in the
    shooting) or 3D (X, Y and shot number; drag to turn).
    Time is coloured as a rainbow, dark blue (first) to dark red
    (last). The colour bar beside the beam path, X–Y by time and 3D
    shows real times (the day too over several days); drag the
    round knob on it to walk through the shots — the exact time is
    shown beside it and the frame opens. Every switch redraws without
    measuring again. Click a point — on the cloud or on the beam path
    — to open that frame; the frame on screen is ringed in red. The
    wheel zooms either plot,
    double-click shows all of it again. "Close graph" only hides the
    graph — "Open graph" brings it back as you left it — until you
    open other cameras.

  SHOT FINDER
    Give a value and a tolerance — an energy, a waveplate angle —
    and it finds every shot in a date range that matches, across
    several conditions at once. The results can be opened, previewed
    or sent on to the Image Finder, the Image Slider or Workshop.
    "Act on" says what the three send buttons and Save work with:
    the day rows in the table, or the one shot picked in the day
    detail.

    Two buttons say what is searched: "Time window" for the days and
    hours and "Cameras" for the cameras. Both pickers are the ones
    the Image Slider uses — one calendar with the times to the
    minute, and the camera list with its saved sets — so the days
    need not follow one another, and a set of cameras saved in one
    tab is offered in the other. The times open on 07:00 to 21:00,
    the shift rather than the whole calendar day.

    Under "PVs" one button, "Configure PVs", opens the same picker
    the Image Slider uses, so a PV you add or rename in one tab is
    the same PV in the other. Every PV whose eye is open gets a row
    under the button: tick the row to search by it, and type the
    value and the tolerance beside it; leave it unticked to only
    see its value in the results.

    Then two buttons: "Load data" reads only the numbers and says
    how many shots fall inside the band - no pictures, so it is
    quick even over a month - and "Load images" fetches the picture
    of every day as well. Pressing "Load data" first costs nothing:
    "Load images" then only looks for the pictures, the rows already
    on screen stay where they are, and the "Image" column beside the
    date fills in - a green tick where the picture is there, a red
    cross where it is not, a grey dash where nobody has looked yet.

    A day where nothing fell inside the tolerance is skipped: no
    picture is looked for on it. Those days used to be the slowest
    of the whole search, and the table had already said there was
    nothing there. The row stays in the list, yellow, and says
    "image not searched" instead of pretending a picture was
    looked for and not found. Widen the tolerance to search them.

    Double-click a day in the results and a window opens with every
    matching shot of that day, and a graph beside it showing the
    searched value over the whole day and where in it the shot you
    are looking at sits. The graph gets the bigger half of the
    window, and its curve holds each value until the next one
    arrives, the way CS Studio draws it. The window holds nothing
    else - the picture stays in the picture area of the main window -
    and it can be moved aside or made big. Switching to another
    camera's tab keeps that place.

    The last column of that list is the time the picture itself
    carries, and it fills in by itself as the window opens. Double-
    click a row to open the folder with that frame selected.

    While a search runs, the bar under the two buttons says how far
    it has got and the line under it what is being read right now, so
    one slow day no longer looks like a program that has stopped.
    How long is left is measured - the archiver read and the picture
    hunt are timed separately and added up - and each search
    remembers the two speeds for the next one.
    Changing the picked days shows the same bar while the cameras of
    those days are being looked up.

  WORKSHOP
    One image at a time, properly. Zoom and pan, non-destructive
    brightness / contrast / gamma / palette, a histogram, drawing
    tools (freehand, lines, arrows, boxes, ellipses, polygons,
    text), a ruler and an angle tool with a real scale, measuring
    regions with a results table, profiles across the beam with
    width and fit, beam size and roundness, filters, rotation and
    cropping, image arithmetic, and export as picture, data file or
    animation.

    THE RULE THAT MATTERS: everything in the Display section only
    changes how the picture LOOKS, never the picture itself. Only
    the Filters and Edit sections and the Crop tool change it, and
    every one of those can be undone. Saving always writes a NEW
    file — the file the image came from is never overwritten.

    "Save with overlay" in the Save section, and in the right-click
    menu on the picture, decides whether everything you drew goes
    into the file with it — text, lines, strokes, regions, the scale
    bar and the numbers the measuring tools wrote themselves. Untick
    it and only the picture is written.


ABOUT BRIGHTNESS — THE ONE THING WORTH READING

  The stored images are 16-bit, but the archive does NOT store them
  on a fixed scale. It multiplies the sensor counts by a factor
  taken from each frame's OWN brightest pixel. So dividing a stored
  value by the maximum gives a number that changes from frame to
  frame even when the camera saw exactly the same thing — and a
  camera sitting near the boundary flickered between dark orange and
  green every few seconds at unchanged shot energy.

  So the program does not do that. It works out the real sensor
  counts and shows every frame of a camera against the largest range
  that camera has ever needed, learned and remembered. Two frames of
  the same camera are then directly comparable, and so are two
  cameras. A frame that reads as a few percent of full scale is a
  weak frame, not a broken display.


GENERAL

  - The images come from one network path, the same one from the lab
    and from the office. Nothing to choose: the Shot Finder used to
    show a Lab / Office switch whose two entries were the same path.
  - Everything slow runs in the background. The red "Stop All"
    button in the status bar stops all of it.
  - A very busy day holds more samples than the archive will answer
    for in one request. The program notices and asks for the day in
    halves until each piece fits — slower, never incomplete.
  - Supported formats: PNG, TIFF, JPG, BMP.

-----------------------------------------------------------------
