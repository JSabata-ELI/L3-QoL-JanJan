CSS Logger — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_CSS Logger_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Looks at anything the control system has archived. You pick the
  values you want and a period, and they arrive on their own — as a
  table, as graphs, as statistics, and as a file you can take away.
  There is nothing to press to fetch data.

  The window has two tabs: CSS Logger and Spectra. Spectra is a
  separate tool for spectrometer data and has its own documentation.


THE IDEA IN ONE PARAGRAPH

  The archive holds every value the machine has ever reported, but it
  will only answer so much at a time. This program does the asking:
  it works out how much the archive is willing to hand over in one
  go, cuts your period accordingly, asks for the pieces all at once,
  puts the answers back together, lines up values that were recorded
  at slightly different moments, and gives you one table with one row
  per moment and one column per value. Everything else — the graphs,
  the filters, the statistics, the export — reads that table.

  Any period works: an hour, a night, a year back. There is no limit
  and nothing is skipped.


LOADING DATA (the left-hand side)

  Set time window   both ends by date and time, or quick choices
                    like the last hour or the last 24 hours. It starts
                    on the last hour. Turning the calendar to another
                    month moves the chosen day with it, and the blue
                    line at the bottom always spells out the two
                    moments OK will hand back.
  Presets           named lists of values, so a routine question is
                    two clicks.
  Browse            find values to add. Type words; they must all
                    appear in the name, in the order you typed them.
  Live mode         On, the window rolls along with the clock and new
                    values keep arriving. Off, the period you chose
                    stands still.
  Stop loading      appears while data is arriving. It keeps whatever
                    is already on screen and tells you what is missing.

  There is no "Load data" button. Adding or removing a value, choosing
  a different period or switching preset fetches the data by itself,
  whether Live is on or off. Several changes in a row are collected
  into one request rather than one each.

  A long period is read from the present backwards, and the graph is
  drawn as it comes in — so the picture fills in from the right-hand
  edge instead of leaving you with an empty plot.

  Live mode follows the clock, so it stays on only while the period
  ends at "now". Choose an end at a fixed moment — in the calendar, or
  through a preset that carries its own period — and Live switches off
  by itself, because you asked to look at a stretch of the past. The
  quick choices for the start (12 h, 1 Day, 3 Days, 7 Days) leave the
  end at "now", so those keep Live running. The Live button always
  overrides either way.

  Master PV and "Keep multiples of" thin the rows down where the
  archive recorded far more often than you need.


THE TABS

  Graph          all the values in one plot, each with its own
                 horizontal band and its own vertical axis on the
                 left, in the style of the control-room displays.
  XY Plot        one value against another, as a scatter. The two are
                 rarely recorded at the very same instant, so each
                 one's last known value is held until the other
                 reports; a value that has gone stale stops counting.
                 The colour says how far through the period a point
                 is, running from dark blue to red.
  PV Time Plot   the daily pattern of one value: one shape per day
                 showing how the shots were spread, read straight
                 from the archive for the days you pick.
  Table          every row that was loaded.
  Log            what the program did, and any problems.


IN THE GRAPH

  Move the mouse   a crosshair with the time and the value of every
                   visible signal.
  Drag with the    statistics for that interval: count, average,
  left button      spread, smallest, largest, peak to peak. The blue
                   band and its numbers stay put through a redraw, a
                   reload or a change of settings; "Clear selection"
                   removes them.
  Drag with the    zoom in on that stretch of time.
  right button
  F11              the graph in its own window; Ctrl+F11 fullscreen.

  Above the graph is a row of picture buttons:

    house      back to the whole period
    arrows     the previous / next view you were looking at
    cross      pan — drag the plot around
    magnifier  zoom by drawing a box. While this is pressed in, the
               left button zooms instead of taking statistics; press
               it again to get statistics back.
    sliders    how far the plot sits from the edges
    disk       save the graph as a picture

  "View" offers the same reset, typing axis limits in by hand, and
  switching every grid off at once.

  Reference lines   horizontal lines at heights you choose. A line can
                    belong to one signal, so it sits on that signal's
                    own scale. "Add by clicking in the graph" walks you
                    through it: click the signal's vertical axis, then
                    click the height. Everything you are not meant to
                    click is greyed out, and Esc backs out. Each line
                    has a name, colour, style and thickness, and can be
                    moved up and down the list or deleted.
  Conditions        keep only the rows where chosen values are inside
                    a range. Everything outside is dropped from the
                    table, the graph and the export.
  Add custom PV     a new signal computed from the loaded ones with a
                    formula.
  Graph settings    fonts, spacing, time stamps, margins, and a few
                    switches that trade detail for speed.

  Below the graph, one row per signal: show, name, colour, the value
  under the cursor, the vertical range, autoscale, line width, line
  style, point style, point size, transparency, smoothing and grid.
  Grid can be ticked for as many signals as you like: each one gets
  its own grid, in its own colour and its own kind of line — solid,
  dashed, dotted, dash-dot and so on — so several can be read apart
  at a glance. The upright time lines are shared, since every signal
  shares one time axis.
  Six more are available and start switched off: unit, last value,
  smallest, largest, average and how many samples were recorded — the
  quickest way to spot a signal that writes nothing.

  Drag a heading to move a column anywhere. Right-click the headings
  to choose which are shown. "Reset columns" puts everything back.
  When the columns no longer fit, the list scrolls sideways.

  "Styles" saves the whole look — colours, line and point styles,
  reference lines and the column layout — under a name you choose, and
  loads it back later. It can also be written to a file and read on
  another PC. Nothing is kept automatically: start the program again
  and the graph is plain until you load a saved look.


EXPORT

  "Export table" asks two questions and then writes the file.

  Channels to export        the columns. It starts with exactly the
                            values on screen.
  Channels that define a
  shot                      the rows. Only values that are recorded
                            once per shot can say when a shot
                            happened. A shot rate, a timing or state
                            word, a beam fate, a valve position or the
                            waveplate are written whenever they feel
                            like it, so they must not make rows of
                            their own — they are exported all the
                            same, each shot carrying the last value
                            they had. The program ticks the diagnostic
                            energies for you and leaves the rest
                            unticked; change it as you like.
  Rows                      "Every shot in the window" reads the whole
                            period from the archive again, in full
                            detail, whatever the graph was thinned
                            down to. "The rows already on screen" is
                            the instant version.
  Conditions                keep only the rows that pass them.
  Decimal comma             for a Czech Excel.

  A year is a legitimate period: the file is written as the data
  arrives, a couple of hours of it at a time, so it never has to fit
  in memory. The window on top says where it is and can cancel; what
  is already written stays.

  One time column, then one column per value, under the same names as
  the graph. A semicolon between columns, announced on the first line,
  so Excel opens it directly. A value that is a word (a beam fate)
  goes out as the word.


TWO THINGS TO KNOW

  - The table only draws the newest few thousand rows, to stay
    responsive. The graph, the scatter and the export always use
    everything.
  - The table shows the values that are on the graph, and only those:
    switch one off under the graph and its column goes as well. A
    value with nothing at all in the period — not even an older value
    to carry forward — gets no column, instead of an empty one.
  - A value that did not change inside the period is not missing: the
    last value it had before the period is carried across it, in the
    table and in the export as well as on the graph. Formulas follow,
    so a computed signal is no longer blank for a period in which its
    sources happened to stand still.
  - Live mode uses whatever period you give it. A rolling window of
    several days has to be re-merged and redrawn on every refresh, so
    it will feel slower; the Log says so when you choose one. Nothing
    is shortened behind your back.
  - The line under the graph says what was and was not read. If some
    of the period could not be fetched it names the signals and how
    much is missing, and the Log lists the exact stretches. "Nothing
    could be read" means the archive was not answering — not that it
    holds nothing for that period.
  - In Live mode the graph and the table are refreshed on separate
    clocks: the graph as often as the computer can manage, so the
    window glides along, and the table about once a second, because
    rebuilding it is the slow part. Both, and how often the archive
    is asked, can be changed under Graph settings → Live speed.

-----------------------------------------------------------------
