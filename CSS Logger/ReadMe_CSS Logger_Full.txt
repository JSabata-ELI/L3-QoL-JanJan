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
                 program, left over from an earlier way of working.
                 Nothing has been written into it since June 2026 and
                 no tab reads it any more.

HOW MUCH IT WILL ANSWER AT ONCE. The archiver refuses a request when
the answer would be too big, and how much is "too big" depends on the
signal: a valve position changes a handful of times a day, a shot
energy many times a second. So the program does not guess a fixed
size. It asks for a lot, and whatever comes back refused it halves
and asks again, remembering per signal how much that signal will
serve. A quiet signal ends up costing one request for a whole month;
a busy one is cut down to what it can give. The pieces are fetched
many at a time.

Two consequences worth knowing:

  - No period is too long. A year works. It may take a while, the
    line under the graph counts the requests, and "Stop loading" is
    there if you change your mind.
  - Nothing is skipped. Earlier versions asked one hour at a time and
    threw away every hour between 22:00 and 06:00 on the assumption
    that nothing is recorded overnight — without saying so, so a
    period over a night came back with holes in it. That is gone.

WHEN SOMETHING CANNOT BE READ. A signal that could only be read in
part still shows what there was, and the line under the graph names
it and says how many hours are missing; the Log lists the exact
stretches. If nothing at all came back, it says so in those words —
it never reports an unanswered archive as an empty one.

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

    THE BLUE LINE AT THE BOTTOM IS THE ANSWER. It spells out the two
    moments OK will hand back, and it is the one thing worth reading
    before pressing OK: each end is taken from the tab that is on
    top, so a date typed on the absolute side while the relative side
    is showing is not what you get.

    Turning the calendar to another month (◀ ▶, the month name, the
    year box) takes the chosen day with it — the same day of the
    month, or the last day of a shorter one. Before, the day stayed
    behind: February was on screen while the dialog still held today,
    and with the clock boxes untouched OK handed back the period you
    started from. A months-long request came back as the last hour.

    The end of the period is also what decides live mode. "Now" on
    the relative side means "keep up with the clock", and Live stays
    on. Any fixed end means "show me that stretch of the past", and
    Live switches off — see 3.5.

3.2 THE VALUE LIST

    "Browse" opens the channel browser. The full list of archived
    channels — about ten thousand of them — is downloaded once and
    then filtered as you type, so typing is instant.

    HOW THE SEARCH WORKS. Type words separated by spaces. Every word
    has to appear in the name, in the order you typed it, so "023 l3"
    and "*023*l3*" find the same thing. Asterisk and question mark
    also work if you want to be explicit.

    "Remove" drops the selected entries, the bin clears the list.
    Nothing is left selected afterwards: the entry that moves up into
    the freed place used to come out highlighted, so pressing Remove
    twice took a value you had not chosen.

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

    IT DRAWS WHILE IT LOADS. The period is read from the present
    backwards, and the graph and the table are redrawn every second
    or so with whatever has arrived. On a long period the picture
    fills in from the right-hand edge instead of leaving you looking
    at an empty plot and a progress bar. "Stop loading", beside the
    bar, keeps everything already drawn and tells you what is
    missing; it is there because a year is a fair thing to ask for
    and changing your mind halfway should not cost you the wait.

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

    Live mode follows the clock, so it belongs to a period that ends
    at "now". Choose an end at a fixed moment and it switches itself
    off: you asked to look at a stretch of the past, and holding Live
    on would have quietly turned your From and To into a WIDTH — ask
    for yesterday 08:00 to 12:00 and you would have got the last four
    hours instead. That is done for the calendar and for a preset
    that carries its own period. Setting only the START relatively
    (the 12 h / 1 Day / 3 Days / 7 Days buttons) leaves the end at
    "now", so those keep Live running. The Live button itself is
    always the override, in both directions.

    Five things about live mode are worth knowing, because they are
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

      - The live window is NOT capped. It used to be shortened to
        twelve hours, which meant the graph disagreed with the period
        it was labelled with. It now uses what you give it; above a
        day the Log points out that every refresh re-reads and
        redraws the whole span, so it will feel slower. Nothing is
        shortened without telling you.

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

        THE TIME STAMPS. Left on "Automatic", they show as much of
        the clock as the spacing warrants and no more: seconds only
        while the stamps are seconds apart, the clock alone once they
        are minutes apart, and the date alone once they are days
        apart. Over more than one day they come out on two lines,
        the date above the time, and the date is printed only where
        the day changes — so a two-day period is labelled by day
        without repeating the date under every stamp. Over a few
        months the year joins the date, because a year-long period
        would otherwise open and close on the same "09-02".

        Both ends of the period are always stamped, and an automatic
        stamp landing too close to one of them is dropped rather than
        written over it. The two outer stamps are also pulled inside
        the plot instead of being centred on its edge. Those two
        rules are what fixed the stamp on the right being written
        over its neighbour.
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

    THE QUESTION IT ANSWERS is "how did this value behave day by day",
    over days that need not be next to each other. It reads the
    archive itself, whole days at a time, and is independent of the
    period the Graph tab is showing.

    PICK DAYS opens the same calendar the rest of the suite uses: a
    click takes one day, Ctrl+click adds days one by one, and
    Ctrl+Shift+click takes a stretch. The tab opens on the last five
    working days. Weekends are not offered on purpose — nothing is
    archived there.

    Y VARIABLE is any channel in the PV list on the left, including a
    computed one. The channels do not have to be loaded in the Graph
    tab; the list is what the tab reads.

    DAILY DISTRIBUTION draws one shape per day — a violin, at its
    widest where most of the shots were, with the median marked in
    red. A day that ran at two different levels shows as two humps,
    which a daily average and an error bar hide completely. A day with
    fewer than two shots is left out and said so in the caption.

    RAW SHOTS draws every shot on a real time axis instead. Each day
    is its own run of points, so no line is dragged across the gap
    between two days that are not neighbours.

    ONE POINT PER MEASUREMENT of the chosen value, and no more: the
    conditions are read at those moments, holding their last value.
    Without that, a slowly-changing channel like the waveplate would
    add points of its own and the same number would be counted into
    the distribution several times over.

    CONDITIONS are target ± percent, one row each, and each row can be
    switched off without losing it. A row starts switched off, because
    a filter that appears already active would empty the plot for no
    stated reason. The caption always says which conditions were in
    force and how many shots of how many survived them.

    WHAT IT COSTS. A day of eight channels is about fifty requests and
    two or three seconds; a week is read while you watch, a month is
    worth starting and leaving alone. Every day that has been read
    stays in memory for as long as the program runs, per channel, so
    changing the Y value, adding a condition or retyping a target
    redraws at once. Only a day — or a channel — that has not been
    read yet is fetched, and pressing Plot again in the middle of a
    long read keeps everything already fetched.

    A CHANNEL THAT REPORTS RARELY (a waveplate, a valve) can have its
    last change hours before the day starts. Its value still holds, so
    the value from before the day is fetched and carried in, instead of
    leaving the morning's shots blank and having a condition drop them
    all.

6.3 TABLE

    Every merged row. Only the newest few thousand rows are actually
    drawn, to keep the window responsive — the export, the graph and
    the scatter always use all of them.

    The columns are the values that are on the GRAPH. Switch one off
    in the list under the graph and its column goes too, so the table
    can never disagree with the picture. A value with nothing in the
    period at all — no sample and no earlier value to carry in — is
    left out rather than shown as an empty column. A value that is a
    word (a beam fate) has no line on the graph but does have a
    column: a table of shots is where it belongs.

    A value that did not change inside the period is not blank. The
    last value it had BEFORE the period is carried across it, in every
    row, the same carry-forward the graph draws. Formulas are computed
    from those carried values too, so a computed value is no longer
    empty for a period in which its sources happened to stand still —
    which is what made every formula look broken on an old period.

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

"Export table", under the table, opens one window with two lists and
three switches.

7.1 THE TWO LISTS

    Channels to export
        The COLUMNS of the file. It opens with exactly the values that
        are on screen — the same rule as the table: what the graph
        shows, nothing switched off, nothing with an empty column.

    Channels that define a shot
        The ROWS of the file. This is the question that decides whether
        the file is a table of shots or a table of "something was
        written here".

        Only a value that is recorded once per shot can say when a shot
        happened. The diagnostic energies do that. A shot rate, a
        timing or state word, a beam fate, a valve or shutter position,
        the waveplate — these are written at their own pace, whenever
        the control system feels like it, and letting them mark shots
        multiplies the file: one measured window of eight minutes held
        sixteen real shots and two thousand two hundred rate samples.

        They are still exported. Every shot carries the last value each
        of them had at that moment, exactly like the carry-forward on
        the graph. They simply do not add rows.

        The program ticks the numeric diagnostics and leaves the rest
        unticked, going by the name (rate, timing, fate, state, status,
        mode, enable, open, close, shutter, valve, position, raw
        position, temperature, pressure, flow, alarm) and always
        leaving out the master value and the computed ones. It is a
        starting point, not a verdict — tick and untick freely.

7.2 THE SWITCHES

    Rows
        "Every shot in the window — reads the archive again" goes back
        to the archive for the whole period in FULL detail. This is
        what you want for data: the graph is thinned to a couple of
        thousand points per signal for drawing, and exporting that
        would be exporting the picture, not the measurement.
        "The rows already on screen" writes the merged rows as they
        are, instantly, with no reading at all.

    Keep only rows that pass the Conditions
        The same filter as on screen. Available for the re-read; the
        rows on screen have already been through it.

    Decimal comma
        Writes 1,5 instead of 1.5, for an Excel set to Czech.

7.3 WHAT IT WRITES, AND HOW LONG IT TAKES

One time column (to the millisecond, Prague time), then one column per
value, headed with the same name the value wears on the graph. A
semicolon between the columns and the separator announced on the first
line, so Excel opens the file directly instead of showing an import
wizard. A value that is a word — a beam fate — is written as the word,
quoted if it contains a semicolon itself.

Numbers are written with ten significant digits, so nothing is rounded
away, and 0.1+0.2 comes out as 0.3 rather than 0.30000000000000004.

A year is a legitimate period. The period is read a couple of hours at
a time and each piece is appended to the file straight away, so the
file can be far larger than the computer's memory. The window on top
says which day it is on and how many shots are written so far, and
Cancel stops it — everything already written stays, and the Log says
where it stopped.

While it runs you can keep working. It is reading the archive, though,
so if Live mode is on the two are competing; the Log says so.


=================================================================
8. WHAT IS STORED NEXT TO THE PROGRAM
=================================================================

  the main settings file      window state, the last time range, the
                              conditions, the master value and the
                              graph settings
  the presets file            named value lists
  the conditions presets      named filter sets
  the computed values file    the formulas and their bindings
  the ramping folder          old local records and the named setups.
                              No tab reads them any more; the PV Time
                              tab goes to the archive instead

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

      A condition can now also judge a value that has no sample inside
      the period, because the value carried in from before it is a
      real value. Before, such a condition was skipped and said so in
      the Log. So a condition on a value that was last left outside
      its range — a closed shutter, a laser that was off — empties the
      table, which is the honest answer.

  A computed value's column is empty
      A value the formula needs is not loaded, or the formula has a
      syntax error. Both are in the Log, and the dialog marks the
      missing value. It is no longer caused by the sources standing
      still: the last value from before the period is carried in and
      the formula is computed from that.

  A computed value has no column at all
      Then it cannot be computed anywhere in this period — one of its
      values is not in the list and has no earlier value either. The
      Log names it. A column that would be empty from top to bottom is
      left out on purpose.

  I asked for a long period and got the last hour
      Turning the calendar to another month used to leave the chosen
      DAY where it was, so February looked selected while the dialog
      still held today — and with the clock boxes untouched that is
      the period you started from. Fixed: the day follows the month
      you turn to. Whatever happens, the blue line at the bottom of
      the dialog spells out the two moments OK will hand back; if it
      does not say what you meant, OK will not either.

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
      The archiver is not reachable from this computer. Nothing will
      load, the PV Time Plot tab included — it reads the archive too.

  The PV Time Plot says "add a channel to the PV list first"
      Its Y drop-down is filled from the PV list on the left. An empty
      list, or a preset that carries no channels, leaves it with
      nothing to offer.

  The PV Time Plot came out empty with conditions on
      The caption says how many shots were read and that none passed.
      Read the target and the tolerance: they are a percentage of the
      target, so "70 ± 10 %" means 63 to 77, and a condition left
      pointing at the wrong channel will drop everything.

  The graph is a solid block of ink
      Too many samples for the width. Use "Avg to" to reduce the
      point count, or the master multiple filter to thin the rows.

  The time stamps along the bottom disappeared
      A fixed time-stamp spacing on a very long window asks for more
      stamps than the drawing library will produce. The program
      doubles the spacing until the count is sane, but if you have
      set something extreme, set it back to automatic.

  The time stamps are written over each other
      Set the spacing back to "Automatic" and lower "Max time
      stamps" in Graph settings. On automatic they should never
      collide, whatever the period — if they do, that is a fault
      worth reporting.

  A long period seems not to load
      Watch the line under the graph: it counts the requests and
      estimates the time left, and the picture fills in from the
      right as the data arrives. If it ends with a warning that some
      signals are incomplete, the Log lists the stretches that could
      not be read. "Nothing could be read" means the archive was not
      answering.

  Scrolling changed a setting
      It should not any more — see section 4.6. If it still does,
      the field had keyboard focus from an earlier click.

-----------------------------------------------------------------
