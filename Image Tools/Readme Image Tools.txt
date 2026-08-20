Image Tools — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Image Tools_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT IS

  Everything to do with the camera images, in one window with four
  tabs. Find them, watch them, search them by machine value, and
  measure and mark them up.

      Image Finder   which images exist, and what the machine was
                     doing when each one was taken
      Image Slider   play a sequence like a film, one camera or
                     several side by side, recorded or live
      Shot Finder    find the shots where a value was what you want
      Workshop       look at one image properly: measure it, mark
                     it, export it

  The tabs hand images to each other. Anything you find in the first
  three can be sent to Workshop with one button.


THE FOUR TABS

  IMAGE FINDER
    Pick a day and an hour and see what was recorded. Each image is
    listed with the shot energy and any machine values you asked
    for. From here you can preview an image, open its folder, or
    send it to Workshop.

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

    With several cameras open, the program sizes their windows so
    that even the smallest picture comes out as large as the screen
    allows. To arrange them yourself, drag a camera by its name bar
    to move it and by its border to resize it, while the pictures
    keep playing. Right-click the name bar and choose "Auto-arrange
    cameras" to hand the arrangement back to the program.

  SHOT FINDER
    Give a value and a tolerance — an energy, a waveplate angle —
    and it finds every shot in a date range that matches, across
    several conditions at once. The results can be opened, previewed
    or sent to Workshop.

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

  - Two data sources: the lab network path, or the office drive.
  - Everything slow runs in the background. The red "Stop All"
    button in the status bar stops all of it.
  - A very busy day holds more samples than the archive will answer
    for in one request. The program notices and asks for the day in
    halves until each piece fits — slower, never incomplete.
  - Supported formats: PNG, TIFF, JPG, BMP.

-----------------------------------------------------------------
