"""
daypicker.py — THE day-and-time picker. One calendar, one set of rules, every tab.

Image Tools used to carry three different calendars: the Slider's dialog (From/To
to the minute, but a "Multiple days" tick and no Ctrl/Ctrl+Shift), the Finder's
panel calendar (Ctrl/Ctrl+Shift, but one Hour dropdown and no way to confirm or
cancel) and a third one in PV Region Search. The painter, the factory and the
stylesheet existed in three near-identical copies, and a whole multi-day dialog
was never opened by anything. Picking a day meant relearning the widget per tab.

This module owns all of it. The rules, in full:

  1. ONE calendar. Never a Start/End pair side by side.
  2. Clicking
       plain            → exactly that one day, the rest of the selection drops
       Ctrl+click       → adds/removes ONE day — Saturdays and Sundays included
       Ctrl+Shift+click → the stretch from the last click to this one, XOR-ed in
                          (the same stretch again takes it back out), and the day
                          you started from is never dropped
     Weekends are skipped ONLY by the Ctrl+Shift stretch, and only while they are
     unticked in the Mon–Sun row (Mon–Fri ticked by default). A Ctrl+click always
     takes any day — a click that silently does nothing reads as a broken widget.
  3. MORE THAN ONE DAY switches the per-day time table on by itself. No tick to
     find. One day → no table; two or more → a row per day with its own From/To.
  4. A DAY JUST ADDED gets 08:00–19:00, or 08:00–the current hour if it is today.
     A day already in the list keeps the times it has.
  5. ALWAYS OK AND CANCEL. Nothing takes effect until OK.
  6. THE HOUSE LOOK — Monday first, white day cells, Sat/Sun red (spill-over days
     too), day names on a grey band.
  7. LIVE MODE IS THE IMAGE SLIDER'S ALONE (allow_live). Ticking it moves the day
     to today and leaves the times alone; only when nothing has been picked yet
     does it fall back to the last hour.

Qt-free and testable on their own: `compute_click`, `default_window_for`,
`PickSeg`, `hour_end_hm`, `seg_bounds_ns`. The dialog is a thin shell over them.

`is_t.py` re-exports the time primitives and subclasses `DayTimePicker` as
`DatePickerDialog` (adding only the archive camera scan), so Shot Finder and One
Moment keep calling exactly what they always called.

THIS FILE IS THE MASTER. A second, byte-identical copy lives in
`CSS Logger/daypicker.py`, because the Spectra tab uses this same calendar and the
builder only bundles .py files that sit in the program's own folder (Dev Tools
passes that folder as PyInstaller's only --paths). So: edit THIS file, then copy
it over the other one. `CSS Logger/testing/test_daypicker_sync.py` fails while the
two differ, which is the guard working — never patch the copy instead.
"""

from collections import namedtuple
from datetime import date as _date, datetime, timedelta
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, QEvent, QLocale, Qt, QTime, QTimer
from PySide6.QtGui import QColor, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QCalendarWidget, QCheckBox,
    QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QMenu, QMessageBox, QPushButton, QSpinBox, QStyle,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTimeEdit,
    QToolButton, QVBoxLayout, QWidget,
)

TZ_PRAGUE = ZoneInfo("Europe/Prague")

# Rule 4. The lab day, and the hours the archive actually holds anything worth
# looking at. Deliberately two plain numbers and not a setting: every tab has to
# open on the same window or "the same everywhere" means nothing.
DEFAULT_FROM_HOUR = 8
DEFAULT_TO_HOUR = 19

MONTHS = ("January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December")

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ── TIME PRIMITIVES (no Qt) ───────────────────────────────────────────────────
# One selected window on one calendar day, in Prague wall time. "to" is the
# EXCLUSIVE end, so a whole hour reads as an exact span — 12:00–13:00, never
# 12:00–12:59.
PickSeg = namedtuple("PickSeg", "date h_from m_from h_to m_to")


def seg_fields(seg) -> tuple:
    """Unpack a PickSeg — or a legacy (date, hour_from, hour_to) tuple."""
    if isinstance(seg, PickSeg):
        return seg.date, seg.h_from, seg.m_from, seg.h_to, seg.m_to
    d, hf, ht = seg
    return d, int(hf), 0, int(ht) + 1, 0


def hour_end_hm(hour: int) -> "tuple[int, int]":
    """Exclusive end of `hour` as (hour, minute) — the next whole hour.
    23 → 23:59, the closest a QTimeEdit can express to midnight; seg_bounds_ns
    stretches that back out to the next day's 00:00."""
    h = max(0, min(23, int(hour)))
    return (h + 1, 0) if h < 23 else (23, 59)


def ns_from_dt(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def seg_bounds_ns(seg) -> "tuple[int, int]":
    """[start_ns, end_ns) of one segment.

    The "to" time is the EXCLUSIVE end: 12:00–13:00 is exactly one hour and
    touches only the 12 h archive folder. A "to" of 23:59 means "to the end of
    the day" — a QTimeEdit cannot show 24:00 — and is stretched to midnight."""
    d, hf, mf, ht, mt = seg_fields(seg)
    if (ht, mt) == (23, 59):
        ht, mt = 24, 0
    midnight = datetime(d.year, d.month, d.day, tzinfo=TZ_PRAGUE)
    start = midnight + timedelta(hours=hf, minutes=mf)
    end = midnight + timedelta(hours=ht, minutes=mt)
    if end <= start:
        end = start + timedelta(minutes=1)
    return ns_from_dt(start), ns_from_dt(end)


def default_window_for(day: _date, now: "datetime | None" = None
                       ) -> "tuple[int, int, int, int]":
    """Rule 4 — the window a day gets the moment it is added.

    A past day is the whole lab day, 08:00–19:00. TODAY cannot be read past the
    current hour (the archive has not been written yet), so it ends at the hour
    it is now — and never earlier than 09:00, or a day picked at 08:20 would open
    on an empty window and read as "the archive is broken"."""
    now = now or datetime.now(TZ_PRAGUE)
    if day != now.date():
        return (DEFAULT_FROM_HOUR, 0, DEFAULT_TO_HOUR, 0)
    end_h = max(DEFAULT_FROM_HOUR + 1, min(now.hour + 1, 23))
    return (DEFAULT_FROM_HOUR, 0, end_h, 0)


def last_hour_window(now: "datetime | None" = None) -> "tuple[int, int, int, int]":
    """Rule 7 — what Live mode falls back to when nothing has been picked yet."""
    now = now or datetime.now(TZ_PRAGUE)
    return (now.hour, 0) + hour_end_hm(now.hour)


def date_range(d1: _date, d2: _date) -> "list[_date]":
    if d2 < d1:
        d1, d2 = d2, d1
    out, cur = [], d1
    while cur <= d2:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def compute_click(days: "list[_date]", clicked: _date, anchor: "_date | None",
                  ctrl: bool, shift: bool,
                  weekday_gate: "set[int] | None" = None) -> "list[_date]":
    """Rule 2, whole and on its own — no widget, no Qt, no state.

    `days` is what is selected now, `clicked` the day just clicked, `anchor` the
    day clicked before it. `weekday_gate` is the set of weekday numbers (Mon=0 …
    Sun=6) a Ctrl+Shift stretch may add; it gates NOTHING else.

    Returns the new selection, sorted.
    """
    cur = list(days)
    if ctrl and shift:
        a = anchor if anchor is not None else clicked
        allowed = weekday_gate if weekday_gate is not None else set(range(7))
        stretch = [d for d in date_range(a, clicked) if d.weekday() in allowed]
        # XOR the stretch in, so dragging back over it takes it out again — but
        # the day you started from is NEVER dropped: click Mon, then Ctrl+Shift
        # Fri, and Mon stays with Tue–Fri added.
        result = {d: d for d in cur}
        for d in stretch:
            if d == a:
                continue
            if d in result:
                del result[d]
            else:
                result[d] = d
        result[a] = a
        out = list(result)
    elif ctrl:
        # Any day, weekends included. The old rule refused Sat/Sun here and the
        # click simply did nothing, which reads as a dead widget.
        out = [d for d in cur if d != clicked] if clicked in cur else cur + [clicked]
    else:
        out = [clicked]
    return sorted(out)


def qdate_to_date(qd: QDate) -> _date:
    return _date(qd.year(), qd.month(), qd.day())


def date_to_qdate(d: _date) -> QDate:
    return QDate(d.year, d.month, d.day)


# ── LOOK ──────────────────────────────────────────────────────────────────────
# Rule 6. Without this a calendar inherits the app's dark stylesheet and comes
# out red/brown with unreadable cells. The grey QHeaderView band is what the
# week-number column needs; the rest is the multi-select look.
CAL_STYLE = """
QCalendarWidget QWidget { background: #ffffff; color: #111; }
QCalendarWidget QAbstractItemView:enabled {
    background: #ffffff; color: #111;
    selection-background-color: #1565C0; selection-color: white;
}
QCalendarWidget QHeaderView { background: #bdbdbd; }
QCalendarWidget QHeaderView::section {
    background: #bdbdbd; color: #111;
    font-weight: bold; padding: 3px 0px; border: none;
    border-bottom: 1px solid #9a9a9a; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: #eeeeee; }
QCalendarWidget QToolButton {
    color: #222; background: transparent;
    font-weight: 700; font-size: 13px;
    border-radius: 3px; padding: 3px 6px;
}
QCalendarWidget QToolButton:hover { background: #d0d0d0; }
QCalendarWidget QSpinBox {
    color: #222; background: #eeeeee; border: none; font-weight: 700;
}
QCalendarWidget QMenu { color: #111; background: #fff; }
"""

CHECKBOX_STYLE = """
QCheckBox { spacing: 6px; padding: 2px 4px; font-weight: 600; color: #111; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 2px solid #4a4a4a;
    border-radius: 3px; background: #fff; }
QCheckBox::indicator:hover { border: 2px solid #2d7dff; background: #f4f8ff; }
QCheckBox::indicator:checked { border: 2px solid #2d7dff; background: #2d7dff; }
QCheckBox:disabled { color: #9a9a9a; }
QCheckBox::indicator:disabled { border: 2px solid #c9c9c9; background: #f0f0f0; }
QCheckBox::indicator:checked:disabled { border: 2px solid #a9c6ee; background: #a9c6ee; }
"""

# A table must look like a table: light cells, dark text, a header row that is
# visibly a header. Every background here names its own foreground — a tint with
# an undecided text colour is how the unreadable dark-red row happens.
TABLE_STYLE = """
QTableWidget { background: #ffffff; color: #111; gridline-color: #d0d0d0;
    border: 1px solid #b0b0b0; selection-background-color: #cfe3ff;
    selection-color: #111; }
QTableWidget::item { padding: 2px 4px; color: #111; background: #ffffff; }
QTableWidget::item:alternate { background: #f4f4f4; color: #111; }
QHeaderView::section { background: #e0e0e0; color: #111; font-weight: 700;
    padding: 3px 4px; border: none; border-bottom: 1px solid #9a9a9a;
    border-right: 1px solid #cccccc; }
QTableCornerButton::section { background: #e0e0e0; border: none; }
"""

TIMEEDIT_STYLE = (
    "QTimeEdit { background: #ffffff; color: #111; border: 1px solid #aaa;"
    " border-radius: 3px; padding: 1px 4px; font-weight: 600; }"
    "QTimeEdit:disabled { background: #f0f0f0; color: #9a9a9a; }")

BUTTON_STYLE = (
    "QPushButton { background: #f0f0f0; color: #1e2530; border: 1px solid #aaa;"
    " border-radius: 3px; padding: 3px 10px; font-weight: 600; }"
    "QPushButton:hover { background: #e0e0e0; }"
    "QPushButton:disabled { color: #9a9a9a; background: #f6f6f6; }")

# The dialog paints its OWN light ground and its own text colours. The app runs a
# dark stylesheet, and a calendar that only styles its cells ends up with white
# labels on white, dark-blue "Live mode" on near-black, and grey hint lines that
# cannot be read at all — measured, not guessed. A stylesheet set on the widget
# beats the one set on QApplication, so naming the dialog by objectName is what
# makes these rules stick for the dialog AND for its subclasses.
DIALOG_OBJECT_NAME = "dayTimePicker"
DIALOG_STYLE = f"""
QDialog#{DIALOG_OBJECT_NAME} {{ background: #f2f2f2; color: #111; }}
QDialog#{DIALOG_OBJECT_NAME} QLabel {{ background: transparent; color: #111; }}
QDialog#{DIALOG_OBJECT_NAME} QCheckBox {{ background: transparent; color: #111; }}
QDialog#{DIALOG_OBJECT_NAME} QDialogButtonBox {{ background: transparent; }}
"""

HINT_STYLE = "color:#5a5a5a; font-size:10px;"
# For the light picker dialog.
SECTION_STYLE = "color:#444; font-size:10px; font-weight:700; letter-spacing:1px;"
# For a caller whose panel is dark (PV Search's sidebar). Same row, same words —
# only the ink changes, and it is stated rather than inherited.
SECTION_STYLE_ON_DARK = ("color:#c8c8c8; font-size:10px; font-weight:700;"
                         " letter-spacing:1px; background: transparent;")


class MultiSelectDelegate(QStyledItemDelegate):
    """Paints the day cells: selected = blue fill, Sat/Sun = red text, the focused
    day = a blue outline. initStyleOption strips State_Selected from every cell
    that is not in the selection, so Qt's own highlight never bleeds through and
    the painted days are exactly the ones the caller asked for."""

    def __init__(self, cal: QCalendarWidget):
        super().__init__(cal)
        self._cal = cal
        self._selected_keys: set = set()      # (year, month, day)
        self._focus_key = None                # (year, month, day) | None

    def _first_cell(self) -> "tuple[int, int]":
        """Row/column of the first *day* cell. Qt drops the header row when
        NoHorizontalHeader is set and the week-number column when
        NoVerticalHeader is set, so the grid does not always start at (1, 1)."""
        first_row = 1
        if (self._cal.horizontalHeaderFormat()
                == QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader):
            first_row = 0
        first_col = 1
        if (self._cal.verticalHeaderFormat()
                == QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader):
            first_col = 0
        return first_row, first_col

    def _date_for_index(self, index) -> "QDate | None":
        # The model knows the real date for in-month cells — always prefer it.
        d = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(d, QDate) and d.isValid():
            return d
        first_row, first_col = self._first_cell()
        if index.row() < first_row or index.column() < first_col:
            return None                       # header row / week-number column
        first = QDate(self._cal.yearShown(), self._cal.monthShown(), 1)
        if not first.isValid():
            return None
        offset = (first.dayOfWeek() - self._cal.firstDayOfWeek().value) % 7
        row = index.row() - first_row
        # Qt shifts the whole grid one week back when the 1st sits in the very
        # first column (QCalendarModel::dateForCell, MinimumDayOffset = 1), so
        # row 0 then shows the PREVIOUS week. Without this the painted days are a
        # week off — clicking one day highlighted a different one.
        if offset < 1:
            row -= 1
        start = first.addDays(-offset)
        return start.addDays(row * 7 + (index.column() - first_col))

    def _repaint(self):
        view = self._cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if view is not None:
            view.viewport().update()

    def set_selected(self, dates: "list[QDate]"):
        self._selected_keys = {(d.year(), d.month(), d.day()) for d in dates}
        self._repaint()

    def set_focus_date(self, d: "QDate | None"):
        self._focus_key = None if d is None else (d.year(), d.month(), d.day())
        self._repaint()

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        d = self._date_for_index(index)
        if d is not None and (d.year(), d.month(), d.day()) not in self._selected_keys:
            option.state = option.state & ~QStyle.StateFlag.State_Selected

    def paint(self, painter, option, index):
        d = self._date_for_index(index)
        if d is None:
            super().paint(painter, option, index)
            return
        key = (d.year(), d.month(), d.day())
        is_weekend = d.dayOfWeek() in (6, 7)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if key in self._selected_keys:
            painter.save()
            painter.fillRect(option.rect, QColor("#1565C0"))
            painter.setPen(QColor("#ffcccc") if is_weekend else QColor("#ffffff"))
            painter.setFont(option.font)
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()
        else:
            super().paint(painter, option, index)
            if is_weekend:
                painter.save()
                painter.setPen(QColor("#cc0000"))
                painter.setFont(option.font)
                painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, text)
                painter.restore()
        if key == self._focus_key:
            painter.save()
            painter.setPen(QPen(QColor("#1565C0"), 2))
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
            painter.restore()


class NoScrollCalendar(QCalendarWidget):
    """A calendar whose views ignore the mouse wheel — inside a scrolling panel a
    wheel over the calendar used to flip the month instead of scrolling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._noscroll_installed = set()

    def _install_on_all_children(self):
        for child in self.findChildren(QAbstractScrollArea):
            if id(child) not in self._noscroll_installed:
                child.installEventFilter(self)
                child.viewport().installEventFilter(self)
                self._noscroll_installed.add(id(child))

    def showEvent(self, event):
        super().showEvent(event)
        self._install_on_all_children()

    def wheelEvent(self, event):
        event.accept()                      # consume — do NOT propagate

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            event.accept()
            return True
        return super().eventFilter(obj, event)


def make_calendar(initial: "QDate | None" = None) -> "tuple[QFrame, QCalendarWidget]":
    """Return (wrapper_frame, cal): one calendar with a grey day-name header, a
    light nav bar (◀ · month menu · year · ▶) and the multi-select delegate
    installed. Qt's own header and nav bar are hidden and replaced, because
    neither can be styled into the house look reliably.

    Selection is driven by the caller through `cal.day_delegate.set_selected()`.
    """
    cal = NoScrollCalendar()
    cal.setGridVisible(True)
    cal.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom))
    cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader)
    if initial:
        cal.setSelectedDate(initial)
    cal.setStyleSheet(CAL_STYLE)

    nav_internal = cal.findChild(QWidget, "qt_calendar_navigationbar")
    if nav_internal:
        nav_internal.hide()

    view = cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
    if view is not None:
        cal.day_delegate = MultiSelectDelegate(cal)
        view.setItemDelegate(cal.day_delegate)
        # Kept under the old name too: if_t/is_t code has reached for
        # cal._wk_delegate since the Spectra port and there is no reason to make
        # every call site churn.
        cal._wk_delegate = cal.day_delegate

    nav_row = QWidget()
    nav_row.setAutoFillBackground(True)
    nav_pal = nav_row.palette()
    nav_pal.setColor(QPalette.ColorRole.Window, QColor("#eeeeee"))
    nav_row.setPalette(nav_pal)
    # The palette alone loses to the app's dark `QWidget { background: … }`, and
    # the ◀ ▶ arrows then sit dark-on-dark — an invisible button.
    nav_row.setStyleSheet("QWidget { background: #eeeeee; }")
    nav_lay = QHBoxLayout(nav_row)
    nav_lay.setContentsMargins(4, 3, 4, 3)
    nav_lay.setSpacing(4)

    _arrow_qss = ("QToolButton { border: none; color: #1e2530; font-weight: bold;"
                  " font-size: 18px; padding: 1px 6px; }"
                  "QToolButton:hover { background: #d0d0d0; border-radius: 3px; }")
    prev_btn = QToolButton(); prev_btn.setText("◀"); prev_btn.setStyleSheet(_arrow_qss)
    next_btn = QToolButton(); next_btn.setText("▶"); next_btn.setStyleSheet(_arrow_qss)
    month_btn = QPushButton(); month_btn.setMinimumWidth(100)
    month_btn.setStyleSheet(
        "QPushButton { border: 1px solid #aaa; border-radius: 3px; background: #f5f5f5;"
        " color: #111; font-weight: bold; font-size: 12px; padding: 2px 10px; }"
        "QPushButton:hover { background: #e0e0e0; }")
    year_spin = QSpinBox()
    year_spin.setRange(2000, 2100)
    year_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    year_spin.setStyleSheet(
        "QSpinBox { border: 1px solid #aaa; border-radius: 3px; background: #f5f5f5;"
        " color: #111; padding: 1px 4px; font-weight: bold; font-size: 12px; }")
    year_spin.setFixedWidth(60)

    nav_lay.addWidget(prev_btn); nav_lay.addStretch()
    nav_lay.addWidget(month_btn); nav_lay.addWidget(year_spin)
    nav_lay.addStretch(); nav_lay.addWidget(next_btn)

    def _update_nav():
        month_btn.setText(MONTHS[cal.monthShown() - 1])
        year_spin.blockSignals(True)
        year_spin.setValue(cal.yearShown())
        year_spin.blockSignals(False)

    def _on_month_btn():
        menu = QMenu(month_btn)
        menu.setStyleSheet("QMenu { background: #fff; color: #111; }"
                           "QMenu::item:selected { background: #cfe3ff; color: #111; }")
        for i, name in enumerate(MONTHS, 1):
            menu.addAction(name).setData(i)
        chosen = menu.exec(month_btn.mapToGlobal(month_btn.rect().bottomLeft()))
        if chosen:
            cal.setCurrentPage(cal.yearShown(), chosen.data())

    prev_btn.clicked.connect(cal.showPreviousMonth)
    next_btn.clicked.connect(cal.showNextMonth)
    month_btn.clicked.connect(_on_month_btn)
    year_spin.valueChanged.connect(lambda y: cal.setCurrentPage(y, cal.monthShown()))
    cal.currentPageChanged.connect(lambda _y, _m: _update_nav())
    _update_nav()

    hdr_row = QWidget()
    hdr_row.setAutoFillBackground(True)
    hdr_pal = hdr_row.palette()
    hdr_pal.setColor(QPalette.ColorRole.Window, QColor("#bdbdbd"))
    hdr_row.setPalette(hdr_pal)
    hdr_row.setStyleSheet("QWidget { background: #bdbdbd; }")
    hdr_lay = QHBoxLayout(hdr_row)
    hdr_lay.setContentsMargins(0, 0, 0, 0)
    hdr_lay.setSpacing(0)
    for i, name in enumerate(DAY_NAMES):
        lbl = QLabel(name)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: %s; font-weight: 700; padding: 4px 0;"
                          % ("#cc0000" if i >= 5 else "#111111"))
        hdr_lay.addWidget(lbl, stretch=1)

    wrapper = QFrame()
    wrapper.setStyleSheet("QFrame { border: 1px solid #b0b0b0; border-radius: 3px;"
                          " background: #ffffff; }")
    w_lay = QVBoxLayout(wrapper)
    w_lay.setContentsMargins(0, 0, 0, 0)
    w_lay.setSpacing(0)
    w_lay.addWidget(nav_row)
    w_lay.addWidget(hdr_row)
    w_lay.addWidget(cal)
    return wrapper, cal


def weekday_gate_row(tooltip: str = "") -> "tuple[QWidget, list[QCheckBox]]":
    """The Mon–Sun row that gates the Ctrl+Shift stretch (rule 2). Day names sit
    above the boxes, weekends red so the row reads the same way as the calendar.
    Mon–Fri on, so a stretch across three weeks skips the weekends by default."""
    tooltip = tooltip or (
        "Click = one day.\n"
        "Ctrl+click = add or remove one day (weekends included).\n"
        "Ctrl+Shift+click = the stretch from the last click — only the weekdays\n"
        "ticked here. Clicking the same stretch again takes it back out.")
    host = QFrame()
    # Its OWN light ground, not the host's. This row goes into the light picker
    # dialog and into PV Search's dark sidebar, and dark-on-dark is how the Mon–Fri
    # names disappeared there. Painting the band here makes it read the same in
    # both, which is the whole point of having one widget.
    # Selected by objectName, NOT by type: QLabel inherits QFrame, so a plain
    # `QFrame { border: … }` would draw a box around every day name in the row.
    host.setObjectName("dayGateRow")
    host.setStyleSheet("QFrame#dayGateRow { background: #f2f2f2;"
                       " border: 1px solid #b0b0b0; border-radius: 3px; }")
    grid = QGridLayout(host)
    grid.setHorizontalSpacing(2); grid.setVerticalSpacing(1)
    grid.setContentsMargins(3, 3, 3, 3)
    checks: "list[QCheckBox]" = []
    for i, name in enumerate(DAY_NAMES):
        lbl = QLabel(name)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("background: transparent; font-size:9px; font-weight:700;"
                          " color:%s;" % ("#cc0000" if i >= 5 else "#333"))
        lbl.setToolTip(tooltip)
        grid.addWidget(lbl, 0, i)
        cb = QCheckBox()
        cb.setChecked(i < 5)                # Mon–Fri on, Sat/Sun off
        cb.setStyleSheet(CHECKBOX_STYLE)
        cb.setToolTip(tooltip)
        grid.addWidget(cb, 1, i, alignment=Qt.AlignmentFlag.AlignCenter)
        grid.setColumnStretch(i, 1)
        checks.append(cb)
    return host, checks


# ── THE DIALOG ────────────────────────────────────────────────────────────────
class DayTimePicker(QDialog):
    """One calendar, From/To to the minute, a per-day table that appears by itself
    the moment a second day is picked, and always OK / Cancel.

    Public API — every name here is called from Image Slider, Shot Finder and One
    Moment and must keep working:
        is_multiday() · is_online_mode() · selected_times() · selected_hours()
        selected_date_obj() · selected_dates() · selected_segments()
        selected_windows() · selected_axis()

    Subclasses hook `_on_selection_changed()` to react to a new day/time set
    (the Slider scans the archive for cameras there).
    """

    def __init__(self, parent=None, hour_from_init=None, hour_to_init=None,
                 min_from_init=None, min_to_init=None, init_date=None,
                 init_segments=None, allow_live: bool = False,
                 title: str = "Time window"):
        super().__init__(parent)
        self.setWindowTitle(title)
        # Named so DIALOG_STYLE reaches this dialog and every subclass of it.
        self.setObjectName(DIALOG_OBJECT_NAME)
        self.setStyleSheet(DIALOG_STYLE)
        self._allow_live = bool(allow_live)

        now_dt = datetime.now(TZ_PRAGUE)
        self._now = now_dt
        init_day = init_date or now_dt.date()

        # Rule 4 supplies the window for a day nobody has picked before; an
        # explicit hour handed in by the caller (the last pick, a folder the tab
        # was opened on) outranks it — reopening on a different window than the
        # one you left reads as the app forgetting.
        d_hf, d_mf, d_ht, d_mt = default_window_for(init_day, now_dt)
        # A window handed in by the caller is a window the user already chose —
        # the pick they left last time, or the tab's own working span. It counts
        # as "chosen", so days added later follow IT rather than snapping back to
        # 08:00–19:00. Only a genuinely fresh open falls through to rule 4.
        caller_gave_window = hour_from_init is not None or hour_to_init is not None
        if hour_from_init is None: hour_from_init = d_hf
        if hour_to_init is None:   hour_to_init = d_ht
        if min_from_init is None:  min_from_init = d_mf
        if min_to_init is None:    min_to_init = d_mt

        # ── state ─────────────────────────────────────────────────────────────
        self._days: "list[_date]" = [init_day]
        self._anchor: "_date | None" = init_day
        # date → (h_from, m_from, h_to, m_to). Every picked day is in here; this
        # IS the answer the dialog gives back.
        self._per_day: dict = {init_day: (hour_from_init, min_from_init,
                                          hour_to_init, min_to_init)}
        # Days the user typed a time for in the table. They are pinned: moving the
        # global From/To must not drag them along, or one keystroke wipes every
        # per-day window that was just set by hand.
        self._pinned: "set[_date]" = set()
        # True once a window has been CHOSEN — typed into From/To here, or handed
        # in by the caller. After that, a newly added day follows that window
        # instead of rule 4's default: typing 10:00–12:00 and then clicking another
        # day must not throw the typing away. Until then, rule 4 decides.
        self._global_touched = caller_gave_window
        self._building = True               # suppress handlers during __init__

        # ── calendar ──────────────────────────────────────────────────────────
        self._cal_frame, self.cal = make_calendar(date_to_qdate(init_day))
        self.cal.setMinimumWidth(270)
        self.cal.clicked.connect(self._on_calendar_clicked)

        self._gate_row, self._wd_checks = weekday_gate_row()
        for cb in self._wd_checks:
            cb.toggled.connect(self._on_gate_changed)

        # ── times ─────────────────────────────────────────────────────────────
        self.time_from = QTimeEdit(self)
        self.time_from.setDisplayFormat("HH:mm")
        self.time_from.setTime(QTime(max(0, min(23, int(hour_from_init))),
                                     max(0, min(59, int(min_from_init)))))
        self.time_from.setFixedWidth(74)
        self.time_from.setStyleSheet(TIMEEDIT_STYLE)
        self.time_to = QTimeEdit(self)
        self.time_to.setDisplayFormat("HH:mm")
        self.time_to.setTime(QTime(max(0, min(23, int(hour_to_init))),
                                   max(0, min(59, int(min_to_init)))))
        self.time_to.setFixedWidth(74)
        self.time_to.setStyleSheet(TIMEEDIT_STYLE)
        self.time_from.timeChanged.connect(self._on_times_changed)
        self.time_to.timeChanged.connect(self._on_times_changed)

        btn_now = QPushButton("Now")
        btn_now.setToolTip("Move the calendar to today (nothing else changes)")
        btn_now.setFixedWidth(52)
        btn_now.setStyleSheet(BUTTON_STYLE)
        btn_now.clicked.connect(self._go_to_now)

        self.cb_now = QCheckBox("Live mode")
        self.cb_now.setToolTip(
            "Load the picked window and keep following new images as they arrive.\n"
            "The From/To times are left exactly as set — only the date moves to "
            "today, because live mode can only follow today's folders.\n"
            "Off = the window is loaded once and nothing follows live.")
        self.cb_now.setStyleSheet(
            CHECKBOX_STYLE + "QCheckBox { font-size: 11px; color: #1a3a8f; }")
        self.cb_now.setVisible(self._allow_live)
        self.cb_now.toggled.connect(self._on_live_toggled)

        # ── per-day table (rule 3: it appears by itself) ───────────────────────
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Date", "From", "To", ""])
        self._table.setStyleSheet(TABLE_STYLE)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(c, 80)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(3, 30)
        # Room for six rows before it scrolls — a week of shifts is the common
        # pick and having to scroll a four-row list reads as the table being full.
        self._table.setMinimumHeight(120)
        self._table.setMaximumHeight(250)

        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet("color:#222; font-size:11px; font-weight:600;")

        self._btn_clear = QPushButton("Clear")
        self._btn_clear.setToolTip("Back to a single day")
        self._btn_clear.setFixedWidth(60)
        self._btn_clear.setStyleSheet(BUTTON_STYLE)
        self._btn_clear.clicked.connect(self._clear_extra_days)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel, self)
        btns.setStyleSheet(BUTTON_STYLE)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)

        # ── layout ────────────────────────────────────────────────────────────
        hint = QLabel("Click = one day · Ctrl+click = add a day · "
                      "Ctrl+Shift+click = a stretch of days")
        hint.setStyleSheet(HINT_STYLE)

        gate_lbl = QLabel("Ctrl+Shift stretch adds:")
        gate_lbl.setStyleSheet(SECTION_STYLE)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("From:")); time_row.addWidget(self.time_from)
        time_row.addSpacing(10)
        time_row.addWidget(QLabel("To:")); time_row.addWidget(self.time_to)
        time_row.addSpacing(10); time_row.addWidget(btn_now)
        if self._allow_live:
            time_row.addSpacing(10); time_row.addWidget(self.cb_now)
        time_row.addStretch(1)

        count_row = QHBoxLayout()
        count_row.addWidget(self._count_lbl, 1)
        count_row.addWidget(self._btn_clear)

        lay = QVBoxLayout(self)
        lay.addWidget(self._cal_frame)
        lay.addWidget(hint)
        lay.addWidget(gate_lbl)
        lay.addWidget(self._gate_row)
        lay.addLayout(time_row)
        lay.addLayout(count_row)
        lay.addWidget(self._table, 1)      # the table takes any spare height
        lay.addWidget(btns)

        if init_segments:
            self._restore_segments(init_segments)

        self._building = False
        self._refresh()

    # ── restoring a previous pick ─────────────────────────────────────────────
    def _restore_segments(self, segments):
        """Reopen on the day list of the previous pick, per-day windows and all."""
        segs = sorted((PickSeg(*seg_fields(s)) for s in segments),
                      key=lambda s: (s.date, s.h_from, s.m_from))
        if not segs:
            return
        self._days = sorted({s.date for s in segs})
        self._anchor = self._days[-1]
        self._per_day = {s.date: (s.h_from, s.m_from, s.h_to, s.m_to) for s in segs}
        # A day whose window differs from the global From/To was given its own
        # times last time round; keep it pinned so a From/To change here does not
        # silently overwrite what the user set before.
        glob = self.selected_times()
        self._pinned = {d for d, w in self._per_day.items() if w != glob}
        if len(self._days) == 1:
            self._show_times(self._per_day[self._days[0]])

    # ── calendar interaction ──────────────────────────────────────────────────
    def _gate(self) -> "set[int]":
        return {i for i, cb in enumerate(self._wd_checks) if cb.isChecked()}

    def _on_calendar_clicked(self, qd: QDate):
        from PySide6.QtWidgets import QApplication
        mods = QApplication.keyboardModifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        clicked = qdate_to_date(qd)

        new_days = compute_click(self._days, clicked, self._anchor,
                                 ctrl, shift, self._gate())
        self._anchor = clicked
        self._set_days(new_days)

    def _set_days(self, days: "list[_date]"):
        """Adopt a new day list.

        A day that has just appeared gets rule 4's window — 08:00–19:00, or
        08:00–now for today — unless the user has already chosen a window by hand,
        in which case it follows that. A day that is leaving takes its window with
        it, so re-adding it later starts clean rather than resurrecting an old one.
        """
        days = sorted(set(days)) or [self._anchor or self._now.date()]
        for d in days:
            if d not in self._per_day:
                self._per_day[d] = (self.selected_times() if self._global_touched
                                    else default_window_for(d, self._now))
        for d in list(self._per_day):
            if d not in days:
                del self._per_day[d]
                self._pinned.discard(d)
        self._days = days
        # One day → the From/To fields ARE that day's window, in both directions.
        # Single-day callers (Shot Finder, One Moment) read selected_times().
        if len(days) == 1:
            self._show_times(self._per_day[days[0]])
        # Live mode can only follow today's folders, so picking another day
        # leaves it — otherwise the viewer would poll a finished day forever.
        if (self.cb_now.isChecked()
                and (len(days) > 1 or days[0] != self._now.date())):
            self.cb_now.setChecked(False)
        self._refresh()

    def _clear_extra_days(self):
        """Back to a single day — the one that leads."""
        keep = self._anchor if self._anchor in self._days else self._days[-1]
        self._set_days([keep])

    def _go_to_now(self):
        """Now — move the calendar to today, and nothing else. It must never
        change a mode, a window or the day list."""
        self.cal.setSelectedDate(date_to_qdate(datetime.now(TZ_PRAGUE).date()))
        self._refresh()

    # ── time controls ─────────────────────────────────────────────────────────
    def _show_times(self, hm: "tuple[int, int, int, int]"):
        """Put a window into the From/To fields without it reading as a user edit."""
        hf, mf, ht, mt = hm
        for w, t in ((self.time_from, QTime(hf, mf)),
                     (self.time_to, QTime(min(23, ht), mt))):
            w.blockSignals(True)
            w.setTime(t)
            w.blockSignals(False)

    def _on_times_changed(self):
        if self._building:
            return
        # "To" is the exclusive end, so From == To is an EMPTY window: keep the
        # two fields at least one whole hour apart by pushing the other one.
        if self.time_from.time() >= self.time_to.time():
            if self.sender() is self.time_to:
                t = self.time_to.time().addSecs(-3600)
                self.time_from.blockSignals(True)
                self.time_from.setTime(max(QTime(0, 0), t))
                self.time_from.blockSignals(False)
            else:
                t = self.time_from.time().addSecs(3600)
                self.time_to.blockSignals(True)
                self.time_to.setTime(t if t > self.time_from.time() else QTime(23, 59))
                self.time_to.blockSignals(False)
        # From/To is the bulk setter: it moves every day that has NOT been given
        # its own time in the table. Days typed by hand stay where they were put.
        self._global_touched = True
        glob = self.selected_times()
        for d in self._days:
            if d not in self._pinned:
                self._per_day[d] = glob
        self._refresh()

    def _on_gate_changed(self):
        if not self._building:
            self._refresh_count()

    def _on_live_toggled(self, on: bool):
        """Rule 7. Ticking Live must NOT rewrite a window the user has typed —
        that used to snap From back to the start of the current hour, so the
        history before it was never loaded. It only moves the date to today.

        The one exception is a picker still sitting on its untouched default: with
        nothing chosen yet, "follow what arrives" means the last hour."""
        if not on or self._building:
            return
        today = datetime.now(TZ_PRAGUE).date()
        if self._days != [today]:
            self._days = [today]
            self._per_day = {today: (self.selected_times() if self._global_touched
                                     else default_window_for(today, self._now))}
            self._pinned.clear()
            self._anchor = today
        if not self._global_touched and today not in self._pinned:
            # Nothing has been chosen yet, so "follow what arrives" means the last
            # hour. A window the user DID choose is left exactly as it is — ticking
            # Live used to snap From back to the start of the current hour, and the
            # history before it was then never loaded.
            self._per_day[today] = last_hour_window(self._now)
        self._show_times(self._per_day[today])
        self._refresh()

    # ── the per-day table ─────────────────────────────────────────────────────
    def _window_for(self, d: _date) -> "tuple[int, int, int, int]":
        """The window of one day: its own if it was edited by hand, otherwise the
        global From/To — which is what makes one From/To cover the selection."""
        return self._per_day.get(d) or self.selected_times()

    def _refresh(self):
        self._refresh_calendar()
        self._refresh_table()
        self._refresh_count()
        self._on_selection_changed()

    def _refresh_calendar(self):
        deleg = getattr(self.cal, "day_delegate", None)
        if deleg is None:
            return
        deleg.set_selected([date_to_qdate(d) for d in self._days])
        deleg.set_focus_date(date_to_qdate(self._anchor)
                             if (self._anchor and len(self._days) > 1) else None)

    def _refresh_table(self):
        # Rule 3 — the table IS the multi-day switch. Nothing to tick.
        multi = self.is_multiday()
        was_visible = self._table.isVisible()
        self._table.setVisible(multi)
        self._btn_clear.setVisible(multi)
        if not multi:
            self._table.setRowCount(0)
            if was_visible:
                # The table just went away — shrink back, or the dialog keeps a
                # tall empty gap where the rows used to be.
                QTimer.singleShot(0, self.adjustSize)
            return
        grew = not was_visible or self._table.rowCount() != len(self._days)
        if grew:
            # Let the window take the rows instead of scrolling a four-row list —
            # up to the table's own maximum, after which scrolling is right.
            QTimer.singleShot(0, self.adjustSize)
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for d in self._days:
            hf, mf, ht, mt = self._window_for(d)
            r = self._table.rowCount()
            self._table.insertRow(r)

            item = QTableWidgetItem(d.strftime("%a %d.%m.%Y"))
            item.setForeground(QColor("#cc0000") if d.weekday() >= 5
                               else QColor("#111111"))
            item.setToolTip(d.strftime("%A %d %B %Y"))
            self._table.setItem(r, 0, item)

            self._table.setCellWidget(r, 1, self._row_time_edit(d, QTime(hf, mf), True))
            self._table.setCellWidget(r, 2, self._row_time_edit(
                d, QTime(min(23, ht), mt), False))

            rm = QPushButton("✕")
            rm.setFixedSize(24, 22)
            rm.setToolTip("Take this day out")
            rm.setStyleSheet(BUTTON_STYLE + "QPushButton { padding: 0; font-size: 10px; }")
            rm.clicked.connect(lambda _c=False, dd=d: self._remove_day(dd))
            self._table.setCellWidget(r, 3, rm)
        self._table.blockSignals(False)

    def _row_time_edit(self, d: _date, t: QTime, is_from: bool) -> QTimeEdit:
        w = QTimeEdit()
        w.setDisplayFormat("HH:mm")
        w.setTime(t)
        w.setStyleSheet(TIMEEDIT_STYLE)
        w.timeChanged.connect(lambda nt, dd=d, f=is_from: self._on_row_time(dd, f, nt))
        return w

    def _on_row_time(self, d: _date, is_from: bool, t: QTime):
        """A time typed in a row pins that day: the global From/To stops dragging
        it around."""
        hf, mf, ht, mt = self._window_for(d)
        if is_from:
            hf, mf = t.hour(), t.minute()
        else:
            ht, mt = t.hour(), t.minute()
        self._per_day[d] = (hf, mf, ht, mt)
        self._pinned.add(d)
        self._refresh_count()

    def _remove_day(self, d: _date):
        """✕ on a row. Deferred by one tick: dropping the day rebuilds the table,
        which deletes the very button whose clicked handler we are standing in —
        Qt crashes on that."""
        if len(self._days) <= 1:
            return
        QTimer.singleShot(0, lambda: self._set_days(
            [x for x in self._days if x != d]))

    def _refresh_count(self):
        n = len(self._days)
        if n == 1:
            hf, mf, ht, mt = self._window_for(self._days[0])
            self._count_lbl.setText(
                f"{self._days[0].strftime('%a %d.%m.%Y')}  ·  "
                f"{hf:02d}:{mf:02d} – {ht:02d}:{mt:02d}")
        else:
            pinned = len(self._pinned & set(self._days))
            self._count_lbl.setText(
                f"{n} days selected" + (f"  ·  {pinned} with their own times"
                                        if pinned else ""))

    def _on_selection_changed(self):
        """Hook for subclasses — the Slider rescans the archive for cameras."""

    # ── public API ────────────────────────────────────────────────────────────
    def is_multiday(self) -> bool:
        """Rule 3 — more than one day IS multi-day. There is no tick."""
        return len(self._days) > 1

    def is_online_mode(self) -> bool:
        return self._allow_live and self.cb_now.isChecked()

    def selected_times(self) -> "tuple[int, int, int, int]":
        """The global (from_hour, from_minute, to_hour, to_minute)."""
        tf, tt = self.time_from.time(), self.time_to.time()
        return tf.hour(), tf.minute(), tt.hour(), tt.minute()

    def selected_hours(self) -> "tuple[int, int]":
        """Whole-hour span (folder granularity) — kept for existing callers."""
        return self.time_from.time().hour(), self.time_to.time().hour()

    def selected_date_obj(self) -> _date:
        """The leading day — the one a single-day caller works on."""
        return self._anchor if self._anchor in self._days else self._days[0]

    def selected_dates(self) -> "list[_date]":
        return list(self._days)

    def selected_segments(self) -> "list[PickSeg] | None":
        """Per-day windows when more than one day is picked, else None — the
        single-day callers key off that None."""
        if not self.is_multiday():
            return None
        return [PickSeg(d, *self._window_for(d)) for d in self._days]

    def all_segments(self) -> "list[PickSeg]":
        """Every picked day with its window, single-day selections included."""
        return [PickSeg(d, *self._window_for(d)) for d in self._days]

    def selected_windows(self) -> "list[tuple[int, int]]":
        """[(start_ns, end_ns)) …] — one entry per picked day."""
        return [seg_bounds_ns(s) for s in self.all_segments()]

    def selected_axis(self) -> "tuple[int, int]":
        """The bounding box of the selection; gaps between days stay blank."""
        wins = self.selected_windows()
        return min(w[0] for w in wins), max(w[1] for w in wins)

    # ── OK ────────────────────────────────────────────────────────────────────
    def _on_accept(self):
        for d in self._days:
            hf, mf, ht, mt = self._window_for(d)
            if (hf, mf) >= (ht, mt):
                QMessageBox.warning(
                    self, "Invalid time",
                    f'{d.strftime("%d.%m.%Y")}: "From" must be earlier than "To".')
                return
        if len(self._days) > 14:
            r = QMessageBox.question(
                self, "That is a lot of days",
                f"{len(self._days)} days selected. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        self.accept()
