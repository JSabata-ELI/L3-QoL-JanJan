Diagnostic Webex listener — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-26  (remote_launcher.py, 533 lines)
-----------------------------------------------------------------
Short version: ReadMe_Diagnostic Webex listener.txt ("ReadMe" button)
Code map:      STRUCTURE.md  (in the Diagnostic folder)
-----------------------------------------------------------------

A single always-on program whose whole job is to start Diagnostic on
command from the Webex chat. It is built from remote_launcher.py, which
lives in the Diagnostic source folder, but it is built, versioned and
deployed as a program of its own, because it has to be able to run when
Diagnostic is not.


1. WHY IT IS SEPARATE

  Diagnostic answers chat commands itself, but only while it is open —
  that listener is part of the application window. The one command that
  matters when nobody is at the PC is "open the application", and that
  cannot be answered by the application. Hence a second, tiny program.

  It deliberately does not import the graphical libraries (no PySide6,
  no matplotlib). It borrows only the Webex part of Diagnostic, so it
  stays small enough to leave running all day.

  It is an .exe and not a .py on purpose: as a script it would only
  start on a PC that has Python with the requests library installed.


2. CONFIGURATION — THERE IS NONE OF ITS OWN

  Everything comes from Diagnostic's own settings, so there is only one
  bot identity to look after:

    Bot token, spaces        PV Monitor -> Settings -> Notifications
                             -> Webex  (stored in monitor_config.json)
    Which space it listens in the space with "Listen for commands"
                             ticked — the first one, if several are
    Who may command it        the "command allowlist" of e-mail
                              addresses; empty means everyone in the
                              space
    How often it looks        the command poll interval, in seconds
                              (minimum 1, default 5)

  Channels baked into a build win over the local file, so a freshly
  copied build works on a PC where Settings has never been opened.

  Without a bot token and a listening space it prints one line saying
  what is missing and exits. Nothing is retried, because nothing can
  be: there is no channel to complain on.


3. THE COMMANDS

  /run                (also accepted: /rundiagnostic — the old name)
  /food [when]        (also accepted: /menu, /lunch)

  Only the first word has to be the command, so "/run please" or
  "/run." still work.

  IN A SPACE WITH OTHER PEOPLE THE BOT MUST BE TAGGED:

      @Diagnostics /run

  Webex hands a bot only the messages that mention it and answers 403
  for the rest, so an untagged command never reaches this program.
  The mention is part of the text Webex delivers ("Diagnostics /run"),
  and it is stripped off before the command is matched.

  Messages the bot itself wrote, and messages from before it started,
  are ignored: on the first successful poll it takes the newest message
  as its starting point, so a backlog is never answered twice.


4. WHAT /run DOES, IN ORDER

  1. If Diagnostic already runs, it says so and stops there — with a
     note if the program is open but not tracking.

  2. It picks the NEWEST version folder under C:\Dev\dist\Diagnostic
     (compared as numbers, so v1.0.10 correctly beats v1.0.9 — as text
     it would not) and starts the .exe in it. A version
     folder only counts when it really holds an .exe, so a half-built
     folder cannot shadow the working version below it.

  3. If there is no build, it starts the source next to itself with
     pythonw — main.py in the source folder, or the version-named copy
     of it inside a build folder. This needs Python on the PC; when
     there is none, the answer says exactly that.

  4. That one launch is asked to arm monitoring by itself, so /run
     gives a program that is actually tracking without the Settings
     checkbox having to be left on for every manual start too.

  5. It then waits for the program to report that it is tracking, and
     only then answers "Done". The waiting runs on its own thread, so
     the bot keeps answering while a cold start takes its minute.


5. WHAT /food DOES

  It reads the menu Diagnostic saved earlier and writes it into the
  chat. No sign-in to OKbase happens here, which is why the answer
  arrives with Diagnostic closed — that is the whole point of the
  saved copy.

      /food                today
      /food week           this week; also "next week"
      /food tomorrow       also "yesterday", or a weekday: /food friday
      /food 27.8.          a date; also 2026-08-27
      /food refresh        read OKbase again now (see below)
      /food status         how many days are saved and how old they are.
                           Answered honestly: this program cannot see
                           whether the sign-in still works, so it says
                           to ask again with Diagnostic open.

  WHILE DIAGNOSTIC IS OPEN THIS PROGRAM SAYS NOTHING ABOUT /food and
  lets Diagnostic answer. Both replying would double every menu.

  The saved copy is looked for in the shared folder next to the shared
  PV list, and in %APPDATA%\Diagnostic. The newer of the two wins.

  Every answer says when the menu was read. A copy older than about a
  day, or one that does not cover the day asked about, says so in the
  answer — it never shows another day's food instead.

  /food refresh does try OKbase directly, and works only where the
  saved sign-in decrypts, which means the same Windows account that
  typed it into Diagnostic's Settings. Where it does not, the answer
  is the saved menu plus the reason the fresh read failed.

  THIS PROGRAM ALSO KEEPS THE SIGN-IN ALIVE. While Diagnostic is
  closed, it says hello to OKbase every ten minutes and reads the
  menu once a day. That is what stops the borrowed browser sign-in
  from expiring overnight - a web sign-in dies of not being used,
  and this is the process that is always up. It stands down whenever
  Diagnostic is open, so the two never do it at the same time or
  both write to the settings file. A renewed session is saved back
  automatically.

  Its console says what happened: every menu read, and the sign-in
  only when its state CHANGES ("stopped working" / "works again") -
  a line every ten minutes would bury everything else.


6. THE ANSWERS AND WHAT THEY MEAN

  ▶️ Starting Diagnostic — <what>     the launch was accepted; <what>
                                     names the version, or "source"
  ✅ Done — runs and tracks           confirmed from the program itself
  ℹ️ Already runs                     nothing was started
  ℹ️ Already runs, but not tracking   send @Diagnostics /start (or press
                                      Start monitoring in the app)
  ⛔ Not allowed                      the sender is not on the allowlist
  ❌ Could not be started             no build, no Python, or the launch
                                     itself failed — the reason is in
                                     the message
  ❌ Ended right after starting       it started and died; the exit code
                                     is in the message
  ⚠️ Runs, but monitoring did not
     switch itself on                 send @Diagnostics /start, or use
                                      the Start monitoring button
  ⚠️ Runs, but does not report its
     state                            that build is older than this
                                      feature — rebuild Diagnostic
  ❌ Did not come up in 240 s          the ceiling on hoping; the program
                                      may still appear later

  The last two are told apart by whether the launched program is still
  alive. Without that, an old build that simply never reports would look
  exactly like one that crashed, and somebody would go hunting for a
  crash that never happened.


7. RUNNING IT

  It is a console program. The window is the only sign that it is
  alive, and closing the window is how you stop it — that is why it is
  not windowless. Leave it minimised.

  Autostart at logon:

      "Diagnostic Webex listener.exe" --install-startup
      "Diagnostic Webex listener.exe" --uninstall-startup

  This puts a shortcut into the current user's Startup folder (started
  minimised), which needs no administrator rights — that is the reason
  it is a shortcut and not a scheduled task or a service. Nothing is
  installed unless asked.

  Everything it prints goes to its window only. There is no log file:
  what matters is said in the chat.

  One exception worth reading: a memory line, printed once at start and
  once an hour, for example

      [remote_launcher] Memory: this app 24 MB committed (31 MB in
      RAM), +2 MB since start. Whole PC 27.2 GB of 30.6 GB committed
      (89%), RAM 14.7 GB of 15.7 GB. Running for 148 h.

  "Committed" is the memory Windows has promised, which is what runs
  out on a PC left up for weeks: the whole-PC figure has a hard limit
  (RAM plus page file) and once it is reached nothing new can start,
  even with free RAM showing in Task Manager. Two things to read from
  the line — whether the whole-PC figure is near its limit (close
  things or restart the PC), and whether this program's own figure
  keeps climbing hour after hour (that would be a leak in it; tell me).
  Diagnostic itself shows the same figures in its status bar and writes
  them into its Log tab every half hour.


8. WHERE IT LOOKS FOR ITS FILES

  Next to the .exe — not in the temporary folder Windows unpacks a
  single-file build into. That distinction matters: monitor_config.json
  is read from the .exe's own folder, so a PC that is configured
  properly is not reported as unconfigured.

  Files it wants beside it:

    monitor_config.json    the Webex settings, if they are not baked in
    notify_provision.dat   the baked-in channels, copied in by the build

  Diagnostic writes a small status file that says whether it is running
  and whether it is tracking. That file is how this program knows the
  difference, whichever way the application was started.


9. WHEN SOMETHING IS WRONG

  Nothing happens on a command
    The bot was probably not tagged. In a group space write
    "@Diagnostics /run". Also check that the window is still open.

  The window closes right after starting
    The Webex bot is not configured for listening: bot mode and a
    space with "Listen for commands" ticked, in Diagnostic's Settings.

  "Not allowed"
    Add the e-mail address to the command allowlist in Diagnostic's
    Webex settings.

  It starts an old version
    It always takes the highest version folder under
    C:\Dev\dist\Diagnostic. Build the new version, or remove the folder
    that should not win.

  It answers, but nothing opens
    There is no build and no Python on the PC. Deploy a build of
    Diagnostic to C:\Dev\dist\Diagnostic.


10. BUILDING AND DEPLOYING IT

  Dev Tools -> Builder: the Diagnostic row has an arrow in front of it;
  click the program and this listener appears underneath with its own
  tick box, its own next version, and its own ReadMe and Details
  buttons.

  It builds into its own folder, dist\Diagnostic Webex listener\vX.Y.Z,
  as a single .exe, and it carries its own copies of what it reads at
  run time and of these two documents. Ticking Diagnostic ticks the
  listener too, which is the usual case: a release where the two match.
  Untick it afterwards to rebuild only the application.
