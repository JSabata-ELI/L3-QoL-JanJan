Spectra — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19  (sp_t.py, 4658 lines)
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

3.1 LOADING A DAY

    "Load day..." opens a calendar. A plain click picks or unpicks a
    single day. Ctrl+click adds the whole range from your previous
    click, so a week is two clicks. Several days at once are
    allowed, and they are treated as one continuous stretch of time.

    Loading fetches the currently active search signal for those days
    and draws it in the top graph. That happens in the background;
    the window stays usable and the status line says what is
    happening.

3.2 THE SEARCH SIGNAL

    The box "Search data by" holds a list of signals you can navigate
    by, shown as a label and the channel it comes from. Click a row
    to plot it. Double-click the label to rename it to something
    meaningful to you — the underlying channel is unchanged.

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

3.3 MARKING REGIONS

    Switch "Select" on in the toolbar above the top graph, then drag
    horizontally across the graph. The dragged stretch becomes a
    region, drawn as a shaded band, and appears in the region list.

    "Select" and the pan/zoom tools are mutually exclusive: turning
    on Pan or Zoom switches Select off, because otherwise a drag
    means two things at once.

3.4 ANALYZING

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

3.5 THE FOUR AVERAGING METHODS

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


=================================================================
5. THE SPECTRUM CHANNEL AND THE WAVELENGTH AXIS
=================================================================

A spectrum needs two things: the intensity values, and the
wavelengths they belong to. The archive does not always provide both.

"Change..." in the active card picks the intensity channel. Then:

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


=================================================================
7. THE REGION LIST
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
8. COMPARING TWO REGIONS
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
9. WORKING WITH THE GRAPHS
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
10. EXPORT
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
11. WHAT IS REMEMBERED
=================================================================

Per user, in your own application data:

  the search-signal list
  the named presets
  the spectrum channel and how its wavelength axis is built
  the window layout — the splitter position and the graph margins

Deleting that folder resets everything to defaults. Nothing about a
particular day's analysis is saved; regions live only as long as the
window is open.


=================================================================
12. WHEN SOMETHING GOES WRONG
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
