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

  1. "Load day..." and pick a day. A plain click picks a day;
     Ctrl+click adds the whole range from your previous click, so
     several days at once are allowed.
  2. The top graph shows the signal you are searching by. Click a row
     in the signal table to plot a different one.
  3. Switch "Select" on in the toolbar above the graph and drag over
     the graph to mark a region. Repeat as often as you like.
  4. Press "Analyze". Every region that has not been done yet is
     fetched and averaged in the background.
  5. Change the averaging method, the normalisation, the colouring,
     the smoothing or the spread band whenever you like — nothing is
     fetched again. All the methods are worked out at the same time.
  6. "Export results".


LIVE MODE

  Choose how many recent spectra to average, press "Start Live", and
  it preloads the last ten minutes and then checks for new shots
  every three seconds. The graph is only redrawn when a shot actually
  arrives, so the window stays responsive. "Stop Live" freezes it.


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

  Each region in the list can be hidden, expanded to show its
  numbers — energy, dispersion, peak wavelength, width, centre,
  area — and switched to show every individual spectrum behind the
  average rather than just the average.

  "Compare regions" draws the difference or the ratio of two regions
  as an extra curve.


SETTINGS AND WHERE THEY LIVE

  Your signal list, your presets, the chosen spectrum channel and the
  window layout are all remembered per user. Deleting that settings
  folder puts everything back to default.

-----------------------------------------------------------------
