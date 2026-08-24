Calibrations — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Calibrations_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Calibrates one of the energy detectors. You type in the pairs you
  measured, the program works out the calibration factor for each
  pair, averages it over the range you choose, and gives you the
  three new calibration constants.

  This is the Calibrations2.xlsx procedure turned into a program.
  It does not open the spreadsheet — the sheet is where the formulas
  came from, nothing more. Everything you see is either typed in by
  you or generated for the device you picked.


HOW IT WORKS

  - Pick a device and the table fills itself with that device's
    usual waveplate values, ready to be measured against.
  - For every row you fill in, the Cal Factor column is worked out
    for you: the reference value divided by what the detector read.
  - From and To pick which part of the range counts. Rows outside it
    are ignored, both by the average and by the fit. The rows inside
    are shaded so you can see them.
  - A straight line is fitted through the points inside the range,
    and the new constants come out of that line together with the
    old ones you typed in.
  - Everything recalculates as you type. There is no Calculate
    button.


THE SEVEN STEPS

  1. Pick the device (PAP1, PTM1, PCM2, PCM4).
  2. Type the measurements: the reference column and the device
     column. Add row / Remove row if your set of waveplate values
     is not the default one.
  3. Set From and To, and read the average factor underneath.
  4. Type the old calibration on the right: multiplicator, offset
     and int_multiplicator.
  5. Read the new calibration below it. Those three fields are the
     result and cannot be edited. The Convert? and Use Int?
     switches choose which of the four formulas is used, exactly as
     on the Settings sheet.
  6. Fill in the date and the timing, so a saved file says what it
     belongs to.
  7. Save. A .csv file always works. A .xls file needs an extra
     library; if it is missing, the program says so and saves .csv.


IMPORTANT

  - Nothing is loaded or saved by itself. Close the program and the
    table is gone — save before you leave.
  - If the fit cannot be made, because there are too few points in
    the range, or one of the old constants is missing, all three new
    values stay empty. A half-computed calibration would be worse
    than none.
  - The (i) button repeats the short version of all this inside the
    program.

-----------------------------------------------------------------
