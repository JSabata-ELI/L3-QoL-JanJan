CSS Logger — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_CSS Logger_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Looks at anything the control system has archived. You pick the
  values you want, pick a period, press Load, and then you have them
  as a table, as graphs, as statistics, and as a file you can take
  away.

  The window has two tabs: CSS Logger and Spectra. Spectra is a
  separate tool for spectrometer data and has its own documentation.


THE IDEA IN ONE PARAGRAPH

  The archive holds every value the machine has ever reported, but it
  can only be asked small questions at a time. This program does the
  asking: it cuts your period into pieces, asks for them all at once,
  puts the answers back together, lines up values that were recorded
  at slightly different moments, and gives you one table with one row
  per moment and one column per value. Everything else — the graphs,
  the filters, the statistics, the export — reads that table.


LOADING DATA (the left-hand side)

  Set time window   both ends by date and time, or quick choices
                    like the last hour or the last 24 hours.
  Presets           named lists of values, so a routine question is
                    two clicks.
  Browse...         find values to add. Type words; they must all
                    appear in the name, in the order you typed them.
  LOAD DATA         fetch everything in the list for that period.
  Live              keep fetching, every third of a second, with the
                    window rolling along with the clock.

  Master PV and "Keep multiples of" thin the rows down where the
  archive recorded far more often than you need.


THE TABS

  Graph          all the values in one plot, each with its own
                 horizontal band and its own vertical axis on the
                 left, in the style of the control-room displays.
  XY Plot        one value against another, as a scatter.
  PV Time Plot   the daily pattern of the ramping data kept locally.
                 Works without the archive.
  Table          every row that was loaded.
  Log            what the program did, and any problems.


IN THE GRAPH

  Move the mouse   a crosshair with the time and the value of every
                   visible signal.
  Drag a span      statistics for that interval: count, average,
                   spread, smallest, largest, peak to peak.
  Drag a box       zoom. "Back" steps out again.
  F11              the graph in its own window; Ctrl+F11 fullscreen.

  Reference lines   horizontal lines at values you choose.
  Conditions        keep only the rows where chosen values are inside
                    a range. Everything outside is dropped from the
                    table, the graph and the export.
  Add custom PV     a new signal computed from the loaded ones with a
                    formula.
  Graph settings    fonts, spacing, time stamps, margins, and a few
                    switches that trade detail for speed.

  Below the graph, one row per signal: show, name, colour, the value
  under the cursor, the vertical range, autoscale, line width,
  smoothing and grid.


EXPORT

  The export writes every row that was loaded, not just what the
  table shows. One time column, then one column per value. It uses a
  semicolon between columns and announces that on the first line, so
  Excel opens it directly.


TWO THINGS TO KNOW

  - The table only draws the newest few thousand rows, to stay
    responsive. The graph, the scatter and the export always use
    everything.
  - Live mode keeps at most the last twelve hours, on purpose. A
    rolling window of several days has to be re-merged and redrawn
    continuously and grinds to a halt. Load Data will still load any
    period you ask for.

-----------------------------------------------------------------
