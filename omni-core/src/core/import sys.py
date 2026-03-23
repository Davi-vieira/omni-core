import sys
from PyQt6.QtWidgets import QApplication, QWidget

app = QApplication(sys.argv)
window = QWidget()
window.setWindowTitle("Omni-Core Teste")
window.resize(300, 200)
window.show()

sys.exit(app.exec())