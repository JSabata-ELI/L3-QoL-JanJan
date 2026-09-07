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


DIFFERENT LIMITS FOR DIFFERENT OPERATION

  A value can be perfectly fine at one shot rate and badly wrong at
  another, so a PV may carry several sets of limits with a rule
  saying when each applies — "high power on and 0.2 Hz", "high power
  on and 3.3 Hz". The set in force is picked automatically from the
  values of the PVs the rule names; the "Depends on" column shows
  which one won and lets you pin one by hand.

  The moment such a rule changes hands, the value is still where the
  old limits left it. A chiller switched on in the morning is the
  plainest case: its water is at 20 degrees, the limits that apply
  while it runs ask for eleven and a half, and it takes ten minutes
  of perfectly correct cooling to get there. So after every change
  of the limits in force, alerting for that PV is held for "Hold
  after a rule change (min)" in Settings — 20 minutes by default.
  The State cell keeps telling the truth about where the value sits,
  and the "Alarm status" cell says "new limits → HH:MM" so it is
  clear why nothing is being raised.

  It is a delay, not an amnesty: a value that is still outside the
  new band when the wait is over alerts then, in the ordinary way.

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
  longer than "Mark as not refreshed after (min)" in Settings (3.5
  minutes by default, and never less than the time the program needs
  to give up on a stuck reading and try a fresh one) — then:

    the status bar leads with NOT REFRESHED and the time of the last
      reading that worked,
    a red band appears above the graph, because a curve that simply
      stops looks exactly like a steady value,
    every State cell reads "not refreshed" instead of "ok", and
    every answer the bot gives until it clears opens with one short
      line saying the values are old and when they were last read.

  For the first half hour nothing is sent to the chat room about it.
  Almost every stall the program can see is one it cures by itself
  seconds later, so announcing those — and the "recovered" message a
  breath behind — only ever woke somebody for nothing. The marking is
  the point there: it is what stops an out-of-date value being
  reported as "ok".

  A stall that lasts longer than "Say it in the chat after (min)" in
  Settings (30 minutes by default) is a different matter: it is not
  curing itself, nobody watching a phone would know the numbers are
  frozen, and no limit is being watched meanwhile. That one gets one
  message, and one more when reading starts again. Set the figure to
  0 to keep it on screen only.

  The chat's own graphs are checked the same way: if the newest point
  falls short of the end of the window, the picture is stamped
  "NOT CURRENT" and the message repeats it in words. A graph of a
  past window — "yesterday 7-18" — is never stamped, since a curve
  ending where the window ends is what was asked for.

  The marking cannot be switched off. Everything else the program
  says is only worth as much as this.


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
      @Diagnostics /plot Chiller 1; 1.1. 9:00 - 1.9. 12:00

  In a one-to-one chat with the bot the tag is not needed. Type
  /help in the room for the whole list.

  A GRAPH OVER MONTHS is drawn as the average of each point on the
  picture with a shaded band from the lowest to the highest reading
  behind it, so a peak that lasted two seconds is still visible. Half
  a year of two chillers is 26 million readings; it comes back in
  about four minutes, and the reply says up front how long it expects
  to take. If that would still be too much, the program reads an
  evenly spread part of the period instead and stamps the picture
  SAMPLED - 24 % of the window read, so a gap is never passed off as
  a calm stretch. Add "; detail" to read every single reading.

  IT WILL NOT TAKE THE COMPUTER DOWN ANY MORE. On 2.9.2026 a graph
  over 180 days filled the whole memory of the machine and the
  desktop froze. The program now refuses an answer from the archive
  that is too big to hold, asks for a shorter stretch instead, and
  keeps an eye on its own memory while it reads. If it ever runs
  short anyway, it stops reading, draws what it has, and says
  STOPPED EARLY on the picture - rather than freezing the PC.

  ASKED FOR THE WRONG PERIOD? Send /cancel. A graph of a whole year
  takes minutes to collect, and /cancel drops it on the spot so you
  can ask for the right one instead of sitting the old one out.
  Nothing is sent for a cancelled graph. (/stop is a different
  thing - it switches alerting off.)


THE CANTEEN MENU

  Ask the bot /food and it answers with today's menu; /food week
  gives the whole week, and /food tomorrow, /food friday or
  /food 27.8. a single day.

  From 14:30 a plain /food shows the NEXT serving day instead of
  today - by then today's lunch is over and the useful question is
  what there is tomorrow. On a Friday afternoon that is Monday: it
  skips days the canteen has nothing on. The heading always names
  the day it is showing, and /food today still means today.

  The meals are grouped by course - soups first, then main courses -
  and numbered inside each group.

  The canteen writes the Czech and English name into one field, and
  not always both. So by default you get the Czech name with the
  English one underneath it, and:

      /food; cz        Czech names only
      /food; en        English names only

  Where only one language exists, that one is shown either way - a
  blank line where lunch should be would be worse. It combines with
  everything: /food week; en, /food friday; cz.

  The menu comes from the OKbase portal, which needs a sign-in, so
  fill in Settings -> Canteen menu once. If OKbase asks you for a
  user name and password of its own, put those there and you are
  done for good.

  IF YOU SIGN IN THROUGH MICROSOFT (single sign-on, with a
  confirmation in the authenticator), no program can get past that
  prompt - that is what it is for. So it borrows the sign-in you
  have already done in the browser instead, and there are two
  buttons in Settings -> Canteen menu for exactly that.

  SIGN IN WITH EDGE - the easy one. Fill in WORK ACCOUNT first (the
  address the canteen knows you by), then press the button. A
  browser window opens and does the rest by itself: it gets past
  OKbase's own sign-in page and answers Microsoft's "Pick an
  account" with the address you gave. About five seconds, no
  clicks. The window closes itself as soon as it is done and the
  sign-in is taken from it. Nothing to copy, no F12.

  Two work accounts on one PC is why WORK ACCOUNT matters:
  Microsoft always asks which one, and only one of them is the
  account the canteen portal knows. Leave it empty and the window
  stops on that question and waits for you. Anything else it asks -
  a password, a confirmation in the authenticator - you answer in
  that window; it is brought to the front so it cannot hide behind
  the Settings window.

  It is a window of its own, so your everyday Edge is not touched,
  not closed and not restarted. The first time you may have to pick
  your account; after that the window remembers it.

  PASTE SIGN-IN FROM CLIPBOARD - for when the first one cannot be
  used. In the browser, signed in to OKbase: F12 -> "Network" (if
  that tab is not there, click "+" and pick it) -> open Stravovani
  -> Objednavka jidel so the list fills up -> right-click the row
  called  nacti-vse  -> Copy -> "Copy as cURL". Then press the
  button. A plain "Cookie" line works too.

  Either way it tells you what it found - the cookie names and how
  long they are, never the sign-in itself - and then reads the menu
  so you can see straight away that it worked.

  "Read the menu now" checks it again at any time.

  The same thing from a shell, for when the program will not start
  at all (run it in the Diagnostic folder):

      python okbase_capture.py --edge     open a browser window
      python okbase_capture.py            from what you copied
      python okbase_menu.py --check       is my sign-in still good?

  THAT LAST ONE ANSWERS THE QUESTION THAT KEEPS COMING UP. After
  rebuilding Diagnostic you do NOT have to sign in again - the
  sign-in is kept for your Windows account, not inside the program,
  so every version reads the same one. If you are not sure, ask
  --check instead of guessing.

  You sign in once, not every morning. Two things see to it:

    - The program says hello to OKbase every ten minutes, so the
      sign-in does not lapse from not being used. The always-on
      listener does the same while this program is closed.
    - If it lapses anyway, the program asks OKbase for a new one and
      gets it, because the sign-in also carries your company
      sign-on - not just the one session. No authenticator, nothing
      for you to do.

  THE BOT NEVER WRITES ABOUT THE MENU BY ITSELF. Lunch is not worth
  a message in a room meant for laser alarms, so nothing is
  announced when the sign-in lapses. You are told where you are
  already looking: /status ends with one quiet line about it, and
  every /food answer says why the menu cannot get any newer.

  AND IT ONLY SAYS THAT WHEN IT MEANS IT. A portal that simply did
  not answer - the network hiccupped, the site was busy - used to be
  reported as "the sign-in has expired", which sent you off to sign
  in again for nothing. Now the two are told apart: an unreachable
  portal says so, in its own words, and asks you to do nothing.

  Ask /food status any time and it tells you what it has saved, what
  it is doing at that moment, and whether the sign-in still works.

  It is read once a day and saved, which is why the bot can answer
  /food with the program closed. Every answer says when the menu was
  read, and if that was a long time ago it says so in bold rather
  than passing old food off as today's. /food refresh reads it again
  on the spot.


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
