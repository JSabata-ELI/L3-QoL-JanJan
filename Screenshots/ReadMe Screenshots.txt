Screenshots — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Screenshots_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Collects camera images and screen pictures into one folder, ready
  for a shift log or a report. You tick what you want, press Copy,
  and everything lands in the destination with sensible names.


THE TWO SOURCES

  Archiver           takes the newest stored image of each camera you
                     ticked, straight out of the image store on the
                     network. This is the real image data.

  Screenshot window  takes a picture of a monitor, of all screens, or
                     of the camera window as it is displayed. This is
                     what the screen looked like, including any
                     scaling and palette the display was using.

  Use the archiver when you want the image. Use the screenshot when
  you want the evidence of what the operator was looking at.


PICKING CAMERAS

  Cameras are grouped by section of the beamline: LT1 to LT7, the
  Compressor and L3BT. There is a search box, and each group has an
  "All" tick.

  Cameras that do not exist at this workstation are greyed out —
  hover one to see where it does exist. The workstation is
  recognised automatically from the computer's name.

  Presets tick a whole set at once. There are built-in ones (all
  cameras, and one per section) and you can save your own; yours are
  kept next to the program and survive a restart. Hover a preset to
  see what is in it.

  Ticking cameras also ticks the monitors those cameras are shown
  on, so a screenshot run does not need a second selection.


CAPTURING

  Copy         once, now.
  Start auto   every so many seconds, for a number of rounds or
               until you stop it.
  Start live   continuously, with the preview updating as it goes.

  A progress bar counts through the run and keeps counting across
  rounds. Stop interrupts the round in progress.


NAMING AND SAVING

  Pick a destination folder. Then:

  - one image      the name field is a FILE name
  - several images the name field is a FOLDER name; leave it empty
                   to write straight into the destination

  Camera images are named after the camera and the image's own
  timestamp, in Prague time. Screen pictures are named after the run
  time and the monitor.

  Labels adds your own prefix or suffix per camera, and an
  automatic 01, 02, 03 counter. Detail attaches a short text note,
  saved as a small text file next to the images.


THE PREVIEW

  Tick Preview and the run opens as a grid of what it copied, before
  you commit to it:

    Save                    keep the files
    Delete                  throw the whole run away
    Delete and Try Again    throw it away and copy again

  Above the grid, contrast and brightness sliders (each with an Auto),
  zoom, and a choice of colour palettes. Hover a thumbnail for a
  bigger look; click it to open the editor, where you can crop, draw
  on it, and apply or revert.

  Images you cropped or drew on are also written out as a separate
  file with "_annotated" in the name, so the untouched original is
  never lost.


ONE THING WORTH KNOWING

  The camera folder listings are re-read in the background every
  three seconds, so pressing Copy does not have to wait for the
  network. The first run after starting the program is the slow one.

-----------------------------------------------------------------
