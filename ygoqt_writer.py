import sys
import os
import re
import json
import sqlite3
import unicodedata
import time
import socket

from urllib.parse import quote_plus

from smartcard.System import readers

from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import QUrl, QEventLoop, QFile, Signal, QObject, QThread, Qt, QPoint, QSettings, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QLabel,
    QListWidgetItem,
    QWidget,
    QVBoxLayout,
    QMenu,
    QDialog,
    QLineEdit
)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkRequest,
    QNetworkReply,
    QSslConfiguration,
    QSslSocket,
)
from PySide6.QtGui import QPixmap, QAction, QDesktopServices

from qt_material import apply_stylesheet


class YuGiOhCard:
    def __init__(self, identifier, passcode, konami_id, variant, set_id, lang, number, rarity, edition):
        self.identifier = identifier
        self.passcode = passcode
        self.konami_id = konami_id
        self.variant = variant
        self.set_id = set_id
        self.lang = lang
        self.number = number
        self.rarity = rarity
        self.edition = edition  # New field for edition

        # Validate and encode the card during initialization
        self.encoded_data = self.encode_card()

    def encode_card(self):
        # Ensure identifier is 4 characters (e.g., YG01)
        if len(self.identifier) != 4 or not self.identifier.startswith("YG"):
            raise ValueError("Identifier must be 4 characters long and start with 'YG'.")

        # Ensure passcode is numeric and 8 digits long, pad with '-' if needed
        if len(self.passcode) < 5:
            raise ValueError("Passcode must be 5 or more digits.")
        self.passcode = self.passcode.ljust(10, '-')  # Pad passcode to 10 chars

        # Ensure Konami DB ID is numeric and is 8 characters long
        if not str(self.konami_id).isdigit() or len(str(self.konami_id)) > 8:
            raise ValueError("Konami DB ID must be a numeric value and no longer than 8 characters.")
        self.konami_id = str(self.konami_id).ljust(8, '-')  # Pad Konami ID to 8 chars

        # Ensure variant is a 4-digit number
        if not self.variant.isdigit() or len(self.variant) != 4:
            raise ValueError("Variant must be a 4-digit number.")
        self.variant = self.variant.zfill(4)

        # Validate set ID: 4 characters
        if len(self.set_id) <= 2:
            raise ValueError("Set ID must be exactly 3-4 characters long.")
        self.set_id = self.set_id.ljust(4, '-')  # Pad passcode to 10 chars

        # Validate language: 2 characters
        if len(self.lang) != 2:
            raise ValueError("Language must be exactly 2 characters.")

        # Validate card number: 3 digits
        if len(self.number) != 3:
            raise ValueError("Card number must be exactly 3 digits long.")
        self.number = str(self.number).zfill(3)

        # Validate rarity: pad with '-' if needed to 4 characters
        if len(self.rarity) > 2:
            raise ValueError("Rarity must be a maximum of 2 characters.")
        self.rarity = self.rarity.ljust(2, '-')

        # Validate edition: pad with '-' if needed to 2 characters
        if len(self.edition) > 2:
            raise ValueError("Edition must be a maximum of 2 characters.")
        self.edition = self.edition.ljust(2, '-')

        # Construct final encoded string with the addition of the edition field
        return f"{self.identifier}{self.passcode}{self.konami_id}{self.variant}{self.set_id}{self.lang}{self.number}{self.rarity}{self.edition}XXX"

    @classmethod
    def decode_card(cls, data):
        if len(data) != 42:  # Updated length to 44 (42 bytes + 2 bytes for edition)
            raise ValueError("Encoded data must be exactly 42 bytes long.")

        identifier = data[:4]
        passcode = data[4:14].rstrip('-')
        konami_id = data[14:22].rstrip('-')
        variant = data[22:26]
        set_id = data[26:30]
        lang = data[30:32]
        number = data[32:35]
        rarity = data[35:37].strip()
        edition = data[37:39].strip()  # Extract the edition from the data

        if not identifier.startswith("YG"):
            raise ValueError("Invalid identifier. It must start with 'YG'.")
        if len(passcode) < 5:
            raise ValueError("Invalid passcode. It should be 5 or more digits.")
        if not konami_id.isdigit() or len(konami_id) > 8:
            raise ValueError("Invalid Konami DB ID. It should be numeric and at most 8 digits long.")
        if not variant.isdigit() or len(variant) != 4:
            raise ValueError("Invalid variant. It should be a 4-digit number.")
        if len(set_id) <= 2:
            raise ValueError("Invalid Set ID. It should be exactly 4 characters.")
        if len(lang) != 2:
            raise ValueError("Invalid language. It should be exactly 2 characters.")
        if len(number) != 3:
            raise ValueError("Invalid card number. It should be a 3-digit number.")
        if len(rarity) > 4:
            raise ValueError("Invalid rarity. It should be no more than 4 characters.")
        if len(edition) > 2:
            raise ValueError("Invalid edition. It should be no more than 2 characters.")

        # return cls(identifier, passcode, konami_id, variant, set_id, lang, number, rarity, edition)
        return {
            "identifier": identifier,
            "passcode": passcode,
            "konami_id": konami_id,
            "variant": variant,
            "set_id": set_id,
            "lang": lang,
            "number": number,
            "rarity": rarity,
            "edition": edition
        }

    def get_encoded_data(self):
        return self.encoded_data

    def __repr__(self):
        return (f"YuGiOhCard(identifier={self.identifier}, passcode={self.passcode}, "
                f"konami_id={self.konami_id}, variant={self.variant}, set_id={self.set_id}, "
                f"lang={self.lang}, number={self.number}, rarity={self.rarity}, edition={self.edition})")


class NFCReadThread(QThread):
    def __init__(self, nfc_monitor):
        super().__init__()
        self.nfc_monitor = nfc_monitor

    def run(self):
        self.nfc_monitor.read_tag()


class NFCMonitor(QObject):
    # Emits an integer state:
    #   0 = no reader detected,
    #   1 = reader connected (but no tag),
    #   2 = tag detected.
    statusChanged = Signal(int)
    # Emits the tag UID as a hex string when a tag is detected.
    tagUIDDetected = Signal(str)
    # Emits a message after a write attempt.
    writeResult = Signal(str)
    # Emits intermediate messages to show in a console label.
    consoleMessage = Signal(str)
    # New signal for the read tag result.
    readTagResult = Signal(str)

    def __init__(self, poll_interval=1.0, parent=None):
        super().__init__(parent)
        self.poll_interval = poll_interval
        self._running = True
        self.last_state = None  # Holds the previous state

    def stop(self):
        """Stops the monitoring loop."""
        self._running = False

    def monitor(self):
        """
        Continuously polls for NFC reader and tag presence.
        Emits statusChanged only when the state changes.
        If a tag is detected, emits tagUIDDetected with the tag's UID.
        Also emits intermediate console messages.
        """
        while self._running:
            new_state = None
            tag_uid = None
            try:
                available_readers = readers()
                if not available_readers:
                    new_state = 0  # No reader detected.
                    self.consoleMessage.emit("NO NFC READER DETECTED")
                else:
                    new_state = 1  # Reader available (default: no tag)
                    self.consoleMessage.emit(f"Using reader: {available_readers[0]}")
                    reader = available_readers[0]
                    connection = reader.createConnection()
                    try:
                        connection.connect()
                        # Retrieve the UID using the ACR122U command: [0xFF, 0xCA, 0x00, 0x00, 0x00].
                        uid_command = [0xFF, 0xCA, 0x00, 0x00, 0x00]
                        response, sw1, sw2 = connection.transmit(uid_command)
                        if sw1 == 0x90 and sw2 == 0x00 and response:
                            # Convert UID bytes to hex string.
                            tag_uid = ''.join('{:02X}'.format(b) for b in response)
                            new_state = 2  # Tag detected.
                        connection.disconnect()
                    except Exception as e:
                        new_state = 1  # Unable to read tag; remains at state 1.
                        self.consoleMessage.emit(f"Error reading tag: {e}")
            except Exception as e:
                new_state = 0  # On error, assume no reader.
                self.consoleMessage.emit(f"Error checking device: {e}")

            # Emit status only if the state has changed.
            if new_state != self.last_state:
                self.statusChanged.emit(new_state)
                self.last_state = new_state
                if new_state == 2 and tag_uid:
                    self.tagUIDDetected.emit(tag_uid)

            time.sleep(self.poll_interval)

    def write_to_tag(self, data):
        """
        Writes the provided string to the NFC tag.
        Encodes the string as UTF-8 and splits it into 4-byte chunks.
        Emits console messages and a writeResult signal.
        """
        try:
            available_readers = readers()
            if not available_readers:
                raise Exception("No smart card readers found!")

            reader = available_readers[0]
            data_bytes = data.encode('utf-8')
            start_page = 4
            page_size = 4
            pages = [data_bytes[i:i + page_size] for i in range(0, len(data_bytes), page_size)]

            for i, page_data in enumerate(pages):
                if len(page_data) < page_size:
                    page_data = page_data.ljust(page_size, b'\x00')
                page_number = start_page + i

                # Reconnect for each page write.
                connection = reader.createConnection()
                connection.connect()
                command = [0xFF, 0xD6, 0x00, page_number, 0x04] + list(page_data)

                try:
                    response, sw1, sw2 = connection.transmit(command)
                except Exception as e:
                    connection.disconnect()
                    raise Exception(f"Exception transmitting to page {page_number}: {e}")

                if sw1 == 0x90 and sw2 == 0x00:
                    self.consoleMessage.emit(f"Successfully wrote page {page_number}")
                else:
                    connection.disconnect()
                    raise Exception(f"Error writing to tag at page {page_number}, response: {sw1:02X} {sw2:02X}")

                connection.disconnect()
                time.sleep(0.2)  # Increase delay if needed

            self.writeResult.emit("Write successful.")
        except Exception as e:
            self.writeResult.emit(f"Write failed: {e}")

    def read_tag(self):
        """
        Reads pages 4 through 15 from an NTAG213 tag.
        Emits readTagResult with the full tag data when complete.
        """
        try:
            available_readers = readers()
            if not available_readers:
                self.readTagResult.emit("No NFC reader found.")
                return

            reader = available_readers[0]
            full_data = b""
            for page in range(4, 16):  # Pages 4 to 15 inclusive.
                connection = reader.createConnection()
                connection.connect()
                command = [0xFF, 0xB0, 0x00, page, 0x04]
                response, sw1, sw2 = connection.transmit(command)
                connection.disconnect()
                if sw1 == 0x90 and sw2 == 0x00:
                    full_data += bytes(response)
                    self.consoleMessage.emit(f"Page {page} read successfully.")
                else:
                    self.consoleMessage.emit(
                        f"Failed to read page {page}: response {sw1:02X} {sw2:02X}"
                    )
            try:
                data_str = full_data.decode("utf-8").rstrip("\x00")
            except UnicodeDecodeError:
                data_str = full_data.hex()
            # Emit the new signal with the read data.
            self.readTagResult.emit(data_str)
        except Exception as e:
            self.readTagResult.emit(f"Error during tag read: {e}")


class KonamiDatabase:
    def __init__(self, root_path, db_name="codes.db"):
        self.db_name = os.path.join(root_path, db_name)

    def get_konami_id(self, passcode):
        """Retrieve the Konami ID based on the passcode."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM codes WHERE passcode=?", (passcode,))
        result = cursor.fetchone()

        conn.close()
        return result[0] if result else "00000000"


class QLabel_clickable(QtWidgets.QLabel):
    clicked = QtCore.Signal(str)

    def __init__(self, parent=None, card_id=None, card_name=None, indx=None, sizeParm=None, percentage=None):
        super(QLabel_clickable, self).__init__(parent)
        self.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        if not sizeParm:
            size = (195, 265)
        else:
            size = (sizeParm[0], sizeParm[1])
        if percentage:
            if percentage != 0:
                size = (size[0] * percentage, size[1] * percentage)
        self.sizer = size
        self.setMaximumWidth(size[0] + 20)
        self.setMaximumHeight(size[1] + 20)
        self.setMinimumSize(size[0], size[1])
        self.resize(size[0], size[1])
        self.setSizePolicy(QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed))
        self.setStyleSheet('QToolTip { color: #717171; background-color: #ffffff; border: 1px solid white; }')
        self.indx = indx
        # Store card ID and name
        self.card_id = card_id
        self.name = card_name
        self.anim = QtCore.QPropertyAnimation(self, b'size')
        self.anim.setDuration(100)
        # Store the original pixmap for scaling on resize.
        self.original_pixmap = None

    def setPixmap(self, pixmap):
        # Save the original pixmap so that it can be rescaled later.
        self.original_pixmap = pixmap
        if pixmap and not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(self.sizer[0], self.sizer[1],
                                          QtCore.Qt.KeepAspectRatio,
                                          QtCore.Qt.SmoothTransformation)
            super(QLabel_clickable, self).setPixmap(scaled_pixmap)
        else:
            super(QLabel_clickable, self).setPixmap(pixmap)

    def resizeEvent(self, event):
        # When the label is resized, re-scale the original pixmap to fit the new size.
        if self.original_pixmap and not self.original_pixmap.isNull():
            scaled_pixmap = self.original_pixmap.scaled(self.size(),
                                                        QtCore.Qt.KeepAspectRatio,
                                                        QtCore.Qt.SmoothTransformation)
            super(QLabel_clickable, self).setPixmap(scaled_pixmap)
        super(QLabel_clickable, self).resizeEvent(event)

    def mousePressEvent(self, ev):
        self.clicked.emit(str(self.card_id))
        super(QLabel_clickable, self).mousePressEvent(ev)

    def enterEvent(self, event):
        self.anim.setDirection(QtCore.QAbstractAnimation.Forward)
        if self.anim.state() == self.anim.State.Stopped:
            self.anim.setStartValue(self.size())
            self.anim.setEndValue(QtCore.QSize(self.sizer[0] + 20, self.sizer[1] + 20))
            self.anim.start()
        super(QLabel_clickable, self).enterEvent(event)

    def leaveEvent(self, event):
        self.anim.setDirection(QtCore.QAbstractAnimation.Backward)
        if self.anim.state() == self.anim.State.Stopped:
            self.anim.start()
        super(QLabel_clickable, self).leaveEvent(event)


class YGOWriter(QMainWindow):
    def __init__(self, parent=None):
        super(YGOWriter, self).__init__(parent)
        root_path = self.getRootPath()
        # Initialize the SQLite DB
        self.init_db()
        # Load the UI from file
        ui_file = QFile(os.path.join(root_path, "writer.ui"))
        ui_file.open(QFile.ReadOnly)
        loader = QUiLoader()
        self.mw = loader.load(ui_file)
        self.setCentralWidget(self.mw)
        self.appversion = "0.2.4"
        self.setWindowTitle(f"YGO NFC Tools: {self.appversion} by SideswipeeZ")
        self.ico = QtGui.QIcon(os.path.join(self.getRootPath(), "assets", "icon.png"))
        self.setWindowIcon(self.ico)
        self.resize(1280, 720)

        self.console_label = QLabel("Console:")
        # Add the label to the status bar (which has the default name `statusbar`)
        self.mw.statusbar.addWidget(self.console_label)

        # Vars
        # YGOProDeck API URL
        self.ygo_api_url = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
        self.current_card = None
        self.encoded_card = None
        self.kdb = KonamiDatabase(self.getRootPath(True))
        self.rarities = self.get_rarity()
        self.edition = self.get_edition()
        self.set_editions()
        self.nfc_status = 0
        self.current_card_urls = []
        self.server_host = "localhost"
        self.server_port = 41114

        # Initialize QSettings
        self.settings = QSettings("SideswipeeZ", "ygo_writer")

        # Load the saved value for the spinbox, defaulting to 1.0 if not set.
        saved_spinbox_value = self.settings.value("dspin_delay_value", 1.0, type=float)
        self.mw.dspin_delay.setValue(saved_spinbox_value)

        # Connect the valueChanged signal to the save slot.
        self.mw.dspin_delay.valueChanged.connect(self.save_dspin_delay_value)

        # Setup Pixmaps
        self.default_card_pixmap = QPixmap(os.path.join(self.getRootPath(), "assets", "blank_card_previewsized.png"))
        self.mw.lbl_preview_img.setPixmap(self.default_card_pixmap)
        nfc_img_basepath = os.path.join(self.getRootPath(), "assets")
        self.nfc_pixmaps = [
            QPixmap(os.path.join(nfc_img_basepath, "nfc_black.png")),
            QPixmap(os.path.join(nfc_img_basepath, "nfc_blue.png")),
            QPixmap(os.path.join(nfc_img_basepath, "nfc_green.png")),
            QPixmap(os.path.join(nfc_img_basepath, "nfc_red.png"))
        ]
        self.set_nfc_preview(0)  # Init Pixmap for NFC Label

        # Connect button to the search function
        self.mw.bttn_card_search.clicked.connect(self.search_card)
        self.mw.bttn_write.clicked.connect(self.on_write_button_clicked)
        self.mw.bttn_launch_ygoprodeck.clicked.connect(lambda: self.launch_link(0))
        self.mw.bttn_launch_yugipedia.clicked.connect(lambda: self.launch_link(1))
        self.mw.bttn_send_card.clicked.connect(self.send_string)
        self.mw.bttn_send_removed.clicked.connect(lambda: self.send_string("Removed"))

        # Connect Combo Boxes
        # parse_ygo_nfc_encode
        self.mw.cmb_setid.currentTextChanged.connect(self.parse_ygo_nfc_encode)
        self.mw.cmb_rarity.currentTextChanged.connect(self.parse_ygo_nfc_encode)
        self.mw.cmb_edition.currentTextChanged.connect(self.parse_ygo_nfc_encode)

        # Checkbox
        self.mw.chk_readonly.toggled.connect(self.update_readonly_mode)

        # Menu items
        self.mw.actionLoad_Cards_from_DB.triggered.connect(self.load_all_cards)
        # Theme
        self.mw.actionLight.triggered.connect(lambda: self.changeStyle(0))
        self.mw.actionDark.triggered.connect(lambda: self.changeStyle(1))

        self.mw.actionCreated_by_SideswipeeZ.triggered.connect(lambda: self.launch_link_menu("https://github.com/SideswipeeZ"))
        self.mw.actionUsing_YGOPRODECK_API.triggered.connect(lambda: self.launch_link_menu("https://ygoprodeck.com/api-guide/"))
        self.mw.actionPySide6_Qt_Designer.triggered.connect(lambda: self.launch_link_menu("https://pypi.org/project/PySide6/"))
        self.mw.actionpyscard.triggered.connect(lambda: self.launch_link_menu("https://pypi.org/project/pyscard/"))
        self.mw.actionqt_material.triggered.connect(lambda: self.launch_link_menu("https://github.com/UN-GCPDS/qt-material"))


        self.mw.listWidget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.mw.listWidget.customContextMenuRequested.connect(self.show_list_widget_menu)

        self.filter_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Shift+F"), self.mw)
        self.filter_shortcut.activated.connect(self.activate_filter_shortcut)

        # Create a network manager for reuse
        self.network_manager = QNetworkAccessManager()

        # NFC Init
        self.nfc_monitor_thread = QThread()
        self.nfc_monitor = NFCMonitor(poll_interval=1.0)
        self.nfc_monitor.moveToThread(self.nfc_monitor_thread)

        self.nfc_monitor_thread.started.connect(self.nfc_monitor.monitor)
        self.nfc_monitor.statusChanged.connect(self.handle_nfc_status)
        self.nfc_monitor.tagUIDDetected.connect(self.handle_tag_uid)
        self.nfc_monitor.writeResult.connect(self.handle_write_result)
        self.nfc_monitor.consoleMessage.connect(self.console_out_nfc)
        self.nfc_monitor.readTagResult.connect(self.handle_read_tag_result)

        self.nfc_monitor_thread.start()
        # Connect button to the read_tag function
        self.mw.bttn_readtag.clicked.connect(self.start_read_tag)

        # Load and apply the saved theme index; default to 0 if not set.
        saved_theme_index = self.settings.value("theme_index", 0, type=int)
        self.changeStyle(saved_theme_index)

        # Debug SSL support information
        self.console_out(("SSL Supported:", QSslSocket.supportsSsl()))
        self.console_out(("SSL Library Build Version:", QSslSocket.sslLibraryBuildVersionString()))
        self.console_out(("SSL Library Version:", QSslSocket.sslLibraryVersionString()))
        self.console_out("Ready...")

    def closeEvent(self, event):
        """Ensures the NFC monitor stops when the application closes."""
        self.nfc_monitor.stop()
        self.nfc_monitor_thread.quit()
        self.nfc_monitor_thread.wait()
        event.accept()  # Ensures the window closes properly

    def getRootPath(self, extended=False):
        """
        Gets the appropriate root path depending on whether the app is running
        from PyInstaller bundle or directly.

        Parameters:
        extended (bool): If True, returns current working directory even when bundled

        Returns:
        str: Path to use as root for accessing resources
        """
        try:
            if sys._MEIPASS and not extended:
                return sys._MEIPASS
            else:
                return os.getcwd()
        except AttributeError:
            return os.getcwd()

    def send_string(self, flag):
        """Send a command string from the line edit to the NFCServer app."""
        command_string = self.mw.le_finalstring.text().strip() + "XX"

        if not command_string and flag != "Removed":
            self.console_out("Error: No Card is Selected.")
            return

        host = self.mw.le_debug_host.text().strip() or self.server_host
        port_text = self.mw.le_debug_port.text().strip() or self.server_port
        port = int(port_text)

        if flag and flag == "Removed":
            command_string = "RemovedTag"
        else:
            if self.mw.chk_sendtagremoved.isChecked():
                # Get Delay from Double Spinbox (in seconds)
                delay = self.mw.dspin_delay.value()
                # Send "RemovedTag" first
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.connect((host, port))
                        s.sendall("RemovedTag".encode('utf-8'))
                    self.console_out("Success: Removed tag sent successfully!")
                    # Use QTimer.singleShot to delay sending the actual command
                    QTimer.singleShot(int(delay * 1000), lambda: self.send_actual_command(command_string, host, port))
                    return  # Exit the function; the command will be sent later.
                except Exception as e:
                    self.console_out(f"Connection Error: Failed to send RemovedTag: {e}")
                    return

        # If not using the delayed "RemovedTag" path, send the command immediately.
        self.send_actual_command(command_string, host, port)

    def send_actual_command(self, command_string, host, port):
        """Send the actual command string to the server."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((host, port))
                s.sendall(command_string.encode('utf-8'))
            self.console_out("Success: Card Data sent successfully!")
        except Exception as e:
            self.console_out(f"Connection Error: Failed to send Card: {e}")

    def save_dspin_delay_value(self, value):
        """Save the current value of the doublespinbox to QSettings."""
        self.settings.setValue("dspin_delay_value", value)

    def console_out(self, message):
        """Update the console label with a new message."""
        if hasattr(self, 'console_label'):
            self.console_label.setText(str(message))

    def console_out_nfc(self, message):
        if message.startswith("Error reading tag:"):  # Fix this by improved error handling
            message = "NFC READER ONLINE"
        self.mw.lbl_tag_detection.setText(str(message))

    # Define your slot to process status changes.
    def handle_nfc_status(self, state):
        if state == 0:
            self.set_nfc_preview(3)  # Red
            self.nfc_status = 3
            self.mw.lbl_taguid.setText(f"Tag UID: ")
            self.mw.lbl_tag_detection.setText("NO NFC READER DETECTED")
        elif state == 1:
            self.set_nfc_preview(1)  # Blue
            self.nfc_status = 1
            self.mw.lbl_taguid.setText(f"Tag UID: ")
            self.mw.lbl_tag_detection.setText("NFC READER ONLINE")
        elif state == 2:
            self.set_nfc_preview(2)  #Green
            self.nfc_status = 2
            self.mw.lbl_tag_detection.setText("NFC TAG DETECTED")

    def init_db(self):
        """
        Initialize the SQLite database.
        The 'cards' table includes columns for card id, name, full JSON data,
        and file paths for the small and cropped images.
        """
        db_path = os.path.join(self.getRootPath(extended=True), "cards.db")
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                card_id INTEGER PRIMARY KEY,
                name TEXT,
                json_data TEXT,
                image_small TEXT,
                image_cropped TEXT
            )
        """)
        self.conn.commit()
        self.console_out(f"Database initialized at: {db_path}")

    def changeStyle(self, indx):
        self.settings.setValue("theme_index", indx)

        if indx == 0:
            theme = "light_pink.xml"
            apply_stylesheet(self, theme='light_pink.xml', invert_secondary=(
                    'light' in theme and 'dark' not in theme))
        elif indx == 1:
            apply_stylesheet(self, theme='dark_pink.xml')

    def set_nfc_preview(self, indx):
        self.mw.lbl_nfc_img.setPixmap(self.nfc_pixmaps[indx])

    def handle_tag_uid(self, uid):
        formatted_uid = ' '.join(uid[i:i + 2] for i in range(0, len(uid), 2))
        self.mw.lbl_taguid.setText(f"Tag UID: {formatted_uid}")
        # Auto-read the tag if read-only mode is enabled.
        if self.mw.chk_readonly.isChecked():
            self.start_read_tag()

    def on_write_button_clicked(self):
        if self.nfc_status == 2 and self.current_card:
            data_to_write = self.encoded_card.get_encoded_data()
            self.nfc_monitor.write_to_tag(data_to_write)
        else:
            self.console_out("Connect a NFC Tag to Write.")

    def handle_write_result(self, message):
        self.console_out(message)

    def download_and_save_image(self, url, folder, file_name):
        """
        Downloads an image from the given URL and saves it to disk.
        The image is stored in the specified folder with the provided file name.
        Returns the relative file path if successful, or an empty string otherwise.
        """
        os.makedirs(folder, exist_ok=True)
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        request.setSslConfiguration(QSslConfiguration.defaultConfiguration())
        loop = QEventLoop()
        reply = self.network_manager.get(request)
        reply.finished.connect(loop.quit)
        loop.exec()  # Wait for the download to finish

        if reply.error() != QNetworkReply.NoError:
            self.console_out(f"Image download error for {url}: {reply.errorString()}")
            return ""

        image_data = reply.readAll().data()
        file_path = os.path.join(folder, file_name)
        try:
            with open(file_path, 'wb') as f:
                f.write(image_data)
            # Compute the relative path with respect to the root directory.
            relative_path = os.path.relpath(file_path, self.getRootPath(extended=True))
            self.console_out(f"Saved image to {file_path}, relative path: {relative_path}")
            return relative_path
        except Exception as e:
            self.console_out(f"Failed to save image to disk: {e}")
            return ""

    def search_card(self):
        """
        Queries the API for cards using the card name inputted in the UI.
        Saves the returned card entries (and downloads images if not already saved)
        to the SQLite database and displays them in the list widget.
        """
        card_name = self.mw.le_card_name.text().strip()
        if not card_name:
            self.console_out("Search box is empty. Please enter a card name.")
            return  # Exit the function if the box is blank

        self.mw.listWidget.clear()  # Clear Widget for Query.
        # Get the card name from the UI input
        search_type = self.mw.cmb_search_type.currentText()
        normalized_name = unicodedata.normalize("NFC", card_name)
        cleaned_name = re.sub(r"[^a-zA-Z0-9À-ÿĀ-ž -]", "", normalized_name)
        encoded_name = quote_plus(cleaned_name)
        query_url = f"{self.ygo_api_url}?{search_type}={encoded_name}"
        self.console_out(f"Requesting: {query_url}")

        request = QNetworkRequest(QUrl(query_url))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        request.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        request.setSslConfiguration(QSslConfiguration.defaultConfiguration())

        loop = QEventLoop()
        reply = self.network_manager.get(request)
        reply.sslErrors.connect(lambda errors: self.console_out("SSL Errors:", errors))
        reply.finished.connect(loop.quit)
        loop.exec()  # Wait until the reply is finished

        if reply.error() != QNetworkReply.NoError:
            self.console_out(f"Error: {reply.error()} - {reply.errorString()}")
            return

        response_data = reply.readAll().data().decode("utf-8")
        try:
            json_data = json.loads(response_data)
            card_items = json_data.get("data", [])
            # Save each card to the database and add a label to the list widget.
            self.save_cards_to_db(card_items)
            for card in card_items:
                self.add_card_to_list_widget(card)
        except json.JSONDecodeError as e:
            self.console_out("Failed to parse JSON:", e)

    def save_cards_to_db(self, card_items):
        """
        Insert or update each card entry in the SQLite database.
        For new cards, downloads the small and cropped images and saves them to disk.
        The file paths for the saved images are stored in the database.
        """
        # Define base folders for saving images
        base_folder = os.path.join(self.getRootPath(extended=True), "cards")
        folder_small = os.path.join(base_folder, "small")
        folder_cropped = os.path.join(base_folder, "cropped")

        for card in card_items:
            card_id = card.get("id")
            card_name = card.get("name")
            json_str = json.dumps(card, ensure_ascii=False)

            # Check if the card already exists in the database.
            self.cursor.execute("SELECT COUNT(*) FROM cards WHERE card_id = ?", (card_id,))
            exists = self.cursor.fetchone()[0]

            if exists:
                # Update JSON data only (skip re-downloading images).
                self.cursor.execute(
                    "UPDATE cards SET json_data = ? WHERE card_id = ?",
                    (json_str, card_id)
                )
                self.console_out(f"Card {card_name} (ID: {card_id}) already exists. Updated JSON data.")
            else:
                image_small = ""
                image_cropped = ""
                card_images = card.get("card_images", [])
                if card_images:
                    first_image = card_images[0]
                    url_small = first_image.get("image_url_small", "")
                    url_cropped = first_image.get("image_url_cropped", "")
                    if url_small:
                        image_small = self.download_and_save_image(url_small, folder_small, f"{card_id}_small.jpg")
                    if url_cropped:
                        image_cropped = self.download_and_save_image(url_cropped, folder_cropped,
                                                                     f"{card_id}_cropped.jpg")
                self.cursor.execute(
                    """
                    INSERT INTO cards (card_id, name, json_data, image_small, image_cropped)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (card_id, card_name, json_str, image_small, image_cropped)
                )
                self.console_out(f"Saved new card {card_name} (ID: {card_id}) to database with images.")
        self.conn.commit()

    def add_card_to_list_widget(self, card):
        """
        Creates a custom clickable label for a card and adds it to the list widget.
        The label's tooltip is set to the card's name and its pixmap is set using the saved image file.
        """
        name = card.get("name", "Unknown Card")
        card_id = card.get("id")
        # Query the DB for the file path of the small image.
        self.cursor.execute("SELECT image_small FROM cards WHERE card_id = ?", (card_id,))
        row = self.cursor.fetchone()
        image_small_path = row[0] if row and row[0] else ""
        pixmap = QPixmap()
        if image_small_path and os.path.exists(image_small_path):
            pixmap.load(image_small_path)
        else:
            self.console_out("No image available for", name)

        # Create a clickable label for the card.
        label_item = QLabel_clickable(percentage=0.5, card_id=card_id, card_name=name)
        label_item.card_id = card_id
        label_item.card_name = name
        label_item.clicked.connect(self.procLabelClick)
        label_item.setToolTip(f"{name}")
        label_item.setPixmap(pixmap)

        # Create a container widget with a vertical layout.
        itemN = QListWidgetItem()
        widget = QWidget()
        widgetLayout = QVBoxLayout()
        widgetLayout.addWidget(label_item)
        widgetLayout.addStretch()
        widgetLayout.setSizeConstraint(QVBoxLayout.SetFixedSize)
        widget.setLayout(widgetLayout)
        itemN.setSizeHint(widget.sizeHint())

        # Add the custom widget to the list widget in the UI.
        self.mw.listWidget.addItem(itemN)
        self.mw.listWidget.setItemWidget(itemN, widget)

    def procLabelClick(self, card_id):
        # self.console_out(f"Label clicked for card ID: {card_id}")
        if not card_id:
            return

        # Call search_db with extended=True to get both JSON data and image_small.
        results = self.search_db(card_id, extended=True)
        if results:
            card = results[0]  # Assuming card_id is unique.
            self.current_card = card

            # Retrieve the relative image path from the extended data.
            image_small = card.get("_image_small", "")
            full_image_path = os.path.join(self.getRootPath(extended=True), image_small)

            pixmap = QPixmap()
            if image_small and os.path.exists(full_image_path):
                pixmap.load(full_image_path)
            else:
                pixmap = self.default_card_pixmap

            # Set the pixmap on the preview label.
            self.mw.lbl_preview_img.setPixmap(pixmap)

            # --- New Code: Populate Combo Boxes ---
            # Clear the combo boxes first.
            self.mw.cmb_setid.clear()
            self.mw.cmb_rarity.clear()

            # Retrieve the card sets from the card data.
            card_sets = card.get("card_sets", [])
            set_codes = set()
            rarities = set()

            # URLs
            ygo_pro_url = card.get("ygoprodeck_url", None)
            yugipedia_url = None
            if card_id:
                yugipedia_url = f"https://yugipedia.com/wiki/{str(card_id)}"

            self.current_card_urls = [ygo_pro_url, yugipedia_url]

            # Loop through each card set and extract unique set codes and rarities.
            for card_set in card_sets:
                code = card_set.get("set_code", "")
                rarity = card_set.get("set_rarity", "")
                if code:
                    set_codes.add(code)
                if rarity:
                    rarities.add(rarity)

            # Optionally sort the values before adding to the combo boxes.
            for code in sorted(set_codes):
                self.mw.cmb_setid.addItem(code)
            for rarity in sorted(rarities):
                self.mw.cmb_rarity.addItem(rarity)
            # --- End New Code ---

            self.parse_ygo_nfc_encode()

        else:
            self.console_out(f"No card found for ID: {card_id}")

    def parse_ygo_nfc_encode(self):
        if not self.current_card:
            return

        identifier = "YG01"
        passcode = str(self.current_card.get("id", "0000000000"))
        if not passcode == "0000000000":
            konami_id = self.kdb.get_konami_id(passcode)
        else:
            konami_id = "00000000"

        variant = "0000"

        set_id_text = self.mw.cmb_setid.currentText()
        # Regular expression to match the pattern
        match = re.match(r"([A-Za-z0-9]+)-([A-Za-z]{1,2})?([A-Za-z0-9]+)", set_id_text)

        if match:
            set_id = match.group(1)
            lang = match.group(2) if match.group(2) else "XX"  # Language could be None if not present
            lang = lang.ljust(2, 'X')
            number = match.group(3)
        else:
            set_id = "XXXX"
            lang = "XX"
            number = "XXX"

        rarity = self.rarities.get(self.mw.cmb_rarity.currentText(), "00")
        edition = self.edition.get(self.mw.cmb_edition.currentText(), "00")

        self.encoded_card = YuGiOhCard(
            identifier=identifier,
            passcode=passcode,
            konami_id=konami_id,
            variant=variant,
            set_id=set_id,
            lang=lang,
            number=number,
            rarity=rarity,
            edition=edition
        )

        self.mw.le_finalstring.setText(self.encoded_card.get_encoded_data())

    def get_rarity(self):
        # Load the JSON data from the file
        rarity_file = os.path.join(self.getRootPath(extended=True),'rarities.json')
        with open(rarity_file, 'r') as file:
            rarities = json.load(file)
        return rarities

    def get_edition(self):
        # Load the JSON data from the file
        edition_file = os.path.join(self.getRootPath(extended=True),'edition.json')
        with open(edition_file, 'r') as file:
            rarities = json.load(file)
        return rarities

    def set_editions(self):
        # Get the existing items in the combo box
        existing_items = [self.mw.cmb_edition.itemText(i) for i in range(self.mw.cmb_edition.count())]

        for key in self.edition.keys():
            # Only add the key if it's not already in the combo box
            if key not in existing_items:
                self.mw.cmb_edition.addItem(key)

    def search_db(self, query, extended=False):
        """
        Searches the database for cards by id (if the query is numeric) or by name.
        If extended is True, returns a tuple containing both the JSON data and the image_small path.
        Otherwise, returns just the card JSON.
        """
        try:
            card_id = int(query)
            if extended:
                self.cursor.execute("SELECT json_data, image_small FROM cards WHERE card_id = ?", (card_id,))
            else:
                self.cursor.execute("SELECT json_data FROM cards WHERE card_id = ?", (card_id,))
        except ValueError:
            if extended:
                self.cursor.execute("SELECT json_data, image_small FROM cards WHERE name LIKE ?", (f"%{query}%",))
            else:
                self.cursor.execute("SELECT json_data FROM cards WHERE name LIKE ?", (f"%{query}%",))

        results = self.cursor.fetchall()
        cards = []
        for row in results:
            if extended:
                # row[0] is JSON data, row[1] is the image_small path.
                card_data = json.loads(row[0])
                card_data["_image_small"] = row[1] if len(row) > 1 else ""
                cards.append(card_data)
                self.console_out(("Found Card:", card_data.get("name"), card_data.get("id"), row[1]))
            else:
                card_data = json.loads(row[0])
                cards.append(card_data)
                self.console_out(("Found Card:", card_data.get("name"), card_data.get("id")))
        return cards

    def load_all_cards(self):
        """
        Loads all locally stored cards from the SQLite database and displays them
        in the list widget, using the same method as search results.
        """
        self.mw.listWidget.clear()  # Clear any existing entries in the UI.
        try:
            # self.cursor.execute("SELECT json_data, image_small FROM cards")
            self.cursor.execute("SELECT json_data, image_small FROM cards ORDER BY name ASC")
            results = self.cursor.fetchall()
            if not results:
                self.console_out("No cards found in the local database.")
                return

            for row in results:
                json_str, image_small = row
                try:
                    card = json.loads(json_str)
                    # If needed, attach the local image path so downstream methods have access:
                    card["_image_small"] = image_small
                    self.add_card_to_list_widget(card)
                except json.JSONDecodeError as e:
                    self.console_out("Error decoding card data:", e)
        except Exception as e:
            self.console_out("Error loading cards from the database:", e)

    def show_list_widget_menu(self, position):
        """Shows a right-click menu with actions to load or filter cards."""
        menu = QMenu(self.mw)

        load_action = QAction("Load Cards from DB", self.mw)
        load_action.triggered.connect(self.load_all_cards)
        menu.addAction(load_action)

        filter_action = QAction("Filter Cards from DB", self.mw)
        # Pass the context menu position into the filter function.
        filter_action.triggered.connect(lambda: self.show_filter_input(position))
        menu.addAction(filter_action)

        menu.exec(self.mw.listWidget.mapToGlobal(position))

    def show_filter_input(self, position):
        """
        Creates a small popup dialog with a QLineEdit to let you type a fuzzy filter.
        As you type, the list widget is updated with cards matching the query.

        Parameters:
        position: Can be either a QPoint (when called from shortcut) or a relative position
                 (when called from context menu). If None, positions over the list widget.
        """
        # Create a simple popup dialog.
        dialog = QDialog(self.mw)
        dialog.setWindowFlags(Qt.Popup)
        layout = QVBoxLayout(dialog)
        line_edit = QLineEdit(dialog)
        line_edit.setPlaceholderText("Enter card name to filter...")
        layout.addWidget(line_edit)
        dialog.setLayout(layout)

        # Connect text changes to filtering the cards.
        line_edit.textChanged.connect(self.filter_cards)

        # Set focus to the line edit
        line_edit.setFocus()

        # Optionally, close the popup when the user hits Enter.
        line_edit.returnPressed.connect(dialog.close)

        # Position the dialog based on how it was triggered
        if position is None or isinstance(position, QPoint):
            # For keyboard shortcut or if given a QPoint
            # Center the dialog over the list widget
            list_center = self.mw.listWidget.rect().center()
            global_center = self.mw.listWidget.mapToGlobal(list_center)

            # Get dialog size after layout is applied
            dialog.adjustSize()
            dialog_width = dialog.width()
            dialog_height = dialog.height()

            # Calculate position to center dialog
            x = global_center.x() - (dialog_width // 2)
            y = global_center.y() - (dialog_height // 2)

            dialog.move(x, y)
        else:
            # If it's a relative position (from context menu), map to global
            dialog.move(self.mw.listWidget.mapToGlobal(position))

        dialog.exec()

    def filter_cards(self, query):
        """
        Queries the local database for cards whose name matches the given query (fuzzy search)
        and displays the matching results in the list widget.
        """
        self.mw.listWidget.clear()  # Clear existing items

        try:
            # Query using a LIKE statement for fuzzy matching and order by name.
            self.cursor.execute(
                "SELECT json_data, image_small FROM cards WHERE name LIKE ? ORDER BY name ASC",
                (f"%{query}%",)
            )
            results = self.cursor.fetchall()
            if not results:
                self.console_out(f"No matching cards found for: {query}")
                return

            for json_str, image_small in results:
                try:
                    card = json.loads(json_str)
                    # Optionally, attach the local image path for use in downstream methods.
                    card["_image_small"] = image_small
                    self.add_card_to_list_widget(card)
                except json.JSONDecodeError as e:
                    self.console_out("Error decoding card data:", e)
        except Exception as e:
            self.console_out("Error filtering cards from the database:", e)

    def activate_filter_shortcut(self):
        """Triggers the filter dialog when Shift+F is pressed, positioned at the top-middle of the list widget."""
        # Calculate the position for the dialog (top-middle of the list widget)
        list_widget_rect = self.mw.listWidget.rect()
        list_widget_top_middle = QPoint(list_widget_rect.width() // 2, 10)  # 10px from the top

        # Convert to global coordinates
        global_pos = self.mw.listWidget.mapToGlobal(list_widget_top_middle)

        # Show the filter dialog at the calculated position
        self.show_filter_input(global_pos)

    def launch_link(self, indx):
        if self.current_card:
            url_str = self.current_card_urls[indx]
            if url_str:
                url = QUrl(url_str)  # Replace with your desired URL
                QDesktopServices.openUrl(url)
            else:
                self.console_out("URL is None.")
        else:
            self.console_out("Select a Card First.")

    def launch_link_menu(self, url):
        if url:
            url = QUrl(url)  # Replace with your desired URL
            QDesktopServices.openUrl(url)
        else:
            self.console_out("URL is None.")

    def start_read_tag(self):
        if self.nfc_status == 2:
            # Create a new thread for the read operation.
            self.read_thread = NFCReadThread(self.nfc_monitor)
            self.read_thread.start()
        else:
            self.mw.lbl_read_console.setText("Error: No Tag is Detected.")

    def handle_read_tag_result(self, tag_str):
        # Decode string
        try:
            decoded_card = YuGiOhCard.decode_card(tag_str)
        except ValueError:
            self.mw.lbl_read_console.setText("Error: ValueError on Decode.")
            return

        read_passcode = decoded_card.get("passcode")
        # --- First: Look for the card in the local database ---
        results = self.search_db(read_passcode, extended=True)
        if results:
            card = results[0]
            self.current_card = card

            # Set the preview image from the stored local path.
            image_small = card.get("_image_small", "")
            full_image_path = os.path.join(self.getRootPath(extended=True), image_small)
            pixmap = QPixmap()
            if image_small and os.path.exists(full_image_path):
                pixmap.load(full_image_path)
            else:
                pixmap = self.default_card_pixmap
            self.mw.lbl_preview_img.setPixmap(pixmap)

            # Update combo boxes for set code and rarity.
            self.mw.cmb_setid.clear()
            self.mw.cmb_rarity.clear()
            card_sets = card.get("card_sets", [])
            set_codes = set()
            rarities = set()
            # Update URL links.
            ygo_pro_url = card.get("ygoprodeck_url", None)
            yugipedia_url = f"https://yugipedia.com/wiki/{str(read_passcode)}"
            self.current_card_urls = [ygo_pro_url, yugipedia_url]
            for card_set in card_sets:
                code = card_set.get("set_code", "")
                rarity = card_set.get("set_rarity", "")
                if code:
                    set_codes.add(code)
                if rarity:
                    rarities.add(rarity)
            for code in sorted(set_codes):
                self.mw.cmb_setid.addItem(code)
            for rarity in sorted(rarities):
                self.mw.cmb_rarity.addItem(rarity)

            self.parse_ygo_nfc_encode()
            self.mw.lbl_read_console.setText("Card loaded from local database.")
            return

        # --- If not found locally, try fetching from the YGOPRODeck API ---
        query_url = f"{self.ygo_api_url}?id={read_passcode}"
        self.console_out(f"Fetching card from API: {query_url}")

        request = QNetworkRequest(QUrl(query_url))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        request.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        request.setSslConfiguration(QSslConfiguration.defaultConfiguration())

        loop = QEventLoop()
        reply = self.network_manager.get(request)
        reply.finished.connect(loop.quit)
        loop.exec()  # Wait until the API reply is finished

        if reply.error() != QNetworkReply.NoError:
            self.mw.lbl_read_console.setText(f"API Error: {reply.errorString()}")
            return

        response_data = reply.readAll().data().decode("utf-8")
        try:
            json_data = json.loads(response_data)
            card_items = json_data.get("data", [])
            if not card_items:
                self.mw.lbl_read_console.setText("Card not found in API.")
                return
            card = card_items[0]
        except json.JSONDecodeError:
            self.mw.lbl_read_console.setText("Error: Failed to decode API response.")
            return

        # Save the new card to the local DB.
        self.save_cards_to_db([card])
        self.current_card = card

        # Retrieve the local image path from the database.
        self.cursor.execute("SELECT image_small FROM cards WHERE card_id = ?", (card.get("id"),))
        row = self.cursor.fetchone()
        image_small = row[0] if row and row[0] else ""
        pixmap = QPixmap()
        full_image_path = os.path.join(self.getRootPath(), image_small)
        if image_small and os.path.exists(full_image_path):
            pixmap.load(full_image_path)
        else:
            pixmap = self.default_card_pixmap
        self.mw.lbl_preview_img.setPixmap(pixmap)

        # Update combo boxes for card sets.
        self.mw.cmb_setid.clear()
        self.mw.cmb_rarity.clear()
        card_sets = card.get("card_sets", [])
        set_codes = set()
        rarities = set()
        ygo_pro_url = card.get("ygoprodeck_url", None)
        yugipedia_url = f"https://yugipedia.com/wiki/{str(card.get('id'))}"
        self.current_card_urls = [ygo_pro_url, yugipedia_url]
        for card_set in card_sets:
            code = card_set.get("set_code", "")
            rarity = card_set.get("set_rarity", "")
            if code:
                set_codes.add(code)
            if rarity:
                rarities.add(rarity)
        for code in sorted(set_codes):
            self.mw.cmb_setid.addItem(code)
        for rarity in sorted(rarities):
            self.mw.cmb_rarity.addItem(rarity)

        self.parse_ygo_nfc_encode()
        self.mw.lbl_read_console.setText("Card loaded from API.")

    # Then add this new slot to your class:
    def update_readonly_mode(self, checked):
        if checked:
            self.mw.bttn_write.setEnabled(False)
            self.console_out("Read-only mode enabled. Write function disabled.")
            # Optionally, if a tag is already detected, trigger a read immediately.
            if self.nfc_status == 2:
                self.start_read_tag()
        else:
            self.mw.bttn_write.setEnabled(True)
            self.console_out("Read-only mode disabled. Write function enabled.")


if __name__ == '__main__':
    try:
        # this is to add the Icon to the Taskbar while running in ide (Windows).
        import ctypes; ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ygonfc.app.sideswipe')
    except:
        pass
    app = QApplication(sys.argv)
    window = YGOWriter()
    window.show()
    sys.exit(app.exec())
