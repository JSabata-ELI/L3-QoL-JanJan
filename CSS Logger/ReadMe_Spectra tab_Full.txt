Spectra — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-21  (sp_t.py)
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

3.2 LOADING A DAY

    "Load day..." opens a calendar. A plain click picks or unpicks a
    single day. Ctrl+click adds the whole range from your previous
    click, so a week is two clicks. Several days at once are
    allowed, and they are treated as one continuous stretch of time.

    Loading fetches the currently active search signal for those days
    and draws it in the top graph. That happens in the background;
    the window stays usable and the status line says what is
    happening.

3.3 THE THREE NUMBERED CARDS

    The top of the left panel is three numbered cards, read downwards
    in the order you actually work:

      1  DAY          which day (or days) is loaded.
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

    "Select" and the pan/zoom tools are mutually exclusive: turning
    on Pan or Zoom switches Select off, because otherwise a drag
    means two things at once.

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


=================================================================
4. LIVE MODE
=================================================================

Switch to "Live" in the Mode box, set "Average last N", and press
"Start Live".

  - The last ten minutes are loaded first, so you are not looking at
    an empty graph while it fills.
  - After that it asks for new shots every three seconds.
  - The graph is redrawn only when a shot has actually arrived. Most
    polls bring nothing, and redrawing anyway would make the window
    stutter for no reason. The status line still updates every time.
  - Up to two thousand spectra are kept in memory, oldest discarded.
  - "Stop Live" freezes the display; what is on screen stays there.

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

  Average             the four methods from section 3.5.

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

  Spectrum range      the wavelength window, From and To, plus
                      "Auto-fit range to data on Analyze" which sets
                      it from what was actually measured.

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

"Expand all" opens every region at once. There are buttons to clear
all regions and to analyze.


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

The file uses a semicolon between columns and a point as the decimal
separator, and it announces the separator on its first line so Excel
opens it directly instead of showing an import wizard.


=================================================================
12. WHAT IS REMEMBERED
=================================================================

Per user, in your own application data:

  the search-signal list
  the named presets
  the spectrum channel and how its wavelength axis is built
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
