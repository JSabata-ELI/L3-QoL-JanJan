"""Announcer — making a noise, on the output device the operator picked.

No Qt. Windows only, through winmm.

WHY NOT `winsound`
------------------
`winsound.Beep()` drives the motherboard beeper through the kernel and never
reaches a Bluetooth speaker. That is why the lab once stayed completely silent
while the same script was plainly audible at the PC. So the beep is SYNTHESISED
as real samples and handed to a real output device.

WHY THE DEVICE IS NAMED
-----------------------
Windows keeps a separate output device and a separate volume PER PROGRAM. A
program parked on the built-in output stays there even after a Bluetooth speaker
has been made the default — which is exactly how a lab can hear every system
sound and nothing from this one. Naming the device outright sidesteps the
guessing. It is stored BY NAME, never by number: the numbers shuffle every time
a Bluetooth speaker connects or drops.

WHY THE SILENCE IN FRONT
------------------------
A Bluetooth link only carries audio once it has been opened, which takes a
moment when nothing has been played for a while. Every sound gets a run-up of
silence; without it the whole beep lands in the gap and nobody hears anything.
"""

from __future__ import annotations

import array
import ctypes
import math
import sys
import time
import wave
from pathlib import Path

LEADIN_MS = 800          # run-up of silence before the sound
TAIL_MS = 150            # silence after it, so the end is not clipped
RATE = 44100             # samples per second of the synthesised tone
DEFAULT_DEVICE = "Default (Windows chooses)"

_WAVE_MAPPER = 0xFFFFFFFF
_WHDR_DONE = 0x00000001
# WAVERR_STILLPLAYING: "the driver still owns that buffer, do not touch it".
_STILL_PLAYING = 33

# Buffers the driver would not give back. Held for the life of the program on
# purpose: freeing memory winmm is still reading from is a corrupted heap, and
# a few kilobytes kept for ever is not a problem worth having a crash over.
# One entry here means a sound device stopped answering; nothing else uses it.
_ORPHANED = []


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", ctypes.c_ushort),
                ("nChannels", ctypes.c_ushort),
                ("nSamplesPerSec", ctypes.c_uint),
                ("nAvgBytesPerSec", ctypes.c_uint),
                ("nBlockAlign", ctypes.c_ushort),
                ("wBitsPerSample", ctypes.c_ushort),
                ("cbSize", ctypes.c_ushort)]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_char_p),
                ("dwBufferLength", ctypes.c_uint),
                ("dwBytesRecorded", ctypes.c_uint),
                ("dwUser", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint),
                ("dwLoops", ctypes.c_uint),
                ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_void_p)]


class _WAVEOUTCAPS(ctypes.Structure):
    _fields_ = [("wMid", ctypes.c_ushort),
                ("wPid", ctypes.c_ushort),
                ("vDriverVersion", ctypes.c_uint),
                ("szPname", ctypes.c_wchar * 32),
                ("dwFormats", ctypes.c_uint),
                ("wChannels", ctypes.c_ushort),
                ("wReserved1", ctypes.c_ushort),
                ("dwSupport", ctypes.c_uint)]


def _winmm():
    return ctypes.WinDLL("winmm")


def output_devices():
    """Every output the sound layer offers, newest Bluetooth link included.

    Read fresh every time: a speaker only appears once it has connected, so a
    list built at start-up would never contain it.
    """
    names = [DEFAULT_DEVICE]
    try:
        mm = _winmm()
        caps = _WAVEOUTCAPS()
        for i in range(mm.waveOutGetNumDevs()):
            if mm.waveOutGetDevCapsW(i, ctypes.byref(caps),
                                     ctypes.sizeof(caps)) == 0:
                name = caps.szPname.strip()
                if name and name not in names:
                    names.append(name)
    except Exception:
        pass
    return names


def _device_number(name):
    """The picked name back as the number the sound layer wants.

    A device that has since disconnected falls back to whatever Windows would
    have used: a missing speaker must not silence the alarm.
    """
    if not name or name == DEFAULT_DEVICE:
        return _WAVE_MAPPER
    try:
        mm = _winmm()
        caps = _WAVEOUTCAPS()
        for i in range(mm.waveOutGetNumDevs()):
            if mm.waveOutGetDevCapsW(i, ctypes.byref(caps),
                                     ctypes.sizeof(caps)) == 0:
                if caps.szPname.strip() == name:
                    return i
    except Exception:
        pass
    return _WAVE_MAPPER


def play_pcm(pcm, rate, channels, width, device_name=None):
    """Send raw samples to one output device and wait for them to finish.

    Raises on any failure, so the caller can say so on screen. A sound that
    quietly goes nowhere is the whole problem being solved here.
    """
    mm = _winmm()
    fmt = _WAVEFORMATEX(1, channels, rate,
                        rate * channels * width, channels * width,
                        width * 8, 0)
    handle = ctypes.c_void_p()
    dev = _device_number(device_name)
    err = mm.waveOutOpen(ctypes.byref(handle), dev, ctypes.byref(fmt), 0, 0, 0)
    if err != 0 and dev != _WAVE_MAPPER:
        # The named device refused the format, or vanished mid-play. Rather
        # than stay silent, try again on whatever Windows would have used.
        err = mm.waveOutOpen(ctypes.byref(handle), _WAVE_MAPPER,
                             ctypes.byref(fmt), 0, 0, 0)
    if err != 0:
        raise OSError(f"cannot open the output device (code {err})")

    buf = ctypes.create_string_buffer(pcm, len(pcm))
    hdr = _WAVEHDR()
    hdr.lpData = ctypes.cast(buf, ctypes.c_char_p)
    hdr.dwBufferLength = len(pcm)
    try:
        err = mm.waveOutPrepareHeader(handle, ctypes.byref(hdr),
                                      ctypes.sizeof(hdr))
        if err != 0:
            raise OSError(f"cannot prepare the sound (code {err})")
        err = mm.waveOutWrite(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        if err != 0:
            mm.waveOutUnprepareHeader(handle, ctypes.byref(hdr),
                                      ctypes.sizeof(hdr))
            raise OSError(f"cannot play the sound (code {err})")
        seconds = len(pcm) / float(rate * channels * width)
        deadline = time.time() + seconds + 5.0
        while not (hdr.dwFlags & _WHDR_DONE) and time.time() < deadline:
            time.sleep(0.02)
    finally:
        # The order here is the whole point, and getting it wrong is a heap
        # corruption rather than a missing beep.
        #
        # If the wait above ran out — a Bluetooth link that stalls is exactly
        # that case — the buffer is STILL PLAYING. `waveOutUnprepareHeader`
        # then returns WAVERR_STILLPLAYING (33) and does nothing, `waveOutClose`
        # refuses too, and `buf` is freed by Python while the driver is still
        # reading it. So: `waveOutReset` first, which stops the device and marks
        # every buffer done; then unprepare, retried until it really succeeds;
        # and only then close. `buf` and `hdr` are referenced throughout.
        try:
            mm.waveOutReset(handle)
        except Exception:
            pass
        for _ in range(100):            # 100 x 10 ms, then give up and leak
            if mm.waveOutUnprepareHeader(handle, ctypes.byref(hdr),
                                         ctypes.sizeof(hdr)) != _STILL_PLAYING:
                break
            time.sleep(0.01)
        else:
            # Never free a buffer the driver still owns: leaking the handle and
            # the buffer is the lesser of the two evils by a long way.
            _ORPHANED.append((handle, hdr, buf))
            return
        mm.waveOutClose(handle)


def _silence(ms, rate, channels, width):
    return bytes(int(rate * ms / 1000) * channels * width)


def tone_pcm(freq=1500, duration_ms=500, leadin_ms=LEADIN_MS):
    """The beep, as real samples: a sine with a 5 ms fade either end.

    The fade is what kills the click at the start and the end.
    """
    freq = max(50, min(10000, int(freq)))
    duration_ms = max(20, min(10000, int(duration_ms)))
    n = int(RATE * duration_ms / 1000)
    fade = min(n // 2, int(RATE * 0.005))
    step = 2.0 * math.pi * freq / RATE
    samples = array.array("h", bytes(2 * n))
    for i in range(n):
        level = 1.0
        if fade:
            if i < fade:
                level = i / fade
            elif i > n - fade:
                level = (n - i) / fade
        samples[i] = int(16000 * level * math.sin(step * i))
    pad = _silence(leadin_ms, RATE, 1, 2)
    tail = _silence(TAIL_MS, RATE, 1, 2)
    return pad + samples.tobytes() + tail, RATE, 1, 2


def sounds_dir():
    base = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
    return base / "sounds"


def sound_names():
    """"beep" plus every wav in the sounds folder next to the program."""
    names = ["beep"]
    try:
        names += sorted(p.stem for p in sounds_dir().glob("*.wav"))
    except Exception:
        pass
    return names


def file_pcm(path, leadin_ms=LEADIN_MS):
    """One wav with the silent run-up put in front of it."""
    with wave.open(str(path), "rb") as r:
        channels, width, rate = r.getnchannels(), r.getsampwidth(), r.getframerate()
        body = r.readframes(r.getnframes())
    if width == 1:
        # 8-bit PCM is unsigned, so its silence is 128 and not 0. Padding with
        # zeros would put a loud click in front of every sound.
        pad = bytes([128]) * (int(rate * leadin_ms / 1000) * channels)
    else:
        pad = _silence(leadin_ms, rate, channels, width)
    return pad + body, rate, channels, width


def build_pcm(name, freq=1500, duration_ms=500, leadin_ms=LEADIN_MS):
    """The sound to play, or None when the file has gone missing."""
    if not name or name == "beep":
        return tone_pcm(freq, duration_ms, leadin_ms)
    wav = sounds_dir() / f"{name}.wav"
    if not wav.exists():
        return None
    return file_pcm(wav, leadin_ms)


def play(name="beep", *, freq=1500, duration_ms=500, leadin_ms=LEADIN_MS,
         device=None, report=None):
    """Play a sound in the background. Never raises.

    `report(message)` is called with a sentence when something went wrong, or
    with what was played when it worked and reporting was asked for.

    A sound problem must NEVER stop the alarm — the flash is the part that
    matters — so a failure falls back to the old motherboard beep and moves on.
    """
    import threading

    def _work():
        try:
            made = build_pcm(name, freq, duration_ms, leadin_ms)
            if made is None:
                if report:
                    report(f'The sound file "{name}.wav" is not in the sounds '
                           f'folder next to the program.')
                return
            pcm, rate, channels, width = made
            play_pcm(pcm, rate, channels, width, device)
            if report:
                report(f"Sound played on: {device or DEFAULT_DEVICE}")
        except Exception as exc:
            if report:
                report(f"The sound failed: {exc}")
            try:
                import winsound
                winsound.Beep(int(freq), int(duration_ms))
            except Exception:
                pass

    threading.Thread(target=_work, daemon=True).start()
