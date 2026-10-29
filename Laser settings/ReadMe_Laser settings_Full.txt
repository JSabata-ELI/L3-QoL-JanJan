Laser settings — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-10-15
  (main.py 754 lines, ls_core.py 2865, ov_t.py 1724, ls_ui.py 1608,
   st_t.py 1472, ft_t.py 1467, ls_dm.py 1392, ls_cpva.py 764,
   dm_t.py 564, ls_screen.py 470, ls_engine.py 361, dw_t.py 229,
   ls_log.py 174, in_t.py 168; 14012 lines in all)
  Program folder: 804 kB in 19 files (without testing/, build output and caches)
-----------------------------------------------------------------
Short version: ReadMe Laser settings.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. THE FIVE HALLS
=================================================================

The question the program answers: we are shooting into one of five
halls, and each hall wants the machine set up its own way. Is it?

The program has five tabs: Overview, Limits, DM settings, DM writer
and Settings. The two DM tabs are about the deformable mirror and
are described in section 12.

1.1 WHICH HALL IS BEING SHOT INTO

    L3, E2, E3, E4 and E5 — in that order, left to right, until
    somebody arranges them otherwise on the Settings tab. (L3 is
    stored as "Int" in the settings file; see 1.4.)

    Whether a hall is being shot into is read off its safety gate
    valve. The valve reports its position on two channels, but ONE
    of them is read:

      L3 (SGV501), from the CPVA archiver (10.78.0.57):
        L3-PSS:SGV501_IN_OPEN_POSITION     1 = open,   0 = not open
                                           READ
        L3-PSS:SGV501_IN_CLOSED_POSITION   1 = closed, 0 = not closed
                                           fallback

      E2 to E5 (SGV502 to SGV505), from the L3BT archive
      (10.78.0.64, or its twin 10.78.0.65 when .64 is down):
        L3BT-MSS:SGV50n_OPEN_IN            1 = open,   0 = not open
                                           READ
        L3BT-MSS:SGV50n_CLOSED_IN          1 = closed, 0 = not closed
                                           fallback

      L3   SGV501     E2  SGV502     E3  SGV503
      E4   SGV504     E5  SGV505

    Which archive a channel is read from is decided by the program:
    a name only the L3BT archive lists is read there, everything
    else from the CPVA archiver. The channel picker on the Limits
    tab lists the names of both. A channel whose newest archived
    sample is "Disconnected" is "not read", never its last value.

    The open switch is a complete answer on its own: "not open"
    means closed, because a gate valve that is not open is shut.
    Reading the second channel every second as well would be a
    request a second spent on a question already answered.

    THE CLOSED SWITCH IS THE FALLBACK AND NOTHING ELSE. It is asked
    for only while the open one cannot be read — it failed, the
    archiver holds nothing for it, or nothing has arrived for half
    a minute — and it drops back out of the pass the moment the
    open one comes back. Its name is not a setting: it is built
    into the program, and a new version is what changes it. A hall
    whose open channel has been cleared by hand reads the closed
    one straight away.

    A switch counts as made anywhere from 0.6 to 1.4, because a
    switch archived as a float has been seen to publish 0.9999.

    WHICH POSITION MEANS SHOOTING is a fact about the machine, not
    a setting, so nobody is asked: L3 is internal shooting, so the
    beam goes there while the gate valve is CLOSED, and for the
    experimental halls the valve is open when the beam goes in.
    Whenever one valve is open the others are shut.

    ALL FIVE ARE LIVE since 15.10.2026. Until then SGV502 to SGV505
    were set to L3-PSS:SGV50n_IN_*_POSITION names on the CPVA
    archiver, which never published; a settings file still holding
    one of those old open names is moved onto the new one when it is
    read. Names that LOOK right but hold only "Disconnected" since
    13.8.2026, so do not use them: E3/E4/E5-PSS-SGV50n:VALVE_IN_*_
    POSITION, E3-PSS-SGV501:IN_*_POSITION, L3BT-MSS:SGV50n_CLOSED and
    L3BT-MSS:SGV50n_OPEN_FOR_BEAM. If the L3BT archive cannot be
    reached, the four are read off the L3BT MSS window — see 1.2.

1.2 THE L3BT MSS WINDOW — THE LAST RESORT

    On L3-OPR03 the L3BT MSS window shows each valve's position in
    a small box under its name. While a valve's channels cannot be
    read, the program photographs that window once a second and
    reads the box:

      [ O ■ ]    black square on the right     OPEN
      [ ■ C ]    black square on the left      CLOSED
      [ ⊠ ⊠ ]    crossed out                   taken as CLOSED

      SGV502 = E2    SGV503 = E3    SGV504 = E4    SGV505 = E5

    How it copes with the window moving:

      - The window is found by its title, which must be exactly
        "L3BT MSS". A title that only CONTAINS it — a screenshot of
        the window open in the Photos app, say — is never taken.
      - It is photographed the way Windows prints a window, so it
        does not matter which monitor it is on, how the monitors
        are arranged, or whether another window covers it. A
        MINIMISED window cannot be photographed.
      - Each box is looked for around the place it was measured at
        on 1.10.2026, scaled to the window's real size, and found
        by its black outline; a few pixels of drift, or a different
        display scale, change nothing.

    A box that cannot be made out is NOT "closed" — that hall says
    "not read", and the tooltip says why ("the window is not open",
    "the SGV503 box could not be read"). A look older than half a
    minute is not believed either.

    Order of trust, per hall: the open channel, then the closed
    channel, then the window. While all four channels answer, the
    window is not looked at at all.

1.3 THE SQUARE

      WHITE, dark letters      the beam is not going into this hall
      GREEN, white letters     it is, and everything is set right
      RED blinking to WHITE    it is, and something watched is out
                               of range
      PALE GREY, dashed edge   no channel set, or nothing could be
                               read

    GREEN HAS TO BE EARNED BY A SHOT. The valve only says where the
    beam would go; whether the laser is firing is read off SBW4's
    energy (both its archive names, the newer sample counts). A
    hall is green only while its valve points at it, the newest
    energy is at least 0.1 J, AND that energy came in less than 15
    seconds ago. With no new shot for 15 s the square goes back to
    white "idle", and the next shot makes it green again
    (2.10.2026). The archive writes a value only when it changes,
    so without the 15 s the last shot of the evening kept the
    square green all night. The time is counted on this PC's own
    clock from the moment a new sample is noticed — not from the
    sample's own time stamp, because this PC's clock runs about
    25 s ahead of the archive. The shot that is already in the
    archive when the program starts does not count: nobody knows
    when it was fired, so the squares wait for the next one.

    Grey is deliberately not white. "We are not shooting into E4"
    and "we cannot tell what E4 is doing" are different sentences
    and must not share a picture. The word INSIDE the square says
    which one it is: "idle", "no channel" or "not read". Hovering
    the square says it in a full sentence, names the channel it
    came from, and says so when the answer came from the fallback.

    THE WORD IS IN THE SQUARE, NOT UNDER IT, and it can be switched
    off. It used to be a caption on its own line underneath, which
    meant the square could not be moved without dragging its
    caption with it. Now the square carries everything it says —
    name above, word below, both centred — and the Settings tab has
    a switch that takes the word off altogether and a size for it.
    With it off the colour still answers the question; the word
    only names the reason.

    The blink is red and white — the fault colour, not the green
    one. Green means "this hall is set right", and it has no
    business being on screen while it is not. All five squares
    blink on one shared beat of 450 ms, so they can never drift out
    of step with each other.

1.4 INT IS CALLED L3

    Since 2.10.2026 on the squares — it is the laser itself,
    shooting internally while SGV501 is shut — and since 14.10.2026
    everywhere on screen: the Limits tab's hall button, the gate
    valve box and every tooltip and sentence say L3. Only the
    settings file and the saved settings sets still store it as
    "Int", so nothing saved by an older version needs converting.
    What the L3 square shows:

      SGV501 closed                      SHOOTING or idle, by
                                         SBW4's energy, exactly as
                                         above
      SGV501 open, a hall's valve open   idle — that hall's square
                                         lights up
      SGV501 open, no hall's valve open  white, "waiting for hall":
                                         the beam has left L3 and
                                         has nowhere to go yet
      SGV501 cannot be read              as before — "not read",
                                         or WATCHING

    "No hall's valve open" includes valves that cannot be read at
    all — not from their channels and not off the L3BT MSS window —
    and the tooltip names them. A fault keeps blinking: "waiting
    for hall" only replaces a plain white square. The rows are
    judged exactly as before (section 4).

1.5 WHERE THE SQUARES SIT

    WHERE THE FIVE SQUARES SIT AND HOW BIG THEY ARE IS SET BY HAND,
    on a board on the Settings tab — see section 8. Five equal
    cells of one row is only the starting arrangement, and "Arrange
    them again" is the way back to it.

    ONE SQUARE INSTEAD OF FIVE. "One square that follows the hall
    being shot into" on the Settings tab replaces the five with a
    single square on the left and the list beside it. Its name
    changes by itself to the hall the beam is going into. The beam
    never goes into two halls at once, and when it goes into none
    of E2 to E5 it is going into L3 (SGV501 shut) — so the square
    names the hall whose limits are in force, and L3 whenever there
    is none. Its colour, its word and its blink are that hall's,
    exactly as on the five. In this mode "Height of the band" is
    called "Size of the square" and is the square's side; the fit
    works it out like the band, and the board is hidden because it
    only arranges five.

1.6 A GATE VALVE IS THE SLOWEST THING IN THE PROGRAM

    It is archived on change only, and a valve nobody has touched
    since the summer has published nothing for months. The
    look-back therefore widens all the way to a YEAR before giving
    up (an hour, six hours, a day, a week, a month, three months, a
    year), and the window that answered is remembered per channel,
    so the cost is paid once. What makes a square go grey is our
    READ failing, never the value being old.


=================================================================
2. THE WATCHED VALUES
=================================================================

2.1 ONE LIST, LIMITS PER HALL

    There is ONE list of channels, shared by all five halls. What
    differs per hall is the limits, and ONLY the limits. Show is
    one switch for the whole program: untick it in E2 and the value
    stops being watched in L3 and E5 too, and the row leaves the
    first tab there and then.

    A value is in range when

      Min <= value <= Max

    with the limits themselves counted as in range. That was asked
    for explicitly and it is what the program does.

    An empty Min or Max switches that side off. It never means
    zero — a zero upper limit on a voltage would fire the moment
    the laser was switched on. Both boxes empty means there is
    nothing to judge, which is shown as a grey dash rather than as
    a pass.

2.2 HOW THE LIST IS DRAWN

    The first tab lists the name, the number and the little square,
    and nothing else. Not as a table: there is no heading row, and
    the three sit together instead of the name being stretched
    across the window with the square yards away at the end of it.
    That was asked for in those words.

                    GDD:  23800  fs²  [tick]
                    TOD: -88000  fs³  [tick]
        SBW4 Compressed:  19.81  J    [tick]

    The name has a colon after it and is written flush RIGHT, so
    the names all end on one line. After the colon comes one space
    and room for one minus sign, then the digits, flush LEFT — so
    every number starts on one line and a minus stands in front of
    its digits. The unit is a column of its own, so the units stand
    under each other, and the squares stand in one column straight
    after the longest unit. Asked for on 2.10.2026; from 1.10.2026
    the numbers were flush right, and before that the order was
    name, square, number, all flush left.

    THE THREE COLUMNS ARE CENTRED IN THE WINDOW, as one block —
    "zarovnej tu tabulku na střed", 1.10.2026. Every row is exactly
    as wide as the block, the names all get one width and the
    numbers all get another, so the squares stand in a straight
    line down the list.

    EVERY NAME AND EVERY NUMBER IS SHOWN WHOLE. "SBW4 Compressed"
    came out as "SBW4 Compres…" and that was the complaint —
    "udělej to tak, aby vždy byly vidět slova a hodnoty". So the
    name column takes what the longest name really needs and the
    TEXT SIZE is what gives way: the fit shrinks it until the whole
    row fits the window. A name is cut only when even the smallest
    readable text cannot fit it, which takes a few hundred
    characters, and then the whole of it is in the row's tooltip.

2.3 HOW A NUMBER IS WRITTEN

    Energies read to hundredths — 17.53 J, not 17.5266 J ("energii
    zarovnej na setiny") — because the digits after that are
    shot-to-shot noise and a number whose last digits dance is read
    more slowly. Everything else keeps the shortest form that loses
    nothing. A unit typed as fs^2 is written fs² on screen, with
    the power raised; cm^-1 becomes cm⁻¹. That happens when it is
    DRAWN and nowhere else — the Unit box on the Limits tab goes on
    showing exactly what was typed into it, and the settings file
    keeps what was typed, because a box that rewrites your text the
    moment you leave it is a box nobody trusts.

2.4 THE LITTLE SQUARE

      GREEN with a white tick (dark edged)   shooting, inside the
                                             limits
      RED with a black cross                 shooting, outside them
      ORANGE with a tick or a cross          read fine, but nothing
                                             is being shot (no SBW4
                                             shot for 15 s, or no
                                             hall in force), so it
                                             is not checked
      WHITE with a grey dash                 read fine, nothing to
                                             judge it by: no Min /
                                             Max, or Alarm unticked
      GREY with a dash                       cannot be read

    Asked for on 12.10.2026: grey means ONE thing, "something like a
    network error". Orange is "it reads, but we are idle, so there is
    nothing to check" — the tick or cross is still there so the
    operator sees where the value stands before the next shot. It is
    worked out against the limits of the hall the beam would go into
    (L3 when none other). An orange cross never makes a hall blink.
    Hover a row: its tooltip ends with this key.

    RENAMING ON THIS TAB. Double-click a row: a box opens over the
    name. Enter (or clicking elsewhere) keeps the new name, Esc the
    old one. It is the same "Name on the first tab" as on the Limits
    tab, which follows at once.

    THE SCANNING BOX (12.10.2026) sits at the left of the line above
    the list, beside the shot counter. ONE switch for the whole list:
    ticked, every row with Scan min or Scan max set (Limits tab) is
    judged against its scan pair, the same in every hall; a row with
    both scan boxes empty keeps its hall's Min and Max. Unticked, Min
    and Max apply again at once — a value inside the scan range but
    outside Min / Max turns red on the next refresh. While it is
    ticked the caption is bold, and the hall being shot into says
    SHOOTING + SCANNING (or OUT OF RANGE + SCANNING while it blinks).
    It is saved with the settings, so it survives a restart.

    Nothing at all is painted behind the list: no sheet, no box, no
    stripes, and no red band on the row that is out of range. The
    text is written straight onto the window, and the square is the
    only mark a row wears — that is what the square is for. Asked
    for in those words, twice.

    How big it is written, in what typeface, bold or not, and how
    big the square is, are all on the Settings tab — see section 8.

    Everything else is the row's TOOLTIP — hover it and you get the
    channel or the formula behind it, the verdict in words ("above
    25", "below 1"), and the limits of all five halls with the one
    in force marked. Those were three more columns once. This tab
    is read from across the room, and a wall of sentences is not.


=================================================================
3. WHAT A GREY OR WHITE DASH MEANS
=================================================================

GREY — the number cannot be read. The row's tooltip says which:

  "not read yet"            the program has only just started
  an error sentence         the read failed — the archiver was
                            unreachable, refused the name, timed
                            out
  "nothing archived for     the read worked and the archiver holds
  this channel"             nothing at all for that name
  "not read for 34 s"       nothing has come back for longer than
                            half a minute; the last number is no
                            longer claimed to be current

A formula is grey when one of its sources is.

WHITE — the number is read, but there is nothing to judge it by:

  "no limits set"           Min and Max are both empty, so there is
                            nothing this value could be judged
                            against. Two empty boxes are not a
                            pass.
  "alarm off — ..."         Alarm is unticked; the rest of the
                            sentence still says where the number
                            stands against the limits

None of them is ever drawn as a tick. A read that did not arrive
must never look like a value comfortably in range — that is the
failure that looks most like success.

The age that matters is the age of the last successful READ, never
the age of the value. The archiver writes on change only, so a
setting nobody has touched since Monday is perfectly healthy and
its newest sample is days old.


=================================================================
4. WHEN NOTHING IS BEING SHOT
=================================================================

Nothing being shot — no fresh SBW4 shot, or no hall in force —
means nothing is CHECKED: no hall blinks and no square is green or
red. Since 12.10.2026 a row that reads fine is then ORANGE, with
the tick or cross it would get against the limits of the hall the
beam would go into (L3 when none other). Until then it was a grey
dash, which could not be told apart from a channel that cannot be
read — the complaint was exactly that.

The rows themselves do not come and go. The first tab lists every
channel whose Show is ticked, and Show does not depend on the hall,
so the list never reshuffles under your hand when the machine
switches halls.

If two halls are being shot into at once, every row is judged
against both and the worse verdict wins; the row's tooltip then
names the hall it came from.

4.1 WHILE ONLY L3 CAN ANSWER

    There is one exception to all of the above, and it is the state
    the machine is actually in today. SGV502 to SGV505 publish
    nothing. L3 shoots while its valve is SHUT, so with that valve
    open no hall could ever say it was being shot into and the
    whole list sat grey for ever.

    So: while not one of those four valves can be read — not from
    its channels and not off the L3BT MSS window — L3's limits are
    in force whatever L3's own valve says. L3's square goes WHITE
    with a darker edge and the word WATCHING — not green, because
    nobody is shooting into it and the square must not claim
    otherwise — and the rows are judged. If something is out of
    range the square blinks red and white exactly as it would while
    shooting.

    A hall really being shot into always wins over this. And the
    rule switches itself off the moment the FIRST of those four
    valves answers, whatever it says: from then on the valves
    decide again. What counts as "cannot answer" is the same thing
    everywhere else in this program — no channel set, the read
    failed, nothing archived, or nothing for half a minute.
    Hovering a square says which of the two is happening, and the
    log file has the same line.


=================================================================
5. THE LIMITS TAB
=================================================================

Called "Settings" until 1.10.2026, when the Font tab took that
name.

The buttons at the top pick the hall. Everything below belongs to
it.

5.1 GATE VALVE

    ONE line: "PV for the valve", the channel, the Find button, and
    what that channel is reading right now, so a wrong name is
    obvious straight away:

      value=1 : open          value=0 : closed

    The word after the colon comes from the CHANNEL'S NAME and
    never from the number. An _IN_OPEN_POSITION channel reads 1
    while the valve is open; an _IN_CLOSED_POSITION one reads 1
    while it is SHUT, so there the same 1 means the opposite thing.
    A name that says neither word gets the number on its own —
    nothing is claimed that cannot be known. The program judges the
    valve the same way round as it labels it, so the two can never
    disagree.

    There is no sentence under the box. What the valve MEANS —
    shooting into this hall, not shooting, limits in force — is the
    hall's big square on the first tab, which says it for all five
    halls at once instead of for the one hall you happen to have
    picked here. Hovering the reading still gives that sentence in
    words.

    Which valve position means shooting into this hall, and the
    name of the closed channel that stands in when the open one
    cannot be read, are the box's TOOLTIP rather than a line on
    screen: they are true for ever and were being read every day
    for the one thing that changes. That closed name is built into
    the program and cannot be typed over.

5.2 THE TABLE

    Double-click a cell to change it, press F2, or pick it and just
    start typing. A centred number stays centred while it is typed
    (14.10.2026 — Qt's own editor jumped to the left edge). Every
    number in every table of the program is centred; only names and
    channels read from the left. Its columns: Show, Alarm, Letter,
    Channel or formula, Name on the first tab, Unit, Min, Max,
    Scan min, Scan max, Reading now.

    What belongs to the ROW and is the same in every hall: Show,
    Alarm, the channel or formula, the Name on the first tab, the
    Unit, and Scan min / Scan max. What belongs to THIS HALL alone: Min and Max, and nothing
    else. (That used to be written along the top of the table; it
    was taken off the screen on 30.9.2026 and it is written here
    instead.)

      Show          one switch for the whole program. Unticked, the
                    channel is not watched anywhere: it leaves the
                    first tab and can make no hall blink. Its
                    number is still shown here, because the reading
                    and the watching are two different things.
      Alarm         (6.10.2026) whether out of range is REPORTED.
                    Unticked, the row stays on the first tab with
                    its number, but its square is a white dash and
                    it can make no hall blink. Saved in the settings
                    file like Show, so it stays off until it is
                    ticked again.
      Scan min,     the range used while the Overview's Scanning box
      Scan max      is ticked (section 2.4) — one pair for every
                    hall, on every row but the shot counter and DM
                    settings. Empty means that side is not limited,
                    as with Min/Max; both empty and the row keeps Min
                    and Max while scanning. Out of the scan range
                    blinks the hall as usual, and the row's tooltip
                    says "scan: ...". Alarm still wins. Until
                    12.10.2026 Scanning was a tick box per row and
                    only GDD, TOD and FOD had one; a file with such a
                    tick ticks the Overview's box on its first load.
      Letter       what a formula calls this row. It is the row's
                    position, so it moves as the list is edited.
      Reading now   what the machine is holding right now, so a
                    limit can be set against the real number rather
                    than from memory. Formula rows included — their
                    number is worked out here as well.

    THE ROWS WITH SHOW UNTICKED ARE AT THE BOTTOM, under a grey
    "Not shown" line (2.10.2026), so they are out of the way of the
    ones being watched. Ticking Show moves the row back up at once.
    Only the TABLE is in that order: the list itself is not
    reordered by Show, so an unticked row's letter simply sits out
    of sequence at the bottom.

    YOUR OWN ORDER (12.10.2026): grab any cell of a row and drag it
    up or down; several picked rows move together. The list takes
    the new order, the first tab shows it, and the letters follow —
    every formula is rewritten so it keeps meaning the same rows. A
    row dropped under the "Not shown" line has its Show unticked; one
    dragged back above it is ticked again. The shot counter has a
    table of its own and is not dragged. A blue line shows where the
    rows will land, and Esc cancels. Pick several with Ctrl / Shift
    first, then drag one of them; a plain drag no longer picks a
    range of rows (it used to, and that was all a drag ever did —
    fixed 12.10.2026).

    THE TABLE TAKES THE WINDOW'S HEIGHT (12.10.2026). It used to be
    fixed to the height of its rows, so on a big window it stayed
    small with empty paper under it. Now it fills the page above the
    shot counter's table and scrolls inside itself; it is never
    shorter than six rows, and below that the page scrolls.

    A comma is accepted as the decimal point. Anything that is not
    a number empties the box rather than sitting there looking like
    a limit. If a Min ends up above its Max the program writes it
    into the log file — nothing could ever be in range.

    THERE IS NO COUNTING LINE UNDER THE TABLE. "4 rows on the list,
    3 watched, 4 with a limit set for Int" sat along the bottom
    until 1.10.2026 and was taken off: every one of those three
    numbers can be counted off the table itself, which is directly
    above it, and the row it took is a row of the table.

5.3 THE BUTTONS

    ADD CHANNEL searches the archiver and puts a channel on the
    list, for every hall at once. Type a few words from the name:
    every word has to appear somewhere, nothing has to be at the
    start and nothing has to be adjacent, so "chl temp" finds
    L3-UTIL-CHL03-001:Temp.

    ADD FORMULA puts a row on the list that has no channel of its
    own and works its number out instead — see section 6.

    CHANGE CHANNEL replaces the name in the picked row. A formula
    row has no channel to change; the formula itself is edited in
    its own box.

    REMOVE takes every picked row off the list — several at a time
    — in all five halls, and says which ones before it does it. If
    a formula uses one of them, it says that too, and names the
    formulas that will stop working.

    There is no button for the shot counter: it is always on the
    list, and Remove passes it by — see 6.2.


=================================================================
6. A ROW THAT WORKS ITS NUMBER OUT
=================================================================

6.1 FORMULAS

    Every row wears a LETTER, and a formula row is a sum over those
    letters:

      A * 0.749          three quarters of what row A reads
      (A + B) / 2        the two of them, averaged

    That is how a value nobody archives — the energy after the
    compressor, say — is watched, limited and judged exactly like a
    read channel. + - * / and brackets are allowed, and abs, min,
    max, round and math. Nothing else: the settings file is a file
    like any other, and one edited by hand must not be able to make
    the program run anything.

    A formula may use any row on the list, above or below it (since
    12.10.2026, when rows became draggable — they are worked out in
    the order they need each other). Only a ring of formulas that
    use each other cannot be worked out, and its box says so. A new
    one is added at the bottom. Its box opens holding
    "= ", which is how the row says it is a formula and not a
    channel name waiting to be typed.

    A DECIMAL COMMA IS FINE. 0,749 is stored as 0.749, the same way
    the Min and Max boxes have always taken a comma.

    Worth knowing why that had to be done: A * 0,749 is a VALID sum
    in the language underneath, and it quietly means something else
    entirely. It used to compile without complaint, show no red
    box, and come out as "did not work out to a number" once a
    second with nothing to say why.

    The one place a comma is NOT a decimal point is INSIDE
    brackets, because there it separates the arguments: write
    min(A, 0.749) and never min(A, 0,749). The program deliberately
    does not warn about that one — min(1, 2) is the same shape and
    is perfectly correct, so a warning would mark correct formulas
    red, and a red box that is sometimes wrong is one nobody reads.
    It is the one thing in a formula to type carefully.

    THE LETTERS ARE A DRAWING, NOT THE MEANING. What a formula
    points at is stored as the rows themselves, so renaming a
    channel, pointing a row somewhere else or moving rows about
    cannot quietly change what a formula computes — the box is
    rewritten in the new letters and goes on meaning the same
    thing.

    A formula that cannot be worked out turns light red with dark
    red text, and hovering it spells out every letter and says what
    is wrong: a letter that is not one of the rows, a row that
    refers to itself, a row further down the list, or an expression
    that will not parse. A formula whose source has no number has
    no number either — a computed value is never a tick while
    something under it is a dash — and a formula is exactly as
    stale as its oldest source.

6.2 THE SHOT COUNTER

    Since 2.10.2026; always on the list since 5.10.2026. A row
    called Shot counter is always on the list: a settings file
    without it gets it at the bottom, shown, and Remove passes it
    by. Untick Show to hide it. It counts shots by Image Slider's
    rules:

      - a NEW archived value of any row with Show ticked is a shot.
        One row changing is enough — they do not all have to
        change. A shown formula counts through the rows it is
        worked out from;
      - the same shot lands on several rows "roughly together", one
        after another — their time stamps can be a second apart and
        they can arrive in different reads. So every row is counted
        on its own and the number shown is the LARGEST of those
        counts: three rows changing for one shot are one shot.
        (Until 5.10.2026 a 0.2 s window joined them, and a
        staggered shot was counted two or three times.) A late
        row's share of the last shot, arriving after the count went
        idle, does not start a new count;
      - every value is counted, not one per second: the reader
        already holds the last two minutes of each channel's time
        stamps, so a laser firing faster than the once-a-second
        read is still counted shot by shot, and it costs no extra
        request;
      - after "Start again from 1 after" (in the shot counter's
        table at the bottom of the Limits tab; 10 s to start with,
        1 to 3600) the number STAYS on screen, but the count is
        zeroed: the next shot shows 1. The time is this PC's own
        clock, never the archive's time stamps — this PC runs about
        25 s ahead of the archive;
      - what is already archived when the program starts — or when
        a row is ticked Show — is history, not a shot.

    The row has no channel, no unit and no Min or Max (5.10.2026 —
    a number of shots is never right or wrong; limits in an older
    settings file are dropped). Its Name and Show work like any
    other row's, and a formula may use its letter. On the Limits
    tab it is NOT in the channels' table: it has a one-row table of
    its own at the very bottom, under the "Not shown" rows
    (5.10.2026), with its own headings:

      Show | Letter | Name on the first tab |
      Start again from 1 after | Text size | Bold | Shots now

    Type a number into "Start again from 1 after" or "Text size"
    ("30" or "30 s"; the cell is written back with the value in
    force, held to 1–3600 s and 8–200 px). Both tables are as tall
    as their rows, so the page scrolls rather than the tables.

    On the Overview it is NOT a row of the list: it is one line
    above it, "Shot counter: 12", centred, in the list's typeface
    but at its own size (Text size and Bold in its table on the
    Limits tab; 16 px, not bold, to start with). The fit never
    changes that size. No verdict square, and it can never make a
    hall blink. Its old default name "Shots" is renamed "Shot
    counter" on load.

    The reset time must be LONGER than the time between two shots.
    Measured 5.10.2026: SBW4's energy gets a new value every 10.0 s
    while HAPLS fires at 0.1 Hz (10.4–10.7 J), so with the 10 s
    default every shot was the first of a new run and the counter
    showed 1 for ever.


=================================================================
7. THE LOG — AND WHY THERE IS NO INFO / LOG TAB
=================================================================

There were four tabs until 1.10.2026 and the third was Info / Log.
It is gone from the window. The page is still in the program and
still collects every line, but it is never shown: asked for in
those words — "info / log smaž, nebo minimálně zneviditelni; v kódu
to můžeš nechat, ale tahle záložka tam už nebude".

Everything it said is still written down, in the log FILE:

  %LOCALAPPDATA%\Laser settings\laser_settings_crash.log

Every line the program has said, stamped with the time, over every
run. A channel that could not be read, a Min above its Max, a pass
written off, a settings file that would not open — they are all in
there. It is written line by line, so the last thing in it is the
last thing that happened before the program died, including deaths
inside Qt itself that leave no Python traceback.

What is NOT in the file is the live sentence the tab's top line
used to carry — whose limits are in force and why. That is on the
first tab now, in the squares themselves and in their tooltips,
which say it for all five halls at once rather than in one sentence
on a page nobody had open.


=================================================================
8. THE SETTINGS TAB
=================================================================

How the first tab LOOKS: where the five squares are, and how the
list under them is written. Called "Font" until 1.10.2026, when it
took over the arrangement of the squares as well.

Nothing on this tab reads the machine and nothing on it can change
a limit.

8.1 FIT EVERYTHING TO THE WINDOW

    The window has no smallest size and no biggest (1.10.2026,
    "úplně bez minima"); make it whatever size suits the screen it
    is on, and this works the rest out.

    Ticked, the program solves for four numbers — the height of the
    band, the size of the list's text, the size of the verdict
    square and the size of the word inside a hall square — so that
    the squares and the rows fill the window with nothing left
    over. "User si nastaví velikost okna, pak klikne na fit
    everything to the window a script naškáluje 'tabulku' s textem
    a haly tak, aby se to tam vlezlo bez nějakých větších mezer."

    AND IT WRITES THE FOUR NUMBERS INTO THE BOXES. Whatever the
    Settings tab says is what is on screen, always. The first
    version of this kept a scaling factor inside the drawing code
    and left the boxes alone, so "Height of the band" read 400 px
    over a band 253 px tall — "zatím to absolutně neodpovídá".

    The verdict square comes out exactly one and a half times the
    text beside it. That ratio is fixed and there is no box for it:
    it is close to the pair the program has always started with
    (19 px of text, 30 px of square), and it is what was asked for.

    TYPING IN ONE OF THOSE FOUR BOXES SWITCHES THE FIT OFF, and
    your number applies from then on. It has to work that way round
    — otherwise the next time the window changed size the fit would
    work the number out again and yours would be gone. Switch the
    fit back on and it takes over; switch it off again and the
    numbers you typed come back. They are remembered in the
    settings file, so they survive closing the program.

    What the fit cannot do is make a hall square stop being square.
    On a window that is wide and not tall, five squares in a row
    run out of width long before they run out of height, and the
    band cannot grow past that without putting a strip of nothing
    above and below them — which is the thing this was all for. So
    on such a window there is room left under the last row, and not
    one pixel of it could have been used by anything. That is the
    honest consequence of "čtverečky zůstanou čtverce, pás se
    zkrátí".

    THE SQUARES FIRST, THE TEXT ALWAYS INSIDE (12.10.2026). The
    squares are as big as they can be while the list still fits
    beside them (one square) or under them (five) at 19 px of text.
    Only on a window too small for even that does the text go
    smaller, and only at 8 px can anything run past the edge — "co
    největší tak, aby se tam vlezl i text.. pokud se už text
    nevleze, tak až teprve tehdy se bude moct text přepisovat přes
    hranu". In the one-square mode the text used to come first and
    the square was held to a third of the width; on a 1920 x 1080
    window it now takes the whole height.

    WITH THE FIT OFF, your numbers apply, but a text too big for the
    window is DRAWN smaller (the verdict square keeps its proportion
    to it), so the list never runs past the window's edge. The
    numbers in the boxes and in the file stay as you typed them.

8.2 THE BOARD

    The white rectangle is a scale model of the band the squares
    live in on the first tab. Not just its SHAPE — its real SIZE in
    pixels, which is what the arrangement is worked out for, so the
    squares here are arranged exactly the way the first tab will
    arrange them and the word inside them is the model's own size.
    It used to arrange them at the board's own size instead, and a
    970 px band modelled 478 px wide showed 88 px squares where the
    real ones are 186: a different arrangement altogether, with the
    word running off the sides. Reported 1.10.2026 — "neodpovídá
    ... stejně tak dané texty".

    Arrange them with the mouse:

      drag a square       move it
      drag an edge        make it wider or taller
      drag a corner       both at once

    Squares may overlap, sit in two rows, be of five different
    sizes — there is no grid and no cell. What there is, is a
    magnet: an edge within a fiftieth of the band of another
    square's edge, or of the band's own middle, jumps onto it and a
    green line shows what it jumped to. If that is in the way,
    waggle the pointer during the drag and the magnet lets go for
    the rest of that drag. The same board, the same magnet and the
    same waggle as Image Slider's camera layout.

    The first drag takes the arrangement OFF AUTOMATIC and it then
    stays exactly where it was put. "Arrange them again" is the way
    back to five equal squares in one centred row, which is what a
    fresh install does.

    The rectangles are stored as FRACTIONS of the band, so dragging
    the window wider keeps the arrangement's proportions instead of
    piling the squares up on the left.

      Height of the band    1 to 2000 px, 150 to begin with

    That height is room the list does not get, which is the one
    real trade on this tab. It is an upper limit. If the window is
    too short for the number, the band takes what there is; and in
    the even row it is never placed taller than the squares' own
    side, because a taller band was nothing but an empty strip
    round the squares — the window was made taller and the gap grew
    while the list stayed hidden (1.10.2026). That room goes to the
    list.

    The board itself is as wide as its box and as tall as the
    band's shape makes it, so it is a model at about 1:1 and its
    squares are the Overview's size. Held at 90 px tall it was a
    quarter-size model and looked nothing like the first tab.

      Write SHOOTING / idle inside the squares
                                 on or off, on to begin with
      Its size                   6 to 400 px, 11 to begin with

    Off, and a square carries its hall's name and nothing else.
    Nothing is lost that matters: the colour is what answers "are
    we shooting into it and is it right". The word only names the
    reason, and the tooltip still gives it in full.

    A square too small for its own lettering shows no lettering
    rather than lettering cut in half.

8.3 THE WINDOW

    "Maximise to this size" with a Width, a Height, From the left
    and From the top: ticked, the Maximise button makes the window
    that size at that place. The place is counted from the top left
    of the free area (screen minus taskbar) of whichever screen it
    is maximised on, so it means the same on any screen. The size
    is first cut to that free area and the window is then slid back
    in, so it never hangs over an edge. "Use the current size and
    place" fills all four boxes.

    The window really is maximised — Windows is told this size as
    the maximised size — so the restore button and Restore work as
    usual. The size is the window as seen, frame and title bar
    included, and is held to the monitor's work area. Stored as
    "window_max" (on, w, h, x, y) in the settings file.

    Changed while the window is maximised, it is maximised again to
    the new size a moment after the last change — once, not at every
    step of the box.

    No number box or drop-down on this tab answers the mouse wheel
    until it has been clicked: rolling the wheel over one on the way
    down scrolls the page and changes nothing.

8.4 THE LIST

    It is not a number buried in the program: the list is read from
    across the room, and how big "readable" is depends on the
    screen, the room and the person in it.

      Typeface         every font installed on this PC
      Text size        8 to 500 px, 19 to begin with
      Bold             on or off, on to begin with
      Verdict square   8 to 750 px, 30 to begin with

    Five hundred pixels so that the list can be set for a
    wall-sized screen — "zvyš maximum fontu třeba až na 500 nebo
    něco.. prostě do doby, kde to nebude dělat problémy, ale dá se
    to nastavit i na to největší okno". Eight at the other end,
    because the window may now be tiny and a floor of ten would be
    the thing stopping a small window from fitting its own list.
    750 is one and a half times 500, so a value the fit works out
    can never be clipped by its own bound.

    Under the four boxes are three example rows — a tick, a cross
    and a grey dash, with one deliberately long name, because that
    is the one that decides how wide the name column ends up. They
    are the same row the first tab draws, AT THE SIZE THEY WILL
    REALLY BE, with the numbers written exactly the way the real
    readings are, so the energy reads to hundredths here too and
    fs² is lifted here too. They were drawn smaller for a while,
    capped and with the proportions kept, and that was the wrong
    trade: "v záložce nastavení neodpovídá ... stejně tak dané
    texty". A preview that is not the real size is not a preview. A
    big setting makes this box tall and the page scrolls.

    A change is applied at once and written to the settings file a
    second later, like every other setting here. "Back to the
    defaults" puts the four list values back; "Arrange them again"
    does the same for the squares.

    The shot counter's settings are NOT on this tab: they sit in
    its own table at the bottom of the Limits tab (5.10.2026) — see
    6.2.

8.5 IN THE SETTINGS FILE

    The values live in the settings file under "look" (the list),
    "halls" (the squares, the fit, and "manual" — the sizes you
    last typed) and "shots" (the shot counter's "reset_s",
    "size_px" and "bold"). A file edited by hand is repaired on the
    way in: a number outside its bounds is pulled back to them, an
    empty typeface becomes Segoe UI, a rectangle hanging off the
    side of the band is pulled back inside it, and an arrangement
    that does not place all five halls is dropped in favour of the
    even row. It is not possible to make the first tab unreadable
    and then have no way of putting it right.

8.6 SAVED SETTINGS (12.10.2026)

    At the top of the tab. Settings saved under a name — one set per
    operator, say — so that whoever is at the controls picks theirs
    and everything changes at once: "dropdown menu těch nastavení,
    ze kterých si vyberu, klik na import a voala.. všechny nastavení
    se změnili".

    The sets are one file on the scratch share, settings_sets.json
    beside dm_setups.json, so every PC sees the same list. It is read
    and written in the background; with the share out of reach the
    box says so and nothing else is affected.

      Load          the ticked parts of the picked set replace this
                    PC's settings, and every tab shows them at once
      Save          this PC's settings (the ticked parts) over the
                    picked set
      Save as new   the same, as a new set under the name typed in
                    the Name box; a name already taken is refused
      Rename        the picked set gets the typed name
      Delete        click twice within four seconds; gone for every
                    PC

    The parts, each a tick box:

      Channels and limits     the list of rows (formulas, Show,
                              Alarm, names, units, every hall's Min /
                              Max, scan limits, the order), the gate
                              valve channels, Scanning, the hall the
                              Limits tab shows
      Look (text, squares)    "look" and "halls" — typeface, sizes,
                              bold, one square or five, their
                              arrangement, the fit
      Shot counter            its reset time, size and bold
      DM reading              the Remote Desktop title and Look every
      Window size and place   the Maximise size and place, and where
                              the window is (unticked to begin with:
                              that is a matter of the PC's screen)

    A set saved with some parts unticked holds only the others; the
    drop-down's tooltip says what each set holds. Loading a part a
    set does not hold leaves that part as it is.

    NEVER IN A SET: the DM writer switch. It says which PC reads the
    mirror, and loading somebody's set on a second PC must not make
    two of them. The saved DM set-ups and their High / Low power
    ticks are on the share already and are the same on every PC.

    The file is read again before every change and only the one set
    is changed, so a set another PC saved a moment ago is never lost;
    a file that cannot be read is never overwritten.


=================================================================
9. WHAT IT READS, AND HOW OFTEN
=================================================================

The archiver, the same one Announcer, Image Tools and CSS Logger
read:

  https://10.78.0.57:8443/api/1.0/cpva

Channels that archiver does not have are read from the archives that
do: the L3 beam transport (L3BT) and the facility (FCT) archives on
10.78.0.64 (twin .65). The search in the Limits tab finds their
channels too.

If that archiver cannot be reached at all, the same readings are
taken from its backup, the L3 proxy on 10.78.0.64 (or its twin
.65), which serves the same archive. The program then stays on the
backup for a minute before trying the archiver again. Nothing on
screen changes; the log line counts it as "backup=". A slow archiver
is not a reason to switch: the backup reads the same data just as
slowly.

One pass a second reads one channel per gate valve — five — and
every watched channel, side by side over eight keep-alive
connections. The valves are asked for first, so if a slow pass has
to be cut short the squares are the last thing to go missing. A
channel watched twice is read once, and a formula row is not read
at all: the rows it works from are on the list in their own right.

A valve whose open switch is down costs one channel more, its
closed switch, for as long as that lasts.

Nothing is ever written to the machine. The program only reads.

A pass that does not come back cannot be cancelled, so after a
while it is written off, written into the log and counted. A
program quietly showing the last good numbers for ever is the
failure that looks most like success.


=================================================================
10. WHERE THINGS ARE KEPT
=================================================================

  %APPDATA%\LaserSettings\laser_settings.json
      the channels, the limits, the arrangement of the squares and
      the list's look
  %LOCALAPPDATA%\Laser settings\laser_settings_crash.log
      the log (section 7)

The settings are written one second after the last change and never
on close — a program is not reliably told it is quitting. They are
written to a temporary file and renamed, so a half-written file
cannot cost you every limit you set.

The four sizes the FIT works out are not saved on every drag of the
window's edge. They do not need to be: they are worked out again
from the window the moment the program opens, and writing them a
hundred times while a window is being dragged is how a freak size
ends up persisted. The window's own size and position are saved,
and so are the sizes you typed yourself.

They live under APPDATA rather than beside the program because they
are your own work and must survive a new version being copied out
over the old one.

The crash log holds what happened, including deaths inside Qt
itself that leave no Python traceback.


=================================================================
11. IF SOMETHING LOOKS WRONG
=================================================================

  "Everything is grey"
      Most often none of the values has a Min or a Max yet. Two
      empty limit boxes are deliberately not a tick: there is
      nothing to judge against, so the row says so with a dash.
      Otherwise: nothing is being read, or no hall's limits are in
      force. Hover a square — it says which of the two it is and
      names the channel it read.

  "A row is missing from the first tab"
      Its Show is unticked. Show is one switch, so it does not
      matter which hall you look at: untick it anywhere and the row
      is gone everywhere. It is still on the Limits tab, still
      showing its number.

  "A square has moved, or is the wrong size"
      The arrangement is saved, so a stray drag on the Settings
      tab's board is remembered. That is by design; "Arrange them
      again" on that tab is the way back.

  "The sizes keep changing on their own"
      "Fit everything to the window" is ticked, which is what it
      does: every time the window changes size the four numbers are
      worked out again. Untick it and your own numbers come back.

  "The text will not get any bigger, and there is room underneath"
      A row is already as wide as the window. The text cannot grow
      without the longest name or the longest number running off
      the side, and the band cannot grow because its squares would
      stop being square. Make the window narrower and taller, or
      untick the fit and set the sizes yourself.

  "A square stays pale grey"
      That valve does not publish yet, its channel is not set, or
      the archiver does not know the name. Hover over the square;
      it says which, and names the channel. For E2 to E5 the usual
      reason is the L3BT MSS window: it is closed, minimised, or
      its title is not exactly "L3BT MSS". Restore it and the
      squares come back within a second.

  "A formula row shows a dash"
      Hover its box on the Limits tab. Either the formula itself is
      wrong, in which case the box is red and it says what is
      wrong, or one of the rows it works from has no number of its
      own.

  "Reading now is empty for a row I made with Add formula"
      Look at what is in its Channel or formula box. If it is the
      sum itself with no "= " in front of it, that sum went in as a
      CHANNEL NAME and the program has been asking the archiver for
      a channel by that name. Older versions could do this; this
      one cannot, and it puts any such row right the first time it
      opens the file.

  "A red exclamation mark appeared beside the halls"
      Nothing at all has been read for five minutes. The archiver
      is unreachable from this PC.

  "A value is right but shows a cross"
      Check whose limits are in force. Hovering the row lists all
      five halls' limits with that one marked. It may not be the
      hall you were thinking of: while SGV502 to SGV505 are silent,
      L3's are the ones in force.

  "DM settings says Unknown"
      The line under it says why. Most often the Remote Desktop
      window on OPR1 is minimised: restore it (it may stay behind
      other windows). Otherwise it is not open, or Mirror Control
      Window's Applied voltages grid (the left one) is covered by
      another window inside the remote desktop.

  "DM settings says No fresh reading"
      OPR1 has stopped writing: Laser settings is not running there,
      or DM writer has been switched off, or OPR1 cannot reach the
      scratch share.


=================================================================
12. THE DEFORMABLE MIRROR — DM SETTINGS AND DM WRITER
=================================================================

Added 8.10.2026. The deformable mirror is set in "Mirror Control
Window" on a PC that has no network share and no internet. The
only way to it is the Remote Desktop window that OPR1 keeps open
(its title starts with 192.168.1.107). So OPR1 reads the screen.

12.1 WHAT IS READ

Mirror Control Window shows two 6 x 6 grids. The LEFT-hand one,
Applied voltages, is the one read: it is what is really on the
mirror. The right-hand one, Set-up, is only what the operator is
about to apply, and is never judged. (Until 12.10.2026 it was the
other way round — "se musí kontrolovat to nastavení vlevo!! ne
vpravo".) Since 14.10.2026 the Set-up grid is read as well, but
ONLY so a set-up can be saved from it before it is applied ("Save
the Set-up grid", 12.3); OPR1 writes it into the state file beside
the Applied grid. When it is covered nothing else changes. The two
grids of one set-up differ by about 5 levels, well inside the
tolerance below, so a set-up saved from the right grid is
recognised on the left one once applied. The numbers 0 to 35 in it are only the
electrode numbers and are the same in every set-up; each
electrode's voltage is shown only as the colour of its cell — blue
-150 V, white 0, red +150 V. No voltage is worked out from it. The
36 colours are compared with set-ups saved under a name, and the
answer is the name of the one that matches.

Two set-ups count as the same when no electrode's colour differs by
more than a hair (15 levels out of 255 in any of red, green and
blue — about 9 V). Remote Desktop sends the picture slightly lossy:
on OPR1 the Low power grid came through up to 7 levels off. The
low- and high-power set-ups of 8.10.2026 differ by up to 49 levels,
in 17 of the 36 electrodes by more than 15.

The grid is found wherever it is: the Remote Desktop window can be
any size, Mirror Control Window can be anywhere on the remote
screen, and other windows (SID4) may cover parts of it. Only the
Applied voltages grid itself has to be visible. Its colour scale is
on its LEFT; the Set-up grid's is on its right, so the two are never
confused.

12.2 DM WRITER — ON OPR1 ONLY

Tick "Read the Applied voltages grid from the Remote Desktop window
and write it to the scratch share" on OPR1, and on no other PC. The setting is
kept per PC. Every few seconds — "Look every", 1 to 10 s, 5 s at
first — the window is copied into memory; every other PC then reads
the state file just as often. It
is never activated, moved, resized or clicked, and nothing is typed
into it, so it is used exactly as before. The tab shows the last
look, how long it took in milliseconds, and the grid it read.

"The window's title contains" is how the window is found: the
address of the mirror PC, 192.168.1.107.

TWO WAYS OF TAKING THE PICTURE (9.10.2026). First the window is
drawn into memory, which works even under other windows. When the
grid is not in that picture, the window's rectangle is copied off
the screen instead — the way a screenshot is taken — which needs
the window uncovered. The tab then says "Copied off the screen".
When neither picture holds the grid, the message says what each
picture did hold (all black, no colour scale, ...), and both
pictures are saved once to the share as dm_look_<PC>_window.png and
dm_look_<PC>_screen.png (overwritten, never piling up). "Save a
picture of the window" saves the next look's pictures on demand.

A MINIMISED WINDOW CANNOT BE READ. Windows does not draw it. While
it is minimised, OPR1 writes "unknown" with no colours, so no PC
goes on showing an old set-up as if it were current. Covered by
other windows is fine.

12.3 DM SETTINGS — ON EVERY PC

A big banner:

    the set-up's name     green — one of the saved set-ups
    Not a saved set-up    yellow — read, but nothing saved matches;
                          the closest one is named, with how many
                          electrodes differ
    Unknown               grey — OPR1 could not read the grid; the
                          line under it says why
    No fresh reading      grey — nothing written for 30 seconds
    Not read yet          grey — no PC has ever written

Under it two grids: the one on the mirror now, and a saved set-up —
the one it matches, otherwise the closest one, or whichever you pick
in the list. Every electrode that differs between the two has a
thick outline in both.

The list of saved set-ups is one file on the share, so every PC sees
the same names.

    Save the grid on the mirror now   saves the current colours
                    under the name typed beside it. A name already on
                    the list (capitals and spaces do not count)
                    REPLACES that set-up's grid after a second click.
                    Refused when these colours are already saved
                    under another name.
    Save the Set-up grid   the same, from the RIGHT-hand Set-up
                    grid: a set-up prepared but not yet applied.
                    Greyed out while OPR1 has not read that grid
                    (covered, or OPR1 runs a version older than
                    14.10.2026). The Overview still checks the left
                    grid only.
    Replace with the grid on the mirror now   gives the one picked
                    set-up the grid on the mirror now; click it
                    twice. Name and High / Low tick stay.
    Undo replace    gives the picked set-up back the grid it had
                    before its last replace; click it twice. One step
                    back is kept.
    Rename          gives the one picked set-up the typed name.
    Restore built-in   puts the built-in Low power / High power back
                    on the list after they were deleted or renamed.
    Delete          deletes the picked set-ups; click it twice.

A replaced set-up shows "replaced <date time> (<PC>)" in the Saved
column.

"Low power" and "High power" come BUILT IN, on every PC, marked
"built in". Their colours are the Applied voltages grid (re-read
12.10.2026; up to 5 levels off the Set-up grid they were taken from
before) of the two screenshots of 8.10.2026 (High power 7:27, Low
power 7:31 — swapped on 9.10.2026, when OPR1 showed the first guess
was the wrong way round).

Since 21.10.2026 they are set-ups like any other (that day a new
Low power could not be saved over the old one, and a third set-up
had to be made):
    - replaced: the shared file holds the new grid under the
      built-in name, and every PC uses it;
    - deleted: the shared file marks the name as removed, and it
      leaves the list on every PC until Restore built-in;
    - renamed (since 12.10.2026): the shared file holds the
      built-in colours under the new name, marked "built in, was
      "Low power"". A built-in name nobody carries any more can be
      given to another set-up.

IMPORTANT: a PC running a version older than 21.10.2026 does not
understand a replaced or deleted built-in set-up: it still shows
the old built-in grid, and if somebody saves, ticks or deletes
anything on such a PC, it writes the file back without the
replacement. Update every PC first.

A yellow line under the list warns when two or more set-ups are
ticked for the same power: the Overview accepts the mirror on any of
them, so an old grid left ticked next to its replacement still
counts as OK.

The list has two tick columns, High power and Low power (added
9.10.2026). They say which set-ups the Overview accepts:

    High power ticked   right while high-power shots arrive
    Low power ticked    right while low-power shots arrive
    neither             right for no shot — a cross on any shot

Tick as many set-ups in each column as you like; the mirror on any
one of them is OK. Ticking one column unticks the other on that
row. "Low power" and "High power" start ticked as their names say
and can be unticked like any other. The ticks are kept in
dm_setups.json, so every PC judges alike. A newly saved set-up is
ticked for neither.

12.4 DM SETTINGS ON THE OVERVIEW

The Limits tab has a row called "DM settings" (like the shot
counter, it is always on the list and cannot be removed). It starts
with Show unticked. Tick Show and the Overview gets a line:

    DM settings:  High power   [tick]

It says the name of the set-up on the mirror — or Unknown, No fresh
reading or Not a saved set-up. Its square is judged by SBW4's
energy, because low-power shooting cannot be seen any other way:

    a new SBW4 shot over 0.05 J        the set-up on the mirror
                                       must have High power ticked
    new SBW4 shots of 0.05 J or less,  the set-up on the mirror
    5 or more within 10 seconds        must have Low power ticked

A TRIGGER IS NOT LOW POWER (15.10.2026). A trigger shot sent in the
middle of high-power shooting — on request or by mistake — comes
through SBW4 as one or two shots of about 0.02 J, far apart. Low
power shooting is a steady stream of them (the laser fires at
3.3 Hz; the archive keeps about one a second). So one or two shots
under 0.05 J change nothing: the line stays on High power, the hall
stays SHOOTING, and nothing blinks. Five or more within 10 seconds
are low power, recognised within a few seconds of it starting.
Measured on SBW4's archive of 5.–15.10.2026: replayed through the
new rule, every trigger inside a high-power sequence is ignored and
every start of low-power shooting is still caught.

Low-power shooting may leave SBW4 silent instead of under 0.05 J,
so it is also recognised by the SAST1 shutter (added 9.10.2026,
channel L3-SHUT_SAST1_Shutter_LT6_SP-MSS:State, 1 = open): a hall's
valve open, the shutter open, and no SBW4 shot over 0.05 J in the
last 2 minutes means low power is being shot into that hall, and
the set-up must be ticked Low power. Within those 2 minutes of a
high-power shot the open shutter is still taken as the same
high-power sequence and contradicts neither set-up. The shutter is
read only while the DM settings line is shown.

A grid that is no saved set-up, or a set-up ticked for neither, is
a cross for any new shot. With no new shot it is a grey dash.
Unknown and No fresh reading are always a grey dash.

A shot counts for 15 seconds after it arrived (the same clock as
SHOOTING). A contradiction is a red cross, and the halls whose
limits are in force blink, as for any row out of range — also while
the laser is not counted as firing, because a low-energy shot on
High power is exactly that case. No new shot: a tick. Alarm
unticked: a grey dash, nothing blinks.

On the Limits tab the row has no channel, unit or limits to type;
"Reading now" shows the set-up.

12.5 THE FILES

    <scratch>\Software\Laser settings\shared\dm_state.json
        what OPR1 saw last: when, which PC, whether it could read
        the grid, the matching set-up and the 36 colours
    <scratch>\Software\Laser settings\shared\dm_setups.json
        the saved set-ups

<scratch> is \\hapls-share.cs.eli-beams.eu\scratch or
\\hapls-share.lcs.local\scratch, whichever this PC can reach.

-----------------------------------------------------------------
