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

  There is no mode switch to set. The calendar decides: pick a day in
  the past and leave "Live mode" alone and you are reading the
  archive — load one or more days, mark regions, average them,
  compare and export.

  "Live mode" is the button in the top left corner. It is red while
  it is off and green while it runs. Switch it on and the tab loads
  the time window you picked in the calendar — its "From" time, moved
  onto today — and then keeps taking every new shot as it is
  measured. Switch it off and that stretch is loaded into the search
  graph, so you can mark regions in what you just watched.

  The calendar has its own "Live mode" tick, exactly like the Image
  Slider: set "From" to 07:00, tick it, press OK, and live mode starts
  on the whole day so far. "To" is ignored while live runs, because
  the window stays open.


READING THE ARCHIVE, STEP BY STEP

  The left panel is laid out in the order you work through it. At the
  very top are the four buttons you use all the time: "Live mode" and
  "Stop" on the first line, "Analyze" and "Export" on the second,
  with the progress bar under them. While live is running, the box
  "Average last N" sits beside "Export" on that same second line.

  Below that is one grey-blue block, "Loading data", holding the three
  numbered steps: the day and time, the signal you search on together
  with the list you pick it from, and the spectrum you measure. Card 2
  always names the signal the search is actually running on. Click the
  "Loading data" title to fold all three away once the data is in —
  the panel then has room for the list of spectra instead.

  Then comes the list of what you have marked
  ("Selected spectra"), whose heading carries "Expand all" and
  "Clear all" — the two buttons that act on the whole list.

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

     Card 3 is the spectrum itself. "Change..." picks the measured
     channel, and the "Unit" box beside it fills itself in from that
     channel's name — nanometres for a spectrometer, femtoseconds for
     the SPIDER time domain — so the graph is headed "Wavelength [nm]"
     or "Time [fs]" without you setting anything. It follows the
     channel: pick a different one and the unit changes with it.
     Type over it only if a channel name lies; what you type is then
     remembered for that channel alone and never carried onto
     another. Clearing the box hands it back to the automatic
     setting. If the numbers on the axis cannot be in the unit you
     typed — a wavelength that goes below zero — the line under the
     buttons says so, and the export says so too.
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
  4b. If you only want the shots taken at one particular setting, open
     the teal "Shot filter" block under the list, tick "Filter shots by
     PV value", and set for example GDD = 24700. Everything then uses
     only the shots that match — see SHOT FILTER below.
  5. Change what is shown — one of the averages or every spectrum —
     the normalisation, the colouring, the smoothing or the spread
     band whenever you like. Nothing is fetched again: every shot is
     already there and all the methods are worked out at the same
     time. The graph keeps the size you gave it while you do this,
     and what you picked is named in the graph title.
  6. "Export" — one file with the numbers on top and the
     curves below. With "Every spectrum" on, that is one column per
     measured shot: its own wavelength-and-intensity data, every
     shot, even the ones the graph left out.


LIVE MODE

  Press "Live mode" at the top left. The button turns green and stays
  green — it is the light that tells you shots are coming in. The tab
  reads the whole time window you picked in "Load day and time" — its
  "From" time, moved onto today — and then checks for new shots every
  three seconds. The graph is only redrawn when a shot actually
  arrives, so the window stays responsive.

  If you have not picked anything yet, it starts at the top of the
  current hour. Above two hours it asks first, because a whole day is
  fifteen thousand spectra and takes about a minute to read.

  "Average last N" appears beside "Export" while it runs — the one
  setting live mode has: how many of the most recent spectra to
  average. That same number decides how many single shots are drawn
  as the faint blue traces, so it is what is on the graph at all, not
  just the black average. The line under the buttons says how many
  are really being averaged.

  Press the button again and it turns red: streaming stops and the
  stretch you were watching is loaded into the search graph, so you
  can mark regions in it straight away. Opening the calendar without
  the "Live mode" tick also leaves live mode — whatever you pick
  there wins.


SHOT FILTER — "show me only the shots taken at GDD 24700"

  The teal "Shot filter" block sits under the list of selected
  spectra. It is folded away until you need it; its title always says
  what it is doing, so you can see that from the folded block.

  "Filter shots by PV value" is already ticked, with no condition set
  — an armed filter with nothing to match keeps every shot, so typing
  a value is the only step. Untick it and the whole block is off,
  conditions and all, and it stays off until you tick it again.

  Each line below is
  one condition: a tick box, the PV, the value, and a "±" for how far
  from that value still counts. Leave the ± at 0 and it means exactly
  this value. Click the PV name to choose a different one — any
  channel in the archive, not only the dispersion orders — and
  "+ Add condition..." adds another line. All the ticked lines have to
  hold at once, so "GDD 24700 and TOD -98000" is two lines.
  A line you untick stays where it is, set up and doing nothing, so
  you can switch a condition on and off without typing it again.

  What it changes: everything. The average is built only from the
  shots that match, and so are the numbers in each region's details,
  the peak and width readings, the counts in the legend, the bar under
  the search graph, and the exported file. The legend and the title
  say "37 of 892", so you always know how much you are looking at.
  The GDD and TOD shown per region become the value of the matching
  shots, not the average over the whole stretch of time.

  The value is taken at each shot's own moment: the last value the
  archive holds at or before it. That matters, because these settings
  are written once and then stand for hours — a ten-minute selection
  usually contains no entry of its own, and the value still comes out
  right.

  If nothing matches, the graph says so and names the filter and the
  counts — it never leaves you looking at an empty graph wondering
  whether anything was measured. A condition whose PV has no entry at
  all before your selection is named separately: that is a different
  problem from a value nobody ever set.

  In live mode the filter searches the whole window that was loaded,
  and "Average last N" then takes the last N of the shots that
  matched. So if the dispersion was stepped at noon, asking for the
  morning value still finds the morning shots — the filter reaches
  back through everything live has read, not only through the last N
  that happened to arrive.

  The line under the panel spells it out: "averaging last 200 of 3256
  matching (N=200), 15167 shots in window". The red "newest" curve is
  the newest shot that matches, and its label says how many newer ones
  were left out. Nothing is thrown away: switch the filter off and
  everything is back, unchanged.

  One thing to watch: live mode can only filter what it has loaded.
  If the value you are looking for was set this morning, pick 07:00 in
  the calendar before you start live — otherwise live begins at the
  top of the current hour and the morning is simply not there.


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
                  measured dispersion, which adds a colour scale. If
                  the shot filter has pinned that dispersion to one
                  value, every spectrum would come out the same colour,
                  so it falls back to the selection order and says so.
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

  The "Live mode" button at the top of the panel is the light that
  says whether shots are streaming: green while they are, red while
  they are not. "Stop" next to it stops whatever is running — live
  streaming, a day load or an analysis. The line under the buttons,
  and the progress bar with it, say what is going on meanwhile.

  Each region in the list can be hidden, expanded to show its
  numbers — energy, dispersion, peak wavelength, width, centre,
  area — and switched to show every individual spectrum behind the
  average rather than just the average. While the shot filter is on,
  the shot count there reads "37 of 892 (shot filter)", and the energy
  and dispersion lines say they are averages over the matching shots.

  If you change the spectrum with "Change...", your marked regions
  stay where they are and are simply worked out again for the new
  channel. You do not have to delete them and mark them a second
  time.


SETTINGS AND WHERE THEY LIVE

  Your signal list, your presets, the chosen spectrum channel, the
  shot-filter conditions and the window layout are all remembered per
  user. Deleting that settings folder puts everything back to default.

-----------------------------------------------------------------
