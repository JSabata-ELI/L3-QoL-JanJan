Diagnostic — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Diagnostic_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Watches the machine values that matter, shows them as a table and
  a graph, compares them against limits, and tells somebody when one
  goes wrong — by Teams message, e-mail or Webex chat. You can also
  ask it questions from the Webex chat and it answers with numbers
  and graphs.

  In practice the PV Monitor tab is the whole program. The History
  tab is dormant: the file it needs is no longer produced by
  anything.


THE IMPORTANT DISTINCTION: READING VERSUS ALERTING

  Values are read from the moment the program opens, always. The
  table, the values, the times and the graph are live whether or not
  anything is armed.

  "Start monitoring" arms only the alerting: comparing against
  limits, sending messages, and the watchdog. While it is off, the
  state cell still shows the real severity but is painted dark red
  with a pause mark, and the status bar says "stopped (reading
  only)". The number is true; nobody is watching it.


HOW IT DECIDES SOMETHING IS WRONG

  Plain limits — a warning level and an alarm level, high and low.
  No dead bands and no hysteresis, deliberately. Instead:

    a reading must break the limit several times in a row before it
      counts (so one bad sample is not an alarm),
    it must stay broken for a settling period,
    while it stays broken you get reminders, and the reminders come
      faster or slower depending on which way the value is heading.

  Recovery is announced too.

  There are four other kinds of fault it can find:

    no data at all      every value stops answering — one message
                        when it happens, one when it comes back.
    bad data            a reading outside the range that sensor
                        could physically produce. Shown differently
                        from missing data, because it means the
                        sensor is wrong rather than absent.
    not updating        the archive keeps answering with fresh
                        timestamps and a plausible number, but the
                        number never moves. A dead sensor or a stuck
                        system. This one is worth understanding —
                        see below.
    not refreshed       this program itself stopped reading. Nothing
                        on screen is wrong, it is simply old — see
                        below.


"NOT UPDATING"

  The quietest way a value can fail: it still arrives, it still
  looks reasonable, and it is days old. Nothing else notices, because
  it sits inside its limits and looks like a beautifully steady
  reading.

  So each reading is also asked whether it is still alive: has the
  value been exactly the same for longer than two hours, and has the
  newest stored sample stopped advancing. Exactly the same, on
  purpose — a working sensor's noise always moves the last digit;
  only a stuck one repeats it.

  Such a value is shown as "not updating" on a dark teal background,
  and that outranks warning and alarm, because a limit verdict on a
  dead reading means nothing. Values that genuinely hold still —
  switch positions, setpoints, enable flags — can be excused
  individually.


"NOT REFRESHED"

  The one above is about a value being dead. This one is about the
  program being stuck: a reading that goes out and never comes back
  leaves everything on screen exactly as it was, and nothing about a
  screen full of last-known values says they are last-known. Asked
  from a phone, it would happily keep answering "ok" for hours.

  So the program watches its own pulse. If nothing has been read for
  a few minutes — long enough that its own attempt to unstick itself
  has already been tried and failed — then:

    the status bar leads with NOT REFRESHED and the time of the last
      reading that worked,
    a red band appears above the graph, because a curve that simply
      stops looks exactly like a steady value,
    every State cell reads "not refreshed" instead of "ok",
    the bot says it, unasked, in the chat room, and says it again
      when the readings come back, and
    every answer it gives until then carries the warning at the top
      and says when the values were last read.

  The chat's own graphs are checked the same way: if the newest point
  falls short of the end of the window, the picture is stamped
  "NOT CURRENT" and the message repeats it in words. A graph of a
  past window — "yesterday 7-18" — is never stamped, since a curve
  ending where the window ends is what was asked for.

  This one cannot be switched off. Everything else the program says
  is only worth as much as this.


THE GRAPH

  The recent history of the values you selected. Beside it, a list of
  the drawn curves: hover one and that curve is drawn thick while the
  others fade, so a single trace can be picked out of an overlay
  without reading colours. If you prefer a legend inside the plot,
  the right-click menu offers one, and the two are alternatives.


THE WEBEX BOT

  In the room set up for it, the bot answers one-line commands:
  the state of everything, only what is alarming, the configured
  list, and a graph of any values over any period. It can also arm
  and disarm alerting, per value or as a whole.

  ONE THING NOBODY GUESSES: in a room with more than two people,
  Webex shows a bot only the messages that @mention it. A command
  typed without the tag never reaches the program at all — which from
  the room looks exactly like a dead bot.

      @Diagnostics /status
      @Diagnostics /plot Chiller 1; yesterday 7-18

  In a one-to-one chat with the bot the tag is not needed. Type
  /help in the room for the whole list.


LEFT RUNNING FOR WEEKS

  The program is meant to stay open, so it reports its own memory
  use. The right-hand end of the status bar shows two figures:

    mem   how much memory Windows has promised this program, and in
          brackets how much of that is really in RAM.
    PC    how much of the whole computer's memory promise has been
          used up, out of the total it can promise (RAM plus page
          file).

  The second figure is the one that ends a long uptime. When it
  reaches its limit the computer cannot start anything new — new
  programs fail, windows come up empty — even though Task Manager
  still shows free RAM. A warning mark appears in the status bar past
  90 %, and the answer is to close what is not needed or restart the
  computer.

  The Log tab writes both figures every half hour, together with how
  much this program has grown since it was opened. That is how you
  tell a program that simply needs its memory (the figure settles)
  from one that is losing it (the figure keeps climbing). Those lines
  are also proof it is still running through a quiet day.


WHAT IS SHARED AND WHAT IS NOT

  The list of watched values, their limits and the monitoring
  settings live in one file on the scratch share, so every copy of
  the program agrees and nothing has to be set up twice.

  The notification accounts are baked into the build, so a fresh
  copy alerts through the same accounts with no setup.

  Only the share location and your personal graph preferences are
  kept on your own computer.

  A computer that cannot reach the share still starts, with the last
  known list, and will not publish — so it cannot overwrite
  everybody's settings with a stale copy.

-----------------------------------------------------------------
