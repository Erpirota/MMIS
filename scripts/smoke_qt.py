"""Qt smoke check: build a window, render it, save a PNG.

Usage: python scripts/smoke_qt.py <output.png>
Set QT_QPA_PLATFORM=offscreen to run headless.
Exit code 0 only if the grabbed image has the expected size and text was rendered.
"""

import os
import sys

# The offscreen platform ships no fonts on Windows: without this, text is not drawn.
if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow


def main(out_path: str) -> int:
    app = QApplication(sys.argv[:1])
    window = QMainWindow()
    window.setWindowTitle("MMIS smoke")
    label = QLabel("Minecraft Mod Intelligence Studio")
    label.setStyleSheet("background:#14161a; color:#e8eaed; font-size:20px; padding:40px;")
    window.setCentralWidget(label)
    window.resize(640, 360)
    window.show()

    result = {"code": 1}

    def grab() -> None:
        image = window.grab().toImage()
        colors = {image.pixel(x, y) for x in range(image.width()) for y in range(image.height())}
        # Antialiased text yields many shades; 2 colors means the text was not drawn.
        ok = image.width() >= 640 and image.height() >= 360 and len(colors) > 8
        if ok and image.save(out_path):
            result["code"] = 0
        print(f"grab {image.width()}x{image.height()} colors={len(colors)} saved={result['code'] == 0}")
        app.quit()

    QTimer.singleShot(200, grab)
    app.exec()
    return result["code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "smoke.png"))
