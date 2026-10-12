Laser settings — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Laser settings_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Is the machine set right for the hall we are shooting into?

  The window is any size you like. There is no smallest and no
  biggest, and whatever size you leave it is the size it opens at
  next time. With "Fit everything to the window" ticked on the
  Settings tab, the squares and the list grow and shrink with it —
  see that tab below.


THE FIRST TAB — OVERVIEW

  Five big squares across the top: L3, E2, E3, E4, E5. L3 is
  internal shooting — on the Limits tab and in the settings it is
  still called Int.

    white           we are not shooting into that hall
    green           we are shooting into it, and everything is
                    set right
    red / white     we are shooting into it and something is
                    WRONG — the square blinks red and white until
                    it is fixed
    pale grey       we cannot tell. The word in the square says
                    why.

  Inside each square is its hall's name and, under it, what is
  happening:

    SHOOTING          the beam is going in there
    OUT OF RANGE      it is, and something watched is outside the
                      limits
    WATCHING          see "WHILE ONLY INT CAN ANSWER" below
    idle              nobody is shooting into it
    waiting for hall  L3 only: SGV501 is open, but no hall's gate
                      valve is open (or none of them can be read)
                      — the beam has nowhere to go yet
    not read          its gate valve could not be read
    no channel        no gate valve channel has been set for it

  White with a darker edge and the word WATCHING means something
  else: nobody is shooting into that hall, but its limits are the
  ones being checked.

  How big the squares are and where they sit — and whether that
  word is written at all — is yours to set on the Settings tab.

  ONE SQUARE INSTEAD OF FIVE. Tick "One square that follows the
  hall being shot into" on the Settings tab and there is a single
  square on the left with the list beside it. Its name changes by
  itself to the hall the beam is going into — L3 whenever it is
  going into none of the others — and its colour and word mean
  exactly what they mean on the five.

  Whether a hall is being shot into is read off its safety gate
  valve. L3 is internal shooting, so the beam goes there while
  SGV501 is CLOSED; for the experimental halls the valve is open
  when the beam goes in. Whenever one valve is open the others are
  shut, so nobody is asked about this — it is how the machine is
  built.

  So the L3 square says:

    SGV501 closed                      SHOOTING or idle, by SBW4's
                                       energy
    SGV501 open, a hall's valve open   idle — that hall's square
                                       lights up
    SGV501 open, no hall's valve open  waiting for hall

  THE VALVE ONLY SAYS WHERE THE BEAM WOULD GO. Whether the laser is
  really firing is read off SBW4's energy: a hall is green SHOOTING
  (or blinks red) only while its valve points at it AND SBW4 reads
  at least 0.1 J. Under that, the square is white "idle" — the rows
  are still checked and keep their ticks and crosses. SHOOTING
  lasts 15 seconds after the last shot: when no new energy has come
  in for 15 s the square goes back to "idle", and the next shot
  makes it SHOOTING again. Just after the program starts the
  squares stay "idle" until the first new shot comes in. SBW4 is
  read under both its names (HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy
  and L3-SBW4-PM311:Energy); the newer sample counts. Hover the
  square to see the energy it went by.

    Int   SGV501      live
    E2    SGV502      \
    E3    SGV503       |  the names are already filled in, but
    E4    SGV504       |  these four valves do not publish yet.
    E5    SGV505      /   Until they do, they are read off the
                          L3BT MSS window — see below.

  Under the squares is the list of watched values, IN THE MIDDLE of
  the window. One line each, written straight onto the background,
  and nothing on it but the name, the number, and a little square:

             Front end delay:  12     ns   [tick]
         Amplifier 2 voltage:  31.8   kV   [cross]
                         GDD: -12000  fs²  [tick]

  The names end on one line. After the colon there is a space and
  room for a minus sign, so all the numbers start on one line. The
  units stand under each other, and the little squares stand in one
  column right after them.

  Every name and every number is shown WHOLE — nothing is ever cut
  short. If a name is too long for the window, the writing gets
  smaller until it fits, not the other way round.

  Energies read to hundredths: 17.53 J, not 17.5266 J. And a unit
  typed as fs^2 is written fs² on screen, with the 2 raised.

    green with a white tick   shooting, and the value is inside the
                              limits
    red with a black cross    shooting, and it is outside them
    orange, tick or cross     read fine, but nothing is being shot,
                              so it is not checked — the tick or
                              cross says where the value would stand
    white with a dash         read fine, but there is nothing to
                              judge it by (no Min / Max, or Alarm
                              unticked)
    grey with a dash          the value cannot be read — archiver or
                              network error, nothing archived, or
                              nothing new for 30 s

  The square is the only mark. A row that is out of range gets no
  colour of its own — the red cross is what says so. Only a RED
  cross makes a hall blink; an orange one never does.

  Hover any row and it tells you the rest: the channel or the
  formula behind it, why the square is what it is, the limits of
  all five halls with the one in force marked, and what each colour
  of the square means.

  RENAMING: double-click a row and type its new name; Enter keeps
  it, Esc keeps the old one. The same name is in the Limits tab's
  "Name on the first tab" column, and can be typed there too.

  SCANNING: the "Scanning" tick box above the list, at the left. Tick
  it for a scan and every value that has Scan min / Scan max set on
  the Limits tab is checked against those instead of its hall's Min
  and Max; values with no scan limits keep their normal ones. The
  hall being shot into says SHOOTING + SCANNING (or OUT OF RANGE +
  SCANNING). Untick it when the scan is over — the normal limits
  apply at once. Example: Min / Max 24800 … 25000, Scan min / max
  24000 … 26000: 24500 is green while scanning and red the moment
  Scanning is unticked.

  How big the list is written, and in what, is set on the Settings
  tab.


READING THE VALVES OFF THE L3BT MSS WINDOW

  On L3-OPR03 the L3BT MSS window shows every valve's state in a
  little box under its name. While SGV502 to SGV505 do not publish,
  the program reads those four boxes off that window, once a
  second:

    O and a black square     the valve is OPEN — the beam goes in
                             there
    black square and C       the valve is CLOSED
    two crossed boxes        taken as closed

  The window may be on any monitor, anywhere, and other windows may
  lie on top of it. It must be OPEN, not minimised, and its title
  must be exactly "L3BT MSS". Hover a hall square: it says "Read
  off the L3BT MSS window" when the answer came from there.

  If the window is closed or minimised, or a box cannot be made
  out, those halls say "not read" and the program watches Int, as
  described below.

  The day the valves publish, their channels win and the window is
  not looked at any more. Nothing has to be switched off.


WHILE ONLY INT CAN ANSWER

  SGV502 to SGV505 do not publish yet. Int shoots while ITS valve
  is shut, so with the valve open no hall could say it was being
  shot into and nothing at all was being judged — the whole list
  was grey dashes.

  So: while none of those four valves can be read — not from their
  channels and not off the L3BT MSS window either — Int's limits
  are in force whatever Int's own valve says. The L3 square goes
  WHITE, not green, because nobody is really shooting into it — it
  says "waiting for hall" while SGV501 is open — but the rows are
  judged, and the square blinks red and white if something is out
  of range.

  The day the first of those four valves publishes, this stops on
  its own and the valves decide again. Hover a square and it says
  which it is.


THE SECOND TAB — LIMITS

  Pick a hall with the buttons at the top. Everything below then
  belongs to that hall.

  Gate valve — ONE channel, "PV for the valve". Press Find to
  search the archiver. Beside it is what that channel is reading
  right now:

    value=1 : open        value=0 : closed

  The word comes from the CHANNEL, not from the number. A channel
  whose name says _IN_OPEN_POSITION reads 1 while the valve is
  open; one that says _IN_CLOSED_POSITION reads 1 while it is shut,
  so there the same 1 means the opposite. Pick a channel whose name
  says neither and you get the number alone, because then nobody
  can say what it means.

  Hover the box: it says which valve position means we are shooting
  into this hall, and names the CLOSED channel. That second channel
  is read only while the first one cannot be, and its name is built
  into the program — it is changed by a new version, not here.

  What it MEANS for the hall — shooting, idle, limits in force — is
  the hall's big square on the first tab, which says it for all
  five halls at once.

  The buttons above the table:

    Add channel      search the archiver and put a channel on the
                     list — for every hall at once
    Add formula      a row that works its number out (see "A ROW
                     THAT WORKS ITS NUMBER OUT" below)
    Change channel   put a different channel name in the picked
                     row
    Remove           take the picked rows off the list, in every
                     hall. Several can be picked at once.

  The table — one row per watched value. Click a cell and type.

    Show                    watch this value — in EVERY hall
    Alarm                   report it when out of range. Untick it
                            and the number stays on the first tab,
                            its square is a white dash and no hall
                            blinks. Tick it again to watch it.
    Letter                 what a formula calls this row
    Channel or formula      the real channel name, or a sum
    Name on the first tab   what the first tab calls it
    Unit                    what it is measured in
    Min, Max                the range that counts as right for
                            THIS hall
    Scan min, Scan max      the range while Scanning is ticked on
                            the first tab — one pair for every hall.
                            Leave both empty and the row keeps Min
                            and Max while scanning.
    Reading now            what the machine is holding while you
                            type

  There is one list of channels shared by all five halls. Adding a
  channel adds it everywhere, and so does Show: untick it in E2 and
  the value stops being watched in Int and E5 as well, and the row
  leaves the first tab. Min and Max are the only things set per
  hall.

  Rows with Show unticked sit at the bottom of the table, under a
  grey "Not shown" line, so they are out of the way. Tick Show and
  the row moves back up.

  YOUR OWN ORDER: grab a row (any cell) and drag it up or down. The
  first tab shows the rows in this order. Drop a row under the "Not
  shown" line and its Show goes off; drag it back up and it is on
  again. The letters follow the new order, and every formula is
  rewritten so it keeps meaning the same rows.

  An empty Min or Max means that side is not limited. It does NOT
  mean zero. The limits themselves count as being in range:
  min <= value <= max.


COUNTING THE SHOTS

  The shot counter has a small table of its own at the very bottom
  of the Limits tab, under the "Not shown" rows. It is always on
  the list and cannot be removed — Remove passes it by. To hide it,
  untick its Show. On the Overview it is one line ABOVE the list,
  "Shot counter: 12", in smaller text and with no square. It counts
  the shots:

  - every time a row with Show ticked gets a NEW value, that is a
    shot — one row changing is enough, they do not all have to
    change;
  - the same shot showing up on several rows counts once, even when
    the rows change one after another;
  - after a while with no new value the number STAYS on screen, but
    the count starts again: the next shot shows 1. How long is
    "Start again from 1 after" in its table (10 s to start with,
    1 to 3600 s). Set it LONGER than the time between two shots:
    the laser at 0.1 Hz fires every 10 s, and with 10 s here every
    shot starts again from 1;
  - what was already there when the program started is not
    counted.

  Its table: Show (the line on the Overview at all), Letter, Name
  on the first tab, Start again from 1 after, Text size and Bold
  (how the line on the Overview is written), and Shots now. It has
  no Min and Max.


A ROW THAT WORKS ITS NUMBER OUT

  Press "Add formula" and a row appears at the bottom that has no
  channel of its own. Its box opens holding "= ". After that you
  type a sum over the LETTERS of other rows on the list:

    A * 0.749          three quarters of what row A reads
    (A + B) / 2        the two of them, averaged

  + - * / and brackets, and abs, min, max, round and math. It is
  watched, limited and judged exactly like a read channel.

  A decimal comma is fine — 0,749 is stored as 0.749. The one place
  it is not is inside brackets, because there a comma separates the
  arguments: write min(A, 0.749), never min(A, 0,749). The program
  cannot warn you about that one — min(1, 2) looks exactly the same
  and is perfectly correct — so it is the one thing to type
  carefully.

  The letters are the rows' positions, so they move as the list is
  edited — the box is rewritten for you and keeps meaning the same
  rows. A formula may use rows above or below it — only two
  formulas that use each other in a ring cannot work. One that
  cannot be worked out turns light red, and hovering it says
  exactly what is wrong with it.


THE THIRD TAB — SETTINGS

  How the first tab LOOKS. Nothing here reads the machine and
  nothing here can change a limit.

  Fit everything to the window

    Make the window whatever size you want — there is no smallest
    size and no biggest — and tick this. The program then works out
    how big the squares, the text and the verdict squares have to
    be to fill that window with nothing left over, and WRITES THOSE
    FOUR NUMBERS INTO THE BOXES BELOW. So whatever the boxes say is
    what is really on screen.

    The verdict square comes out one and a half times the text
    beside it.

    Type into any of those four boxes and this switches itself off
    and your own number applies instead. Switch it back on and it
    takes over again; switch it off and the numbers you typed come
    back — they are remembered, and they survive closing the
    program.

    One thing it cannot do: the hall squares stay SQUARE. On a
    window that is wide and not tall, five squares run out of width
    before they run out of height, and the room underneath the list
    is then room nothing could have used.

  The window

    Maximise to this size          on or off      (off to begin
                                                  with)
    Width, Height                  200 to 10000 px
    From the left, From the top    0 to 10000 px  (0 to begin
                                                  with)

    Ticked, the Maximise button in the title bar makes the window
    this size, at this place, instead of filling the screen. The
    place is counted from the top left corner of the screen (the
    part the taskbar leaves free). Restore goes back to the size it
    had.

    Easiest way: put the window where and how big you want it, then
    click "Use the current size and place" — it fills all four
    boxes. The window is never bigger than the screen and never
    hangs over its edge; if it would, it is moved back in.

  The hall squares

    One square that follows the hall being shot into
                                   on or off      (off)

    Ticked, the first tab shows one square with the list beside it,
    and the band's box is called "Size of the square" — it is that
    square's side. The board and "Arrange them again" are hidden
    then, as they are only for five.

    Unticked, the five squares across the top. The white board is a
    scale model of the strip the squares live in. Arrange them on
    it with the mouse:

      drag a square           move it
      drag an edge            make it wider or taller
      drag a corner           both at once

    The edges are magnetic: they jump to the other squares' edges
    and to the middle of the band, and a green line shows what they
    jumped to. If you want one in a place it keeps refusing, waggle
    the mouse during the drag and the magnet lets go for the rest
    of that drag.

    "Arrange them again" puts the five back in an even row.

      Height of the band   1 to 2000 px     (150 to begin with)

    That height is room the list underneath does not get, so it is
    the one trade to think about. It is the most the band may take:
    if the window is too short for it, the band gets what there is,
    and in the even row it is never taller than the squares
    themselves — anything more goes to the list, not to empty space
    round the squares.

    The board is drawn at the real size, so its squares are as big
    as the ones on the first tab.

      Write SHOOTING / idle inside the squares   on or off
      Its size                                   6 to 400 px

    Switch it off and the squares carry nothing but the hall's
    name. The colour still says everything: white idle, green
    shooting, blinking red wrong.

  The list

    It is read from across the room, and how big that has to be
    depends on the screen and on where you stand, so it is yours to
    set:

      Typeface         any font installed on this PC
      Text size        8 to 500 px          (19 to begin with)
      Bold             on or off            (on to begin with)
      Verdict square   8 to 750 px          (30 to begin with)

    Five hundred pixels is there so the list can be set for a
    wall-sized screen.

    Under the boxes are three example rows AT THE SIZE THEY WILL
    REALLY BE — the same row the first tab draws, with the same
    typeface, the same weight and the same numbers. A big setting
    simply makes this box tall, which is itself worth seeing before
    you rely on it.

    The board above them is the same: it is a scale model of the
    real strip, so the squares in it are arranged exactly the way
    the first tab will arrange them, and the word inside them is
    the right size for the model.

    A change applies at once and is saved a second later. "Back to
    the defaults" puts all four back.

  The shot counter's settings are not on this tab: they are in its
  own table at the bottom of the Limits tab (see COUNTING THE
  SHOTS).


THE DM SETTINGS TAB — WHICH SET-UP THE MIRROR IS ON

  The deformable mirror is set in "Mirror Control Window" on a PC
  that is reached only through the Remote Desktop window on OPR1.
  OPR1 reads the right-hand grid, Set-up, off that window every
  5 seconds, and this tab shows what it found, on every PC:

    the set-up's name   on green — the grid is one of the saved
                        set-ups (for example "High power")
    Not a saved set-up  on yellow — the grid was read, but no saved
                        set-up matches. The closest one is named,
                        with how many electrodes differ
    Unknown             grey — OPR1 could not read the grid. The
                        line under it says why, most often that the
                        Remote Desktop window is minimised
    No fresh reading    grey — OPR1 has not written anything for
                        half a minute

  The numbers 0 to 35 in the grid are the electrode numbers. What
  tells two set-ups apart is the COLOUR of each electrode (blue is
  negative voltage, white zero, red positive). No voltages are
  written anywhere.

  Under the name: the grid as it is on the mirror now, and beside it
  a saved set-up — the one it matches, the closest one, or the one
  you pick in the list. Electrodes that differ have a thick outline.

  SAVING A SET-UP. Type a name and click "Save the grid on the
  mirror now". The list is shared by every PC. Pick one set-up and
  click Rename to give it the name in the box. Pick one or more and
  click Delete twice to delete them. "Low power" and "High power"
  are built in: always on the list and never deleted, but they can
  be renamed like any other. A renamed one keeps its colours and its
  tick; deleting it brings back the built-in name.

  HIGH POWER AND LOW POWER TICKS. Each saved set-up has two tick
  boxes in the list: High power and Low power. Tick every set-up
  that is right for high-power shots under High power, and every
  one right for low-power shots under Low power — as many as you
  like. A set-up can be ticked for one of the two or for neither.
  "Low power" and "High power" start ticked as their names say,
  and can be unticked. The ticks are shared by every PC.

  ON THE OVERVIEW. Tick Show on the "DM settings" row of the Limits
  tab and the Overview shows "DM settings: High power" (the name of
  the set-up on the mirror) with a square. A cross, and the hall
  blinks, when a new SBW4 shot says otherwise: over 0.05 J while the
  mirror is on a set-up not ticked High power, or 0.05 J and less
  while on one not ticked Low power. Low power is also recognised
  by the SAST1 shutter: a hall's valve open, the shutter open and
  no SBW4 shot over 0.05 J for 15 seconds means low power is being
  shot. A grid that is no saved
  set-up, or one ticked for neither, is a cross for any new shot. A
  shot counts for 15 seconds.

  While the Remote Desktop window on OPR1 is minimised, nothing can
  be read — Windows does not draw a minimised window — and the tab
  says Unknown. Leave the window open; another window may cover it.


THE DM WRITER TAB — ONLY ON OPR1

  Tick "Read the Set-up grid from the Remote Desktop window and
  write it to the scratch share" on the PC that has the Remote
  Desktop window open (OPR1), and on no other. The window is found
  by the text in its title, 192.168.1.107. "Look every" sets how
  often (1 to 10 s, 5 s at first); every other PC follows the same
  pace. The window is never activated,
  moved or clicked: the program copies a picture of it in memory,
  which the tab says took so many milliseconds. The tab also shows
  the grid this PC read last.


WHAT IT READS

  The archiver, the same one Announcer, Image Tools and CSS Logger
  use. Every value is read once a second. Nothing is ever written
  to the machine — this program only looks.

  The deformable mirror's set-up comes from two files on the
  scratch share (Software\LaserSettings): dm_state.json, what OPR1
  saw last, and dm_setups.json, the saved set-ups.

  A value that has not been read for half a minute stops being
  judged and shows a dash. Old numbers are never shown as though
  they were current.


THE SETTINGS FILE AND THE LOG

    %APPDATA%\LaserSettings\laser_settings.json
    %LOCALAPPDATA%\Laser settings\laser_settings_crash.log

  The settings are written a second after each change and survive a
  new version being copied out. Everything the program has to say
  goes into the log file — every run, in order. There used to be an
  Info / Log tab showing the same lines on screen; it is gone, and
  the file is where they are read back from now.

-----------------------------------------------------------------
