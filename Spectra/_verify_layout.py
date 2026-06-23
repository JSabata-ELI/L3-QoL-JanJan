"""Throwaway harness: render the Spectra widget at a SMALL window size and screenshot
the left panel, to confirm controls don't overlap and the single outer scrollbar engages.
No CPVA network is needed — we only check layout."""
import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget
from PySide6.QtCore import QTimer
import sp_t

app = QApplication.instance() or QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(sp_t._APP_STYLESHEET)

win = QMainWindow()
win.setMinimumSize(860, 480)
tabs = QTabWidget()
w = sp_t.SpectraWidget()
tabs.addTab(w, "SPIDER spectra")
win.setCentralWidget(tabs)

# Force a deliberately short window — the worst case the user reported.
win.resize(900, 520)
win.show()

def shoot():
    win.grab().save("_verify_small.png")
    # Also a wide/tall one for comparison.
    win.resize(1400, 900)
    QTimer.singleShot(400, lambda: (win.grab().save("_verify_large.png"), app.quit()))

QTimer.singleShot(600, shoot)
QTimer.singleShot(4000, app.quit)  # safety
app.exec()
print("done")
