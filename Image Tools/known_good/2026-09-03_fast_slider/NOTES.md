# Image Slider — known-good snapshot, 3 September 2026

This folder is a frozen copy of the Image Slider on the day it first felt
**right**: fast, snappy, pictures keeping up while dragging, no waiting.
If a later change makes the Slider feel slow or sticky again, this is the
state to come back to and compare against.

Taken from branch `Mouka`, working tree on top of commit
`e73590efdf6509a6d9996a20900567703858e59f`.
The working tree had uncommitted changes at the time, so the files here are
**not** identical to anything in git history — this copy is the only record.

## What "good" means here

* Dragging the slider shows pictures continuously — the preview layer carries
  the drag, the share is never asked for a frame per slider step.
* A picture appears, then sharpens: refine at 200 ms, native at 500 ms.
* A tile that got stuck on an old frame fetches it again by itself and then
  stops as soon as a real new shot arrives.
* Several cameras at once behave the same as a single camera.
* No freeze while a fetch is in flight, no button left grey.

## Still to be checked

**Not yet tested at 3.3 frames per second** (the fastest storage rate the
archive produces). Everything below that looked fine on 3 September 2026.
Until that test is done, treat this snapshot as "good at normal rates,
unverified at the top rate".

How to check it: pick a day and camera where the archive stored ~3 frames per
second, drag through it, then read `Image Tools/image_tools_diag.log` and look
at the `hq=` / `prev=` numbers and the `STUCK retry` lines. A tile drifting
3 seconds behind and retrying over and over is the failure to watch for.

## What is in here

| File | Why it is needed |
|---|---|
| `is_t.py` | the Image Slider itself; also runnable on its own |
| `cpva_client.py` | talks to the archive (channels, day cache) |
| `img_scale.py` | decides what an intensity means (the 4095 sensor scale) |
| `daypicker.py` | the shared calendar and time-window picker |
| `icon.ico` | window and taskbar icon |
| `SHA256SUMS.txt` | fingerprints, so you can tell whether the live files drifted |

Nothing else from Image Tools is required — `is_t.py` loads the three helpers
by path, so this folder runs as it stands:

```
python is_t.py
```

## How to use it later

Compare the live files with this copy:

```
python restore.py --check
```

Put this copy back (the current live files are saved next to them first,
with a timestamp, so nothing is lost):

```
python restore.py --restore
```
