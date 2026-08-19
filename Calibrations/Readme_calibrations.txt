Calibrations — Information
Created by Jan Moučka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------

Do you want to calibrate one of the energy detectors?

This is the Calibrations2.xlsx procedure as a program. You type the
measured pairs into a table, it works out the calibration factor for
each of them, averages that over the range you choose, and gives you
the new calibration constants.

It does NOT open the Excel file. The spreadsheet is where the
formulas came from, not something the program reads — everything you
see is either typed in or generated for the selected device.


=================================================================
HOW TO USE IT
=================================================================

1. Pick the device: PAP1, PTM1, PCM2 or PCM4.
   The table fills itself with that device's usual waveplate values.
   Changing the device rebuilds the table, so it asks first if you
   have already typed something in.

2. Type the measurements. Two columns matter:
      QE95 [mJ/J]     the reference
      <Device> [mJ/J] what the detector read
   Cal Factor next to them is filled in for you — it is simply
   QE95 divided by the device value. The Note column is yours.

   Add row / Remove row if the default set of waveplate values is
   not what you measured.

3. Set From and To. These pick the waveplate range that counts:
   everything outside it is ignored, both for the average factor
   and for the fit. The rows inside the range are shaded so you can
   see what is included.

   Average cal factor underneath is the mean Cal Factor in that
   range.

4. Fill in the old calibration on the right: multiplicator, offset
   and int_multiplicator. int_multiplicator is entered and shown in
   micro units (×10^6), the same way the Excel sheet showed it.

5. Read off the new calibration below it. Those three fields are
   read-only — they are the result, not something to edit.

   Convert? and Use Int? behave exactly like the switches on the
   Settings sheet, and change which of the four formulas is used.

6. Date and timing (Off-A SS / Off-A / Off-B SS / Off-B) are written
   into the export so a saved file says what it belongs to. The date
   can be typed or picked from the calendar.

7. Save. CSV always works. .xls needs the xlwt library; without it
   the program says so and saves CSV instead.

The ⓘ button repeats the short version of all this inside the
program.


=================================================================
WHAT THE NEW CALIBRATION IS BASED ON
=================================================================

  A straight line is fitted through the points inside From..To:

      QE = slope × Device + intercept

  and the new constants come out of that fit together with the old
  ones. PAP1 uses a different exponent from the other three, which
  is why it is treated separately in the formulas.

  If the fit cannot be made — too few points in the range — or one
  of the old constants is missing, all three new values are left
  empty. A half-computed calibration would be worse than none.


=================================================================
GENERAL NOTES
=================================================================

  - Nothing is loaded or saved automatically. Close the program and
    the table is gone, so export before you leave.
  - Highlighting: the rows inside From..To, plus the row and column
    you are working in.
  - For whoever works on the code: STRUCTURE.md.

-----------------------------------------------------------------
