Announcer (python script) — Information
Created by Jan Moučka, ELI-Beamlines

If you have suggestions for improvement or encounter some bugs, please let me know - jan.moucka@eli-beams.eu
---------------------------------

Program for detecting a trip. It is based on difference between reference and image of monitored region in specific moment.

First click on identify to see what monitor you should pick. The region needs to be in the selected monitor of course.

Then click on the "Set reference" button and select a region you want to monitor.

In the settings you can set threshold for the change, if the program needs to do some sound, how long will it be blicking.

When you are done with setting the reference, just click on the big yellow (in that moment yellow, otherwise its gray, green or red, based on the situation) circle to start tracking the region.
	. Green -> Program is tracking the region.
	. Red -> Program detected a change in the region, it tripped.
	. Yellow -> Program is prepared to start tracking.
	. Gray -> Program has no region which it should monitor.

You can view the region via "preview region" button.

