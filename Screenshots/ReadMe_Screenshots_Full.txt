Screenshots — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19  (s.py, 4569 lines)
-----------------------------------------------------------------
Short version: ReadMe Screenshots.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. WHAT IT IS FOR
=================================================================

At the end of a measurement somebody has to put a set of camera
images into a folder, with names that still mean something a month
later, and often with a note saying what they were.

Done by hand that is fifteen minutes of opening folders on the
network, working out which one belongs to which camera, finding the
newest file in each, copying it, and renaming it. Done sixty times a
week it is most of a day.

This program does that in one press. You tick the cameras once, and
from then on the same set is one button.


=================================================================
2. THE TWO SOURCES, AND WHY BOTH EXIST
=================================================================

2.1 ARCHIVER

    The real stored image, copied out of the image store on the
    network. Full resolution, the original pixel values, and the
    image's own timestamp.

    Use this when you want the data: for measuring something later,
    for comparing two shots, for anything quantitative.

2.2 SCREENSHOT WINDOW

    A picture of the screen: one monitor, all screens together, or
    the camera's own display window.

    Use this when you want the evidence: what the operator was
    actually looking at, with whatever contrast, palette and zoom
    the display was set to, and with the surrounding readouts
    visible.

    The two are not interchangeable. A screenshot of a camera window
    has been through the display's own scaling and colouring, so the
    pixel values in it are not the camera's values.

2.3 WINDOW CAPTURE DETAIL

    A screenshot of a single window is cropped to the window's real
    visible edge, so the wide soft shadow Windows draws around a
    window is not included. The program also declares itself
    high-resolution aware, without which a capture on a scaled
    display comes out cut off.


=================================================================
3. CHOOSING CAMERAS
=================================================================

3.1 THE GRID

    Around eighty cameras, grouped by section of the beamline: LT1 to
    LT7, LT4 with PAD, the Compressor, and L3BT. Each group folds
    open and shut and has an "All" tick of its own. There is a search
    box above.

3.2 GREYED-OUT CAMERAS

    A camera that does not exist at this workstation is disabled.
    Hover it and the tooltip says which workstations it does belong
    to.

    The workstation is worked out from the computer's name — VIS-01,
    VIS-02, OPR-01 to OPR-03 and so on. A camera that is not listed
    for any workstation is allowed everywhere, on the assumption
    that the list is incomplete rather than that the camera does not
    exist.

3.3 NAMES AND FOLDERS

    The names on screen are the short ones people use; the folders in
    the image store use longer, less consistent spellings. The
    program keeps a translation between them, including the awkward
    cases where a folder spells a camera without the underscore
    before its NF / FF / DF suffix.

    You should never have to know the folder name. If a camera comes
    back "not found", that translation is where the problem is.

3.4 PRESETS

    Built-in: all cameras, and one preset per section.

    Your own: create, rename, or replace a built-in one with the same
    name. Yours are stored next to the program, so they survive a
    restart and travel with the folder. Hover a preset button to see
    the cameras in it.

    Presets are toggles, and the active ones are highlighted. "Clear
    all" resets cameras, monitors and presets together.

3.5 CAMERAS PRESELECT THEIR MONITORS

    Each workstation has a known physical arrangement of monitors and
    which cameras are displayed on which one. So ticking cameras also
    ticks the monitors they live on. A screenshot run therefore
    needs only one selection, not two.

    "Identify" puts a number on every screen for two seconds, so you
    can match the numbers in the program to the monitors on the desk.


=================================================================
4. CAPTURING
=================================================================

  Copy
      Once, now.

  Start auto
      Every N seconds, for a given number of rounds, or until you
      stop it — set the number of rounds to zero for "keep going".

  Start live
      Continuously, with the preview updating as it goes.

All three use the same machinery underneath. Screen pictures are
taken first, then the cameras. The progress bar counts through the
run and keeps counting across rounds rather than restarting, so a
long automatic run shows real progress. "Stop" interrupts the round
in progress rather than waiting for it to finish.

The window is disabled while a run is in flight, so a second press
cannot overlap the first.

WITH PREVIEW ON, an automatic run does NOT open a window per round.
It reuses one window with arrows to step back and forth through the
rounds. Sixty rounds would otherwise be sixty windows.


=================================================================
5. WHERE THINGS GO, AND WHAT THEY ARE CALLED
=================================================================

5.1 THE DESTINATION

    Pick a folder with the picker, or type or paste a path — network
    paths are accepted. The default is the scratch share. The folder
    button opens the destination in Explorer.

5.2 FILE NAME OR FOLDER NAME

    This trips people up once, so it is worth stating plainly. The
    name field changes meaning depending on how much the run
    produces, and the label above it says which:

      exactly one image   -> the name is a FILE name, written
                             straight into the destination
      more than one       -> the name is a FOLDER name; a folder of
                             that name is created and everything goes
                             in it. Leave it empty to write the files
                             straight into the destination.

5.3 THE AUTOMATIC NAMES

    Camera image:
        <camera>__<YYYY-MM-DD>__<HH-MM-SS>.<ext>

    The timestamp is the IMAGE's own timestamp, converted to Prague
    time — not the time you pressed Copy. That is the point: the name
    says when the shot was taken. (If the timestamp cannot be read
    from the file, the file's own modification time is used instead.)

    Screen picture:
        <run time>_monitor<N>.png
        <run time>_all.png

    A single output with a name typed in uses that name exactly as
    given.

5.4 LABELS

    Per camera, you can add:

      - your own text, as a prefix or a suffix, and
      - an automatic counter that goes 01, 02, 03 ...

    The counter advances after each successful copy of that camera,
    and the reset button puts it back to the start. Useful for a
    series: shot 01, shot 02, shot 03, without renaming anything
    afterwards.

    Label settings last for the session; they are not saved between
    runs of the program.

5.5 DETAIL

    A short text note, saved as a small text file next to the images.
    This is where "second attempt, PAM9 realigned" goes, so that in
    three weeks the folder still explains itself. It can be saved and
    deleted from the same dialog.

5.6 THE DIAGNOSTICS BOX

    Every step of a run is logged there: which folder was resolved
    for each camera, which file was chosen, whether the copy worked,
    and what went wrong if it did not. You can select and copy out of
    it.

    When a camera comes back empty, read this box before anything
    else — it will say whether the folder was not found, or the
    folder was found and was empty.


=================================================================
6. THE PREVIEW
=================================================================

6.1 THE THREE DECISIONS

    Tick Preview and a run opens as a grid of what it copied, before
    you accept it:

      Save                   keep the files
      Delete                 throw the whole run away
      Delete and Try Again   throw it away and copy the same
                             selection again

    The third one is the useful one. A camera that was mid-exposure,
    a shutter that had not opened, a lamp that was still on — see it,
    fix it, press one button, and the bad set is gone rather than
    sitting in the folder confusing somebody later.

6.2 THE VIEWING CONTROLS

    Contrast    a multiplying gain: it stretches the values apart
                around mid grey.
    Brightness  an adding offset: it moves everything up or down.

    Those two are genuinely different operations and are never
    mixed. Each has an "Auto", and when Auto is on it parks its
    slider on the value it worked out — so you can see what Auto
    decided and take over from there rather than starting again.

    Auto contrast stretches the image so that its darkest and
    brightest real content fill the range, ignoring the extreme
    fraction of a percent at each end. Auto brightness lifts the
    image so its bright end reaches the top of the scale.

    Zoom and Palette apply to the whole grid. The palettes are
    Grayscale, Gradient, Hot, Viridis, Plasma, Inferno, Jet and
    Turbo.

    NONE OF THIS CHANGES THE FILES. Adjusting the view is looking,
    not editing.

6.3 THE EDITOR

    Hover a thumbnail for a larger popup; click it to open the
    editor. There you get, for that one image:

      zoom, contrast and brightness with their Autos, palette
      crop, with a preview of the crop and a Clear crop
      drawing: freehand, straight line, rectangle, with a choice of
              colour and thickness
      Undo, Clear drawing
      Apply / Revert

6.4 WHAT SAVE ACTUALLY WRITES

    The copied files, unchanged. Plus, for every image you cropped or
    drew on, a SECOND file with "_annotated" in the name carrying
    those changes.

    So the original is never modified and never lost, and the marked-
    up version exists as its own file to paste into a report.


=================================================================
7. WHY IT IS FAST
=================================================================

Listing a folder on this network takes a noticeable fraction of a
second, and a run touches dozens of folders. Done at the moment you
press Copy, that would be a several-second wait every time.

So the program keeps its own list of what is in each camera folder,
and a background worker re-reads them every three seconds. Pressing
Copy uses the list that is already there.

The consequence: the FIRST run after starting the program is the slow
one, and everything after it is quick. If a brand-new image is
missing from a run, it may have appeared in the last three seconds —
press Copy again.


=================================================================
8. WHEN SOMETHING GOES WRONG
=================================================================

  A camera copies nothing
      Read the Diagnostics box. "Folder not found" is a name
      translation problem; a folder found but empty means the camera
      has not written anything in the period the program looked at.

  Everything copies nothing
      The image store is not reachable. Open the network path in
      Explorer to check.

  All the cameras I want are greyed out
      You are on a workstation where those cameras do not exist. The
      program decides that from the computer's name.

  The file went into the destination instead of a folder
      The run produced exactly one image, so the name field was a
      file name. Section 5.2.

  A screenshot has a grey border round it
      Should not happen — captures are cropped to the window's real
      edge. If it does, the window was partly off-screen.

  A screenshot is cut off
      A display scaling problem. Restart the program; the
      high-resolution declaration has to happen before the first
      window appears.

  An automatic run opened dozens of windows
      Preview was off for some rounds and on for others. With
      Preview on from the start, one window is reused.

  The preview looks nothing like the real image
      Contrast, brightness or palette are set for something else.
      They change the view only; the file on disk is untouched.

  My preset disappeared
      Custom presets live in a file next to the program. A
      redeployment that replaced the folder without keeping that file
      loses them.

  A camera name I expect is not in the list
      It is not in the program's camera table. That needs adding in
      the source, not in the settings.

-----------------------------------------------------------------
