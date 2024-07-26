# mypy: disable-error-code="union-attr"
import sys
import usb
from can import BitTiming, BitTimingFd
from typing import Optional, List, Union, Any, cast
from enum import Enum, auto
from functools import partial
from random import randrange
from PySide6.QtCore import (
    Signal,
    Slot,
    QObject,
    QTimer,
    Qt,
    QThread,
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSize
)
from PySide6.QtGui import (
    QFocusEvent,
    QFont,
    QCloseEvent
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QSpacerItem,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QMessageBox,
    QCheckBox,
    QGridLayout,
    QLineEdit,
    QSpinBox,
    QDialog,
    QGroupBox,
    QHeaderView,
    QTableView
)
from candle.candle_api import (
    CandleDevice,
    CandleInterface,
    CandleChannel,
    GSHostFrame,
    GSHostFrameHeader,
    GSCANFlag,
    GSDeviceBTConstExtended,
    GSDeviceBitTiming,
    GSCANFeature,
    DLC2LEN
)


class CandleManagerState(Enum):
    DeviceSelection = auto()
    ChannelSelection = auto()
    Configuration = auto()
    Running = auto()


class CandleManager(QObject):
    scanResult = Signal(list)
    selectDeviceResult = Signal(list)

    stateTransition = Signal(CandleManagerState, CandleManagerState)
    messageReceived = Signal(GSHostFrame)
    exceptionOccurred = Signal(str)
    channelInfo = Signal(GSDeviceBTConstExtended)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.state = CandleManagerState.DeviceSelection

        self.device_list: List[CandleDevice] = []
        self.interface: Optional[CandleInterface] = None
        self.channel: Optional[CandleChannel] = None

        self.polling_timer = QTimer(self)
        self.polling_timer.timeout.connect(self.polling)
        self.polling_timer.setInterval(1)
        self.polling_timer.start()

    @Slot()
    def scan(self) -> None:
        if self.state == CandleManagerState.Running:
            self.channel.close()
            self.interface = None
            self.channel = None
        if self.state == CandleManagerState.Configuration:
            self.interface = None
            self.channel = None
        if self.state == CandleManagerState.ChannelSelection:
            self.interface = None
        self.transition(CandleManagerState.DeviceSelection)
        self.device_list.clear()
        try:
            self.device_list = list(CandleDevice.scan())
        except usb.USBError as e:
            self.handle_exception(str(e))
        else:
            self.scanResult.emit(self.device_list)

    @Slot(int)
    def select_device(self, index: int) -> None:
        if index < 0:
            return
        if self.state == CandleManagerState.DeviceSelection or self.state == CandleManagerState.ChannelSelection or self.state == CandleManagerState.Configuration:
            try:
                self.interface = self.device_list[index][0]
            except usb.core.USBError as e:
                self.handle_exception(str(e))
            else:
                self.channel = None
                self.transition(CandleManagerState.ChannelSelection)

    @Slot(int)
    def select_channel(self, index: int) -> None:
        if index < 0:
            return
        if self.state == CandleManagerState.ChannelSelection or self.state == CandleManagerState.Configuration:
            try:
                self.channel = self.interface[index]    # type: ignore[index]
            except usb.core.USBError as e:
                self.handle_exception(str(e))
            else:
                self.channel.close()
                self.transition(CandleManagerState.Configuration)
                self.channelInfo.emit(self.channel._bt_const)

    @Slot(GSDeviceBitTiming)
    def set_bit_timing(self, bit_timing: GSDeviceBitTiming) -> None:
        if self.state == CandleManagerState.Configuration:
            self.channel.set_bit_timing(bit_timing.prop_seg, bit_timing.phase_seg1, bit_timing.phase_seg2, bit_timing.sjw, bit_timing.brp)

    @Slot(GSDeviceBitTiming)
    def set_data_bit_timing(self, bit_timing: GSDeviceBitTiming) -> None:
        if self.state == CandleManagerState.Configuration:
            self.channel.set_data_bit_timing(bit_timing.prop_seg, bit_timing.phase_seg1, bit_timing.phase_seg2, bit_timing.sjw, bit_timing.brp)

    @Slot(bool, bool, bool, bool, bool, bool)
    def start(self, fd: bool, loopback: bool, listen_only: bool, triple_sample: bool, one_shot: bool, bit_error_reporting: bool) -> None:
        if self.state == CandleManagerState.Configuration:
            self.channel.open(fd, loopback, listen_only, triple_sample, one_shot, bit_error_reporting)
            self.transition(CandleManagerState.Running)

    @Slot()
    def stop(self) -> None:
        if self.state == CandleManagerState.Running:
            self.channel.close()
            self.transition(CandleManagerState.Configuration)

    @Slot(GSHostFrame)
    def send_message(self, frame: GSHostFrame) -> None:
        if self.state == CandleManagerState.Running:
            try:
                self.channel.write(frame)
            except usb.core.USBError as e:
                try:
                    self.channel.close()
                except usb.core.USBError:
                    pass
                self.handle_exception(str(e))

    def transition(self, to_state: CandleManagerState) -> None:
        if self.state != to_state:
            from_state = self.state
            self.state = to_state
            self.stateTransition.emit(from_state, self.state)

    def handle_exception(self, error: str) -> None:
        self.interface = None
        self.channel = None
        self.transition(CandleManagerState.DeviceSelection)
        self.exceptionOccurred.emit(error)

    @Slot()
    def polling(self) -> None:
        if self.state == CandleManagerState.Running:
            try:
                frame = self.channel.read(1)
            except usb.core.USBTimeoutError:
                pass
            except usb.core.USBError as e:
                try:
                    self.channel.close()
                except usb.core.USBError:
                    pass
                self.handle_exception(str(e))
            else:
                if frame is not None:
                    self.messageReceived.emit(frame)


class InputPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self.grid_layout = QGridLayout(self)
        for i in range(8):
            self.grid_layout.addWidget(QLabel(str(i + 1)), 0, i + 1)
        for i in range(8):
            self.grid_layout.addWidget(QLabel(str(i + 1)), i + 1, 0)
        previous_line_edit: Optional[QLineEdit] = None
        for i in range(8):
            for j in range(8):
                line_edit = QLineEdit()
                line_edit.setInputMask('hh')
                line_edit.setFixedWidth(24)
                line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
                line_edit.setText('00')
                line_edit.mousePressEvent = partial(self.get_focus, line_edit=line_edit)    # type: ignore[method-assign]
                if previous_line_edit:
                    previous_line_edit.textChanged.connect(partial(self.focus_next, next_line_edit=line_edit))
                previous_line_edit = line_edit
                self.grid_layout.addWidget(line_edit, i + 1, j + 1)
        self.setLayout(self.grid_layout)

    @staticmethod
    def get_focus(_event: QFocusEvent, line_edit: QLineEdit) -> None:
        if line_edit.isEnabled():
            line_edit.selectAll()

    @staticmethod
    def focus_next(text: str, next_line_edit: QLineEdit) -> None:
        if len(text) >= 2:
            if next_line_edit.isEnabled():
                next_line_edit.setFocus()
                next_line_edit.selectAll()

    @Slot(int)
    def set_dlc(self, dlc: int) -> None:
        for i in range(64):
            row = i // 8
            column = i % 8
            line_edit: QLineEdit = cast(QLineEdit, self.grid_layout.itemAtPosition(row + 1, column + 1).widget())
            if i < DLC2LEN[dlc]:
                line_edit.setEnabled(True)
            else:
                line_edit.setEnabled(False)
                line_edit.setText('00')

    @Slot()
    def random(self) -> None:
        for i in range(64):
            row = i // 8
            column = i % 8
            line_edit: QLineEdit = cast(QLineEdit, self.grid_layout.itemAtPosition(row + 1, column + 1).widget())
            if line_edit.isEnabled():
                line_edit.setText(f'{randrange(0, 256):02X}')

    def data(self) -> bytes:
        data: List[int] = []
        if self.isEnabled():
            for i in range(64):
                row = i // 8
                column = i % 8
                line_edit: QLineEdit = cast(QLineEdit, self.grid_layout.itemAtPosition(row + 1, column + 1).widget())
                if line_edit.isEnabled():
                    data.append(int(line_edit.text(), 16))
        return bytes(data)


class MessageTableModel(QAbstractTableModel):
    rowInserted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.header = ('Timestamp', 'Rx/Tx', 'Flags', 'CAN ID', 'DLC', 'Data')
        self.message_buffer: List[GSHostFrame] = []
        self.message_pending: List[GSHostFrame] = []
        self.monospace_font = QFont('Monospace')
        self.monospace_font.setStyleHint(QFont.StyleHint.TypeWriter)
        self.flush_timer = QTimer(self)
        self.flush_timer.timeout.connect(self.flush_message)
        self.flush_timer.setInterval(50)
        self.flush_timer.start()

    @Slot(GSHostFrame)
    def handle_message(self, message: GSHostFrame) -> None:
        self.message_pending.append(message)

    @Slot()
    def flush_message(self) -> None:
        if self.message_pending:
            self.beginInsertRows(QModelIndex(), len(self.message_buffer), len(self.message_buffer) + len(self.message_pending) - 1)
            self.message_buffer.extend(self.message_pending)
            self.message_pending.clear()
            self.endInsertRows()
            self.rowInserted.emit()

    def rowCount(self, parent: Any = None) -> int:
        return len(self.message_buffer)

    def columnCount(self, parent: Any = None) -> int:
        return len(self.header)

    def data(self, index: Union[QModelIndex, QPersistentModelIndex], role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            message = self.message_buffer[index.row()]
            column = index.column()
            if column == 0:
                return str(message.timestamp)
            if column == 1:
                return 'Rx' if message.header.is_rx else 'Tx'
            if column == 2:
                flags = ['EFF' if message.header.is_extended_id else 'SFF']
                if message.header.is_error_frame:
                    flags.append('E')
                if message.header.is_remote_frame:
                    flags.append('R')
                if message.header.is_fd:
                    flags.append('FD')
                if message.header.is_bitrate_switch:
                    flags.append('BRS')
                if message.header.is_error_state_indicator:
                    flags.append('ESI')
                return ' '.join(flags)
            if column == 3:
                return f'{message.header.arbitration_id:08X}' if message.header.is_extended_id else f'{message.header.arbitration_id:03X}'
            if column == 4:
                return str(message.header.data_length)
            if column == 5:
                wrapped_data = []
                for i in range(8):
                    data = message.data[i * 8:i * 8 + 8]
                    if not data:
                        break
                    wrapped_data.append(' '.join(f'{j:02X}' for j in data) + '\t' + ''.join(chr(j) if 31 < j < 127 else '.' for j in data))
                return '\n'.join(wrapped_data)
        if role == Qt.ItemDataRole.FontRole:
            if index.column() == 5:
                return self.monospace_font
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(0, 500)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return self.header[section]
            elif orientation == Qt.Orientation.Vertical:
                return str(section + 1)
        return None


class BitTimingDialog(QDialog):
    setBitTiming = Signal(GSDeviceBitTiming)
    setDataBitTiming = Signal(GSDeviceBitTiming)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.channel_info = GSDeviceBTConstExtended(GSCANFeature(-1), -1, -1, -1, -1, -1, -1, -1, -1, -1)
        self.bit_timing: Optional[Union[BitTiming, BitTimingFd]] = None

        vbox_layout = QVBoxLayout()
        self.frequency_label = QLabel('Clock Frequency: unknown')
        hbox_layout = QHBoxLayout()
        nominal_group_box = QGroupBox('Nominal Bit Rate')
        grid_layout1 = QGridLayout()
        self.nominal_bitrate_combox = QComboBox()
        self.nominal_bitrate_combox.addItems(['1000', '800', '500', '250', '125', '100', '83.333', '50', '20', '10'])
        self.nominal_bitrate_combox.setEditable(True)
        self.nominal_sample_point_combox = QComboBox()
        self.nominal_sample_point_combox.addItems(['87.5', '75', '62.5', '50'])
        self.nominal_sample_point_combox.setEditable(True)
        grid_layout1.addWidget(QLabel('Bit Rate [kbit/s]:'), 0, 0)
        grid_layout1.addWidget(self.nominal_bitrate_combox, 0, 1)
        grid_layout1.addWidget(QLabel('Sample Point [%]:'), 1, 0)
        grid_layout1.addWidget(self.nominal_sample_point_combox, 1, 1)
        nominal_group_box.setLayout(grid_layout1)
        data_group_box = QGroupBox('Data Bit Rate')
        grid_layout2 = QGridLayout()
        self.data_bitrate_combox = QComboBox()
        self.data_bitrate_combox.addItems(['12000', '8000', '5000', '2000'])
        self.data_bitrate_combox.setEditable(True)
        self.data_sample_point_combox = QComboBox()
        self.data_sample_point_combox.addItems(['87.5', '75', '62.5', '50'])
        self.data_sample_point_combox.setEditable(True)
        grid_layout2.addWidget(QLabel('Bit Rate [kbit/s]:'), 0, 0)
        grid_layout2.addWidget(self.data_bitrate_combox, 0, 1)
        grid_layout2.addWidget(QLabel('Sample Point [%]:'), 1, 0)
        grid_layout2.addWidget(self.data_sample_point_combox, 1, 1)
        data_group_box.setLayout(grid_layout2)
        hbox_layout.addWidget(nominal_group_box)
        hbox_layout.addWidget(data_group_box)
        self.bit_timing_table = QTableWidget()
        self.bit_timing_table.setColumnCount(6)
        self.bit_timing_table.setRowCount(2)
        self.bit_timing_table.setHorizontalHeaderLabels(['Prescaler', 'TSEG1', 'TSEG2', 'SJW', 'tq', 'Nq'])
        self.bit_timing_table.setVerticalHeaderLabels(['Nominal', 'Data'])
        self.bit_timing_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bit_timing_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ok_button = QPushButton('OK')
        vbox_layout.addWidget(self.frequency_label)
        vbox_layout.addLayout(hbox_layout)
        vbox_layout.addWidget(self.bit_timing_table)
        vbox_layout.addWidget(self.ok_button)
        self.setLayout(vbox_layout)
        self.nominal_bitrate_combox.currentIndexChanged.connect(self.calculate_bit_timing)
        self.nominal_sample_point_combox.currentIndexChanged.connect(self.calculate_bit_timing)
        self.data_bitrate_combox.currentIndexChanged.connect(self.calculate_bit_timing)
        self.data_sample_point_combox.currentIndexChanged.connect(self.calculate_bit_timing)
        self.ok_button.clicked.connect(self.set_bit_timing)

    @Slot()
    @Slot(int)
    def calculate_bit_timing(self, *_args, **_kwargs) -> None:
        self.frequency_label.setText(f'Clock Frequency: {round(self.channel_info.fclk_can / 1e6)} MHz')
        if self.channel_info.feature & GSCANFeature.FD:
            try:
                self.bit_timing = BitTimingFd.from_sample_point(
                    f_clock=self.channel_info.fclk_can,
                    nom_bitrate=round(float(self.nominal_bitrate_combox.currentText()) * 1e3),
                    nom_sample_point=float(self.nominal_sample_point_combox.currentText()),
                    data_bitrate=round(float(self.data_bitrate_combox.currentText()) * 1e3),
                    data_sample_point=float(self.data_sample_point_combox.currentText())
                )
            except ValueError:
                self.ok_button.setEnabled(False)
            else:
                self.bit_timing_table.setItem(0, 0, QTableWidgetItem(str(self.bit_timing.nom_brp)))
                self.bit_timing_table.setItem(0, 1, QTableWidgetItem(str(self.bit_timing.nom_tseg1)))
                self.bit_timing_table.setItem(0, 2, QTableWidgetItem(str(self.bit_timing.nom_tseg2)))
                self.bit_timing_table.setItem(0, 3, QTableWidgetItem(str(self.bit_timing.nom_sjw)))
                self.bit_timing_table.setItem(0, 4, QTableWidgetItem(f'{self.bit_timing.nom_tq} ns'))
                self.bit_timing_table.setItem(0, 5, QTableWidgetItem(str(self.bit_timing.nbt)))
                self.bit_timing_table.setItem(1, 0, QTableWidgetItem(str(self.bit_timing.data_brp)))
                self.bit_timing_table.setItem(1, 1, QTableWidgetItem(str(self.bit_timing.data_tseg1)))
                self.bit_timing_table.setItem(1, 2, QTableWidgetItem(str(self.bit_timing.data_tseg2)))
                self.bit_timing_table.setItem(1, 3, QTableWidgetItem(str(self.bit_timing.data_sjw)))
                self.bit_timing_table.setItem(1, 4, QTableWidgetItem(f'{self.bit_timing.data_tq} ns'))
                self.bit_timing_table.setItem(1, 5, QTableWidgetItem(str(self.bit_timing.dbt)))
                self.ok_button.setEnabled(True)
        else:
            try:
                self.bit_timing = BitTiming.from_sample_point(
                    f_clock=self.channel_info.fclk_can,
                    bitrate=round(float(self.nominal_bitrate_combox.currentText()) * 1e3),
                    sample_point=float(self.nominal_sample_point_combox.currentText())
                )
            except ValueError:
                self.ok_button.setEnabled(False)
            else:
                self.bit_timing_table.setItem(0, 0, QTableWidgetItem(str(self.bit_timing.brp)))
                self.bit_timing_table.setItem(0, 1, QTableWidgetItem(str(self.bit_timing.tseg1)))
                self.bit_timing_table.setItem(0, 2, QTableWidgetItem(str(self.bit_timing.tseg2)))
                self.bit_timing_table.setItem(0, 3, QTableWidgetItem(str(self.bit_timing.sjw)))
                self.bit_timing_table.setItem(0, 4, QTableWidgetItem(f'{self.bit_timing.tq} ns'))
                self.bit_timing_table.setItem(0, 5, QTableWidgetItem(str(self.bit_timing.nbt)))
                self.bit_timing_table.setItem(1, 0, QTableWidgetItem('-'))
                self.bit_timing_table.setItem(1, 1, QTableWidgetItem('-'))
                self.bit_timing_table.setItem(1, 2, QTableWidgetItem('-'))
                self.bit_timing_table.setItem(1, 3, QTableWidgetItem('-'))
                self.bit_timing_table.setItem(1, 4, QTableWidgetItem('-'))
                self.bit_timing_table.setItem(1, 5, QTableWidgetItem('-'))
                self.ok_button.setEnabled(True)

    @Slot(GSDeviceBTConstExtended)
    def update_channel_info(self, info: GSDeviceBTConstExtended) -> None:
        self.channel_info = info
        self.calculate_bit_timing()

    @Slot()
    def set_bit_timing(self) -> None:
        if self.bit_timing is not None:
            if isinstance(self.bit_timing, BitTiming):
                self.setBitTiming.emit(GSDeviceBitTiming(1, self.bit_timing.tseg1 - 1, self.bit_timing.tseg2, self.bit_timing.sjw, self.bit_timing.brp))
            if isinstance(self.bit_timing, BitTimingFd):
                self.setBitTiming.emit(
                    GSDeviceBitTiming(1, self.bit_timing.nom_tseg1 - 1, self.bit_timing.nom_tseg2, self.bit_timing.nom_sjw, self.bit_timing.nom_brp))
                self.setDataBitTiming.emit(GSDeviceBitTiming(1, self.bit_timing.data_tseg1 - 1, self.bit_timing.data_tseg2, self.bit_timing.data_sjw, self.bit_timing.data_brp))
            self.accept()


class MainWindow(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Candle Viewer')
        self.resize(1280, 720)

        # Setup UI.
        vbox_layout = QVBoxLayout(self)
        hbox_layout1 = QHBoxLayout()
        self.scan_button = QPushButton('Scan')
        self.device_selector = QComboBox()
        self.device_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.channel_selector = QComboBox()
        self.channel_selector.setEnabled(False)
        self.bit_timing_button = QPushButton('Set BitTiming')
        self.bit_timing_button.setEnabled(False)
        self.start_button = QPushButton('Start')
        self.start_button.setCheckable(True)
        self.start_button.setEnabled(False)
        hbox_layout1.addWidget(self.scan_button)
        hbox_layout1.addWidget(QLabel('Device:'))
        hbox_layout1.addWidget(self.device_selector)
        hbox_layout1.addWidget(QLabel('Channel:'))
        hbox_layout1.addWidget(self.channel_selector)
        hbox_layout1.addWidget(self.bit_timing_button)
        hbox_layout1.addWidget(self.start_button)
        hbox_layout1.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        hbox_layout2 = QHBoxLayout()
        self.fd_checkbox = QCheckBox('FD')
        self.fd_checkbox.setEnabled(False)
        self.loopback_checkbox = QCheckBox('Loopback')
        self.loopback_checkbox.setEnabled(False)
        self.listen_only_checkbox = QCheckBox('Listen Only')
        self.listen_only_checkbox.setEnabled(False)
        self.triple_sample_checkbox = QCheckBox('Triple Sample')
        self.triple_sample_checkbox.setEnabled(False)
        self.one_shot_checkbox = QCheckBox('One Shot')
        self.one_shot_checkbox.setEnabled(False)
        self.bit_error_reporting_checkbox = QCheckBox('Bit Error Reporting')
        self.bit_error_reporting_checkbox.setEnabled(False)
        hbox_layout2.addWidget(self.fd_checkbox)
        hbox_layout2.addWidget(self.loopback_checkbox)
        hbox_layout2.addWidget(self.listen_only_checkbox)
        hbox_layout2.addWidget(self.triple_sample_checkbox)
        hbox_layout2.addWidget(self.one_shot_checkbox)
        hbox_layout2.addWidget(self.bit_error_reporting_checkbox)
        hbox_layout2.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        self.message_viewer = QTableView()
        self.message_viewer.horizontalHeader().setStretchLastSection(True)
        hbox_layout3 = QHBoxLayout()
        vbox_layout1 = QVBoxLayout()
        self.send_id_spin_box = QSpinBox()
        self.send_id_spin_box.setDisplayIntegerBase(16)
        font = QFont()
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        self.send_id_spin_box.setFont(font)
        self.send_id_spin_box.setEnabled(False)
        self.send_dlc_selector = QComboBox()
        self.send_dlc_selector.setEnabled(False)
        self.send_eff_checkbox = QCheckBox('EFF')
        self.send_eff_checkbox.setEnabled(False)
        self.send_rtr_checkbox = QCheckBox('RTR')
        self.send_rtr_checkbox.setEnabled(False)
        self.send_fd_checkbox = QCheckBox('FD')
        self.send_fd_checkbox.setEnabled(False)
        self.send_brs_checkbox = QCheckBox('BRS')
        self.send_brs_checkbox.setEnabled(False)
        self.send_esi_checkbox = QCheckBox('ESI')
        self.send_esi_checkbox.setEnabled(False)
        vbox_layout1.addWidget(QLabel('CAN ID'))
        vbox_layout1.addWidget(self.send_id_spin_box)
        vbox_layout1.addWidget(QLabel('DLC'))
        vbox_layout1.addWidget(self.send_dlc_selector)
        vbox_layout1.addWidget(self.send_eff_checkbox)
        vbox_layout1.addWidget(self.send_rtr_checkbox)
        vbox_layout1.addWidget(self.send_fd_checkbox)
        vbox_layout1.addWidget(self.send_brs_checkbox)
        vbox_layout1.addWidget(self.send_esi_checkbox)
        vbox_layout1.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        self.input_panel = InputPanel()
        self.input_panel.setEnabled(False)
        vbox_layout2 = QVBoxLayout()
        self.send_once_button = QPushButton('Send Once')
        self.send_once_button.setEnabled(False)
        self.send_repeat_button = QPushButton('Send Repeat')
        self.send_repeat_button.setEnabled(False)
        self.send_repeat_button.setCheckable(True)
        self.cycle_time_spin_box = QSpinBox()
        self.cycle_time_spin_box.setSuffix(' ms')
        self.cycle_time_spin_box.setMinimum(1)
        self.cycle_time_spin_box.setMaximum(100000)
        self.cycle_time_spin_box.setValue(10)
        self.cycle_time_spin_box.setEnabled(False)
        self.random_data_button = QPushButton('Random Data')
        self.random_data_button.setEnabled(False)
        vbox_layout2.addWidget(self.send_once_button)
        vbox_layout2.addWidget(self.send_repeat_button)
        vbox_layout2.addWidget(QLabel('Cycle Time'))
        vbox_layout2.addWidget(self.cycle_time_spin_box)
        vbox_layout2.addWidget(self.random_data_button)
        vbox_layout2.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        hbox_layout3.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        hbox_layout3.addLayout(vbox_layout1)
        hbox_layout3.addWidget(self.input_panel)
        hbox_layout3.addLayout(vbox_layout2)
        hbox_layout3.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        vbox_layout.addLayout(hbox_layout1)
        vbox_layout.addLayout(hbox_layout2)
        vbox_layout.addWidget(self.message_viewer, 1)
        vbox_layout.addLayout(hbox_layout3)
        self.setLayout(vbox_layout)

        # Prepare candle manager and polling thread.
        self.polling_thread = QThread(self)
        self.candle_manager = CandleManager()
        self.candle_manager.moveToThread(self.polling_thread)

        # Timer for send message.
        self.send_timer = QTimer(self)
        self.send_timer.setInterval(self.cycle_time_spin_box.value())

        # Message model for better performance.
        self.message_model_thread = QThread(self)
        message_model = MessageTableModel()
        message_model.moveToThread(self.message_model_thread)
        self.message_viewer.setModel(message_model)

        # Dialog for configurate bit timing setting.
        self.bit_timing_dialog = BitTimingDialog(self)

        # Connect signals and slots.
        self.candle_manager.stateTransition.connect(self.handle_state_transition)
        self.scan_button.clicked.connect(self.candle_manager.scan)
        self.candle_manager.scanResult.connect(self.handle_scan_result)
        self.device_selector.currentIndexChanged.connect(self.candle_manager.select_device)
        self.channel_selector.currentIndexChanged.connect(self.candle_manager.select_channel)
        self.candle_manager.messageReceived.connect(message_model.handle_message)
        self.candle_manager.exceptionOccurred.connect(self.handle_device_exception)
        self.start_button.toggled.connect(self.handle_start)
        self.bit_timing_dialog.setBitTiming.connect(self.candle_manager.set_bit_timing)
        self.bit_timing_dialog.setDataBitTiming.connect(self.candle_manager.set_data_bit_timing)
        self.candle_manager.channelInfo.connect(self.bit_timing_dialog.update_channel_info)
        self.bit_timing_button.clicked.connect(self.bit_timing_dialog.exec)
        self.send_dlc_selector.currentIndexChanged.connect(self.input_panel.set_dlc)
        self.send_rtr_checkbox.toggled.connect(self.handle_remote_frame_checked)
        self.send_once_button.clicked.connect(self.send_message)
        self.send_fd_checkbox.toggled.connect(self.handle_send_fd_checked)
        self.cycle_time_spin_box.valueChanged.connect(lambda v: self.send_timer.setInterval(v))
        self.send_timer.timeout.connect(self.send_message)
        self.send_repeat_button.toggled.connect(self.send_message_repeat)
        self.send_eff_checkbox.toggled.connect(self.handle_extended_id_checked)
        self.random_data_button.clicked.connect(self.input_panel.random)
        message_model.rowInserted.connect(self.message_viewer.scrollToBottom)

        # Start thread and timer.
        self.polling_thread.start()
        self.message_model_thread.start()

    def send_message_repeat(self, checked: bool) -> None:
        if checked:
            self.send_timer.start()
        else:
            self.send_timer.stop()

    @Slot()
    def send_message(self) -> None:
        data = self.input_panel.data()
        header = GSHostFrameHeader(0, self.send_id_spin_box.value(), self.send_dlc_selector.currentIndex(), self.channel_selector.currentIndex(), GSCANFlag(0))
        header.is_extended_id = self.send_eff_checkbox.isChecked()
        header.is_remote_frame = self.send_rtr_checkbox.isChecked()
        header.is_fd = self.send_fd_checkbox.isEnabled() and self.send_fd_checkbox.isChecked()
        header.is_bitrate_switch = self.send_brs_checkbox.isEnabled() and self.send_brs_checkbox.isChecked()
        header.is_error_state_indicator = self.send_esi_checkbox.isEnabled() and self.send_esi_checkbox.isChecked()
        self.candle_manager.send_message(GSHostFrame(header, data, 0))

    @Slot(bool)
    def handle_extended_id_checked(self, checked: bool) -> None:
        if checked:
            self.send_id_spin_box.setMaximum((1 << 29) - 1)
        else:
            self.send_id_spin_box.setMaximum((1 << 11) - 1)

    @Slot(bool)
    def handle_remote_frame_checked(self, checked: bool) -> None:
        if checked:
            self.input_panel.setEnabled(False)
        else:
            self.input_panel.setEnabled(True)

    @Slot(bool)
    def handle_send_fd_checked(self, checked: bool) -> None:
        self.send_dlc_selector.clear()
        self.send_dlc_selector.addItems([str(i) for i in DLC2LEN[:9]])
        if checked:
            self.send_dlc_selector.addItems([str(i) for i in DLC2LEN[9:]])

    @Slot(CandleManagerState)
    def handle_state_transition(self, _from_state: CandleManagerState, to_state: CandleManagerState) -> None:
        if to_state == CandleManagerState.DeviceSelection:
            self.device_selector.setEnabled(True)
            self.channel_selector.setEnabled(False)
            self.bit_timing_button.setEnabled(False)
            self.start_button.setEnabled(False)
            self.fd_checkbox.setEnabled(False)
            self.loopback_checkbox.setEnabled(False)
            self.listen_only_checkbox.setEnabled(False)
            self.triple_sample_checkbox.setEnabled(False)
            self.one_shot_checkbox.setEnabled(False)
            self.bit_error_reporting_checkbox.setEnabled(False)
            self.send_id_spin_box.setEnabled(False)
            self.send_dlc_selector.setEnabled(False)
            self.send_eff_checkbox.setEnabled(False)
            self.send_rtr_checkbox.setEnabled(False)
            self.send_fd_checkbox.setEnabled(False)
            self.send_brs_checkbox.setEnabled(False)
            self.send_esi_checkbox.setEnabled(False)
            self.input_panel.setEnabled(False)
            self.send_once_button.setEnabled(False)
            self.send_repeat_button.setEnabled(False)
            self.send_repeat_button.setChecked(False)
            self.cycle_time_spin_box.setEnabled(False)
            self.random_data_button.setEnabled(False)
        if to_state == CandleManagerState.ChannelSelection:
            self.device_selector.setEnabled(True)
            self.channel_selector.setEnabled(True)
            self.bit_timing_button.setEnabled(False)
            self.start_button.setEnabled(False)
            self.fd_checkbox.setEnabled(False)
            self.loopback_checkbox.setEnabled(False)
            self.listen_only_checkbox.setEnabled(False)
            self.triple_sample_checkbox.setEnabled(False)
            self.one_shot_checkbox.setEnabled(False)
            self.bit_error_reporting_checkbox.setEnabled(False)
            self.send_id_spin_box.setEnabled(False)
            self.send_dlc_selector.setEnabled(False)
            self.send_eff_checkbox.setEnabled(False)
            self.send_rtr_checkbox.setEnabled(False)
            self.send_fd_checkbox.setEnabled(False)
            self.send_brs_checkbox.setEnabled(False)
            self.send_esi_checkbox.setEnabled(False)
            self.input_panel.setEnabled(False)
            self.send_once_button.setEnabled(False)
            self.send_repeat_button.setEnabled(False)
            self.send_repeat_button.setChecked(False)
            self.cycle_time_spin_box.setEnabled(False)
            self.random_data_button.setEnabled(False)
            self.channel_selector.clear()
            self.channel_selector.addItems([str(i) for i in range(len(self.candle_manager.interface))])    # type: ignore[arg-type]
        if to_state == CandleManagerState.Configuration:
            self.device_selector.setEnabled(True)
            self.channel_selector.setEnabled(True)
            self.bit_timing_button.setEnabled(True)
            self.start_button.setEnabled(True)
            try:
                self.fd_checkbox.setEnabled(self.candle_manager.channel.is_fd_supported)
                self.loopback_checkbox.setEnabled(self.candle_manager.channel.is_loop_back_supported)
                self.listen_only_checkbox.setEnabled(self.candle_manager.channel.is_listen_only_supported)
                self.triple_sample_checkbox.setEnabled(self.candle_manager.channel.is_triple_sample_supported)
                self.one_shot_checkbox.setEnabled(self.candle_manager.channel.is_one_shot_supported)
                self.bit_error_reporting_checkbox.setEnabled(self.candle_manager.channel.is_bit_error_reporting_supported)
            except AttributeError:
                pass
            self.send_id_spin_box.setEnabled(False)
            self.send_dlc_selector.setEnabled(False)
            self.send_eff_checkbox.setEnabled(False)
            self.send_rtr_checkbox.setEnabled(False)
            self.send_fd_checkbox.setEnabled(False)
            self.send_brs_checkbox.setEnabled(False)
            self.send_esi_checkbox.setEnabled(False)
            self.input_panel.setEnabled(False)
            self.send_once_button.setEnabled(False)
            self.send_repeat_button.setEnabled(False)
            self.send_repeat_button.setChecked(False)
            self.cycle_time_spin_box.setEnabled(False)
            self.random_data_button.setEnabled(False)
            self.start_button.setChecked(False)
            try:
                self.fd_checkbox.setChecked(self.candle_manager.channel.is_fd_supported)
            except AttributeError:
                pass
        if to_state == CandleManagerState.Running:
            self.device_selector.setEnabled(False)
            self.channel_selector.setEnabled(False)
            self.bit_timing_button.setEnabled(False)
            self.start_button.setEnabled(True)
            self.fd_checkbox.setEnabled(False)
            self.loopback_checkbox.setEnabled(False)
            self.listen_only_checkbox.setEnabled(False)
            self.triple_sample_checkbox.setEnabled(False)
            self.one_shot_checkbox.setEnabled(False)
            self.bit_error_reporting_checkbox.setEnabled(False)
            self.send_id_spin_box.setEnabled(True)
            self.send_dlc_selector.setEnabled(True)
            self.send_eff_checkbox.setEnabled(True)
            self.send_rtr_checkbox.setEnabled(True)
            self.send_fd_checkbox.setEnabled(self.candle_manager.channel.is_fd_supported)
            self.send_brs_checkbox.setEnabled(self.candle_manager.channel.is_fd_supported)
            self.send_esi_checkbox.setEnabled(self.candle_manager.channel.is_fd_supported)
            self.input_panel.setEnabled(not self.send_rtr_checkbox.isChecked())
            self.send_once_button.setEnabled(True)
            self.send_repeat_button.setEnabled(True)
            self.send_repeat_button.setChecked(False)
            self.cycle_time_spin_box.setEnabled(True)
            self.random_data_button.setEnabled(True)
            self.handle_send_fd_checked(self.send_fd_checkbox.isChecked())
            self.handle_extended_id_checked(self.send_eff_checkbox.isChecked())

    @Slot(list)
    def handle_scan_result(self, result: List[CandleDevice]) -> None:
        self.device_selector.clear()
        self.device_selector.addItems([str(i) for i in result])

    @Slot(bool)
    def handle_start(self, start: bool) -> None:
        if start:
            self.candle_manager.start(
                self.fd_checkbox.isChecked() if self.fd_checkbox.isEnabled() else False,
                self.loopback_checkbox.isChecked() if self.loopback_checkbox.isEnabled() else False,
                self.listen_only_checkbox.isChecked() if self.listen_only_checkbox.isEnabled() else False,
                self.triple_sample_checkbox.isChecked() if self.triple_sample_checkbox.isEnabled() else False,
                self.one_shot_checkbox.isChecked() if self.one_shot_checkbox.isEnabled() else False,
                self.bit_error_reporting_checkbox.isChecked() if self.bit_error_reporting_checkbox.isEnabled() else False
            )
        else:
            self.candle_manager.stop()

    @Slot(str)
    def handle_device_exception(self, error: str) -> None:
        message_box = QMessageBox(self)
        message_box.setText(error)
        message_box.open()

    def closeEvent(self, event: QCloseEvent):
        self.polling_thread.requestInterruption()
        self.message_model_thread.requestInterruption()
        self.polling_thread.quit()
        self.message_model_thread.quit()
        self.polling_thread.wait()
        self.message_model_thread.wait()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    main_window = MainWindow()
    main_window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
