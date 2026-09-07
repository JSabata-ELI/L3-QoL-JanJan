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

    The cameras you picked are listed under the Workshop button.
    Click one to look at it, double-click it to take it out again.

    Pick one day as the reference and the others show how far they
    differ from it. That is the quickest way to see when something
    drifted. Right-click a day to make it the reference, or drop it
    again.

    "Save view" writes the whole view into ONE file with the days
    labelled, ready to put in a report — every row, including the ones
    you would have to scroll to. It asks PNG or PDF, and this tab or
    every tab. Click a day to open that frame in a close-up window,
    much bigger than the picture itself; the arrows under it step
    through the rest of the wall.

    You can also pick a day and an hour and see what was recorded,
    with the shot energy and any machine values you asked for, and
    send anything on to Workshop or the Image Slider.

    "PV Search" is the other way in, and it is where One Moment went.
    Mark as many days as you like in its calendar and plot a machine
    value over them — one day at a time, or all the marked days next
    to each other. Then either:

      CLICK the graph. That moment is picked, and every further click
      picks ANOTHER one, on this day or on any other marked day, so
      you build the set up as you go. Ctrl+Z (or the Undo button)
      takes the last one back. You get every picked camera at every
      picked moment.

      DRAG over a stretch. Every stretch gets its own picture from
      every camera, taken from the peak of the value inside it, and
      the table under the graph gives the numbers for it.

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
    tab is offered in the other.

    Double-click a day in the results and you get every matching shot
    of that day, with a graph beside it showing the searched value
    over the whole day and where in it the shot you are looking at
    sits. Switching to another camera's tab keeps that place.

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
