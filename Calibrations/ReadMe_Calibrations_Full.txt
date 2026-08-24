Calibrations — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19  (cal.py, 1055 lines)
-----------------------------------------------------------------
Short version: Readme_calibrations.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. WHAT THIS REPLACES, AND WHAT IT DOES NOT
=================================================================

The calibration of the energy detectors used to be done in a
spreadsheet called Calibrations2.xlsx: a table of measurements, a
range selection, a fit, and a block of formulas that turned the old
constants into new ones.

This program is that procedure written out properly. The advantages
are the ones you would expect: the read-only results cannot be typed
over by accident, the range highlighting is visible, the fit cannot
silently include a row you thought you had excluded, and the export
carries the date and the timing with it.

What it explicitly does NOT do:

  - It does not open, read or write the spreadsheet. The workbook
    was the source of the formulas, not an input file.
  - It does not read the detectors. Every measured number is typed
    in by hand.
  - It does not write the new constants anywhere. It shows them; you
    enter them into the control system yourself.
  - It does not remember anything between runs.


=================================================================
2. THE FOUR DEVICES
=================================================================

  PAP1, PTM1, PCM2, PCM4

Picking a device does two things. It renames the measurement column
to that device's name, and it fills the table with that device's
usual set of waveplate positions, together with a sensible default
for the fit range:

  PAP1    rows from 0 to 1 400 000 in steps of 50 000
          fit range 200 000 to 1 000 000
  PTM1    rows from 0 to 60 000 in steps of 5 000
          fit range 0 to 50 000
  PCM2    rows from 200 000 to 800 000 in steps of 50 000
          fit range the whole of it
  PCM4    the same as PCM2

Changing the device rebuilds the table from scratch, so anything you
have typed in is lost. The program asks first when it sees that you
have edited something.

PAP1 is also treated differently in the formulas — see section 6.


=================================================================
3. THE TABLE
=================================================================

Five columns:

  Waveplate         the position. Generated for the device, or typed
                    in yourself if you add rows.
  QE95 [mJ/J]       the reference measurement.
  <Device> [mJ/J]   what the detector under test read. The heading
                    is renamed to whichever device is selected.
  Cal Factor        filled in for you, and not editable: the
                    reference divided by the device value.
  Note              yours, free text. It is carried into the export.

"Add row" and "Remove row" change the row set when the generated
waveplate positions are not the ones you actually measured. The row
numbers renumber themselves.

A row with a blank or a zero in either measurement column is simply
not used — no error, it just does not take part in the average or
in the fit.

Highlighting, all of it deliberately faint so the numbers stay
readable:

  - the rows inside the From..To range, so you can see at a glance
    what is included;
  - the row you are in;
  - the column you are in.


=================================================================
4. THE RANGE, AND THE AVERAGE FACTOR
=================================================================

From and To are dropdowns filled from the waveplate values actually
present in the table, so they cannot point at a row that does not
exist. Adding or removing rows refills them.

Everything inside the range, inclusive of both ends, counts.
Everything outside it is ignored — both for the average factor and
for the fit. The order does not matter; From above To works the
same.

"Average cal factor" underneath is the plain mean of the Cal Factor
column over the rows in the range. It is a sanity check rather than
an input to anything: if the factors inside the range are not
reasonably constant, the fit that follows is not describing your
measurement well, and that is worth knowing before you copy the
result anywhere.


=================================================================
5. DATE AND TIMING
=================================================================

  Date     Type it, or press the calendar button and pick it. The
           dropdown is prefilled with the last few days, since
           "today" or "yesterday" covers most cases. Typed dates
           are accepted in several forms (25.12.2026, 2026-12-25,
           25-12-26 and similar) and are written out in one
           canonical form.

  Timing   Off-A SS, Off-A, Off-B SS, Off-B — the same list that
           used to sit on the Settings sheet.

Neither affects the calculation. Both go into the export, so a saved
file says what it belongs to instead of being an anonymous table of
numbers.


=================================================================
6. HOW THE NEW CALIBRATION IS COMPUTED
=================================================================

6.1 THE FIT

    A straight line is fitted, by least squares, through the points
    inside the range:

        reference = slope x device_reading + intercept

    The device reading is the horizontal axis and the reference is
    the vertical one, which is the direction that matters: the
    result has to convert a reading into a reference-equivalent
    value.

6.2 THE OLD CONSTANTS

    Three fields on the right, all typed in by you:

        multiplicator
        offset
        int_multiplicator

    int_multiplicator is entered and displayed in micro units — that
    is, the number you type is a millionth of the value used in the
    arithmetic. This is exactly how the spreadsheet presented it, and
    keeping it that way avoids a whole class of transcription error.
    The new int_multiplicator is shown in the same units.

6.3 THE TWO SWITCHES

    Convert? and Use Int? are the two YES/NO switches from the
    Settings sheet. Between them they select one of four formulas.
    Their meaning is unchanged from the spreadsheet, so if you know
    which combination your detector uses, use the same one here.

6.4 THE FOUR FORMULAS

    Two candidate sets are formed from the fit and the old
    constants. Written out with the exponent p, where p is 1 for
    PAP1 and 2 for the other three devices:

        set M:  mult = slope x mult_old
                off  = slope x off_old + intercept
                int  = 1

        set J:  mult = 1
                off  = off_old + intercept / (int_old x slope^p)
                int  = slope x int_old

    and the switches pick between them:

        Convert? no,  Use Int? no    ->  set M as it stands
        Convert? no,  Use Int? yes   ->  set J as it stands
        Convert? yes, Use Int? no    ->  mult = 1
                                         off  = M.off / M.mult
                                         int  = M.mult
        Convert? yes, Use Int? yes   ->  mult = J.mult x J.int
                                         off  = J.off x J.int
                                         int  = 1

    The PAP1 exponent difference is not a mistake in the sheet; it
    is what that detector's own conversion needs.

6.5 WHEN THE RESULT IS LEFT BLANK

    All three new fields are cleared, rather than partly filled, if:

      - the fit could not be made, which in practice means fewer
        than two usable points inside the range, or
      - any one of the three old constants is missing, or
      - in the Convert? yes / Use Int? no case, the intermediate
        multiplier came out at zero, which would mean dividing by
        it.

    A blank result is a deliberate refusal. A calibration that is
    half computed is worse than none, because it looks like an
    answer.


=================================================================
7. SAVING
=================================================================

The Save button offers a filename of the form

    calibration_<DEVICE>_<date>_<time>.xls

and either format writes the same content, in this order:

    Device
    Saved                 the moment of saving, to the second
    Date                  the calibration date you set
    Timing
    Convert?              the two switches, as they stood
    Use Int?
    From                  the range
    To
    Average cal factor

    Calibration           Old        New
    multiplicator         ...        ...
    offset                ...        ...
    int_multiplicator (x10^6)  ...   ...

    then the whole table, with its headings

Trailing empty rows are trimmed off the table before writing.

  .csv    always available. Opens in Excel, in a text editor, and in
          anything else.
  .xls    needs an extra library on the machine. If it is not there,
          the program says so, writes the .csv next to where the
          .xls would have gone, and tells you where it put it.

Because the two switches and the range are written into the file, a
saved calibration is self-explanatory later: you can see which of
the four formulas produced those numbers.

Nothing is written unless you ask for it, and nothing is read at
startup. Close the program and the table is gone.


=================================================================
8. A NORMAL SESSION
=================================================================

  1. Start the program, pick the device.
  2. Take the measurements at the waveplate positions in the table,
     typing each pair in as you go. Watch the Cal Factor column: it
     should stay roughly constant across the useful part of the
     range.
  3. Set From and To to the part that is well behaved. Check the
     average factor.
  4. Type in the three constants the detector currently uses.
  5. Set Convert? and Use Int? to the combination that detector
     uses.
  6. Read the three new constants.
  7. Set the date and the timing, and save.
  8. Enter the new constants into the control system yourself. The
     program will not do it for you, on purpose.


=================================================================
9. WHEN SOMETHING GOES WRONG
=================================================================

  The new constants are empty
      Either the fit has too few points inside From..To, or one of
      the old constants is blank. Check both. Rows with a blank or
      a zero measurement do not count towards the fit.

  Cal Factor is empty on a row I filled in
      One of the two measurement cells is blank, zero, or not a
      number. A comma instead of a decimal point is the usual
      cause.

  The average factor jumps around
      The measurements inside the range are not consistent. Narrow
      the range to the part that behaves, or re-measure. The fit
      will otherwise produce a confident-looking answer from bad
      data.

  I changed the device and lost my table
      Changing the device rebuilds the table. The program asks first
      when it can see unsaved edits; if the table was empty it does
      not ask.

  .xls export refused
      The extra library is not installed on this machine. The .csv
      the program offered instead contains exactly the same data.

  The int_multiplicator is a million times off
      That field is in micro units, in and out. Check whether the
      number you are comparing against is in the same units.

-----------------------------------------------------------------
