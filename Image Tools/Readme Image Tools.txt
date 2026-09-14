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

  The tabs hand images to each other. Anything you find in the first
  three can be sent to Workshop with one button.

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

    The ◀ ▶ arrows move one image per click; hold one down and it
    speeds up to five images a second. With several cameras, the
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

    PCW3_NF shows the permanent reference square that is set on the
    camera itself. The camera screen draws it, but it is not part of
    the stored picture, so the program draws it back on. Right-click
    the picture to switch it off or to nudge its edges. The diodes
    have their measuring grid behind the same right click; on every
    other camera a right click opens nothing, and right-click-drag
    still zooms everywhere.

  SHOT FINDER
    Give a value and a tolerance — an energy, a waveplate angle —
    and it finds every shot in a date range that matches, across
    several conditions at once. The results can be opened, previewed
    or sent to Workshop.

    Two buttons say what is searched: "Time window" for the days and
    hours and "Cameras" for the cameras. Both pickers are the ones
    the Image Slider uses — one calendar with the times to the
    minute, and the camera list with its saved sets — so the days
    need not follow one another, and a set of cameras saved in one
    tab is offered in the other. The times open on 07:00 to 20:00,
    the shift rather than the whole calendar day.

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

    While a search runs, the bar under "Load data" says how far it
    has got and the line under it what is being read right now, so
    one slow day no longer looks like a program that has stopped.
    How long is left appears once it has been measured on the days
    already done, not guessed from the start.
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
