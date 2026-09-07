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
  them side by side and export the result. If you would rather see
  the shots themselves, "Show: Every spectrum" draws every single
  one instead of the average — and a bar under the search graph then
  walks through them one at a time, drawing the one you are on in
  bold and marking it in the graph above.

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

  The left panel is laid out in the order you work through it. The
  top is numbered 1 to 3: the day and time, the signal you search on
  together with the list you pick it from, and the spectrum you
  measure. Card 2 always names the signal the search is actually
  running on. Below the three cards comes the list of what you have
  marked ("Selected spectra"), then Archive / Live, then "Analyze"
  with its progress bar, and then the two buttons that end the job:
  "Clear all" and "Export results".

  Everything that only changes how the result is DRAWN is gathered in
  one purple block at the very bottom, "Display settings" — the graph
  options, the wavelength (or time) window and the comparison curve.
  Click its purple title to fold it away when you do not need it.

  1. "Load day and time..." opens the calendar — the same one the
     Image Tools programs use. A plain click picks one day;
     Ctrl+click adds another day; Ctrl+Shift+click takes a run of
     days from your last click. Underneath, From and To set the hours
     to load, 08:00 to 19:00 by default. As soon as a second day is
     picked, a table appears where each day can be given its own
     From and To — so "Monday morning and Wednesday afternoon" is one
     selection. Only the hours you chose are read from the archive.

     The top graph then shows those stretches placed side by side,
     with the time in between left out. A dashed line and a date
     mark where one day ends and the next begins. Nothing is drawn
     across that line, so a step there is a change of day, never a
     jump in the signal.
  2. The top graph shows the signal you are searching by. Click a row
     in the PV list to search by a different one. The tick box in
     front of each row says whether that signal is drawn in the
     graph at all, so a long list does not turn into a wall of
     curves.
  3. Switch "Select" on in the toolbar above the graph and drag over
     the graph to mark a region. Repeat as often as you like. A drag
     that runs over one of the dashed day lines is split there, into
     one region per day — two days are never averaged into a single
     spectrum by accident.
     Days sit right next to each other, so a drag meant for one day
     easily overshoots the line by a few pixels. Such a sliver of the
     neighbouring day is left out: it takes a fair share of the drag,
     or the whole of its own day, to be marked. Dragging over three
     days on purpose still gives you three regions.
     To take a whole day, do not aim at all — double-click anywhere
     inside it and the entire day is marked, edge to edge.
  4. Press "Analyze". Every region that has not been done yet is
     fetched and averaged in the background.
  5. Change what is shown — one of the averages or every spectrum —
     the normalisation, the colouring, the smoothing or the spread
     band whenever you like. Nothing is fetched again: every shot is
     already there and all the methods are worked out at the same
     time. The graph keeps the size you gave it while you do this,
     and what you picked is named in the graph title.
  6. "Export results" — one file with the numbers on top and the
     curves below. With "Every spectrum" on, that is one column per
     measured shot: its own wavelength-and-intensity data, every
     shot, even the ones the graph left out.


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

  All of these are in the purple "Display settings" block at the
  bottom of the panel.

  Show            Mean, Median, Trimmed mean, Sigma-clipped mean —
                  four ways of ignoring outliers to different
                  degrees — or "Every spectrum", which does not
                  average at all and draws every shot measured in
                  the region as its own curve. Mark a whole day and
                  you get every spectrum of that day on the graph.
                  Above three thousand curves in one region only
                  every n-th is drawn; the title and the legend say
                  how many of how many you are looking at. The
                  exported file always holds all of them.
  Every spectrum  appears only in that display, and NOT in this panel
   — pick one     but directly under the search graph: a bar lined up
                  with that graph's time axis, so the handle stands
                  exactly under the shot it is on, and a mark in the
                  graph shows the same place. It can only stop where a
                  shot was really taken. ◀ ▶ move one shot at a time;
                  beside them, which shot, when, and which selected
                  spectrum it came from.
  Colour by       the order you selected the regions in, or by the
                  measured dispersion, which adds a colour scale.
  Normalize       off, to the peak, or to the area — for comparing
                  shape rather than size.
  Variation band  a shaded band showing how much the spectra inside
                  a region differed from each other.
  Smooth          a moving average, with an adjustable width.
  From / To       the horizontal window, with an automatic fit. Its
                  heading follows what you are looking at: on a
                  spectrometer it says "Wavelength range [nm]", on the
                  SPIDER time domain "Time range [fs]". Ask for a
                  window the spectra do not reach and the graph names
                  both ranges instead of going blank.
  Compare         the difference or the ratio of two of the analyzed
   regions        spectra, as an extra curve.


PER REGION

  The box at the top of the panel says what the tab is doing: Idle,
  "Working..." while a day or an analysis is loading, or a blinking
  LIVE. "Stop" next to it stops whatever is running.

  Each region in the list can be hidden, expanded to show its
  numbers — energy, dispersion, peak wavelength, width, centre,
  area — and switched to show every individual spectrum behind the
  average rather than just the average.

  If you change the spectrum with "Change...", your marked regions
  stay where they are and are simply worked out again for the new
  channel. You do not have to delete them and mark them a second
  time.


SETTINGS AND WHERE THEY LIVE

  Your signal list, your presets, the chosen spectrum channel and the
  window layout are all remembered per user. Deleting that settings
  folder puts everything back to default.

-----------------------------------------------------------------
