Diagnostic Webex listener — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Diagnostic Webex listener_Full.txt
                  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Sits in the Webex chat all day and waits for two commands. On /run
  it starts the Diagnostic program on this PC and switches its
  monitoring on, then writes back to the chat when the program is
  really running. On /food it answers with the canteen menu, and
  can order or cancel lunch.

  It exists because Diagnostic can only answer chat messages while
  it is open. This little program is the part that is always open,
  so somebody away from the PC can get the monitoring started.


HOW TO USE IT

  In the Webex space marked "Listen for commands", write:

      @Diagnostics /run

  The tag at the start is not decoration — in a space with other
  people in it, Webex only shows a bot the messages that mention it.
  An untagged command never arrives, which looks exactly the same as
  this program not running at all.

  Answers you can get:

      ▶️  Starting Diagnostic — it says which version it started
      ✅  Done — the program runs and is tracking values
      ℹ️  It already runs — and if it is open but not tracking, the
          answer names the command that arms it:
          @Diagnostics /start
      ⛔  Your e-mail address is not on the allowed list
      ❌  It could not be started, or it died right after starting
      ⚠️  It runs, but did not report that it is tracking

  A cold start takes up to a minute, sometimes more. The bot keeps
  answering while it waits, and the "Done" message comes when the
  program is really up — it is not forgotten.


THE CANTEEN MENU

      @Diagnostics /food          today
      @Diagnostics /food week     the whole week

  Every /food reads the menu from OKbase there and then, so the
  answer arrives with Diagnostic closed — which is the point. Only
  if that read fails (portal down, sign-in expired) does it answer
  from the copy saved earlier, and then it says when that copy was
  read and why it could not get a newer one, instead of passing old
  food off as today's.

  Ordering and cancelling lunch work here too, with Diagnostic
  closed — the same commands as in Diagnostic:

      @Diagnostics /food order friday 1 pin:bakoli
      @Diagnostics /food cancel friday pin:tumida

  Anybody allowed to command the bot may ask, unless Settings ->
  Canteen menu -> "Restrict ordering to" names particular senders.
  Every order or cancellation needs one of your one-time ordering
  codes (the pin: word); it is used up only when the change is
  really made. When 10 or fewer codes are left, the reply says so —
  make a new list in Settings -> Canteen menu. /food order with no
  meal named only shows the day's list and needs no code.

  While Diagnostic itself is open, this program stays quiet about
  /food and lets Diagnostic answer, so a question never gets two
  answers.

  While Diagnostic is closed, this program also keeps the OKbase
  sign-in alive and reads the menu once a day, so the menu stays
  fresh and the sign-in does not have to be filled in again. That is
  the main reason to leave this window open.

  It also sends the morning lunch reminder while Diagnostic is
  closed: on working days from 08:00, if today has nothing ordered
  and ordering is still open, one message that mentions you.

  Whose menu it is and how it gets saved is set in Diagnostic, in
  Settings -> Canteen menu.


STARTING AND STOPPING IT

  It is a console window. That window is the only sign it is alive,
  and closing it is how you stop it. So leave it open, minimised.

  Because it is left running for weeks, it writes one line into that
  window when it starts and one every hour: how much memory it holds
  and how much it has grown since it started, plus how full the whole
  computer's memory promise is. The second figure is the one that
  ends a long uptime — when the computer has promised all it can,
  nothing new will start, even with RAM to spare. If the hourly line
  shows this program itself growing, tell me.

  To have it start by itself when you log on to Windows, run it once
  with --install-startup; --uninstall-startup takes it out again.
  No administrator rights are needed for either.


WHAT IT NEEDS

  The same Webex bot as Diagnostic itself: the bot token and the
  space are the ones set in Diagnostic under
  PV Monitor -> Settings -> Notifications -> Webex. One space there
  has to have "Listen for commands" ticked — that is the space this
  program watches. Nothing is configured here separately.

  If that space has its "/food" box unticked, the canteen is off
  there: /food only answers "not available in this room" and no
  morning lunch reminder is posted.

  If the bot is not set up, the window says so and closes. That is
  the intended behaviour, not a crash.

  It starts the newest built version of Diagnostic it finds in
  C:\Dev\dist\Diagnostic. A build carries its own libraries, so
  nothing has to be installed for it to run. Only when there is no
  build does it fall back to the source next to it, which then does
  need Python on the PC.
