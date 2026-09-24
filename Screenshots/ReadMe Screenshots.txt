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

  Presets tick a whole set at once. "Config presets" opens the list:
  add one with New, delete any of them with Remove, rename it by
  typing over the name, and drag a row up or down to change the order
  the preset buttons appear in. The Search box finds a camera among
  the hundred in the list. The whole list is kept next to the program
  and survives a restart; "Restore defaults" brings the original
  presets back. Hover a preset to see what is in it.

  Ticking cameras also ticks the monitors those cameras are shown
  on, so a screenshot run does not need a second selection.


CAPTURING

  Copy         once, now.
  Start auto   every so many seconds, for a number of rounds or
               until you stop it.
  Start live   continuously.

  A progress bar counts through a Copy. Stop ends an auto run.

  Auto rounds are evenly spaced: the first round is the moment you
  press Start, and every round after it is exactly the interval
  later, whatever the copying takes. Each round remembers its own
  moment, and each camera gives the picture it had at that moment —
  so the pictures of one round belong together instead of being
  strung out over several seconds.

  The FIRST round simply takes the newest picture each camera has.
  It also measures from that how far the archive runs behind this
  computer's clock (this PC is seconds ahead of the facility), and
  every round after it is corrected by that amount. The log says how
  much it was.

  From the second round on, if a camera has nothing that recent, the
  program goes back in time for it, up to ten seconds. Past that the
  camera is left out of the round, unless it is still sitting on the
  very same picture as the round before — a camera that is standing
  still keeps showing up. Whatever is left out is written in the
  Diagnostics panel, together with how far apart the round's pictures
  ended up.


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

  Above the grid: contrast and brightness (each with an Auto and a ↺),
  gamma with its own ↺, and on the second row zoom and the palette.
  Hover a thumbnail for a bigger look; click it to open the editor,
  where you can crop, draw on it, and apply or revert.

  Contrast stretches the values apart, brightness moves them all up
  or down, gamma bends the middle while leaving black black and white
  white. ↺ puts a control back to untouched and switches its Auto off
  with it.

  The palette starts on Original, which means the picture in the
  colours it arrived in — a colour camera stays in colour, whatever
  you do with contrast, brightness or gamma. Grayscale deliberately
  drops the colour; everything after it paints false colours over the
  grey values.

  Images you cropped or drew on are also written out as a separate
  file with "_annotated" in the name, so the untouched original is
  never lost.


THE PREVIEW DURING AN AUTO RUN

  One window opens with the first round and stays open for the whole
  run. Pictures appear in it as they are copied, so you can watch the
  run happen.

  At the top: the round you are looking at, arrows to step between
  rounds, and Follow latest. Following is on to start with, so the
  window always shows the round being copied. Step with an arrow (or
  pick a round from the list) and following switches off, so the
  round you are studying is not pulled away from under you; tick
  Follow latest again to rejoin the run.

  It stays one window — no new pop-up per round. Save writes the
  annotated copies without closing it, and Delete this cycle throws
  away only the round on screen.

  Under each picture is its own time and, in brackets, how far it is
  behind the round's moment.


ONE THING WORTH KNOWING

  The camera folder listings are re-read in the background every
  three seconds, so pressing Copy does not have to wait for the
  network. Starting an auto run reads them all once up front, so the
  first round is no slower than the rest.

-----------------------------------------------------------------
