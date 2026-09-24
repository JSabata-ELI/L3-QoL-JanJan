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

    The program starts with a ready-made list: all cameras, and one
    preset per section. From there the list is yours to arrange.

    "Config presets" opens the Preset Manager:

      - New writes a new preset; type a name, tick the cameras and
        press Save. Save sits next to the name.
      - Remove deletes the selected preset, whether it came with the
        program or you made it. It asks first.
      - Renaming is typing a new name over the old one and pressing
        Save; the preset stays where it is in the list.
      - Drag a row up or down to reorder the list. The preset buttons
        in the main window follow that order.
      - Search filters the camera list; a camera you ticked stays
        ticked while it is hidden, and Save still writes it.
      - Restore defaults puts the original presets back. Ones you
        deleted return at the end of the list, ones you edited go back
        to their original cameras, and your own presets are left alone.

    The whole list lives in custom_presets.json next to the program, so
    it survives a restart and travels with the folder. Hover a preset
    button to see the cameras in it.

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
      Continuously.

Copy and the automatic rounds share the same camera machinery. A
Copy takes the screens first, then the cameras, with a progress bar
counting through it. "Stop" ends an automatic run.

4.1 HOW THE ROUNDS ARE SPACED

    The rounds sit on a fixed grid. Round one is the moment you press
    Start; round two is exactly the interval later, round three twice
    the interval, and so on — measured from the start of the run, not
    from the end of the previous round. A round that takes longer than
    usual therefore does not push the ones after it.

    Every round also WRITES DOWN the moment it belongs to, and that
    written-down moment is what the cameras are asked about. If the
    copying falls a little behind, the pictures still come from the
    round's own moment: the work may lag, the pictures do not.

    All the cameras of a round are read at the same time, side by
    side, instead of one after another. This is what keeps a round's
    pictures together — read one at a time, six cameras could span
    ten seconds.

4.2 THE ARCHIVE'S CLOCK

    This computer's clock runs seconds ahead of the facility, and the
    archiver publishes about a second after the fact. So "now" by
    this PC's clock is a moment the archive has nothing for at all.
    Asked about it straight, every camera reports a picture half a
    minute "too old" and the whole run is skipped — which is exactly
    what happened on the first live try.

    So the FIRST round simply takes the newest picture each camera
    has, and measures from it how far the archive is behind. Every
    round after it asks about its own moment shifted back by that
    amount. The log line says what was measured.

    If a whole round later falls out of range while the cameras are
    still delivering NEW pictures, the offset itself must have moved;
    it is re-measured from that round and the log says so.

    The manual Copy button works the same way as the first round: it
    means "what the cameras have now".

4.3 WHICH PICTURE A CAMERA GIVES

    From the second round on: the newest picture the camera had AT OR
    BEFORE the round's moment. Never a later one — a picture taken
    after the moment belongs to a different instant, however close it
    looks.

    If the camera's newest picture at that moment is more than ten
    seconds old, it is left out of the round — unless it is the very
    same picture the previous round used, in which case it is kept,
    so a camera that is standing still still appears in every round.

    The Diagnostics panel gets one line per round with how many
    cameras answered, how far apart their pictures ended up
    ("spread"), and how far behind the grid the copying was ("lag"),
    followed by the age of each camera's picture.

    A round that ends up more than ten seconds behind its own moment
    is dropped, and says so — beyond that window the archive can no
    longer answer for that moment. Its screen pictures are kept:
    those are taken at the moment of the round itself, in parallel
    with the camera work, so a screen picture and the camera frames
    of a round always belong to the same instant.

WITH PREVIEW ON, an automatic run opens ONE window with the first
round and keeps it for the whole run — see section 6.1. Pictures
appear in it as they are copied.


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

    DURING AN AUTOMATIC RUN the same window works differently,
    because the run is still going on:

      - It opens with the first round and stays open to the end. It
        is one window, not one per round.
      - Pictures appear in it as they are copied, so you can watch
        the run instead of waiting for it.
      - A bar at the top shows which round you are looking at, with
        arrows to step between rounds and a "Follow latest" switch.
        Following is on to begin with, so the window always shows the
        round being copied. The moment you step with an arrow or pick
        a round from the list, following switches off and the window
        stays where you put it — a round you are studying is never
        pulled away from under you. Tick "Follow latest" again to
        rejoin the run.
      - "Save" writes the annotated copies WITHOUT closing the
        window, and "Delete this cycle" throws away only the round on
        screen; "Delete and Try Again" is not offered, because the
        round's moment has passed and there is nothing to retry.
      - Under each picture is its own time and, in brackets, how far
        behind the round's moment it is.

6.2 THE VIEWING CONTROLS

    Contrast    a multiplying gain: it stretches the values apart
                around mid grey.
    Brightness  an adding offset: it moves everything up or down.
    Gamma       bends the middle of the scale: above 1 lifts the
                midtones, below 1 deepens them, and black stays
                black and white stays white either way.

    These are genuinely different operations and are never mixed.
    Contrast and brightness each have an "Auto", and when Auto is on
    it parks its slider on the value it worked out — so you can see
    what Auto decided and take over from there rather than starting
    again.

    Auto contrast stretches the image so that its darkest and
    brightest real content fill the range, ignoring the extreme
    fraction of a percent at each end. Auto brightness lifts the
    image so its bright end reaches the top of the scale.

    The ↺ next to each control puts it back to untouched, and
    switches that control's Auto off with it — otherwise the next
    redraw would simply park the slider back where Auto wants it and
    the button would look dead.

    Zoom and Palette, on the second row, apply to the whole grid.

    THE PALETTE STARTS ON "ORIGINAL", which means the picture in the
    colours it arrived in. A colour camera stays in colour no matter
    what you do with contrast, brightness or gamma — those are
    applied to each colour channel alike, so the hues do not shift.
    (Before, colour survived only while every control sat at its
    default, so ticking Auto contrast looked as though it had also
    changed the palette. It had not; the picture had been flattened
    to grey.)

    "Grayscale" deliberately drops the colour. Everything after it —
    Gradient, Hot, Viridis, Plasma, Inferno, Jet, Turbo — paints
    false colours over the grey values.

    NONE OF THIS CHANGES THE FILES. Adjusting the view is looking,
    not editing.

6.3 THE EDITOR

    Hover a thumbnail for a larger popup; click it to open the
    editor. There you get, for that one image:

      zoom, contrast and brightness with their Autos and ↺, gamma
      with its ↺, palette
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

A folder only joins that background rotation once it has been read
for the first time, so starting an automatic run reads every selected
camera once up front. That is why the first round is no longer the
slow one.

A list that stops before the moment being asked about cannot answer
it, so it is re-read on the spot. A brand-new picture is therefore
never missed just because the list was a couple of seconds old.

The consequence: the FIRST Copy after starting the program is the
slow one, and everything after it is quick.


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

  An automatic run shows nothing until I press an arrow
      Fixed. The preview now fills itself as the pictures are
      copied. If the window is empty, check that Preview is ticked
      and that the Diagnostics panel is not reporting every camera
      as skipped.

  Every camera is skipped, every round, as "too old"
      The archive clock offset is wrong. The first round measures it;
      if the run was started at a moment when no camera had a recent
      picture, stop and start it again. The log line "Archive clock
      runs ... behind this PC" says what was measured.

  Auto contrast turns my colour picture grey
      Fixed. Set the palette to "Original" (the default) and the
      colours stay whatever the camera sent, whatever the sliders do.

  The pictures of one round are seconds apart
      Fixed. Every round asks all its cameras about one written-down
      moment and reads them side by side. The Diagnostics line for
      the round reports the "spread" — if that is still large, those
      cameras genuinely have nothing closer in the archive, and the
      per-camera ages on the next line say which ones.

  A camera keeps missing from the rounds
      Its newest picture at the round's moment is more than ten
      seconds old and is not the same one as the round before — so
      it is being left out on purpose. The Diagnostics panel names
      it and gives the age.

  The rounds come out slower than the interval
      The copying cannot keep up, so some rounds are waiting. The
      log says how many are waiting and how far behind it is; a
      round more than ten seconds behind its own moment is dropped
      and says so. Fewer cameras, or a longer interval, fixes it.

  The preview looks nothing like the real image
      Contrast, brightness or palette are set for something else.
      They change the view only; the file on disk is untouched.

  My preset disappeared
      The preset list lives in custom_presets.json next to the
      program. A redeployment that replaced the folder without keeping
      that file loses it, and the program starts again with the
      original presets. A preset you deleted yourself comes back with
      "Restore defaults" only if it was one of the originals.

  A camera name I expect is not in the list
      It is not in the program's camera table. That needs adding in
      the source, not in the settings.

-----------------------------------------------------------------
