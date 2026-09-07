Spectra — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-31  (sp_t.py, daypicker.py)
-----------------------------------------------------------------
Short version: ReadMe_Spectra.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. WHAT IT IS, AND WHERE IT LIVES
=================================================================

Spectra analyses spectrometer data from the archive or in real time.
By default that means the SPIDER spectrometer, but in practice any
archived waveform can be used.

It is not a program of its own. It is the second tab of CSS Logger,
which is where you will find it, and it ships inside that build. The
source file also runs on its own for development.

There is deliberately no icon and no build setup in this folder for
that reason.


=================================================================
2. THE PROBLEM IT SOLVES
=================================================================

One spectrum tells you very little: it is noisy, and it belongs to
one shot which may or may not have been representative. A whole day
of spectra is thousands of curves, which tells you nothing at all
because you cannot see any of them.

What is actually useful is: the average spectrum over the period when
the machine was in a particular state, next to the average spectrum
over a period when it was in a different state.

So the program is built around that idea:

  - The upper graph shows a signal you can navigate the day by — the
    shot energy, or a dispersion setting, or anything else archived.
  - You drag over the periods that interest you. Each drag is a
    "region".
  - Each region becomes one averaged spectrum in the lower graph,
    with its own colour, its own numbers, and its own row in the
    list.

Everything after that — averaging method, normalisation, smoothing,
comparison — is a way of looking at those averages.


=================================================================
3. ARCHIVE MODE
=================================================================

3.1 WHAT IT IS DOING, AND HOW TO STOP IT

    At the very top of the panel is a box saying what the tab is busy
    with:

      Idle           nothing running
      Working...     a day is loading, or an analysis is fetching.
                     The progress bar next to "Analyze" says how far.
      LIVE           blinking, while live streaming runs

    It used to track live mode only, so it read "idle" while an
    analysis was fetching half a day of spectra.

    "Stop" beside it stops whatever that is — the live stream, a day
    load, or an analysis in progress. It is greyed out when there is
    nothing to stop. Whatever had already been fetched is kept; only
    the work still outstanding is dropped.

3.2 LOADING A DAY AND A TIME WINDOW

    "Load day and time..." opens the calendar. It is the same calendar
    the Image Tools programs use — one widget, one set of rules, so it
    behaves exactly as it does in the Image Slider or Image Finder.

      plain click        pick one day, dropping everything else
      Ctrl+click         add or remove a single day
      Ctrl+Shift+click   take the run of days from your last click;
                         doing it again over the same run takes them
                         back off
      Mon..Sun boxes     which weekdays a Ctrl+Shift run may include
                         (weekends off by default). A plain click or a
                         Ctrl+click ignores these — a weekend can
                         always be picked by hand.

    Under the calendar, From and To set which hours of the day are
    loaded: 08:00 to 19:00 by default, or 08:00 to the next full hour
    when the day is today. Only those hours are read from the archive,
    so a day is no longer 24 hours of fetching.

    As soon as a second day is picked, a table appears below with one
    row per day, each with its own From and To. Changing the big
    From/To at the top moves every day that you have not typed into by
    hand; a row you did edit keeps what you gave it. So "Monday
    morning and Wednesday afternoon" is one selection, not two loads.
    Nothing takes effect until OK.

    The stretches you picked are called windows in the rest of this
    document: one window per day.

    Loading fetches the currently active search signal inside each
    window and draws it in the top graph. That happens in the
    background; the window stays usable and the status line says which
    PV and which window it is on.

    A word on size: the archive is read in one-hour pieces, per signal,
    so the number of requests is hours x signals. A fortnight of a
    dozen signals is well over a thousand of them, and Spectra asks
    before starting a load that big. It never refuses one — a cut-short
    load that looked complete would be worse than a slow one.

3.2.1 HOW SEVERAL DAYS ARE DRAWN

    The picked windows are placed side by side on the top graph and
    the time between them is left out of the axis altogether. Picking
    Monday 08:00-12:00 and Wednesday 14:00-19:00 gives nine hours of
    graph, not three days of which most is empty.

    This is a deliberate change: the tab used to take the first and the
    last day you clicked and load everything in between, so clicking
    Monday and Friday quietly loaded five days.

    What tells you where you are:

      - a dashed line at every join between two windows;
      - the date printed above each block;
      - the date on the first tick of each block, and plain clock
        times inside it;
      - the crosshair readout, which names the day as well as the time
        as soon as more than one day is loaded.

    Nothing is ever drawn across a join, so a step at the dashed line
    is a change of day and never a jump in the signal. A drag that
    crosses a join is split there into one region per day: two
    different days are never averaged into one spectrum by accident,
    and the status line says so when it happens. A day the drag only
    clipped by a few pixels is dropped rather than marked — see 3.5.

    The horizontal axis is therefore not real elapsed time, and typing
    numbers into it would be meaningless — "Axis limits" leaves X
    alone and says so. Zoom, pan and the crosshair all work normally.

    A region you selected earlier keeps its real date and time. If you
    then load days that do not contain it, it simply stops being drawn;
    it stays in the list and in the export, with its own dates.

3.3 THE THREE NUMBERED CARDS

    The whole left panel is arranged in the order you use it: three
    numbered cards, then the list of what you have marked, then the
    mode, then Analyze, then Clear all and Export — and the drawing
    settings gathered in one foldable purple block at the bottom
    (section 6).

    The top of the left panel is three numbered cards, read downwards
    in the order you actually work:

      1  DAY & TIME   which day (or days) is loaded, and for how many
                      hours. Hover it to see every window spelled out.
      2  SEARCH BY    the signal the search is running on, named in
                      full with its channel underneath, and right
                      below it the whole PV list it is chosen from,
                      with the presets and the channel search. Green.
      3  SPECTRUM     which channel you are measuring, and how its
                      wavelength axis is built. Blue.

    Card 2 is one card on purpose: the list is how you choose the
    signal, so the answer and the place you change it belong
    together. Clicking a row in the list immediately renames the
    headline at the top of the card, so there is never any doubt
    about which signal the search is using.

    Each row has a tick box. Ticked means the signal is drawn in the
    search graph; unticked means it stays in the list but is left off
    the graph, so a long list does not have to become a wall of
    curves. The headline counts the rest as "+N more plotted" and,
    if any are switched off, as "(N off)".

    Two rules keep the graph sensible. One signal always stays
    ticked, so the graph is never empty. And clicking a row that is
    switched off ticks it again, because searching by a curve you
    cannot see makes no sense. Which rows are ticked is remembered
    between sessions.

3.4 THE SEARCH SIGNAL

    The lower half of card 2 holds the list of signals you can
    navigate by, shown as a tick box, a label and the channel it
    comes from. Click a row to search by it. Double-click the label
    to rename it to something meaningful to you — the underlying
    channel is unchanged. Untick a row to take that curve off the
    graph while keeping it in the list.

    The Channel column always reaches the right-hand edge of the
    table. A channel name too long for the panel scrolls sideways
    rather than being cut short with an ellipsis, so you never read a
    truncated channel and think it is the whole name.

    Each signal keeps its own colour, and that colour belongs to the
    signal itself rather than to its position among the currently
    drawn curves. So a day where one of your signals has no data does
    not silently recolour all the others.

    ADDING A SIGNAL. Either the search field just above the table, or
    "+ Add PV..." for the full browser. The complete channel list is
    fetched once and then filtered locally, so typing is instant.

    HOW THE SEARCH WORKS. Type words, separated by spaces (commas,
    semicolons and asterisks work as separators too). Every word has
    to appear somewhere in the channel name — so "l3 sbw4" finds a
    channel containing both, wherever they are. Words found in the
    order you typed them rank above the same words scrambled, and
    camera channels are pushed to the bottom of the list because
    there are hundreds of them and they would otherwise bury what you
    were looking for.

    An empty search box shows nothing, deliberately — the alternative
    is a list of every channel in the facility.

    PRESETS. A named set of search signals. Pick one from the
    dropdown, or use the small buttons next to it to create, rename
    or delete one. "Edit..." opens the full editor, where you can see
    the preset list on one side and build its channel list on the
    other.

3.5 MARKING REGIONS

    Switch "Select" on in the toolbar above the top graph, then drag
    horizontally across the graph. The dragged stretch becomes a
    region, drawn as a shaded band, and appears in the region list.

    A WHOLE DAY: double-click anywhere inside it. The entire loaded
    day is marked, from its first minute to its last, with no aiming.
    Double-clicking a day that is already marked that way changes
    nothing and says so.

    OVERSHOOTING INTO THE NEXT DAY. The days lie flush against one
    another, separated only by the dashed line, so a drag meant for
    one day usually crosses it by a few pixels — and zoomed out over
    a fortnight a few pixels are minutes of archive time. Those
    minutes of the neighbouring day used to become a region of their
    own, which then had to be deleted by hand. Now a day has to earn
    its place in a drag: it is kept if it holds at least a fifth of
    the drag's longest day, or if the drag covers practically the
    whole of that day's own loaded hours. Everything else is an
    overshoot and is left out, and the status line names the day it
    ignored.

    So a rough drag over one block gives one region, while a drag
    made deliberately across three blocks still gives three. The
    second rule is what protects a day loaded with a short window:
    half an hour of Monday next to eleven hours of Wednesday is a
    tiny share of the drag, but selecting all of it is clearly not an
    accident.

    "Select" and the pan/zoom tools are mutually exclusive: turning
    on Pan or Zoom switches Select off, because otherwise a drag
    means two things at once. The double-click follows the same rule,
    so it does nothing while Pan or Zoom has the mouse.

3.6 ANALYZING

    "Analyze" fetches and averages every region that has not been
    done yet — regions already analysed are left alone, so adding one
    more region to six existing ones costs one region's worth of
    work.

    It runs straight away, in the background, with a progress bar.
    There is no confirmation dialog.

    For each region it collects: the spectra themselves, the shot
    energy, and the three dispersion orders. Then it computes EVERY
    averaging method at once, plus the standard deviation and the
    10th and 90th percentile curves.

    That is why switching the averaging method afterwards is
    instant and never refetches. It also means the four methods are
    directly comparable on the same data, which is the point of
    having them.

3.7 THE FOUR AVERAGING METHODS

    Mean                  the plain average. Sensitive to one bad
                          shot.
    Median                the middle value at each wavelength.
                          Ignores outliers entirely, at the cost of
                          being slightly blunt.
    Trimmed mean          throws away the extreme tenth at each end,
                          then averages the rest. A compromise.
    Sigma-clipped mean    throws away anything more than three
                          standard deviations from the average, then
                          averages the rest. Removes genuine
                          outliers while keeping the bulk intact.

    If the spectra in a region have different lengths — which happens
    when the spectrometer is reconfigured mid-day — only the most
    common length is kept, and the count in the region row tells you
    how many spectra actually went in.

    A WARNING ABOUT EXPECTATIONS. On clean spectra the four methods
    land within a fraction of a percent of each other — measured, one
    or two parts in a thousand of the peak. Switching between them
    then looks as though nothing happened, and that is the honest
    answer: nothing much did. They only pull apart once there are bad
    shots in the region, where the difference reaches tens of percent.
    So that the setting is never silently ignored, the active method
    is named in the graph title, in every legend entry, and above each
    region's numbers.

3.8 EVERY SPECTRUM — NO AVERAGING AT ALL

    The last entry in the "Show" box is "Every spectrum". It is not a
    fifth way of averaging: it switches the averaging off. Each shot
    measured inside a region is drawn as its own curve, in that
    region's colour, so a region holding four hundred shots puts four
    hundred curves on the graph. Mark a whole day and select all of
    it, and you are looking at every spectrum that day produced.

    Nothing extra is fetched for this. "Analyze" already brings back
    every individual spectrum — it is the same data the averages are
    computed from — so switching between an average and every
    spectrum is instant either way.

    WHAT CHANGES WHILE IT IS ON.

      - The variation band is greyed out. It describes the spread
        around an average, and there is no average on screen to
        describe.
      - Normalisation applies to each spectrum separately, so
        "Peak" stacks every shot at 1 and shows you the shape
        differences alone.
      - The numbers under each region — peak, centre, width, area —
        still come from the plain mean, and say so, because a single
        number cannot describe several thousand curves.
      - The comparison curve and the auto-fit of the wavelength
        range also fall back to the plain mean.
      - The CSV export changes: one column per measured shot, each
        named by the time it was taken, holding that shot's own
        intensity against the wavelength column, instead of the
        average and its standard deviation.
      - A bar appears under the search graph: "Every spectrum —
        pick one" (see 3.9).

    THE LIMIT. At most three thousand curves are drawn per region.
    Above that, every n-th spectrum is taken, spread evenly over the
    whole region, and both numbers are put in the graph title and in
    the legend — "3000 of 9007 drawn" — because a quietly thinned
    graph reads as if that was all there was. Three thousand curves
    take a couple of seconds to draw, and the mouse pointer shows the
    wait while it happens.

    The limit is about drawing only. The exported file always holds
    every shot, thinned graph or not — see section 11.

3.9 PICKING ONE SHOT OUT OF THE BUNDLE

    A thousand faint curves tell you how much the machine wandered,
    but not which shot is which. A bar appears DIRECTLY UNDER THE
    SEARCH GRAPH — not in the panel on the left — while this display
    is on, and answers that:

      the bar                        drag it, or click anywhere on it,
                                     to move through the shots.
      ◀  ▶                           step exactly one shot. So does
                                     the mouse wheel, and the arrow
                                     keys, once you have clicked the
                                     bar.
      Highlight                      draw the picked shot bold on top
                                     of the bundle, and mark it in the
                                     search graph. On by default;
                                     uncheck it to see the bundle
                                     alone.
      the line beside them           "431 of 892 · 2026-02-25
                                     07:49:10 · Spectrum 2" — which
                                     shot, when it was measured, and
                                     which selected spectrum it came
                                     out of.

    IT IS LINED UP WITH THE GRAPH ABOVE IT. The bar covers exactly
    the same stretch of time as the graph, edge for edge: the same
    place on the graph is the same place on the bar. The handle stands
    under the shot it is on, and a line with the time on it marks the
    same place in the graph itself. Zoom the graph in and the bar
    follows, so it still lines up.

    IT CAN ONLY STAND ON A SHOT THAT EXISTS. There is nothing to look
    at in the time between your selections, so the handle does not go
    there: dragging into an empty stretch stops on the last shot
    before it, and coming from the other side stops on the first shot
    after it. Which means the shaded selections in the graph above are
    also the map of where the bar can go.

    If you have zoomed the graph in and step past its edge with ◀ ▶,
    the graph slides over — same zoom, the shot in the middle — rather
    than leaving the handle stuck against the end.

    THE ORDER IS TIME, not the order you selected things in. The bar
    runs through every drawn shot of every visible spectrum, earliest
    on the left, latest on the right — so it is the same order as the
    search graph you dragged over, and with several days loaded it
    runs straight through them, dates and all.

    IT GOES AWAY WITH THE SEARCH GRAPH. Untick "Show search graph",
    or switch to Live, and the bar goes too — it lives inside that
    graph's panel. That is on purpose: a bar whose whole job is to
    point at a place on that graph has nothing to say without it.

    THE BOLD CURVE IS BLACK, on a thin white outline. Not the
    spectrum's own colour: three thousand curves of one colour make a
    solid band, and a bold line of that same colour inside it reads as
    a white gap. Black is a colour the bundle never has. Which
    spectrum the shot came from is on the coloured border of the tag
    in the top-left corner of the graph — that tag is also why this
    still tells you what you are looking at in full-screen mode
    (F11), where only the spectra graph is on screen.

    THE SAME TREATMENT AS THE BUNDLE. The bold curve is masked to the
    wavelength range, smoothed and normalised exactly like the curves
    behind it, so it sits on the shot it is naming and not next to it.

    IT DOES NOT REDRAW EITHER GRAPH. Moving the bar repaints the one
    curve, and the one marker, on top of a stored picture of the rest
    — measured at about 20 ms a move with three thousand curves of
    2048 points behind it, where redrawing the whole graph takes over
    four seconds. So the bar can be dragged.

    WHAT KEEPS YOUR PLACE. Hiding a spectrum with its eye, changing
    Normalize, re-analyzing — all of them renumber the list. You are
    kept on the shot you were on, not on the number it used to have.
    If that shot is gone, the bar falls back to the nearest position.


=================================================================
4. LIVE MODE
=================================================================

Switch to "Live" in the Mode box, set "Average last N", and press
"Start Live". The mode you are in is the button filled in blue; the
other one is grey. (It used to be neither: the program's own button
styling overrode the "pressed in" look, so both buttons looked exactly
the same and nothing said which mode was on.)

  - The last ten minutes are loaded first, so you are not looking at
    an empty graph while it fills.
  - After that it asks for new shots every three seconds.
  - The graph is redrawn only when a shot has actually arrived. Most
    polls bring nothing, and redrawing anyway would make the window
    stutter for no reason. The status line still updates every time.
  - Up to two thousand spectra are kept in memory, oldest discarded.
  - "Stop Live" freezes the display; what is on screen stays there.
  - With "Show" set to "Every spectrum" the black average curve is
    left off: the newest shot and the buffered ones are all there is.

HOW MANY ARE REALLY AVERAGED. "Average last N" is a ceiling, not a
promise. The average uses the newest N shots in the buffer, or all of
them when there are fewer — and just after starting there are usually
far fewer, because the preload only reaches back ten minutes. The
status line spells it out: "averaging last 37 of 37 buffered (N=100)",
with "filling up" while it is still short.

Two things are dropped quietly, so the status line owns up to them.
Spectra whose length differs from the majority cannot be averaged
together and are counted as "skipped (different length)". And while
every buffered shot goes into the average, at most four hundred of
them are drawn as the faint individual traces — above that every n-th
one is drawn, otherwise the redraw would crawl.


=================================================================
5. THE SPECTRUM CHANNEL AND THE WAVELENGTH AXIS
=================================================================

A spectrum needs two things: the intensity values, and the
wavelengths they belong to. The archive does not always provide both.

"Change..." in card 2 picks the intensity channel. Then:

  - A channel whose name ends in _X or _Y is one half of a pair. The
    wavelength axis is taken from the matching _X channel
    automatically, and nothing more is asked.

  - Any other waveform usually has no _X twin. The program then asks
    how to build the wavelength axis:

      copy it from another channel
      copy it from another channel and stretch it, as a straight-line
        transform of the form  new = a x old + b
      load it from a CSV or text file
      use the plain sample number, 0, 1, 2, ... — honest about the
        fact that there is no wavelength information

Whatever is resolved is always shown under the card as the X and Y
pair actually in use, so you can never be looking at a spectrum
without knowing where its horizontal axis came from. The choice is
remembered.

A partly broken wavelength channel — one with gaps or nonsense in it
— is repaired by fitting the good part and extending it, rather than
being rejected outright.

WHEN THE ARCHIVE KEPT ONLY PART OF THE AXIS. Some axis channels are
stored shorter than the spectra they belong to. The SPIDER time axis
is the case that matters: the archive keeps 2048 points of it while
each measured trace has 4096. If the stored part rises in even steps,
the rest is continued at the same spacing, so the whole trace gets a
real axis; the status line, the tooltip on the channel box and the
exported file all say that this happened. If the stored part is not
evenly spaced — a real spectrometer's wavelength axis is not — nothing
is invented and the graph falls back to sample numbers.

THE UNIT OF THE AXIS. The box marked "Unit" under the channel says
what the horizontal axis is measured in. It is filled in from the
channel name — femtoseconds for the SPIDER time domain, nanometres
for a spectrometer — and you can type over it. It sets the axis
title, the value under the mouse, the Peak and FWHM lines in the
region details and the column names in the exported file, so a pulse
length is never reported in nanometres.

CHANGING IT RE-RUNS WHAT YOU HAD. A region's averaged curves belong to
one channel and one wavelength axis. Change either, and the numbers
you are looking at are about the old channel. So changing the spectrum
now keeps every marked region exactly where it is — same times, same
colours, same names — throws away the stale results, and works them
out again for the new channel by itself. You no longer have to delete
the regions and mark them a second time, and the graph can no longer
show two different channels' curves side by side as though they
belonged together.

If an analysis is still fetching when you change the channel, it is
abandoned and its results discarded rather than being allowed to land
on top of the new ones.


=================================================================
6. THE DISPLAY OPTIONS
=================================================================

WHERE THEY ARE. All of them live in one purple block called "Display
settings" at the very bottom of the left panel, under the buttons —
the graph options, the horizontal window and the comparison curve
together. That is deliberate: none of them fetches anything or changes
what was measured, they only change how it is drawn, so they are kept
out of the way of the steps that do. Clicking the purple title folds
the whole block away; clicking it again brings it back. The colouring
is the same idea as the coloured sections in the Image Slider, so the
two panels read alike.

The panel above it runs in the order you use it: the three numbered
cards, then "Selected spectra" with everything you have marked, then
Archive / Live, then "Analyze" with its progress bar, and finally
"Clear all" and "Export results" side by side.

  Show                the four averaging methods from section 3.7,
                      or "Every spectrum" — no averaging, every
                      measured shot drawn on its own (section 3.8).

  Colour by           Selection order    a fixed palette, in the
                                         order you marked the
                                         regions
                      GDD / TOD          a rainbow keyed to the
                                         measured dispersion, with a
                                         colour scale drawn beside
                                         the graph. Use this when the
                                         regions are a scan of one
                                         parameter — the colour then
                                         means something.

  Normalize           None    the real intensities
                      Peak    every curve scaled so its maximum is 1
                      Area    every curve scaled to the same area

                      Peak and Area are for comparing SHAPE. If you
                      care about how much light there was, leave it
                      off.

  Variation band      a shaded band around each average showing the
                      spread of the spectra inside that region:
                      either plus and minus one standard deviation,
                      or the 10th to 90th percentile band. This is
                      the honest part of the picture — an average
                      with a wide band is not the same statement as
                      an average with a narrow one.

  Smooth              a moving average with an adjustable width. Only
                      the drawing is smoothed; the numbers in the
                      region details are computed from the unsmoothed
                      curve.

  Show search graph   hides the top graph to give the spectra the
                      whole window.

  From / To           the horizontal window, plus "Auto-fit range to
                      data on Analyze" which sets it from what was
                      actually measured. Set a window the spectra do
                      not reach and the graph says so — it names the
                      range you asked for and the range the spectra
                      cover — instead of going blank.

                      Its heading follows the unit of the axis you are
                      looking at, taken from the "Unit" box in card 3:
                      "Wavelength range [nm]" on a spectrometer,
                      "Time range [fs]" on the SPIDER time domain. A
                      pulse duration labelled in nanometres would be
                      worse than no label at all.

  Compare regions     picks two analysed regions and draws their
                      difference or their ratio as an extra curve
                      (section 9).

ONE AXIS FOR EVERYTHING ON THE GRAPH. Every curve, the metrics, the
auto-fitted From/To and the exported file are drawn against the same
axis: the measured one when it fits the waveform (rebuilt from its own
spacing when the archiver stored only part of it, as on SPIDER), the
plain sample number when it does not. This was not always true. With
"Show" set to "Every spectrum" the bundle of individual spectra used a
simpler rule of its own and fell back to sample numbers while
everything else was on femtoseconds: the bundle piled up around
"2000" (its own array position), the picked spectrum was drawn at
0 fs where it really belongs, and the auto-fitted range, being in
femtoseconds too, then cut the bundle down to a slice of its own
baseline — which is why the intensity axis could stop at 0.05 while
the spectra peaked at 1.0.

WHAT MOVES THE INTENSITY AXIS. By itself the graph fits it to the
curves it is showing, so a new From/To window rescales it. Zoom or pan
it by hand (or type limits in the right-click menu) and that is kept
through every later redraw — until Home or "Reset view" hands the
graph back to the data.

THE GRAPH KEEPS ITS SIZE. None of the choices above changes how big
the graph is. That used to be untrue and it was the single worst thing
about the tab: the colour scale for GDD/TOD was built by taking a
slice out of the plot's current width, and putting it away again never
gave the width back. Measured, thirty clicks on "Colour by" shrank the
plot from 93 percent of the frame to nothing at all, leaving a blank
right-hand side — and because every display control redraws through
the same path, "Smooth" and "Normalize" nibbled at it too. The colour
scale now has a slot of its own that is simply shown or hidden, and
the room it needs is a fixed figure rather than a fraction of whatever
is left.

The divider between the two graphs is also left alone. Switching
Archive/Live or hiding the search graph used to reset it to a built-in
ratio; now it comes back where you dragged it, and that position is
remembered between sessions.


=================================================================
7. FOCUS MODE (F11)
=================================================================

F11 shows the spectra graph on its own. There is no window frame and
no title bar — deliberately, the same as the Image Slider's focus
mode. The first time it fills the screen it is on.

  - Esc or F11 again hands the graph back to the main window.
  - It does NOT require Live mode. In Archive mode you get the
    averaged spectra; in Live mode the graph carries on refreshing,
    because focus mode moves the existing graph rather than building
    a second one.
  - Drag anywhere on the graph to move the window. Drag its outer
    edge to resize it. The graph toolbar is hidden while you are in
    there, which is what frees a plain drag for moving.
  - Where you left the window is remembered, per monitor. A saved
    position on a monitor that is no longer connected is ignored
    rather than putting the window somewhere you cannot reach.
  - The main window goes to the taskbar while you are in focus mode.
    Restoring it from there also leaves focus mode.

The same key does the same thing on the CSS Logger tab, for that
tab's graph. Whichever tab is in front gets the key.


=================================================================
8. THE REGION LIST
=================================================================

One row per region. Each row has:

  the eye         show or hide this region in the graph
  the expander    the numbers for this region:
                    the shot energy over the region, and how many
                      shots that was
                    the three dispersion orders
                    peak wavelength and peak intensity
                    the centroid — the "centre of mass" of the
                      spectrum, which is not the same as the peak
                      for an asymmetric spectrum
                    FWHM — the width at half the peak height
                    RMS bandwidth — a width measure that accounts
                      for the whole shape rather than just the half
                      height
                    the area
  a switch        overlay the individual spectra of this region
                  behind its average, up to four hundred of them.
                  This is how you see whether the average is
                  describing a tight family of curves or hiding two
                  different populations.

"Expand all" opens every region at once. "Analyze" sits below the
list, under Archive / Live, with its progress bar; "Clear all", which
empties the list, is on the last row of the panel next to "Export
results".


=================================================================
9. COMPARING TWO REGIONS
=================================================================

"Compare regions" picks two analysed regions, A and B, and draws
either

  A - B     the difference, in absolute terms, or
  A / B     the ratio

as an extra curve. The difference answers "how much more light at
this wavelength"; the ratio answers "by what factor". Both are drawn
alongside the originals, and the comparison curve is included in the
export.


=================================================================
10. WORKING WITH THE GRAPHS
=================================================================

  - Both graphs have a crosshair that follows the mouse, with the X
    and Y values written next to it.
  - Pan and zoom survive a redraw. Changing the averaging method does
    not throw away the view you had set up. "Home" resets it.
  - Right-click inside a graph for: axis limits, axis labels, the
    major and minor grid, a linear or logarithmic vertical scale, and
    reset view.
  - Margins changed in the toolbar's subplot dialog are remembered
    between sessions.


=================================================================
11. EXPORT
=================================================================

"Export results" writes a data file and/or a picture of the graph
(PNG, PDF or SVG).

One data file holds everything, in two blocks:

  # Spectrum details
      one row per region: the label, the date, the time range, how
      many spectra went in, which averaging method, the shot energy,
      the three dispersion orders, and every spectral number from
      section 7. Live shots get one summary row.

  # Curve data
      a wavelength column, then for each region its averaged curve
      and its standard deviation, then one column per live shot, then
      the comparison curve if it is switched on. Seven regions and a
      hundred live shots is a hundred and seven curves in one file.

      With "Show" set to "Every spectrum" the region part changes to
      one column per measured shot, each headed by the time it was
      taken, holding that shot's own intensity point by point against
      the wavelength column. That is the raw data of every spectrum
      you were looking at, not a summary of it.

      EVERY shot, including the ones the graph left out. The
      three-thousand-curve drawing limit is there because curves take
      time to paint; a column in a text file does not. When the two
      differ, the details block says so — "every spectrum (9007),
      graph drew 3000". Thousands of columns make a large file, and
      Excel stops reading at 16 384.

      THE FIRST COLUMN IS THE AXIS THE GRAPH IS DRAWN AGAINST. Its
      heading carries the unit — "wavelength_nm" for a spectrometer,
      "time_fs" for the SPIDER time domain — and so do the peak and
      width columns. If no axis could be resolved for the spectra, the
      graph plots them against the sample number, the column is headed
      "sample_number", the width columns turn into sample columns, and
      a note at the top of the file spells it out. It used to be
      possible for the graph to show sample numbers while the file
      showed a real axis, which put the same peak in two different
      places. If the axis had to be continued past the part the
      archive stored, a note at the top says that too.

The file uses a semicolon between columns and a point as the decimal
separator, and it announces the separator on its first line so Excel
opens it directly instead of showing an import wizard.


=================================================================
12. WHAT IS REMEMBERED
=================================================================

Per user, in your own application data:

  the search-signal list
  the named presets
  the spectrum channel, how its horizontal axis is built, and the
    unit that axis is shown in
  the window layout — the divider position between the two graphs,
    and the graph margins if you ever set them by hand
  where you left the focus-mode window

Deleting that folder resets everything to defaults. Nothing about a
particular day's analysis is saved; regions live only as long as the
window is open.


=================================================================
13. WHEN SOMETHING GOES WRONG
=================================================================

  The top graph is empty after loading a day
      That signal has no data for that day. Click another row in the
      signal table.

  Dragging does not create a region
      "Select" is not switched on, or Pan/Zoom is. They cannot both
      be active.

  Analyze produces a region with very few spectra
      Either the region is short, or the spectra in it had mixed
      lengths and only the most common one was kept. The count in the
      region row is the truth.

  The spectra look wrong along the horizontal axis
      Check the X and Y pair shown under the card. If the axis was
      built from the sample number, there is no wavelength
      information in it at all.

  The axis says "Sample number" although an _X channel exists
      The archive holds an axis that does not match the spectra and
      does not rise in even steps, so it cannot be stretched to fit
      them without making the numbers up. Hover the channel box: it
      says how many axis points the archive actually has.

  The axis is in the wrong unit
      Type the right one into the "Unit" box under the channel. The
      guess comes from the channel name, and a name can lie.

  Live mode shows nothing
      No new shots are arriving. The status line updates every three
      seconds either way, so a still status line means the polling
      itself has stopped.

  The averages all look identical
      Normalisation is on. Peak and Area both hide differences in
      amount.

  A search finds nothing
      Every word you typed has to appear in the channel name. Try
      fewer words. An empty box shows nothing on purpose.

  The colour scale disappeared
      It only exists in the GDD or TOD colouring modes; selection
      order uses a fixed palette and needs no scale.

-----------------------------------------------------------------
