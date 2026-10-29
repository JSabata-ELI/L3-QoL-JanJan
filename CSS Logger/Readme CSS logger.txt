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

  SETTING TABS. Next to them, after a thin line, sit the CSS Logger's
  own tabs — one per setting: e.g. the Operation preset in one, a
  hand-picked set of values in another, a preset with extra values in
  a third. Each has its own values, conditions, time window and Live.
  "+" opens a new one as a copy of the tab you are on; × closes one;
  double-click a tab to rename it; drag to reorder. A tab you are not
  looking at keeps itself up to date in the background (in Live it
  asks the archive every 10 seconds instead of constantly), and when
  you open it, it catches up to "now" at once. The tabs come back the
  next time you start the program.


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

  Set time window   two calendars, Start and End. Each end is either a
                    date and time, or a span before now. One row on
                    top — 30 min, 1 h, 2 h, 6 h, 12 h, 1 day, 3 days,
                    7 days — sets "that long up to now". It starts
                    on the last hour. A side set to a span before now
                    folds its calendar away, so the window stays
                    small. Turning the calendar to another
                    month moves the chosen day with it, and the blue
                    line at the bottom always spells out the two
                    moments OK will hand back. "Live", on the
                    Absolute End tab, sets End to now and Start to
                    1 hour back (change Start if you want more); OK
                    then starts Live mode. Under the button: the
                    period now in use. In Live mode that is
                    "now − 30 d" and so on — the span really on screen.
  Live mode         next to Time window. On, the window rolls along with
                    the clock and new values keep arriving. Off, the
                    period you chose stands still.
  Presets           named lists of values, so a routine question is
                    two clicks. "Blank" is always first and has no
                    values in it: pick it to start from an empty list.
                    It cannot be saved over or deleted; Save on it asks
                    for a new name.
  PV list           shows each value by its name (hover for the full PV).
                    Double-click or F2 renames it — the new name is used
                    on the graph, in the table and the export, and is
                    remembered. Right-click: Rename, Change PV, Copy PV
                    name, Paste PV names, Move up / down, Remove.
                    Drag a value up or down (or Ctrl+Up / Ctrl+Down) to
                    change the order — the graph follows. Ctrl+C copies
                    the selected PV names; Ctrl+V adds the names on the
                    clipboard (one per line, or separated by commas).
  + Add PV          find values to add. Type words; they must all
                    appear in the name, in any order.
  Custom PV         a new signal computed from the loaded ones with a
                    formula.
  Remove            drops the selected values; the one below moves up
                    and is picked, so Remove again takes the next one.
  Stop loading      appears while data is arriving. It keeps whatever
                    is already on screen and tells you what is missing.
  INFO              at the very bottom of the left side, always on
                    screen (it does not scroll away): what the
                    program is doing right now: loading, how much
                    arrived, Live polling, errors. A custom signal
                    that cannot be computed (one of its values is not
                    in the PV list) is named here in orange.

  Above INFO are the controls for the graph and the data, in groups:
  DATA FILTER (Conditions, Limit crossings), GRAPH (Reference lines,
  Graph settings, View ▾, Clean graph, font size, Detail) and SELECTION
  (Clear selection). They are described under "In the graph".

  There is no "Load data" button. Adding or removing a value, choosing
  a different period or switching preset fetches the data by itself,
  whether Live is on or off. Several changes in a row are collected
  into one request rather than one each.

  A long period is read from the present backwards, whole days at a
  time (several days together for a long period), and the graph is
  redrawn every few seconds as they come in — so the curves fill in
  from the right-hand edge instead of leaving you with an empty plot.
  The days not read yet are grey and say "loading"; INFO on the left
  says how far back it has got and about how long is left.

  Days that are over are kept on the scratch share after the first
  time anyone reads them, so the next load of the same period only
  asks the archive for today. Three months of eight values take about
  3 MB there.

  SBW4 is archived under two names that take turns (the HAPLS one and
  the L3 one). Each day is read under the HAPLS name first, and under
  the L3 name when the HAPLS name holds nothing that day; the curve
  shows whichever had the data.

  NOTHING IS AVERAGED AWAY. Over a long period each point on the curve
  stands for a stretch of time (about 10 minutes over a year). The
  light band around the curve runs from the lowest to the highest value
  really recorded in that stretch — the same as CS Studio's "Area"
  trace — so a single spike shows at its true height even on a year.
  Zoom in and the zoomed stretch is read again in finer detail; zoom to
  about an hour and you see every recorded sample. The cursor shows min
  and max next to the value wherever a point stands for a stretch.

  Live mode follows the clock, so it stays on only while the period
  ends at "now". Choose an end at a fixed moment — in the calendar, or
  through a preset that carries its own period — and Live switches off
  by itself, because you asked to look at a stretch of the past. The
  quick choices for the start (12 h, 1 Day, 3 Days, 7 Days) leave the
  end at "now", so those keep Live running. The Live button always
  overrides either way.

  RAMP / WAVEPLATE: the Waveplate PV marks the steps of a ramp. "Keep
  steps every" (1000 to start with, empty = every row) keeps only the
  rows where the waveplate stands on a whole step — graph, table, XY,
  statistics and export alike, applied at once.

  "Leave dead shots out of statistics" (on to start with): a shot
  whose energy (the channel named under it, SBW4 to start with) is
  below the limit while the waveplate has not moved since the shot
  before is a trigger shot or a dropout, and is left out of the
  selection statistics only. Untick it to count those shots too. Both
  channels must be in the PV list. Presets and setting tabs remember
  all of it.


THE TABS

  Graph          all the values in one plot, each with its own
                 horizontal band and its own vertical axis on the
                 left, in the style of the control-room displays.
  XY Plot        one value against another, as a scatter. A point is
                 one shot in which both values were recorded; a value
                 from an earlier shot is never used. Over a period
                 longer than one hour the points are 1-minute archive
                 averages, and the line under the plot warns about it —
                 load one hour or less to see every shot. The colour says how far through the period a point
                 is, running from dark blue to red. In Live mode the
                 cloud grows on its own, and Conditions thin it out
                 straight away — the same rows the Table and the Graph
                 use. Once you have zoomed in, the view stays where
                 you put it; press the house button to let it follow
                 the cloud again. "Clean XY" removes the points.
  PV Time Plot   the daily pattern of one value: one shape per day
                 showing how the shots were spread, read straight
                 from the archive for the days you pick. It redraws by
                 itself when you change the value, the days, the mode
                 or a condition. Its own "Conditions" button opens its
                 own condition rows in a window, working as in the
                 Conditions window (below); a row that is off and empty switches
                 on once you type a number into it. "Apply conditions"
                 switches them all off and on; "Copy from Graph
                 conditions" brings over the Conditions window's rows.
  Table          every row that was loaded.
  Log            what the program did, and any problems. "Clear log"
                 empties it.


IN THE GRAPH

  Move the mouse   a crosshair with the time and the value of every
                   visible signal. When the graph shows more than one
                   day, the time under the axis also gives the date.
  Drag with the    statistics for that interval: count, average,
  left button      spread (Std), smallest, largest, and the distance
                   from smallest to largest (P-P). The average, the
                   smallest and the largest are in the signal's own
                   units; the two that describe the scatter are given
                   as a percentage of the average, which is how a
                   shot is judged — 7 % says more than 0.07 does. The
                   blue band and its numbers stay put through a
                   redraw, a reload or a change of settings; "Clear
                   selection" removes them.
                   Drag out past the right-hand edge of the graph and
                   the selection becomes live: its start stays where
                   you put it, its end sits on the newest point and
                   moves with every new one, so the numbers always
                   include the latest value. The title above the
                   numbers then says "end follows the newest point".
                   A selection that ends inside the graph stays put.
                   The numbers count the real recorded shots, however
                   much the curve is thinned for the screen, so the
                   count does not wander as the graph refreshes.
                   When the period was loaded thinned (Detail above 0,
                   longer than an hour), every archived sample of the
                   selection is read first — the cards show "…" until
                   then — so the numbers are always exact.
                   The title gives the start and end, the length
                   (1 h 30 min 12 s) and the number of shots. A shot is
                   a moment when most of the signals running at the
                   shot rate wrote a value; hold the mouse over the
                   title to see the rate and which signals decide.
                   A signal's own N can be lower than the shot count:
                   that is the shots it did not record. A custom PV has
                   one value per shot.
                   An edge let go a few pixels from the first shot
                   after a pause (or the last one before it) lands
                   exactly on that shot. Start and End above the
                   numbers show the edges: type a time, or press -1 /
                   +1 to move an edge by one shot.
  Drag with the    zoom in on that stretch of time. (It does not start
  right button     a selection.)
  F11              the graph in its own window; Ctrl+F11 fullscreen.
                   Left-drag on the graph moves that window, the dark
                   frame round it resizes it — so left-drag statistics
                   do not work there; dock it back for them.

  Above the graph is a bar of picture buttons in three groups:

    house      back to the whole period
    arrows     the previous / next view you were looking at
    ---
    cross      pan — drag the plot around
    magnifier  zoom by drawing a box. While this is pressed in, the
               left button zooms instead of taking statistics; press
               it again to get statistics back.
    window     the loupe: a small magnified picture of the spot under
               the mouse follows it — only the value whose line is
               nearest the mouse, magnified in time and in value alike.
               Its name, the exact time and the value at the mouse are
               written above it. Use it to put a selection edge on the
               right second in a long graph — it keeps working while
               you drag. "10×" next to it sets how much it magnifies
               (5×, 10×, 20×, 50×).
    ---
    sliders    margins and spacing: how far the plot sits from the
               edges, the gap between a Y-axis title and its numbers,
               the gap between Y axes. Every change shows at once.
    disk       save the graph as a picture. "Save graph" saves the
               graph alone, without the blue band; "Save graph with
               selection" takes the selection with it and writes its
               numbers in a table under the graph. Both put a heading
               on top — the period shown, when it was saved, and the
               values in their colours — and offer a file name made
               of the period and the save time.

  The statistics under the graph are exactly as tall as their cards.
  "All" shows every card (two rows always whole), "One row" only the
  first row (scroll for the rest), "Hide" folds them away and keeps
  only the heading.

  The buttons below are on the left-hand side, above INFO.

  "Clean graph" empties the plot but keeps the frame. "View ▾" offers
  the same reset, "Clear selection", typing axis limits in by hand,
  and switching every grid off at once. In "Axis limits" the time range is
  a date and a clock (From / To). For the vertical range, pick the
  signal from the list first — every signal has its own axis — then
  type its Y min and Y max; the grey hint in an empty box is the
  signal's own data range. These are the same numbers as the Y min /
  Y max cells in the list under the graph. "Fit to data" empties them.

  Reference lines   horizontal lines at heights you choose. A line can
                    belong to one signal, so it sits on that signal's
                    own scale. "+ Add line" adds a row to type in.
                    "+ Add by clicking in the graph" (or ⊹ on a row)
                    walks you through it: click the signal's vertical
                    axis, then click the height; the line is added as
                    a new row. Everything you are not meant to
                    click is greyed out, and Esc backs out. Each line
                    has a name, colour, style and thickness, and can be
                    moved up and down the list or deleted. "Crossing"
                    says which side counts as over the limit: above,
                    below, or off.
  Limit crossings   every time a signal went over one of its reference
                    lines: start, end, how long, the peak and when it
                    happened, to the exact sample. Double-click a row
                    to see that moment in the graph.
  Conditions        keep only the rows where chosen values pass. Each
                    row: a tick to switch it on or off, the value, and
                    the kind — Between (min … max), Target ± %, Target
                    ± value, At least, At most, Outside (below min or
                    above max). Next to each row: how
                    many rows it keeps on its own. Changes apply at
                    once; there is no OK. Everything that fails is
                    dropped from the table, the graph, the XY plot and
                    the export. The
                    same button also sits on the XY and Table tabs and
                    does the same thing from everywhere.
                    Over an hour when the laser was off, every row can
                    fail — the table and the XY plot then say so, name
                    the conditions and count what was thrown away, so
                    an empty screen is never mistaken for lost data.
                    They hold in Live mode too, and for the band and
                    the Min / Max in the list under the graph.
                    Over a long period a point stands for a stretch of
                    shots: it is kept when any part of the stretch
                    (lowest to highest) passes, so a day with a few
                    good shots is not lost. Zoom in to see the shots
                    judged one by one.
                    A condition whose value was never recorded in the
                    period cannot judge it: it is left out, and the
                    graph says "Not applied (no data in this period)".
  Apply conditions  tick box under Conditions: off shows every shot
                    (trigger shots, everything outside the limits),
                    on filters again. The condition rows are kept.
  Restart after     the selection statistics count only from the first
  pause >           shot after the newest pause in firing longer than
                    the set seconds (15 s to start with). A live
                    selection moves its start there; a fixed one keeps
                    its band and its heading says where counting began.
  Graph settings    fonts, spacing, time stamps, margins, and a few
                    switches that trade detail for speed.

  Below the graph, one row per signal: show, name, colour, the value
  under the cursor, the vertical range, autoscale, line width, line
  style, point style, point size, transparency, smoothing and grid.
  It opens on about four rows and the graph keeps the rest of the
  height; drag the grey bar between them to see more rows, and the
  program will not move it back.
  Grid can be ticked for as many signals as you like: each one gets
  its own grid, in its own colour and its own kind of line — solid,
  dashed, dotted, dash-dot and so on — so several can be read apart
  at a glance. The upright time lines are shared, since every signal
  shares one time axis. Ticking Grid only switches the lines — the
  graph is not redrawn and the zoom stays.
  Six more are available and start switched off: unit, last value,
  smallest, largest, average and how many samples were recorded — the
  quickest way to spot a signal that writes nothing.

  Drag a row up or down to change the order of the signals: a blue
  line shows where it will land, and the graph puts its axes in the
  same order. Moving a value in the PV list on the left afterwards
  puts the graph back in the list's order. Original and custom PVs
  each stay in their own group.

  Drag a heading to move a column anywhere; a blue line shows where it
  will land. Right-click the headings to choose which are shown.
  "Reset columns" (left-hand side, PV TABLE) puts everything back.
  When the columns no longer fit, the list scrolls sideways.

  "Styles" (left-hand side, PV TABLE) saves the whole look — colours, line and point styles,
  reference lines and the column layout — under a name you choose, and
  loads it back later. It can also be written to a file and read on
  another PC. Nothing is kept automatically: start the program again
  and the graph is plain until you load a saved look. The line
  "Loaded style:" under the button names the look on screen.

  The window title shows the version you are running, e.g.
  "CSS Logger v1.4.2".


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


GOOD TO KNOW

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
  - INFO on the left says what was and was not read. If some
    of the period could not be fetched it names the signals and how
    much is missing, and the Log lists the exact stretches. "Nothing
    could be read" means the archive was not answering — not that it
    holds nothing for that period.
  - In Live mode the graph and the table are refreshed on separate
    clocks: the graph as often as the computer can manage, so the
    window glides along, and the table about once a second, because
    rebuilding it is the slow part. Both, and how often the archive
    is asked, can be changed under Graph settings → Live speed.
    Custom signals keep up with the graph, not with the table: their
    newest stretch is computed straight from the values they are made
    of on every graph refresh.

-----------------------------------------------------------------
