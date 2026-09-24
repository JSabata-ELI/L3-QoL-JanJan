"""Announcer — the Alarm tab: what the alarm looks and sounds like.

Every control here writes straight into the settings, so a change takes effect
on the next alarm without a Save button to remember. The Test button is the
point of the tab: an alarm nobody has ever seen or heard is an alarm nobody
knows is broken.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDoubleSpinBox, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QSpinBox, QVBoxLayout,
                               QWidget)

import ann_alarm as AL
import ann_core as C
import ann_presets as P
import ann_screen as S
import ann_sound as Snd
import ann_ui as U

_CYCLE_WORDS = {
    "fixed":            "the picked colour",
    "rainbow_blink":    "a different colour every blink",
    "rainbow_spectrum": "blue to red across the width, drifting",
    "rainbow_wave":     "rings running out of the centre, never dark",
}
_MODE_WORDS = {
    "color": "fill the window with the colour",
    "image": "one shape, painted in the colour",
}


class AlarmTab(QWidget):

    # What the sound thread has to say, carried to this thread. A signal is the
    # ONE supported way across: see `_said`.
    sound_said = Signal(str)

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.sound_said.connect(self._show_sound_said)
        self._win = win
        self._cfg = win.config()
        self._loading = True
        self._place_dialog = None
        self._build()
        self.reload()
        self._loading = False

    # ── building ────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(self._build_flash(), stretch=1)
        top.addWidget(self._build_sound(), stretch=1)
        root.addLayout(top)
        root.addWidget(self._build_where())
        root.addStretch(1)

    def _build_flash(self):
        box = QGroupBox("The flash")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        r = 0

        self._mode = QComboBox()
        for key in AL.FLASH_MODES:
            self._mode.addItem(_MODE_WORDS[key], key)
        self._mode.currentIndexChanged.connect(self._changed)
        grid.addWidget(QLabel("What flashes"), r, 0)
        grid.addWidget(self._mode, r, 1, 1, 2)
        r += 1

        self._colour_btn = U.button("Colour")
        self._colour_btn.clicked.connect(self._pick_colour)
        self._colour_show = QLabel("")
        self._colour_show.setFixedSize(38, 22)
        grid.addWidget(QLabel("Colour"), r, 0)
        grid.addWidget(self._colour_show, r, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self._colour_btn, r, 2)
        r += 1

        self._cycle = QComboBox()
        for key in AL.COLOR_CYCLES:
            self._cycle.addItem(_CYCLE_WORDS[key], key)
        self._cycle.currentIndexChanged.connect(self._changed)
        grid.addWidget(QLabel("Colours"), r, 0)
        grid.addWidget(self._cycle, r, 1, 1, 2)
        r += 1

        self._interval = QDoubleSpinBox()
        self._interval.setRange(0.0, 3.0)
        self._interval.setSingleStep(0.05)
        self._interval.setDecimals(2)
        self._interval.setSuffix(" s")
        self._interval.setToolTip("How long each half of a blink lasts.\n"
                                  "Zero means do not blink at all — the alarm "
                                  "just stays on.")
        self._interval.valueChanged.connect(self._changed)
        grid.addWidget(QLabel("Blink"), r, 0)
        grid.addWidget(self._interval, r, 1)
        r += 1

        self._duration = QDoubleSpinBox()
        self._duration.setRange(0.0, 600.0)
        self._duration.setSingleStep(0.5)
        self._duration.setDecimals(1)
        self._duration.setSuffix(" s")
        self._duration.setToolTip("How long the alarm flashes for.\n"
                                  "Zero means until somebody clicks it or "
                                  "presses Esc — which is the usual answer, "
                                  "because an alarm nobody saw did not work.")
        self._duration.valueChanged.connect(self._changed)
        grid.addWidget(QLabel("For"), r, 0)
        grid.addWidget(self._duration, r, 1)
        r += 1

        self._image = QComboBox()
        self._image.currentIndexChanged.connect(self._changed)
        grid.addWidget(QLabel("Shape"), r, 0)
        grid.addWidget(self._image, r, 1, 1, 2)
        r += 1

        self._image_show = QLabel("")
        self._image_show.setMinimumHeight(150)
        self._image_show.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Both colours stated: on this PC an unpainted label is black on black,
        # and a dark shape on it would be invisible.
        self._image_show.setStyleSheet(
            "QLabel { background: #ffffff; color: #666666;"
            " border: 1px solid #b6b6b6; }")
        grid.addWidget(self._image_show, r, 0, 1, 3)
        r += 1
        return box

    def _build_sound(self):
        box = QGroupBox("The sound")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        r = 0

        self._sound_on = QCheckBox("Make a noise when the alarm goes off")
        self._sound_on.setStyleSheet(U.CHK_QSS)
        self._sound_on.toggled.connect(self._changed)
        grid.addWidget(self._sound_on, r, 0, 1, 3)
        r += 1

        self._sound = QComboBox()
        self._sound.currentIndexChanged.connect(self._changed)
        grid.addWidget(QLabel("Sound"), r, 0)
        grid.addWidget(self._sound, r, 1, 1, 2)
        r += 1

        self._freq = QSpinBox()
        self._freq.setRange(50, 10000)
        self._freq.setSingleStep(100)
        self._freq.setSuffix(" Hz")
        self._freq.valueChanged.connect(self._changed)
        grid.addWidget(QLabel("Beep pitch"), r, 0)
        grid.addWidget(self._freq, r, 1)
        r += 1

        self._sound_ms = QSpinBox()
        self._sound_ms.setRange(20, 10000)
        self._sound_ms.setSingleStep(50)
        self._sound_ms.setSuffix(" ms")
        self._sound_ms.valueChanged.connect(self._changed)
        grid.addWidget(QLabel("Beep length"), r, 0)
        grid.addWidget(self._sound_ms, r, 1)
        r += 1

        self._leadin = QSpinBox()
        self._leadin.setRange(0, 5000)
        self._leadin.setSingleStep(100)
        self._leadin.setSuffix(" ms")
        self._leadin.setToolTip(
            "Silence played in front of the sound.\nA Bluetooth speaker only "
            "carries audio once its link has opened, which takes a moment when "
            "nothing has been played for a while — without this run-up the "
            "whole beep lands in that gap and the lab hears nothing.")
        self._leadin.valueChanged.connect(self._changed)
        grid.addWidget(QLabel("Bluetooth run-up"), r, 0)
        grid.addWidget(self._leadin, r, 1)
        r += 1

        self._device = QComboBox()
        self._device.setToolTip(
            "Which speaker it comes out of.\nWindows keeps a separate output "
            "per program, so a program parked on the built-in speaker stays "
            "there even after a Bluetooth one becomes the default. Naming it "
            "here settles it. The list is re-read every time it is opened, "
            "because a speaker only appears once it has connected.")
        self._device.currentIndexChanged.connect(self._changed)
        grid.addWidget(QLabel("Play it on"), r, 0)
        grid.addWidget(self._device, r, 1, 1, 2)
        r += 1

        row = QHBoxLayout()
        btn_test = U.button("Test the sound", icon_name="bell",
                            tip="Play it now, on the speaker chosen above.")
        btn_test.clicked.connect(self._test_sound)
        row.addWidget(btn_test)
        btn_refresh = U.button("Look for speakers again", icon_name="refresh")
        btn_refresh.clicked.connect(self._reload_devices)
        row.addWidget(btn_refresh)
        row.addStretch(1)
        grid.addLayout(row, r, 0, 1, 3)
        r += 1

        self._sound_said = QLabel("")
        self._sound_said.setWordWrap(True)
        self._sound_said.setStyleSheet(f"color: {U.QUIET};")
        grid.addWidget(self._sound_said, r, 0, 1, 3)
        r += 1
        grid.setRowStretch(r, 1)
        return box

    def _build_where(self):
        box = QGroupBox("Where it appears")
        outer = QVBoxLayout(box)
        self._where_said = QLabel("")
        self._where_said.setWordWrap(True)
        outer.addWidget(self._where_said)
        lay = QHBoxLayout()
        outer.addLayout(lay)
        lay.addStretch(1)
        self._btn_place = U.button(
            "Place the alarm window", icon_name="area",
            tip="Show the alarm window as a rectangle you can drag and size, "
                "then keep where you put it.")
        self._btn_place.clicked.connect(self._place)
        lay.addWidget(self._btn_place)
        self._btn_circle = U.button(
            "Place the circle", icon_name="area",
            tip="Show the circle now and drag it to where it should sit while "
                "the program is watching.")
        self._btn_circle.clicked.connect(self._place_circle)
        lay.addWidget(self._btn_circle)
        btn_corner = U.button("Put the circle back in the corner",
                              icon_name="refresh",
                              tip="Forget where the circle was dragged to "
                                  "under this preset. It goes back to the top "
                                  "right of the main screen.")
        btn_corner.clicked.connect(self._reset_hud)
        lay.addWidget(btn_corner)

        spread = QHBoxLayout()
        spread.addStretch(1)
        self._btn_spread = U.button(
            "Use these places for every preset that has none",
            icon_name="copy",
            tip="Give the circle and the alarm window the same places under "
                "every preset that has not been placed yet. A preset that was "
                "placed by hand keeps what it has.")
        self._btn_spread.clicked.connect(self._spread_places)
        spread.addWidget(self._btn_spread)
        outer.addLayout(spread)
        return box

    def _preset_word(self):
        return P.preset_word(self._win.preset())

    def _say_where(self):
        """Which preset these two places belong to, and whether they are set."""
        preset = self._win.preset()
        pos = C.placement(self._cfg, preset, "hud")
        geom = C.placement(self._cfg, preset, "alarm")
        own_hud = C.has_placement(self._cfg, preset, "hud")
        own_alarm = C.has_placement(self._cfg, preset, "alarm")
        circle = (f"at {pos[0]},{pos[1]}" if pos
                  else "in the top right corner")
        window = (f"at {geom[0]},{geom[1]}, {geom[2]}x{geom[3]} px" if geom
                  else "in the middle of the main screen")
        borrowed = "" if (own_hud and own_alarm) else \
            " Not placed under this preset yet, so it follows the last place " \
            "used anywhere."
        unset = [n for n in C.every_preset(self._cfg)
                 if n != preset and not (C.has_placement(self._cfg, n, "hud")
                                         and C.has_placement(self._cfg, n,
                                                             "alarm"))]
        self._btn_spread.setEnabled(bool(unset))
        self._btn_spread.setToolTip(
            f"{len(unset)} preset{'' if len(unset) == 1 else 's'} still "
            f"without a place of their own: "
            f"{', '.join(P.preset_word(n) for n in unset)}."
            if unset else
            "Every preset has been placed already.")
        self._where_said.setText(
            f'Each preset has its own places. Under "{self._preset_word()}" '
            f'the circle sits {circle} and the alarm window {window}.'
            f'{borrowed}')

    def _spread_places(self):
        preset = self._win.preset()
        filled = C.spread_placements(self._cfg, preset)
        self._win.save_soon()
        self._say_where()
        if not filled:
            self._win.log("Every preset already has places of its own.")
            return
        self._win.log(
            f'The places from "{self._preset_word()}" were given to '
            f'{len(filled)} preset{"" if len(filled) == 1 else "s"} that had '
            f'none: {", ".join(P.preset_word(n) for n in filled)}.')

    # ── loading and saving ──────────────────────────────────────────────────
    def reload(self):
        self._loading = True
        try:
            s = AL.read_settings(self._cfg)
            self._set_combo(self._mode, AL.FLASH_MODES, s["mode"])
            self._set_combo(self._cycle, AL.COLOR_CYCLES, s["cycle"])
            self._interval.setValue(s["interval"])
            self._duration.setValue(s["duration"])
            self._show_colour(s["colour"])

            names = AL.image_names()
            self._image.clear()
            if names:
                self._image.addItems(names)
                if s["image"] in names:
                    self._image.setCurrentText(s["image"])
            else:
                self._image.addItem("no pictures in the images folder")
            self._show_image()

            self._sound_on.setChecked(s["sound_on"])
            sounds = Snd.sound_names()
            self._sound.clear()
            self._sound.addItems(sounds)
            if s["sound"] in sounds:
                self._sound.setCurrentText(s["sound"])
            self._freq.setValue(s["freq"])
            self._sound_ms.setValue(s["sound_ms"])
            self._leadin.setValue(s["leadin"])
            self._reload_devices(keep=s["device"])
        finally:
            self._loading = False
        self._sync_enabled()
        # The two places belong to the preset, so they are re-read here: this
        # runs whenever the header's preset changes.
        self._say_where()

    @staticmethod
    def _set_combo(combo, keys, value):
        try:
            combo.setCurrentIndex(list(keys).index(value))
        except ValueError:
            combo.setCurrentIndex(0)

    def _reload_devices(self, keep=None):
        want = keep or self._device.currentText()
        names = Snd.output_devices()
        # A saved-but-absent speaker stays on the list, so an unplugged one does
        # not silently reset the choice to "whatever Windows likes".
        if want and want not in names:
            names.append(f"{want}  (not connected now)")
        self._loading_devices = True
        self._device.blockSignals(True)
        self._device.clear()
        self._device.addItems(names)
        for i, n in enumerate(names):
            if n.startswith(want or ""):
                self._device.setCurrentIndex(i)
                break
        self._device.blockSignals(False)

    def _changed(self, *_a):
        if self._loading:
            return
        cfg = self._cfg
        cfg["flash_mode"] = self._mode.currentData()
        cfg["color_cycle"] = self._cycle.currentData()
        cfg["flash_interval"] = round(float(self._interval.value()), 2)
        cfg["flash_duration"] = round(float(self._duration.value()), 1)
        if AL.image_names():
            cfg["image_file"] = self._image.currentText()
        cfg["sound_enabled"] = bool(self._sound_on.isChecked())
        cfg["sound_file"] = self._sound.currentText()
        cfg["sound_freq"] = int(self._freq.value())
        cfg["sound_duration"] = int(self._sound_ms.value())
        cfg["sound_leadin"] = int(self._leadin.value())
        device = self._device.currentText().split("  (not connected")[0]
        cfg["sound_device"] = device
        self._win.save_soon()
        self._show_image()
        self._sync_enabled()

    def _sync_enabled(self):
        image_mode = self._mode.currentData() == "image"
        self._image.setEnabled(image_mode)
        self._image_show.setEnabled(image_mode)
        sound_on = self._sound_on.isChecked()
        beep = self._sound.currentText() == "beep"
        for w in (self._sound, self._leadin, self._device):
            w.setEnabled(sound_on)
        # The pitch and the length belong to the synthesised beep; a wav file
        # has its own. A control that would change nothing is disabled rather
        # than left there looking broken.
        for w in (self._freq, self._sound_ms):
            w.setEnabled(sound_on and beep)

    # ── the colour and the picture ──────────────────────────────────────────
    def _show_colour(self, colour):
        col = QColor(colour)
        if not col.isValid():
            col = QColor(AL.DEFAULT_COLOR)
        self._colour_show.setStyleSheet(
            f"QLabel {{ background: {col.name()}; border: 1px solid #555; }}")
        self._colour_show.setToolTip(col.name())

    def _pick_colour(self):
        s = AL.read_settings(self._cfg)
        col = QColorDialog.getColor(QColor(s["colour"]), self,
                                    "The alarm colour")
        if col.isValid():
            self._cfg["flash_color"] = col.name()
            self._show_colour(col.name())
            self._win.save_soon()

    def _show_image(self):
        if self._mode.currentData() != "image":
            self._image_show.setPixmap(QPixmap())
            self._image_show.setText("not used — the whole window is filled "
                                     "with the colour")
            return
        path = AL.image_path(self._image.currentText())
        if path is None:
            self._image_show.setPixmap(QPixmap())
            self._image_show.setText("no picture — the alarm will fall back to "
                                     "a plain colour")
            return
        # Shown as it will really flash: the shape, filled with the colour, on
        # nothing. A plain thumbnail of the file would show artwork that never
        # appears on screen.
        try:
            from PIL import Image
            s = AL.read_settings(self._cfg)
            col = QColor(s["colour"])
            img = Image.open(path).convert("RGBA")
            img.thumbnail((360, 150), Image.LANCZOS)
            alpha = img.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
            shape = Image.new("RGBA", img.size,
                              (col.red(), col.green(), col.blue(), 255))
            shape.putalpha(alpha)
            data = shape.tobytes("raw", "RGBA")
            from PySide6.QtGui import QImage
            qimg = QImage(data, shape.width, shape.height, shape.width * 4,
                          QImage.Format.Format_RGBA8888).copy()
            self._image_show.setText("")
            self._image_show.setPixmap(QPixmap.fromImage(qimg))
        except Exception as exc:
            self._image_show.setPixmap(QPixmap())
            self._image_show.setText(f"the picture could not be read: {exc}")

    # ── the buttons ─────────────────────────────────────────────────────────
    def _test_sound(self):
        s = AL.read_settings(self._cfg)
        self._sound_said.setText("playing…")
        Snd.play(s["sound"], freq=s["freq"], duration_ms=s["sound_ms"],
                 leadin_ms=s["leadin"], device=s["device"],
                 report=self._said)

    def _said(self, message):
        """Called from the sound's OWN thread. Only emits — nothing else.

        It used to arm `QTimer.singleShot(0, ...)` here. A QTimer belongs to the
        thread that creates it, the sound thread has no Qt event loop, and the
        measurement is blunt: the callback never ran at all, so the "playing…"
        label never cleared. Emitting a signal from a foreign thread into a
        GUI-thread object is the one mechanism Qt supports for this.
        """
        self.sound_said.emit(str(message))

    def _show_sound_said(self, message):
        self._sound_said.setText(message)

    # ── placing the alarm window and the circle ─────────────────────────────
    #
    # NEITHER of these little boxes may be opened with `exec()`, and that is the
    # whole reason this section looks the way it does. `QDialog.exec()` sets
    # `WA_ShowModal` itself, whatever `setModal(False)` said when the box was
    # built — and an application-modal dialog blocks mouse events to every OTHER
    # top-level window in the program. The alarm window and the circle ARE other
    # top-level windows, so the press that asks Windows to drag them was never
    # delivered: both of them sat there and could not be moved at all.
    #
    # So the box is shown with `show()` and answered through `finished`. The
    # sibling trap is already on record in `ar_t.py`: there `hide()` ended an
    # `exec()` and every area the operator drew was thrown away.

    def _place(self):
        alarm = self._win._ensure_alarm()
        if alarm is None:
            return
        if self._raise_place_dialog():
            return
        alarm.start_placing()
        dlg = AL.PlaceAlarmDialog(self, alarm)
        self._place_dialog = dlg
        # Esc on the alarm window itself puts it away; the box has to go too,
        # or it is left asking about something that is no longer on screen.
        alarm.placing_cancelled.connect(dlg.reject)
        dlg.finished.connect(lambda code: self._placed(dlg, alarm, code))
        self._show_place_dialog(dlg)

    def _placed(self, dlg, alarm, code):
        self._place_dialog_done()
        keep = code == int(QDialog.DialogCode.Accepted)
        try:
            alarm.placing_cancelled.disconnect(dlg.reject)
        except (RuntimeError, TypeError):
            pass                    # already gone; nothing to unhook
        alarm.stop_placing(keep=keep)
        if keep:
            self._win.save_soon()
            g = C.placement(self._cfg, self._win.preset(), "alarm")
            if g:
                self._win.log(f'Under "{self._preset_word()}" the alarm will '
                              f"appear at {g[0]},{g[1]} and be "
                              f"{g[2]}x{g[3]} px.")
            self._say_where()
        S.retire(dlg)

    def _place_circle(self):
        """Show the real circle and let it be dragged to a new home."""
        hud = self._win.ensure_hud()
        if hud is None:
            return
        if self._raise_place_dialog():
            return
        hud.start_placing()
        dlg = AL.PlaceCircleDialog(self)
        self._place_dialog = dlg
        dlg.finished.connect(lambda code: self._placed_circle(dlg, hud, code))
        self._show_place_dialog(dlg)

    def _placed_circle(self, dlg, hud, code):
        self._place_dialog_done()
        keep = code == int(QDialog.DialogCode.Accepted)
        hud.stop_placing(keep=keep)
        if keep:
            self._win.save_soon()
            pos = C.placement(self._cfg, self._win.preset(), "hud")
            if pos:
                self._win.log(f'Under "{self._preset_word()}" the circle will '
                              f"sit at {pos[0]},{pos[1]}.")
            self._say_where()
        S.retire(dlg)

    def _raise_place_dialog(self):
        """True when one of these boxes is already open, and it was put in front.

        A button that does nothing at all is indistinguishable from a broken
        program, so the box that is already up is raised instead of refusing.
        """
        dlg = self._place_dialog
        if dlg is None:
            return False
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return True

    def _show_place_dialog(self, dlg):
        """Up, but NOT in front of the thing being placed.

        `activateWindow()` is deliberately not called: Windows only obeys a
        drag from the window that is in front, and that has to be the rectangle
        or the circle, not this box.
        """
        # The box no longer blocks the rest of the program — that is the point
        # — so the other placing button is greyed by hand. One thing is being
        # positioned at a time.
        self._btn_place.setEnabled(False)
        self._btn_circle.setEnabled(False)
        dlg.show()
        dlg.raise_()

    def _place_dialog_done(self):
        self._place_dialog = None
        self._btn_place.setEnabled(True)
        self._btn_circle.setEnabled(True)

    def _reset_hud(self):
        # This preset only, and recorded as a choice: an empty entry would be
        # read as "never placed" and would go back to borrowing the last place
        # used anywhere.
        C.clear_placement(self._cfg, self._win.preset(), "hud")
        self._win.save_soon()
        self._say_where()
        self._win.log(f'Under "{self._preset_word()}" the circle will go back '
                      f"to the corner of the main screen next time watching "
                      f"starts.")
