Chiller Log — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19  (cpt.py, 3131 lines)
-----------------------------------------------------------------
Short version: ReadMe_Chiller Log.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. WHAT IT IS FOR
=================================================================

The six chillers are the cooling for the laser. Each one reports a
water flow and a water temperature, and both are archived
continuously by the control system. Continuously means far too often
to look at: hundreds of thousands of samples a month, per chiller.

That resolution is exactly what you want when something breaks in
the next five minutes, and exactly what you do not want when the
question is "has chiller 3 been getting worse since May?".

This program answers the second kind of question. It keeps one slow,
deliberately sparse record — three readings a day per chiller — going
back years, and draws it.

  It is NOT a live display. Nothing here updates by itself.
  It is NOT an alarm. Nothing warns you.
  It is the long-term trend, and nothing else.

For live values and alerts, see Diagnostic's PV Monitor. For a
short-window look at raw archived samples, see CSS Logger.


=================================================================
2. THE SIX CHILLERS
=================================================================

  1   Diode Array Chiller 1
  2   Diode Array Chiller 2
  3   Diode Array Chiller 3
  4   Diode Array Chiller 4
  5   Helium Chiller
  6   Utility Chiller

For each one, two quantities are logged:

  flow          in gallons per minute
  temperature   in degrees Celsius

A third value is read but never logged: whether the pump is running.
It decides which readings are kept — see the next section.


=================================================================
3. HOW A LOG ENTRY IS MADE
=================================================================

This is the heart of the program, and it is worth understanding,
because it explains every gap in the graph.

3.1 THREE FIXED TIMES A DAY

    09:00, 13:30 and 18:00, Prague time. Always those, every day.
    Fixed times mean two different days are directly comparable and
    a graph of a whole year has a manageable number of points
    (about a thousand per chiller per year).

3.2 AVERAGED OVER A MINUTE

    Each entry is the average of all the samples in the minute
    following that time, not one instantaneous reading. A single
    sample of a flow meter is noisy; a minute of them is a number.

3.3 ONLY WITH THE PUMP RUNNING — AND SETTLED

    A reading is kept only if the pump was running at that moment
    AND ten minutes before it.

    Both conditions matter, and the second one is the interesting
    one. A chiller that has just been switched on has water at the
    wrong temperature and a flow that has not stabilised. Logging
    that would put a spike into a multi-year trend that means
    nothing except "somebody turned it on at 08:55".

    So gaps in the log are not failures. A gap means the pump was
    off, or had only just come on. That is information, and it is
    why the graph is drawn in steps rather than as a smooth line
    through the gaps.

3.4 WHAT A ROW LOOKS LIKE

    One row per recorded moment, holding up to twelve numbers: flow
    and temperature for each of the six chillers. Where a chiller
    failed the pump test at that moment, its two cells are simply
    empty. A row where only the Utility chiller reported is normal —
    it means only the Utility chiller was running.


=================================================================
4. WHERE THE DATA COMES FROM
=================================================================

4.1 THE ARCHIVER

    Everything is read from the CPVA archiver over the network, over
    HTTPS. The program never talks to a chiller, and it cannot
    change anything — it only reads.

4.2 THE ONE-HOUR RULE

    The archiver only returns reliable results for a query window of
    an hour or less. So a request for six months is cut into
    one-hour pieces, and those are fetched several at a time (up to
    twelve in parallel) and stitched back together in order.

    This is why a big catch-up takes minutes: a year is nearly nine
    thousand pieces per value.

4.3 THE NIGHT SHORTCUT

    Pieces that fall entirely inside 22:00 to 06:00 Prague time are
    skipped without being asked for at all. Nothing is expected
    there — the recorded times are 09:00, 13:30 and 18:00 — so
    asking is pure waiting.

    The exception: any piece that reaches within a minute of the
    present moment is always fetched, whatever the hour, so a run
    late at night still brings the log up to date.

4.4 THE HISTORICAL SPREADSHEET

    Records from before this program existed live in a spreadsheet
    in the same folder, reaching back to November 2018. If the log
    file is missing, the program builds it from that spreadsheet on
    startup, and every row from it is marked as coming from there.

    So the log has two kinds of row, and the "source" column says
    which: "historical_xlsx" for the imported past, "cpva_update"
    for everything this program fetched itself.


=================================================================
5. THE GRAPH TAB
=================================================================

5.1 CHOOSING WHAT TO SEE

    Choose Variable    flow or temperature. One at a time — they
                       have different units and putting them on one
                       axis would make both unreadable.
    CH1 .. CH6         which chillers to draw. Each keeps its own
                       colour, so chiller 3 is the same colour
                       whether or not chiller 2 is shown.
    Grid, Legend       on or off.
    Font               the size of the text on the graph, in points.
                       Type a number and press Enter. Useful when
                       the graph is going into a report.

5.2 CHOOSING THE PERIOD

    All / 1Y / 6M / 1M   measured back from the newest row in the
                         log, not from today. A log that has not
                         been updated for two months shows the last
                         year of data it has, not a year ending
                         today with two empty months on the end.
    Display Range        your own start and end, either as absolute
                         dates from a calendar or as "the last N
                         hours / days".

    The From and To in use are always written next to the buttons.

5.3 ZOOMING

    Drag a rectangle on the graph to zoom into it. Press "Back" to
    step out again — the zoom levels are remembered as a stack, so
    you can go several levels deep and come back one at a time.

5.4 READING VALUES OFF IT

    Moving the mouse over the graph shows a crosshair snapped to the
    nearest real data point, with its value. Underneath the graph, a
    line of statistics per chiller, in that chiller's own colour.

5.5 SAVING IT

    "Save graph" writes the picture to a file, at the size and font
    it is currently drawn with. That is what the font control is
    for.

5.6 SMOOTHING

    Where a smoothed line is drawn, the raw data stays visible
    underneath it, thin and faint, and the smoothed trend is drawn
    solid over the top. You always see both — a smoothed line on its
    own hides how noisy the thing it came from was.

5.7 THE POINT LIMIT

    Above thirty thousand points on one line the data is thinned
    before drawing. Beyond that number the extra points are smaller
    than a pixel and cost only time.


=================================================================
6. THE DATA ARCHIVE TAB
=================================================================

The same rows as a table: the moment, where the row came from, and
the twelve value columns.

  hover a cell         see the full value
  right-click a cell   copy it, or copy the whole row
  double-click         open the value, when it is a path to a file
  Export CSV           write the shown rows out, after choosing
                       which columns to include

The right-click menu also offers "delete row", but it does nothing in
this view — a known defect, not a safety feature. If you need to
remove a row, edit chiller_archive.csv in a text editor; it is plain
text, and a copy of it beforehand is a complete backup.


=================================================================
7. THE LOG TAB
=================================================================

A running commentary on what the program is doing: which value it is
fetching, how many pieces it was cut into, how many samples came
back, how many log entries were produced from them, and how many
rows were appended.

When an update produces less than you expected, this is where the
reason is. The usual one is visible immediately: "PumpON samples =
0" means the pump state could not be read, so no reading passed the
pump test, so nothing was logged.


=================================================================
8. UPDATING THE LOG
=================================================================

Press "Update archive".

  1. It finds the newest moment already in the log and starts one
     second after it. If the log is empty it starts at the first of
     January 2026.
  2. It fetches up to the present moment.
  3. For each chiller it first reads the pump history — reaching a
     day further back than the requested start, so that the
     ten-minutes-earlier test can be answered for the very first
     recorded time in the window.
  4. It builds the candidate entries, drops the ones that fail the
     pump test, and appends what is left.
  5. A moment already present in the log is never written twice, so
     pressing the button again is harmless.
  6. When it finishes, the display switches to the last year.

The fetching runs in the background, so the window stays usable. The
status line at the bottom and the Log tab both report progress.


=================================================================
9. THE FILES NEXT TO THE PROGRAM
=================================================================

  chiller_archive.csv            the log itself. Plain text, one
                                 row per recorded moment. This is
                                 the valuable file — back it up.
  historical_chiller_data.xlsx   the imported past, from 2018
                                 onwards. Read only when the log
                                 file is missing.
  cpva_explorer_config.json      the remembered period and the
                                 network timeout.
  allowed_pvs.json               empty and unused; a leftover.
  icon.ico                       the window icon.

A QUIRK WORTH KNOWING: the settings file is only written if it
already exists. On an installation where it was never created, the
chosen period is not remembered between runs and every start opens
on the default. If you want it remembered, create an empty
cpva_explorer_config.json next to the program containing just {} and
it will start saving.


=================================================================
10. WHEN SOMETHING GOES WRONG
=================================================================

  "Archive CSV not found"
      The log file is missing and so is the historical spreadsheet,
      or the spreadsheet could not be read. Restore either one.

  "Archive is already up to date"
      The newest row in the log is later than the present moment.
      Normally this means there is genuinely nothing new. It can
      also mean the computer's clock is behind — this facility's
      office machines have been known to run tens of seconds off.

  An update adds no rows
      Look at the Log tab. Either the pump was off for the whole
      period, which is a real answer, or the pump state could not be
      read at all, which is a network or archiver problem.

  Only chiller 6 has values in recent rows
      Only the Utility chiller was running. Normal outside
      operation.

  An update is very slow
      A long catch-up is thousands of one-hour requests. The Log tab
      shows how many pieces each value was cut into. If it is a
      year's worth, it will take minutes; leave it.

  Nothing at all comes back
      The archiver is not reachable from this computer. Check that
      you are on the facility network.

  The graph is empty but the table has rows
      The chillers are all unticked, or the chosen variable has no
      values in this period while the other one does.

  "matplotlib not installed"
      The graph library is missing. On a built copy that means the
      shared support folder is missing or out of date.

-----------------------------------------------------------------
