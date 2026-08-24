CSS Logger — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19
  (main.py 6138 lines, cpva_core.py 749, sp_t.py 1729)
-----------------------------------------------------------------
Short version: Readme CSS logger.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
Spectra tab:   ../Spectra/ReadMe_Spectra_Full.txt
-----------------------------------------------------------------


=================================================================
1. WHAT IT IS FOR
=================================================================

Everything the control system measures is archived: energies,
temperatures, voltages, positions, states, and the paths of camera
images. That archive is the record of what the machine did, and
almost every question about yesterday is a question to it.

The archive itself can only be asked small questions. Ask it for a
month and it will not answer usefully. Ask it for one value at a time
and a question with twelve values in it takes twelve times as long.

This program is the interface that makes those questions practical.
It cuts a long period into pieces, asks for them in parallel, joins
the answers, lines up values that were recorded at slightly different
instants, and hands you one table: one row per moment, one column per
value. The graphs, the filters, the statistics and the export all
read that one table.

The window has two tabs. This document is about the CSS Logger tab.
The Spectra tab is a different tool with its own documentation, in
the Spectra folder.


=================================================================
2. WHERE THE DATA COMES FROM
=================================================================

  Archive        the CPVA archiver, over HTTPS on the facility
                 network. Read only; nothing can be changed from
                 here.
  Images         the camera image store on the network, opened when
                 you double-click an image path in the table.
  Local data     a folder of ramping records kept next to the
                 program. The PV Time Plot tab reads that and works
                 with no network at all.

THE ONE-HOUR RULE. The archiver answers reliably for a window of
about an hour. So every request is split into one-hour pieces, and
the pieces are fetched many at a time. A week of ten values is
seventeen hundred requests, and that is why it is done in parallel
rather than in sequence.

THE NIGHT SHORTCUT. Pieces falling in the small hours are skipped,
because nothing is recorded then. On a long window that is a third of
the requests saved for nothing lost.

THE CARRY-FORWARD VALUE. For every value, the last sample BEFORE your
window is also fetched. Without it, a signal that has not changed for
two days looks like it has no data at all — the archive only stores a
sample when something happens. With it, a flat line is drawn as a
flat line, which is what actually happened.

THINNING. For a long window the program can ask the archiver for a
reduced version of the data rather than every single sample. That is
what makes a month-long load possible at all.


=================================================================
3. LOADING DATA
=================================================================

3.1 THE TIME WINDOW

    "Set time window" gives each end of the period its own choice of
    an absolute date and time, or a relative quick pick — the last
    hour, the last 24 hours, now.

3.2 THE VALUE LIST

    "Browse..." opens the channel browser. The full list of archived
    channels — about ten thousand of them — is downloaded once and
    then filtered as you type, so typing is instant.

    HOW THE SEARCH WORKS. Type words separated by spaces. Every word
    has to appear in the name, in the order you typed it, so "023 l3"
    and "*023*l3*" find the same thing. Asterisk and question mark
    also work if you want to be explicit.

    "Remove" drops the selected entries, the bin clears the list.

3.3 PRESETS

    A named list of values. Load, Save, Save as new, delete. A
    recurring question — "the four chiller temperatures and the shot
    energy" — becomes two clicks instead of five searches.

3.4 FETCHING IS AUTOMATIC

    There is no "Load data" button. Anything that changes what ought
    to be on screen fetches the data itself, with a progress bar:

      - a value added through Browse, removed, renamed, or the whole
        list cleared
      - a different period
      - a different preset

    A burst of changes — removing four values one after another — is
    collected into a single request instead of four, and a request
    asked for while another is still running waits its turn rather
    than being dropped. The period starts as the last hour, so there
    is always one set.

    The reason there is no button: it could only ever do what the
    program already knows it has to do, and a button that must be
    pressed before the screen tells the truth is a button that will
    be forgotten.

3.5 LIVE MODE

    The one switch left. On, it keeps fetching — several times a
    second it asks for whatever is new, and the window rolls forward
    with the clock, so From and To read as "now minus the span, to
    now" rather than as fixed times. The countdown next to the button
    shows the next poll. Off, the period you chose stands still.

    Four things about live mode are worth knowing, because they are
    all deliberate:

      - The graph and the table are refreshed on SEPARATE clocks. The
        graph update is cheap; rebuilding the table means re-merging
        the whole accumulated history, which with a dozen values can
        take about a second on its own. They used to share one clock,
        so the slow one set the pace for both and the whole window
        looked about five seconds behind. Now the graph refreshes as
        fast as the computer manages and the table about once a
        second. Between graph refreshes the time axis still slides
        along, which costs almost nothing and is what makes the
        window glide rather than jump. All three intervals are under
        Graph settings → Live speed.

      - Each of the two also keeps at least twice its own measured
        cost free, so a slow machine backs off by itself instead of
        leaving the window no time to respond.

      - The live window is capped at twelve hours. The program
        remembers your last window, so a window that had grown to
        several days would silently become the live window on the
        next start — and a live window is re-merged, re-filtered and
        redrawn for the whole session. When the cap shortens your
        window, the Log says so. With Live off, any period you ask
        for is still loaded in full.

      - "Stop live" also cancels the requests already in flight. Just
        stopping the timer would leave a queue of archiver calls
        finishing in the background, so the program would keep
        working for a while after you told it to stop.

3.6 THINNING THE ROWS

    Master PV      the ramp master, used to recognise rows that are
                   really the same moment recorded twice.
    Keep multiples the row is kept only when the master value is a
    of             multiple of the number you give. Set 5 and you
                   keep every fifth step.

    On a dataset where the archive recorded ten times more often than
    the experiment changed, this is the difference between a readable
    graph and a solid block of ink.


=================================================================
4. THE GRAPH TAB
=================================================================

4.1 THE LAYOUT

    One plot, and every value gets its own horizontal band inside it
    plus its own vertical axis in a column on the left. This is the
    same arrangement as the control-room displays, which is the point
    — a graph that looks like the one people already read.

    A value with autoscale on uses the full height of the plot rather
    than its band.

4.2 THE MOUSE

    CROSSHAIR. Moving the mouse draws a crosshair with the time, and
    next to each curve a small box with that curve's value at that
    moment. Each box sits where the crosshair crosses its own curve;
    boxes only move out of the way when they would genuinely overlap
    each other, and only as far as they have to.

    The boxes can be switched off, and they switch themselves off
    above about twenty visible curves, where they would be a solid
    wall of numbers.

    SELECTION. Drag with the LEFT button to get statistics for that
    interval: one card per value with the number of samples, the
    average, the spread, the smallest, the largest and the peak to
    peak. The numbers can be selected and copied.

    The selected region is remembered as a moment in time, not as a
    picture. So it survives a redraw: change the font, edit the
    settings table, reload, let live mode redraw — the blue band and
    its numbers stay, and in live mode the numbers follow the newly
    arrived samples. Previously they lived only inside the drawing and
    the labels, so any redraw at all silently threw them away, which
    is exactly what happened whenever the period was changed.

    A region is only dropped when you say so ("Clear selection",
    "Clean graph") or when the new period does not overlap it at all.
    A period that covers only part of it keeps it and says so in the
    heading, so the numbers are never quietly taken for the whole
    selection.

    ZOOM. Drag with the RIGHT button to zoom in on that stretch of
    time. The magnifier button does the same thing with a box; while
    it is pressed in, the left button zooms instead of taking
    statistics.

4.3 THE PICTURE BUTTONS (the graph toolbar)

    The same toolbar the Spectra tab uses, so both behave alike:

    house       back to the whole period. This also hands the view
                back to the program, so live mode may scroll it again.
    arrows      the previous / next view. One shared history: a zoom
                made with the right button lands in it too, so there
                is never a second "back" that disagrees.
    cross       pan — drag the plot around.
    magnifier   zoom by drawing a box. Pressed in, it takes the left
                button over from the statistics selection; press it
                again and the selection comes back.
    sliders     how far the plot sits from the edges. What you set
                here is kept, otherwise the next redraw would put the
                stored margins straight back. The LEFT margin stays
                automatic: it is worked out from how many values have
                their own axis, and there is no room to spare there.
    disk        save the graph as a picture.

    matplotlib's own "Customize" is deliberately removed: it knows
    nothing about the stacked axes and would fight every setting made
    in Graph settings.

    Note on the icons: matplotlib tints them ONCE, when the toolbar is
    built, and only if it thinks the background is dark — on a dark
    Windows theme they come out white on white and read as blank
    buttons. So the toolbar is built inside a light-coloured holder
    first. Anything that rebuilds this toolbar must keep doing that.

4.3a THE OTHER BUTTONS

    Clean graph       empties the plot but keeps the frame.
    Clear selection   removes the blue region and its statistics.
    View              reset the view, type axis limits in by hand, or
                      switch every grid off at once. It is a button
                      and not a right-click menu because right-drag on
                      the graph is the zoom.

    Reference lines
        Horizontal lines at heights you choose — a limit, a target, a
        previous day's average.

        A line can be tied to one signal. That matters here: every
        signal has its own vertical scale squeezed into its own band,
        so a line that belongs to nobody sits at the height of the
        first signal and means nothing for any of the others. Tie it
        to a signal and it is drawn on that signal's scale, where the
        number you typed is the number you meant.

        Each line has a name, the signal it belongs to, the height, a
        colour (black to start with), a style and a thickness. Lines
        can be moved up and down the list with the arrows, or deleted
        with the red cross. The name is written next to the line in
        the graph, so a line is never anonymous.

        "Add by clicking in the graph" places a line by eye instead of
        by typing a number. It is two steps and the program says which
        one you are on:

          Step 1  Click the vertical axis of the signal you want — its
                  numbers down the left-hand side — or click its
                  curve. Everything else is greyed out.
          Step 2  Click in the graph at the height the line should
                  sit. Now the axis columns are greyed out instead.

        The line then appears back in the list, where it can be named
        or nudged. Esc, or the Cancel button in the blue strip, backs
        out at any point. While you are placing a line, dragging for
        statistics and dragging to zoom are switched off so a click
        cannot do two things at once; both come back afterwards.

    Conditions
        Minimum and maximum per value. A row survives only if every
        condition value is present AND inside its range; everything
        else is dropped from the table, the graph AND the export.

        Two deliberate details:
          - If the whole window fails the conditions, the table stays
            empty rather than quietly showing you unfiltered data.
            The Log says how many rows were dropped.
          - A condition on a value that has no data at all in this
            window is skipped, and logged. Otherwise one absent
            value would reject every row on its own and look like a
            broken filter.

        Computed values can be used as condition values.

    Add custom PV
        A new signal calculated from the loaded ones — see section 5.

    Graph settings
        Fonts. The spacing of the axis columns. The time stamps
        along the bottom: how many, or a fixed spacing from one
        second to one day, and whether they show seconds. The plot
        margins, as percentages of the picture. The cursor boxes.
        The live speeds (section 3.5). And a couple of switches that
        trade detail for speed — minor tick marks are off by default
        because with a dozen stacked axes they are the single most
        expensive thing in a redraw.

        One thing the bottom margin does on its own: it is a
        percentage of the picture, but the time stamps and the "Time
        (Prague)" title under them are a fixed size in points. Opening
        the statistics strip shortens the graph by well over a third,
        and that percentage then stopped being enough — the title was
        cut in half. The gap now grows as far as it must to keep the
        title readable and goes back to your setting when the room
        returns, so no space is wasted either way.

    Font / Avg to
        The font size, and how many points the curves are averaged
        down to before drawing. A million samples cannot be drawn as
        a million points; the question is only whether the reduction
        is done deliberately.

4.4 THE SIGNAL LIST BELOW THE GRAPH

    One row per value: show, the display name, the colour, the value
    under the cursor, the vertical minimum and maximum, autoscale,
    the line width, the line style, the point style, the point size,
    the transparency, smoothing and grid.

    Line style is solid, dashed, dotted, dash-dot or none. Point style
    is auto, none, or one of ● ○ ▲ ■ ✕ +, with its own size. "Auto" is
    what the program has always done: a small dot, but only while the
    markers switch in Graph settings is on and the curve is short
    enough for the dots to be readable. Choosing anything else settles
    it for that one signal and ignores the global switch.

    Switching the line off and the points off would leave nothing to
    look at, so in that case the line comes back as solid.

    GRID, one per signal. Tick it for as many signals as you like:
    each ticked signal gets its OWN horizontal grid, drawn in that
    signal's colour and with its own kind of line — solid, dashed,
    dotted, dash-dot, long dashes, and so on down a fixed list. The
    colour says which signal a grid belongs to and the line pattern
    says it a second time, which still works where two colours sit
    close together. The tooltip on the tick box names the pattern that
    signal has been given.

    The upright time lines are shared and drawn once, faintly, in
    grey: every signal shares one time axis, so there is nothing to
    tell apart.

    Why this needed fixing: the tick boxes were always independent,
    but at drawing time every one of them was collapsed into a single
    yes/no and one grid was drawn on the first signal's scale. So
    ticking the second, third or fourth box appeared to do nothing at
    all, and there was seemingly no way to have more than one grid.

    Grids are drawn underneath the curves. With the axes stacked the
    way they are, a later signal's grid would otherwise be painted
    across an earlier signal's line.

    No signal starts with its grid ticked, since each one now adds
    lines of its own.

    Six more columns are available but start switched off:

        Unit    the engineering unit the archive reports
        Last    the newest value in the loaded range
        Min     the smallest, over the whole loaded range
        Max     the largest
        Mean    the average
        Count   how many samples were recorded

    Count is the quickest way to find a signal that is not recording:
    it reads 0.

    THE COLUMNS THEMSELVES

    Drag a heading sideways to put that column where you want it.
    Right-click the headings for a list of every column with a tick
    next to the ones on screen. "Reset columns" — the button above the
    list, and the last entry in that menu — puts the order, the widths
    and the selection back to how they started.

    When there are more columns than fit, the list scrolls sideways
    rather than squeezing everything into nothing. The signal name and
    the empty spacer at the end cannot be hidden.

    SAVED LOOKS

    The "Styles" button keeps the whole appearance of the graph under
    a name: every signal's colour, line style, point style, size,
    transparency, range and smoothing, all the reference lines, and
    the column layout. Load brings it back, Delete removes it, and
    Export / Import write it to a file so it can be carried to another
    PC.

    Nothing is saved by itself. Close the program and the current
    colours, styles and reference lines are gone — only the looks you
    named survive, and only because you named them.

    Edits are collected and drawn once, when you stop clicking, so
    ticking six boxes costs one redraw rather than six.

    The list is exactly as tall as the number of values it holds, so
    two values do not leave a huge empty table above the graph. It
    grows only until the graph is down to its minimum height, and
    then scrolls.

4.5 THE POPOUT

    F11 puts the graph in its own resizable window; Ctrl+F11 makes it
    fullscreen. The same shortcut docks it back.

4.6 A SMALL BUT IMPORTANT DETAIL

    The mouse wheel does not change a number box or a dropdown just
    because the pointer happens to be over it. A field only reacts to
    the wheel once you have clicked into it; otherwise the scroll
    goes to the panel underneath, which is what you meant. Scrolling
    past a settings row used to silently change it.


=================================================================
5. COMPUTED VALUES
=================================================================

5.1 WHAT THEY ARE

    A new signal defined by a formula over the loaded ones. The
    values are referred to by letters: A, B, C and so on, in the
    order they appear in the list. So

        compressed beta = A * 0.749

    is a new column equal to the first value times 0.749.

5.2 THE THING TO UNDERSTAND ABOUT THE LETTERS

    The letters are positions in the list, and the list changes — you
    load another preset, you remove a value, you reorder them. If the
    formula only stored "A", its meaning would change every time the
    list did.

    So each formula also stores WHICH VALUE each letter stood for,
    and that is what is actually computed. The letter you SEE can
    therefore change between sessions while the formula keeps its
    meaning:

        "compressed beta = SBW4 * 0.749"
        shows as A*0.749 when SBW4 is first in the list
        shows as H*0.749 when SBW4 is eighth

    Same value, same result. Only the stored value names are
    permanent.

5.3 THE TWO TABLES IN THE DIALOG

    Available channels
        The automatic letter assignment for the list exactly as it is
        loaded now. Read-only — it follows the list.

    What each letter means
        One row per letter per formula, with the value behind it.
        Pick a different value there to re-point that letter, and the
        expression is rewritten to match, so it stays honest about
        what it reads.

5.4 A VALUE A FORMULA NEEDS BUT THAT IS NOT LOADED

    It appears in both tables, marked "not loaded". Its column stays
    empty and the Log says why. The binding is KEPT, not silently
    re-pointed at whatever now happens to hold that letter — which
    would be the one behaviour capable of producing a wrong number
    that looks right.


=================================================================
6. THE OTHER TABS
=================================================================

6.1 XY PLOT

    One loaded value against another, as a scatter. This is the tab
    for "does this depend on that", which a pair of time plots cannot
    answer. Pick the two from the lists at the top; the plot redraws
    as soon as either changes. It has the same picture buttons as the
    main graph, and dragging a rectangle with the right button zooms.

    PAIRING THE TWO UP. A point needs a value for both at the same
    moment, and the two are rarely recorded at the very same instant —
    demanding that they appear in the same merged row gave an almost
    empty plot, which is what made the tab look broken. Each value's
    last known reading is now carried forward until the other reports,
    the same thing the time graph does with its held lines. A reading
    only stands in for a limited time (a small share of the period, at
    least a minute and at most half an hour), so a value that has
    stopped reporting cannot go on inventing points for ever. The
    caption says how many points came out of how many rows, so a thin
    plot explains itself.

    THE COLOURS say how far through the period each point is, from
    dark blue through purple to red. Built by hand rather than taken
    from the ready-made ranges: those end in a pale yellow that is
    almost invisible against white, which hid the newest points —
    usually the interesting ones. Nothing in this range is pale. The
    dots also carry a hairline dark edge and shrink as the count
    grows, so a dense cloud can still be read as points rather than
    as one blob.

6.2 PV TIME PLOT

    The daily pattern, or the raw trace, of the ramping records kept
    locally next to the program. It needs no archiver and no network.
    Conditions can be added per row.

6.3 TABLE

    Every merged row. Only the newest few thousand rows are actually
    drawn, to keep the window responsive — the export, the graph and
    the scatter always use all of them.

    Right-click a row to copy it or to open its image. Double-click a
    cell containing an image path to open that frame.

6.4 LOG

    What the program did, and every problem it decided not to shout
    about: conditions that were skipped, formulas that could not be
    computed, the live window being shortened, rows dropped by a
    filter. When a result surprises you, read this first.


=================================================================
7. EXPORT
=================================================================

The export writes EVERY loaded row, not the few thousand the table
draws. One time column, then one column per value.

Semicolon between columns, point as the decimal separator, and the
separator announced on the first line so Excel opens the file
directly instead of showing an import wizard.


=================================================================
8. WHAT IS STORED NEXT TO THE PROGRAM
=================================================================

  the main settings file      window state, the last time range, the
                              conditions, the master value and the
                              graph settings
  the presets file            named value lists
  the conditions presets      named filter sets
  the computed values file    the formulas and their bindings
  the ramping folder          local records for the PV Time tab, with
                              an index file and the named setups

Deleting those resets the program to its defaults. Nothing about a
particular load is saved.


=================================================================
9. WHEN SOMETHING GOES WRONG
=================================================================

  A value loaded no data
      Either it genuinely has none in that window, or it has not
      changed for longer than the carry-forward search reaches back
      (about a month). The Log distinguishes the two.

  The table is empty after loading
      Almost always the Conditions. Read the Log — it says how many
      rows were dropped and why. That the table is left empty rather
      than unfiltered is deliberate.

  A computed value's column is empty
      A value the formula needs is not loaded, or the formula has a
      syntax error. Both are in the Log, and the dialog marks the
      missing value.

  Live mode shortened my window
      The twelve-hour cap. Section 3.5. Switch Live off and the longer
      period is loaded in full.

  Live mode is falling behind
      Too many values, or too long a window. Under Graph settings →
      Live speed, "Table refresh" is the one that costs: rebuilding
      the table walks the whole history. Raising it to two or three
      seconds leaves the graph moving as before. If that is not
      enough, shorten the list.

  The graph moves in jumps instead of gliding
      "Graph refresh" under Graph settings → Live speed is too high,
      or each refresh is genuinely taking that long — the program
      keeps at least twice the measured cost free, so it slows itself
      down on a machine that cannot keep up. Fewer values, or a
      smaller "Avg to", both help.

  My selected region and its statistics disappeared
      They should not, and no longer do — see section 4.2. They are
      only dropped deliberately, or when the new period does not
      overlap the region at all.

  A long load is very slow
      It is thousands of one-hour requests. Reduce the window, reduce
      the list, or let the thinning do its work.

  Nothing loads at all
      The archiver is not reachable from this computer. The PV Time
      Plot tab still works — it reads local files.

  The graph is a solid block of ink
      Too many samples for the width. Use "Avg to" to reduce the
      point count, or the master multiple filter to thin the rows.

  The time stamps along the bottom disappeared
      A fixed time-stamp spacing on a very long window asks for more
      stamps than the drawing library will produce. The program
      doubles the spacing until the count is sane, but if you have
      set something extreme, set it back to automatic.

  Scrolling changed a setting
      It should not any more — see section 4.6. If it still does,
      the field had keyboard focus from an earlier click.

-----------------------------------------------------------------
