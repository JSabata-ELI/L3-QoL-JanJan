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
      One Moment     pick a moment in the graph and see every
                     camera at that moment
      Workshop       look at one image properly: measure it, mark
                     it, export it

  The tabs hand images to each other. Anything you find in the first
  four can be sent to Workshop with one button.


THE FIVE TABS

  COMPARE DAYS
    Click "Time window" to pick the days in the calendar and
    "Cameras..." to choose the cameras — the two buttons sit side by
    side, as in the Image Slider — and the days come up next to each
    other, so a change shows itself. The camera picker keeps presets,
    the same ones the Image Slider offers. Every day is drawn on the SAME scale, and one set
    of brightness and contrast controls applies to all of them —
    otherwise the comparison would lie.

    The cameras you picked are listed under the Workshop button.
    Click one to look at it, double-click it to take it out again.

    Pick one day as the reference and the others show how far they
    differ from it. That is the quickest way to see when something
    drifted. Right-click a day to make it the reference, or drop it
    again.

    "Save comparison" writes the whole wall as ONE picture with the
    days labelled, ready to put in a report. Click a day to look at
    that one frame closely, and "One frame" / "Days side by side"
    switches between the two.

    You can also pick a day and an hour and see what was recorded,
    with the shot energy and any machine values you asked for, and
    send anything on to Workshop or the Image Slider.

  IMAGE SLIDER
    The one used most. Choose a period and one or more cameras, and
    then scrub through the frames with the slider or play them back.
    In live mode it follows the archive as new shots arrive.

    Every setting — brightness, contrast, palette, zoom — applies to
    the cameras that were SELECTED WHEN YOU MOVED THE CONTROL, or to
    all of them if none is selected. Picking a camera afterwards
    never copies anything onto it.

    A panel of machine values sits beside the picture and always
    shows the values belonging to the frame you are looking at,
    waiting for the archive if it has to rather than showing the
    previous shot's numbers.

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

    To arrange them yourself, drag a camera by its name bar to move it
    and by its border to resize it, while the pictures keep playing.
    Right-click the name bar and choose "Auto-arrange cameras" to hand
    the arrangement back to the program.

    Your arrangement can be saved: open "Cameras...", type a name and
    press Save next to the list of saved sets. The preset then holds
    the cameras AND where each one sits, so it comes back exactly as
    you left it.

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

  ONE MOMENT
    The other way round from every other tab. They show one thing
    over time; this one shows everything at one time.

    All the settings are in the panel down the left, as in the Image
    Slider. The first button opens the calendar and picks the day and
    the From/To times — the same calendar as in the Image Slider —
    and afterwards it says on itself what you picked, for example
    "24.08.  07:00-19:00". The button beside it picks the cameras and
    says how many. "Search / select PVs..." picks the machine values
    — the same search and the same list as everywhere else in the
    program. "Load" then draws them, ALL IN ONE GRAPH:
    values in the same unit share a scale, values in different units
    each get a scale of their own, and nothing is stretched to fit
    anything else.

    On the graph, the LEFT button reads it and the RIGHT button looks
    closer at it:
      - LEFT CLICK a moment. The frame from every camera you picked
        comes up on the right, and the left panel says what each
        value was at that moment. "prev" / "next" step from shot to
        shot.
      - LEFT DRAG across a stretch. "Range statistics" then gives the
        average, the spread and the count for each value; hover a row
        for the smallest, the largest and the peak-to-peak.
      - RIGHT DRAG across a stretch to zoom the time axis into it;
        RIGHT CLICK to step back out again. The marked stretch and
        the picked moment are not touched by zooming.

    A moment you CLICK in the graph is kept, listed under "The
    moment" with the newest at the top. Click a row to go back to
    that moment instead of hunting for it again; "Forget" drops one
    row and "Clear" empties the list. The list survives a restart.

    "prev" and "next" save nothing — stepping through a stretch of
    the day would otherwise bury the list under moments you were only
    walking past. When you get to one worth keeping, press "Save".
    It greys out once that moment is already on the list.

    Going back to a moment you have already looked at is instant: the
    frames are kept in memory, so nothing is read from the share a
    second time.

    "Send to Image Slider" opens the moment in the Image Slider with
    the cameras you picked here — full size, with everything that tab
    can do. It gets the quarter of an hour on either side of the
    moment, so you can slide through the shots around it, and it
    opens on the one you sent.

    If a value has no reading inside the marked stretch — a waveplate
    or a motor is only written down when it MOVES — the last reading
    from before the stretch is carried forward instead. Those rows
    are amber and say "held" where the spread would be, with the
    count at 0, so they cannot be mistaken for a real average.

    A click lands on the nearest real reading of the first value
    drawn, because a moment between two readings has no shot behind
    it. Each frame is captioned with the camera and its own time;
    hover it for how far it is from the moment you asked for — a
    frame is stored only about every 35 seconds, so a few seconds'
    difference is normal.

    "Image / Display" changes how the frames look: contrast,
    brightness, gamma, palette and size, with the same meanings as in
    the Image Slider. Changing them redraws the frames without
    reading them from the share again, and going back to a setting you
    already had is instant too. Holding Ctrl and rolling the
    mouse wheel over the frames makes them bigger or smaller in
    place — the window and the graph do not move.

    The eye next to a value takes it off the graph without unpicking
    it. Click a frame to send it to Workshop. The grey bar between
    the graph and the frames can be dragged to give either of them
    more room.

    A value you built from a FORMULA is drawn like any other one. The
    values it is built from are read even if you did not tick them,
    and the line breaks wherever one of them has nothing to give,
    rather than being drawn straight across the gap. A formula with
    no unit gets a scale of its own, so a ratio around 1.5 is not
    flattened against a motor position of 20000.

    The tab remembers what you left it on: the window, the cameras,
    the values, the saved moments, the sliders and which panels were
    open. Nothing is read from the archive until you press "Load".

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
