Spectra — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Spectra_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Looks at spectrometer data — SPIDER by default — either from the
  archive or live. You mark the stretches of time you are interested
  in, and it averages the spectra inside each one, so you can compare
  them side by side and export the result.

  It is a tab inside CSS Logger, not a program of its own.


THE IDEA IN ONE PARAGRAPH

  A single spectrum is noisy and a whole day of them is unreadable.
  What is useful is the average of the spectra taken while the
  machine was in one particular state. So the top graph shows a
  signal you can navigate by — the shot energy, for instance — and
  you drag over the parts of the day where the state was what you
  wanted. Each drag becomes a region, and each region becomes one
  averaged spectrum in the graph below.


THE TWO MODES

  Archive   load one or more days, mark regions, average them,
            compare and export.
  Live      watch the newest shots and show a rolling average of the
            last so many.


ARCHIVE MODE, STEP BY STEP

  The top of the left panel is numbered 1 to 3 in the order you work
  through it: the day, the signal you search on together with the
  list you pick it from, and the spectrum you measure. Card 2 always
  names the signal the search is actually running on.

  1. "Load day..." and pick a day. A plain click picks a day;
     Ctrl+click adds the whole range from your previous click, so
     several days at once are allowed.
  2. The top graph shows the signal you are searching by. Click a row
     in the PV list to search by a different one. The tick box in
     front of each row says whether that signal is drawn in the
     graph at all, so a long list does not turn into a wall of
     curves.
  3. Switch "Select" on in the toolbar above the graph and drag over
     the graph to mark a region. Repeat as often as you like.
  4. Press "Analyze". Every region that has not been done yet is
     fetched and averaged in the background.
  5. Change the averaging method, the normalisation, the colouring,
     the smoothing or the spread band whenever you like — nothing is
     fetched again. All the methods are worked out at the same time.
     The graph keeps the size you gave it while you do this, and the
     method you picked is named in the graph title.
  6. "Export results".


LIVE MODE

  Choose how many recent spectra to average, press "Start Live", and
  it preloads the last ten minutes and then checks for new shots
  every three seconds. The graph is only redrawn when a shot actually
  arrives, so the window stays responsive. "Stop Live" freezes it.

  Because it starts from only the last ten minutes, there are usually
  fewer shots in the buffer than you asked for at first. The line
  under the panel says how many are really being averaged.


FOCUS MODE — F11

  F11 puts the spectra graph on its own, filling the screen, with no
  window frame and no title bar at all: just the graph. Esc or F11
  again brings it back. It works in either mode — in Live mode the
  graph carries on updating there.

  Drag anywhere on the graph to move that window, drag its outer edge
  to resize it. Where you put it is remembered for next time.


THE MAIN DISPLAY CHOICES

  Average         Mean, Median, Trimmed mean, Sigma-clipped mean —
                  four ways of ignoring outliers to different
                  degrees.
  Colour by       the order you selected the regions in, or by the
                  measured dispersion, which adds a colour scale.
  Normalize       off, to the peak, or to the area — for comparing
                  shape rather than size.
  Variation band  a shaded band showing how much the spectra inside
                  a region differed from each other.
  Smooth          a moving average, with an adjustable width.
  Spectrum range  the wavelength window, with an automatic fit.


PER REGION

  The box at the top of the panel says what the tab is doing: Idle,
  "Working..." while a day or an analysis is loading, or a blinking
  LIVE. "Stop" next to it stops whatever is running.

  Each region in the list can be hidden, expanded to show its
  numbers — energy, dispersion, peak wavelength, width, centre,
  area — and switched to show every individual spectrum behind the
  average rather than just the average.

  "Compare regions" draws the difference or the ratio of two regions
  as an extra curve.

  If you change the spectrum with "Change...", your marked regions
  stay where they are and are simply worked out again for the new
  channel. You do not have to delete them and mark them a second
  time.


SETTINGS AND WHERE THEY LIVE

  Your signal list, your presets, the chosen spectrum channel and the
  window layout are all remembered per user. Deleting that settings
  folder puts everything back to default.

-----------------------------------------------------------------
