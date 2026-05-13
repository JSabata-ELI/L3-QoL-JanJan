Image Finder (python script) — Information
Created by Jan Moučka, ELI-Beamlines
---------------------------------
Do you want to calibrate some of the energy detectors?

Calibration tool

- Loads Calibration Excel and shows device block (PAP1/PTM1/PCM2/PCM4).
- From/To selects range by waveplate value (used later).
- Average cal factor is computed from Cal Factor values inside From..To range.
- New calibration is computed for PTM1/PCM2/PCM4:
  new_multiplicator = multiplicator1 * average_cal_factor
  new_offset        = offset1 * average_cal_factor
  int_multiplicator1 == 1 -> stays 1, else * average_cal_factor

If you have suggestions for improvement or encounter some bugs, please let me know - jan.moucka@eli-beams.eu
