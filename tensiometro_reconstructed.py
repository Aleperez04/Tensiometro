import sys
import asyncio
import time
import csv
import math
import numpy as np
from collections import deque
from threading import Lock
from bleak import BleakClient, BleakScanner
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QPushButton, 
                             QLabel, QComboBox, QVBoxLayout, QHBoxLayout, 
                             QMessageBox, QFileDialog, QSpinBox, QCheckBox, 
                             QProgressBar, QGroupBox, QGridLayout, QFrame)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QImage
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from qasync import QEventLoop

CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
CONTROL_UUID = "12345678-1234-1234-1234-123456789abc"
DEVICE_NAME_PREFIX = "Tensiometro_"
EXPECTED_FREQ = 100

DT_SAMPLE = 1.0 / EXPECTED_FREQ
SAMPLES_PER_PACKET = 4
BYTES_PER_SAMPLE = 3
MAX_18BIT = 262143

class ButterworthFilter:
    """Filtro IIR Butterworth pasabanda de orden 4 (0.5 - 8.0 Hz a fs=100Hz) usando secciones de segundo orden (SOS)."""
    def __init__(self):
        # Coeficientes SOS calculados para fs=100Hz, f_low=0.5Hz, f_high=8.0Hz
        self.sos = [
            [1.78260999e-03, 3.56521998e-03, 1.78260999e-03, -1.29176642e+00, 4.35717576e-01],
            [1.00000000e+00, 2.00000000e+00, 1.00000000e+00, -1.51260719e+00, 7.19524086e-01],
            [1.00000000e+00, -2.00000000e+00, 1.00000000e+00, -1.93751823e+00, 9.38713070e-01],
            [1.00000000e+00, -2.00000000e+00, 1.00000000e+00, -1.97736736e+00, 9.78372608e-01]
        ]
        self.lock = Lock()
        self.reset()
        
    def filter(self, x):
        with self.lock:
            val = x
            for i in range(4):
                b0, b1, b2, a1, a2 = self.sos[i]
                w1, w2 = self.states[i]
                w = val - a1 * w1 - a2 * w2
                y = b0 * w + b1 * w1 + b2 * w2
                self.states[i] = [w, w1]
                val = y
            return val
            
    def reset(self):
        with self.lock:
            self.states = [[0.0, 0.0] for _ in range(4)]

class BLEWorker(QThread):
    """Hilo de trabajo que maneja la comunicación BLE de manera asíncrona."""
    data_received = pyqtSignal(list, list, int)
    connection_status = pyqtSignal(bool, str)
    log_message = pyqtSignal(str, str)
    loop_ready = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.client = None
        self.running = True
        self.connected = False
        self.address = None
        self.should_reconnect = False
        self.reconnect_attempts = 0
        self.loop = None
    
    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop_ready.emit()
        self.loop.run_until_complete(self._keep_alive())
    
    async def _keep_alive(self):
        try:
            while self.running:
                await asyncio.sleep(0.1)
        except Exception:
            pass
        await self._cleanup_client()
        if self.loop:
            self.loop.stop()
    
    def connect(self, address):
        self.address = address
        self.should_reconnect = True
        self.reconnect_attempts = 0
        if self.loop and self.running:
            asyncio.run_coroutine_threadsafe(self._connect_loop(), self.loop)
    
    def disconnect(self):
        self.should_reconnect = False
        if self.loop and self.running:
            asyncio.run_coroutine_threadsafe(self._disconnect(), self.loop)
    
    def stop(self):
        self.running = False
        self.should_reconnect = False
    
    async def _connect_loop(self):
        try:
            while self.should_reconnect:
                if self.running:
                    await self._connect()
                    while self.connected and self.should_reconnect:
                        await asyncio.sleep(1)
                        if not (self.client and self.client.is_connected):
                            self.connected = False
                            self.log_message.emit("⚠️ Conexion perdida - Reconectando...", "WARNING")
                            break
                    else:
                        self.reconnect_attempts += 1
                        if self.reconnect_attempts > 5:
                            self.log_message.emit("❌ Maximo intentos de reconexion", "ERROR")
                            self.connection_status.emit(False, "No se pudo reconectar")
                            return
                        await asyncio.sleep(3)
        except Exception as e:
            self.log_message.emit(f"Error: {str(e)}", "ERROR")
            await asyncio.sleep(3)
    
    async def _connect(self):
        try:
            if self.client and self.client.is_connected:
                await self._cleanup_client()
            self.client = BleakClient(self.address)
            await self.client.connect()
            await self.client.write_gatt_char(CONTROL_UUID, b"START", response=False)
            await self.client.start_notify(CHAR_UUID, self._notification_handler)
            self.connected = True
            self.reconnect_attempts = 0
            self.connection_status.emit(True, f"Conectado a {self.address}")
            self.log_message.emit("✅ Dispositivo conectado", "SUCCESS")
        except Exception as e:
            self.connection_status.emit(False, str(e))
            self.log_message.emit(f"❌ Error de conexion: {str(e)}", "ERROR")
    
    async def _cleanup_client(self):
        if self.client:
            try:
                await self.client.stop_notify(CHAR_UUID)
            except Exception:
                pass
            try:
                await self.client.write_gatt_char(CONTROL_UUID, b"STOP", response=False)
            except Exception:
                pass
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
    
    async def _disconnect(self):
        try:
            self.should_reconnect = False
            await self._cleanup_client()
            self.connected = False
            self.connection_status.emit(False, "Desconectado")
            self.log_message.emit("🔴 Dispositivo desconectado", "INFO")
        except Exception:
            pass
    
    def _notification_handler(self, sender, data):
        try:
            if len(data) == 14:
                red_samples = []
                ir_samples = []
                for i in range(2):
                    offset = i * 6
                    red_val = (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]
                    ir_val = (data[offset + 3] << 16) | (data[offset + 4] << 8) | data[offset + 5]
                    red_samples.append(red_val)
                    ir_samples.append(ir_val)
                packet_seq = (data[13] << 8) | data[12]
                self.data_received.emit(red_samples, ir_samples, packet_seq)
            else:
                self.log_message.emit(f"⚠️ Paquete invalido: {len(data)} bytes", "WARNING")
        except Exception as e:
            self.log_message.emit(f"Error: {str(e)}", "ERROR")

class MainWindow(QMainWindow):
    """Interfaz gráfica estilo Consola Médica HUD con fondo color Beige Claro (HEX #D4C19C)."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESTACIÓN DE MONITOREO CARDIACO - GOBIERNO DE MÉXICO")
        self.resize(1450, 920)
        self.ble_worker = None
        self.is_connected = False
        self.is_previewing = False
        self.is_recording = False
        self.global_sample_index = 0
        self.actual_freq = 0
        self.last_freq_time = time.time()
        self.last_freq_samples = 0
        
        self.dc_filter_enabled = False
        self.filter_red = ButterworthFilter()
        self.filter_ir = ButterworthFilter()
        self.skin_calibration_data = [
            {"name": "Muy clara", "spo2_offset": 0.0, "sbp_offset": 0.0, "dbp_offset": 0.0},
            {"name": "Clara", "spo2_offset": 0.0, "sbp_offset": 0.0, "dbp_offset": 0.0},
            {"name": "Intermedia", "spo2_offset": 0.2, "sbp_offset": -0.5, "dbp_offset": -0.2},
            {"name": "Morena", "spo2_offset": 0.6, "sbp_offset": -1.0, "dbp_offset": -0.5},
            {"name": "Oscura", "spo2_offset": 1.2, "sbp_offset": -2.0, "dbp_offset": -1.0},
            {"name": "Muy oscura", "spo2_offset": 2.0, "sbp_offset": -3.5, "dbp_offset": -1.8}
        ]
        self.recording_start_index = 0
        self.invert_signal = False
        self.expected_packet_seq = 0
        self.received_packets = 0
        self.lost_packets = 0
        self.duplicate_packets = 0
        self.start_time = None
        self.recording_start_time = 0.0
        
        self.time_buffer = deque(maxlen=800)
        self.raw_red_buffer = deque(maxlen=800)
        self.raw_ir_buffer = deque(maxlen=800)
        self.filtered_red_buffer = deque(maxlen=800)
        self.filtered_ir_buffer = deque(maxlen=800)
        
        self.recording_time = []
        self.recording_raw_red = []
        self.recording_raw_ir = []
        self.recording_filtered_red = []
        self.recording_filtered_ir = []
        
        self.setup_ui()
        self.setup_timers()
    
    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        
        # Estilo de consola médica claro institucional
        # Fondo Principal: Beige Claro (HEX #D4C19C)
        # Paneles Internos: Blanco (HEX #FFFFFF)
        # Texto Principal: Guinda Muy Oscuro (HEX #4E232E)
        # Título y Botones: Guinda Oficial (HEX #621132)
        # Bordes: Dorado Institucional (HEX #B38E5D)
        # Resaltado de botones: Guinda Claro (HEX #9D2449)
        
        central.setStyleSheet("""
            QWidget {
                background-color: #D4C19C;
                color: #4E232E;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            
            /* Encabezado Principal */
            #Header {
                background-color: #621132;
                border-bottom: 2px solid #B38E5D;
            }
            #Header QLabel {
                color: #D4C19C;
                background-color: #621132;
            }
            
            /* Panel de Ritmo Cardíaco Grande (Vitals Card) */
            #VitalsPanel {
                background-color: #FFFFFF;
                border: 2px solid #B38E5D;
                border-radius: 8px;
            }
            #VitalsPanel QLabel {
                background-color: #FFFFFF;
            }
            #VitalsTitle {
                color: #621132;
                font-weight: bold;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            #VitalsValue {
                color: #9D2449;
                font-size: 84px;
                font-weight: bold;
            }
            #VitalsUnit {
                color: #B38E5D;
                font-size: 11px;
                font-weight: bold;
            }
            
            /* Panel de Consola Inferior */
            #ConsolePanel {
                background-color: #FFFFFF;
                border: 1px solid #B38E5D;
                border-radius: 8px;
            }
            #ConsolePanel QLabel {
                background-color: #FFFFFF;
                color: #4E232E;
            }
            #ConsolePanel QGroupBox {
                color: #621132;
                font-weight: bold;
                font-size: 10px;
                border: 1px solid #B38E5D;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 8px;
                background-color: #FFFFFF;
            }
            #ConsolePanel QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                background-color: #FFFFFF;
            }
            
            /* Botones del Sistema */
            QPushButton {
                background-color: #621132;
                color: #FFFFFF;
                border: 1px solid #B38E5D;
                padding: 8px 12px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #9D2449;
                color: #FFFFFF;
                border: 1px solid #D4C19C;
            }
            QPushButton:disabled {
                background-color: #E2DCD0;
                color: #A59E93;
                border: 1px solid #C4BEB3;
            }
            
            QComboBox, QSpinBox {
                background-color: #FFFFFF;
                color: #4E232E;
                border: 1px solid #B38E5D;
                padding: 4px;
                border-radius: 4px;
            }
            QCheckBox {
                color: #4E232E;
                spacing: 5px;
                background-color: #FFFFFF;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #B38E5D;
                border-radius: 3px;
                background-color: #FFFFFF;
            }
            QCheckBox::indicator:checked {
                background-color: #9D2449;
                border: 1px solid #D4C19C;
            }
            QProgressBar {
                border: 1px solid #B38E5D;
                border-radius: 4px;
                text-align: center;
                background-color: #FFFFFF;
                color: #621132;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #9D2449;
            }
        """)
        
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)
        
        # 1. ENCABEZADO DE CONSOLA (HUD Top Bar)
        header = QWidget()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 10, 15, 10)
        
        lbl_logo = QLabel("🇲🇽 GOBIERNO DE MÉXICO")
        lbl_logo.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        lbl_title = QLabel("|   ESTACIÓN MÉDICA DE DIAGNÓSTICO CARDIOVASCULAR")
        lbl_title.setFont(QFont("Arial", 10, QFont.Weight.Normal))
        lbl_title.setStyleSheet("color: #D4C19C;")
        
        self.status_label = QLabel("⚫ SENSOR: DESCONECTADO")
        self.status_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.status_label.setStyleSheet("color: #D4C19C;")
        
        header_layout.addWidget(lbl_logo)
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        main_layout.addWidget(header)
        
        # 2. FILA CENTRAL: Osciloscopio (70% Ancho) | Monitor de Signos (30% Ancho)
        fila_central = QHBoxLayout()
        fila_central.setSpacing(15)
        
        # A) Canvas de la Gráfica PPG (Fondo beige claro con rejilla dorada, trazo guinda claro)
        self.figure = Figure(figsize=(10, 5), facecolor="#D4C19C")
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor("#FFFFFF")
        self.ax.grid(True, alpha=0.3, color="#B38E5D")
        self.ax.set_xlabel("Tiempo (segundos)", color="#621132", fontsize=9)
        self.ax.set_ylabel("Amplitud (unidades)", color="#621132", fontsize=9)
        self.ax.tick_params(colors="#621132", labelsize=8)
        self.data_line, = self.ax.plot([], [], linewidth=2.5, color="#9D2449")
        
        
        # B) Panel de Signos Vitales (Vitals Card)
        vitals_panel = QWidget()
        vitals_panel.setObjectName("VitalsPanel")
        vitals_layout = QVBoxLayout(vitals_panel)
        vitals_layout.setContentsMargins(20, 15, 20, 15)
        vitals_layout.setSpacing(8)
        
        lbl_bpm_title = QLabel("RITMO CARDÍACO")
        lbl_bpm_title.setObjectName("VitalsTitle")
        lbl_bpm_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.bpm_value_label = QLabel("--")
        self.bpm_value_label.setObjectName("VitalsValue")
        self.bpm_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        lbl_bpm_unit = QLabel("LATIDOS POR MINUTO (LPM)")
        lbl_bpm_unit.setObjectName("VitalsUnit")
        lbl_bpm_unit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Divisor para la Presión Arterial
        div_bp = QFrame()
        div_bp.setFrameShape(QFrame.Shape.HLine)
        div_bp.setStyleSheet("background-color: #B38E5D; max-height: 1px;")
        
        lbl_bp_title = QLabel("PRESIÓN ARTERIAL ESTIMADA")
        lbl_bp_title.setObjectName("VitalsTitle")
        lbl_bp_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.bp_value_label = QLabel("-- / --")
        self.bp_value_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        self.bp_value_label.setStyleSheet("color: #621132; background-color: #FFFFFF;")
        self.bp_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        lbl_bp_unit = QLabel("mmHg (SISTÓLICA / DIASTÓLICA)")
        lbl_bp_unit.setObjectName("VitalsUnit")
        lbl_bp_unit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Divisor para Oxígeno en Sangre
        div_spo2 = QFrame()
        div_spo2.setFrameShape(QFrame.Shape.HLine)
        div_spo2.setStyleSheet("background-color: #B38E5D; max-height: 1px;")
        
        lbl_spo2_title = QLabel("OXÍGENO EN SANGRE")
        lbl_spo2_title.setObjectName("VitalsTitle")
        lbl_spo2_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.spo2_value_label = QLabel("--")
        self.spo2_value_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        self.spo2_value_label.setStyleSheet("color: #9D2449; background-color: #FFFFFF;")
        self.spo2_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        lbl_spo2_unit = QLabel("% SpO2 (SATURACIÓN)")
        lbl_spo2_unit.setObjectName("VitalsUnit")
        lbl_spo2_unit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Corazón latiendo visualmente
        self.heart_label = QLabel("❤️")
        self.heart_label.setFont(QFont("Arial", 32))
        self.heart_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.heart_label.setStyleSheet("color: #888888;")
        
        # Divisor interno inferior
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background-color: #B38E5D; max-height: 1px;")
        
        # Estadísticas breves de calidad
        self.quality_label = QLabel("📶 Calidad: --")
        self.quality_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loss_label = QLabel("📉 Pérdida: 0.0%")
        self.loss_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.freq_label = QLabel("📊 Muestreo: 0.0 Hz")
        self.freq_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        vitals_layout.addWidget(lbl_bpm_title)
        vitals_layout.addWidget(self.bpm_value_label)
        vitals_layout.addWidget(lbl_bpm_unit)
        vitals_layout.addWidget(div_bp)
        vitals_layout.addWidget(lbl_bp_title)
        vitals_layout.addWidget(self.bp_value_label)
        vitals_layout.addWidget(lbl_bp_unit)
        vitals_layout.addWidget(div_spo2)
        vitals_layout.addWidget(lbl_spo2_title)
        vitals_layout.addWidget(self.spo2_value_label)
        vitals_layout.addWidget(lbl_spo2_unit)
        vitals_layout.addWidget(self.heart_label)
        vitals_layout.addWidget(div)
        vitals_layout.addWidget(self.quality_label)
        vitals_layout.addWidget(self.loss_label)
        vitals_layout.addWidget(self.freq_label)
        vitals_layout.addStretch()
        
        fila_central.addWidget(vitals_panel, stretch=3)
        fila_central.addWidget(self.canvas, stretch=7)
        main_layout.addLayout(fila_central)
        
        # Barra de progreso para grabación
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(18)
        main_layout.addWidget(self.progress_bar)
        
        # 3. PANEL DE CONSOLA INFERIOR (Controles agrupados de forma simétrica)
        console_panel = QWidget()
        console_panel.setObjectName("ConsolePanel")
        console_layout = QHBoxLayout(console_panel)
        console_layout.setContentsMargins(15, 10, 15, 15)
        console_layout.setSpacing(20)
        
        # Columna 1: Enlace BLE
        grp_ble = QGroupBox("🔗 COMUNICACIÓN BLE")
        lay_ble = QGridLayout(grp_ble)
        lay_ble.setSpacing(8)
        self.device_combo = QComboBox()
        self.scan_button = QPushButton("🔍 ESCANEAR")
        self.connect_button = QPushButton("🔌 VINCULAR")
        self.disconnect_button = QPushButton("❌ SOLTAR")
        self.disconnect_button.setEnabled(False)
        
        lay_ble.addWidget(QLabel("Dispositivo:"), 0, 0)
        lay_ble.addWidget(self.device_combo, 0, 1)
        lay_ble.addWidget(self.scan_button, 1, 0)
        lay_ble.addWidget(self.connect_button, 1, 1)
        lay_ble.addWidget(self.disconnect_button, 1, 2)
        console_layout.addWidget(grp_ble, stretch=4)
        
        # Columna 2: Parámetros de la Señal
        grp_params = QGroupBox("🔧 PARÁMETROS DEL CANAL")
        lay_params = QVBoxLayout(grp_params)
        lay_params.setSpacing(6)
        self.filter_checkbox = QCheckBox("Filtro Pasabanda (Butterworth 0.5-8Hz)")
        self.invert_checkbox = QCheckBox("Invertir Espejo PPG")
        
        # Calibración de Piel
        skin_layout = QHBoxLayout()
        self.skin_combo = QComboBox()
        self.skin_combo.addItems([
            "Muy clara (ITA > 55°)",
            "Clara (ITA 41° a 55°)",
            "Intermedia (ITA 28° a 41°)",
            "Morena (ITA 10° a 28°)",
            "Oscura (ITA -30° a 10°)",
            "Muy oscura (ITA < -30°)"
        ])
        self.skin_combo.setCurrentIndex(2) # Default Intermedia
        
        self.skin_photo_btn = QPushButton("📷 Foto...")
        self.skin_photo_btn.setStyleSheet("max-width: 65px; min-width: 65px; height: 18px; font-size: 9px;")
        
        skin_layout.addWidget(QLabel("Piel:"))
        skin_layout.addWidget(self.skin_combo)
        skin_layout.addWidget(self.skin_photo_btn)
        
        lay_params.addLayout(skin_layout)
        
        duration_h = QHBoxLayout()
        duration_h.addWidget(QLabel("Duración:"))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 300)
        self.duration_spin.setValue(10)
        self.duration_spin.setSuffix(" seg")
        duration_h.addWidget(self.duration_spin)
        
        lay_params.addWidget(self.filter_checkbox)
        lay_params.addWidget(self.invert_checkbox)
        lay_params.addLayout(duration_h)
        console_layout.addWidget(grp_params, stretch=3)
        
        # Columna 3: Controladores del Estudio
        grp_actions = QGroupBox("🎮 COMANDOS DE DIAGNÓSTICO")
        lay_actions = QGridLayout(grp_actions)
        lay_actions.setSpacing(8)
        
        self.preview_button = QPushButton("📊 VISTA PREVIA")
        self.preview_button.setEnabled(False)
        self.start_button = QPushButton("💾 GRABAR")
        self.start_button.setEnabled(False)
        self.stop_button = QPushButton("⏹️ DETENER")
        self.stop_button.setEnabled(False)
        self.reset_button = QPushButton("🔄 REINICIAR")
        self.save_button = QPushButton("💿 EXPORTAR CSV")
        self.save_button.setEnabled(False)
        
        self.mode_label = QLabel("⚪ ESPERANDO")
        self.mode_label.setStyleSheet("color: #621132; font-weight: bold; font-size: 11px; background-color: #FFFFFF;")
        
        lay_actions.addWidget(self.preview_button, 0, 0)
        lay_actions.addWidget(self.start_button, 0, 1)
        lay_actions.addWidget(self.stop_button, 0, 2)
        lay_actions.addWidget(self.reset_button, 1, 0)
        lay_actions.addWidget(self.save_button, 1, 1)
        lay_actions.addWidget(self.mode_label, 1, 2, Qt.AlignmentFlag.AlignCenter)
        console_layout.addWidget(grp_actions, stretch=5)
        
        main_layout.addWidget(console_panel)
        
        # Ayudas institucionales en el pie
        self.samples_label = QLabel("Muestras recolectadas: 0")
        self.samples_label.setFont(QFont("Arial", 8))
        self.samples_label.setStyleSheet("color: #4E232E;")
        main_layout.addWidget(self.samples_label)
        
        # Conexiones
        self.scan_button.clicked.connect(self.scan_devices)
        self.connect_button.clicked.connect(self.connect_device)
        self.disconnect_button.clicked.connect(self.disconnect_device)
        self.preview_button.clicked.connect(self.start_preview)
        self.start_button.clicked.connect(self.start_recording)
        self.stop_button.clicked.connect(self.stop_all)
        self.reset_button.clicked.connect(self.reset_all)
        self.save_button.clicked.connect(self.save_csv)
        self.filter_checkbox.stateChanged.connect(self.toggle_filter)
        self.invert_checkbox.stateChanged.connect(self.toggle_invert)
        self.skin_photo_btn.clicked.connect(self.analyze_skin_photo)
        
    def setup_timers(self):
        self.plot_timer = QTimer()
        self.plot_timer.timeout.connect(self.update_plot)
        self.plot_timer.start(30)
        self.freq_timer = QTimer()
        self.freq_timer.timeout.connect(self.update_frequency)
        self.freq_timer.start(1000)
        
    def scan_devices(self):
        asyncio.create_task(self.scan_ble())
        
    async def scan_ble(self):
        try:
            self.device_combo.clear()
            self.status_label.setText("🔍 ESCANEANDO...")
            self.status_label.setStyleSheet("color: #B38E5D; font-weight: bold;")
            self.scan_button.setEnabled(False)
            devices = await BleakScanner.discover()
            count = 0
            for d in devices:
                if not d.name:
                    continue
                if not d.name.startswith(DEVICE_NAME_PREFIX):
                    continue
                self.device_combo.addItem(f"{d.name} - {d.address}", d.address)
                count += 1
            
            self.status_label.setText("🟢 VINCULAR DISPOSITIVO")
            self.status_label.setStyleSheet("color: #D4C19C; font-weight: bold;")
            self.scan_button.setEnabled(True)
            self.connect_button.setEnabled(count > 0)
        except Exception as e:
            self.status_label.setText("❌ ERROR BUSCANDO")
            self.status_label.setStyleSheet("color: #9D2449; font-weight: bold;")
            self.scan_button.setEnabled(True)
            
    def connect_device(self):
        if self.device_combo.count() == 0:
            QMessageBox.warning(self, "Error", "Debe escanear y seleccionar un sensor de la lista")
            return
        address = self.device_combo.currentData()
        self.ble_worker = BLEWorker()
        self.ble_worker.data_received.connect(self.on_data_received)
        self.ble_worker.connection_status.connect(self.on_connection_status)
        self.ble_worker.log_message.connect(self.log_message)
        self.ble_worker.loop_ready.connect(lambda: self._do_connect(address))
        self.ble_worker.start()
        
    def _do_connect(self, address):
        if self.ble_worker:
            self.ble_worker.connect(address)
            self.connect_button.setEnabled(False)
            self.status_label.setText("🟡 CONECTANDO...")
            self.status_label.setStyleSheet("color: #B38E5D; font-weight: bold;")
            
    def disconnect_device(self):
        if self.ble_worker:
            self.ble_worker.disconnect()
        QTimer.singleShot(2000, self.cleanup_ble_worker)
        self.is_previewing = False
        self.is_recording = False
        self.is_connected = False
        self.update_ui_state()
        
    def cleanup_ble_worker(self):
        if self.ble_worker:
            self.ble_worker.stop()
            self.ble_worker = None
            
    def on_connection_status(self, success, message):
        if success:
            self.is_connected = True
            self.status_label.setText("🟢 SENSOR VINCULADO")
            self.status_label.setStyleSheet("color: #D4C19C; font-weight: bold;")
            self.disconnect_button.setEnabled(True)
            self.preview_button.setEnabled(True)
            self.log_message("Conexión con el dispositivo establecida", "SUCCESS")
        else:
            self.is_connected = False
            self.status_label.setText(f"🔴 ERROR ENLACE")
            self.status_label.setStyleSheet("color: #9D2449; font-weight: bold;")
            self.connect_button.setEnabled(True)
            self.disconnect_button.setEnabled(False)
            self.log_message(f"Fallo de vinculación: {message}", "ERROR")
        self.update_ui_state()
        
    def on_data_received(self, red_samples, ir_samples, packet_seq):
        if not self.is_previewing:
            return
        
        if self.start_time is None:
            self.start_time = time.time()
            
        packet_time = time.time() - self.start_time
        
        if self.expected_packet_seq == 0:
            self.expected_packet_seq = packet_seq + 1
        elif packet_seq > self.expected_packet_seq:
            lost = packet_seq - self.expected_packet_seq
            self.lost_packets += lost
        elif packet_seq < self.expected_packet_seq:
            self.duplicate_packets += 1
        self.expected_packet_seq = packet_seq + 1
        self.received_packets += 1
        
        loss_rate = (self.lost_packets / max(1, self.received_packets)) * 100
        if loss_rate < 2:
            quality = "Excelente"
            color = "#621132"
        elif loss_rate < 10:
            quality = "Buena"
            color = "#B38E5D"
        else:
            quality = "Mala"
            color = "#9D2449"
        
        self.quality_label.setText(f"📶 Calidad: {quality}")
        self.quality_label.setStyleSheet(f"color: {color};")
        self.loss_label.setText(f"📉 Pérdida: {loss_rate:.1f}%")
        
        num_samples = len(red_samples)
        # Calcular el espaciado de muestras real según la frecuencia de recepción medida
        freq = self.actual_freq if self.actual_freq > 10 else EXPECTED_FREQ
        dt = 1.0 / freq
        
        for idx, (red_raw, ir_raw) in enumerate(zip(red_samples, ir_samples)):
            red_float = float(red_raw)
            ir_float = float(ir_raw)
            
            if self.dc_filter_enabled:
                red_filt = self.filter_red.filter(red_float)
                ir_filt = self.filter_ir.filter(ir_float)
            else:
                red_filt = red_float
                ir_filt = ir_float
                
            self.raw_red_buffer.append(red_float)
            self.raw_ir_buffer.append(ir_float)
            self.filtered_red_buffer.append(red_filt)
            self.filtered_ir_buffer.append(ir_filt)
            
            # Espaciado de muestras basado en la frecuencia de muestreo real medida
            t = packet_time - (num_samples - 1 - idx) * dt
            self.time_buffer.append(t)
            
            if self.is_recording:
                if len(self.recording_time) == 0:
                    self.recording_start_time = t
                rec_t = t - self.recording_start_time
                self.recording_time.append(rec_t)
                self.recording_raw_red.append(red_float)
                self.recording_raw_ir.append(ir_float)
                self.recording_filtered_red.append(red_filt)
                self.recording_filtered_ir.append(ir_filt)
                
                duration = rec_t
                if duration >= self.duration_spin.value():
                    self.stop_recording_auto()
            self.global_sample_index += 1
            
    def calculate_bpm(self):
        """Algoritmo de detección de picos en tiempo real para estimar los BPM usando la señal IR."""
        if len(self.filtered_ir_buffer) < 300 or len(self.time_buffer) < 300:
            return None
            
        y = np.array(self.filtered_ir_buffer)
        
        # Ajustamos min_dist dinámicamente basado en la frecuencia real de llegada
        actual_freq = self.actual_freq if self.actual_freq > 10 else EXPECTED_FREQ
        
        # Suavizado dinámico (filtro paso bajo de 200ms) para eliminar ruido y muesca dicrota
        window_size = max(5, int(actual_freq * 0.20))
        if window_size % 2 == 0:
            window_size += 1  # Asegurar tamaño impar
        smoothed = np.convolve(y, np.ones(window_size)/window_size, mode='same')
        
        min_dist = int(actual_freq * 0.40)  # Mínimo 400ms entre latidos (máximo 150 BPM)
        
        peaks = []
        ymin, ymax = np.min(smoothed), np.max(smoothed)
        yrange = ymax - ymin
        if yrange < 50:
            return None
            
        # Umbral adaptativo a la mitad de la amplitud para evitar picos menores
        threshold = ymin + yrange * 0.50
        
        last_peak_idx = -min_dist
        for i in range(1, len(smoothed) - 1):
            if smoothed[i] > smoothed[i-1] and smoothed[i] > smoothed[i+1]:
                if smoothed[i] > threshold:
                    if (i - last_peak_idx) >= min_dist:
                        peaks.append(i)
                        last_peak_idx = i
                        
        if len(peaks) < 3:  # Requerimos al menos 3 picos (2 intervalos) para estabilidad
            return None
            
        # Calcular BPM basándose en los tiempos reales (usando la MEDIANA para ignorar picos falsos)
        t_arr = np.array(self.time_buffer)
        peak_times = t_arr[peaks]
        
        intervals_sec = np.diff(peak_times)
        
        # Filtrar intervalos físicamente imposibles para humanos en reposo (menor a 0.33s o mayor a 1.5s)
        valid_intervals = intervals_sec[(intervals_sec >= 0.33) & (intervals_sec <= 1.5)]
        
        if len(valid_intervals) < 2:
            return None
            
        median_interval_sec = np.median(valid_intervals)
        
        if median_interval_sec == 0:
            return None
            
        bpm = 60.0 / median_interval_sec
        
        if 40 <= bpm <= 180:
            return bpm
        return None
        
    def estimate_blood_pressure(self, bpm, peaks, smoothed):
        """Estima la presión arterial sistólica y diastólica basándose en la forma de onda PPG (canal IR)."""
        if bpm is None or len(peaks) < 3 or len(self.time_buffer) < max(peaks):
            return None, None
            
        # Encontrar los valles locales (onsets) entre picos consecutivos
        valleys = []
        for idx in range(len(peaks) - 1):
            start = peaks[idx]
            end = peaks[idx + 1]
            if start >= end:
                continue
            valley_idx = start + np.argmin(smoothed[start:end])
            valleys.append(valley_idx)
            
        if len(valleys) < 2:
            return None, None
            
        rise_times_sec = []
        fall_times_sec = []
        t_arr = np.array(self.time_buffer)
        
        for v in valleys:
            post_peaks = [p for p in peaks if p > v]
            if not post_peaks:
                continue
            p = post_peaks[0]
            
            post_valleys = [nv for nv in valleys if nv > p]
            if not post_valleys:
                continue
            nv = post_valleys[0]
            
            # Tiempos en segundos reales (independiente de la tasa de paquetes)
            rise_time_sec = t_arr[p] - t_arr[v]
            fall_time_sec = t_arr[nv] - t_arr[p]
            
            rise_times_sec.append(rise_time_sec)
            fall_times_sec.append(fall_time_sec)
            
        if not rise_times_sec or not fall_times_sec:
            return None, None
            
        avg_rise_sec = np.mean(rise_times_sec)
        avg_fall_sec = np.mean(fall_times_sec)
        
        # Modelo de regresión lineal empírica para la presión arterial con calibración de piel:
        idx = self.skin_combo.currentIndex()
        cal = self.skin_calibration_data[idx]
        sbp = 120.0 + 0.15 * (bpm - 70.0) - 75.0 * (avg_rise_sec - 0.12) + cal["sbp_offset"]
        dbp = 80.0 + 0.08 * (bpm - 70.0) - 25.0 * (avg_fall_sec - 0.35) + cal["dbp_offset"]
        
        # Límites fisiológicos razonables para mantener la estimación estable
        sbp = max(95.0, min(145.0, sbp))
        dbp = max(60.0, min(95.0, dbp))
        
        # Lógica clínica: SBP debe ser mayor que DBP al menos por 25 mmHg
        if sbp <= dbp + 25:
            sbp = dbp + 30
            
        return int(round(sbp)), int(round(dbp))

    def calculate_spo2(self):
        """Calcula el SpO2 (oxígeno en sangre) usando la relación de amplitudes CA/CC de Rojo e Infrarrojo."""
        if len(self.raw_red_buffer) < 300 or len(self.raw_ir_buffer) < 300:
            return None
            
        # Tomar los últimos 300 elementos (unos 3 segundos de datos a 100Hz)
        red_raw = np.array(self.raw_red_buffer)[-300:]
        ir_raw = np.array(self.raw_ir_buffer)[-300:]
        
        red_filt = np.array(self.filtered_red_buffer)[-300:]
        ir_filt = np.array(self.filtered_ir_buffer)[-300:]
        
        # Componentes de corriente continua (DC): la media de la señal cruda
        dc_red = np.mean(red_raw)
        dc_ir = np.mean(ir_raw)
        
        if dc_red == 0 or dc_ir == 0:
            return None
            
        # Componentes de corriente alterna (AC): amplitud pico a pico de la señal filtrada (sin DC)
        window_size = 5
        red_smooth = np.convolve(red_filt, np.ones(window_size)/window_size, mode='same')
        ir_smooth = np.convolve(ir_filt, np.ones(window_size)/window_size, mode='same')
        
        ac_red = np.max(red_smooth) - np.min(red_smooth)
        ac_ir = np.max(ir_smooth) - np.min(ir_smooth)
        
        if ac_ir == 0:
            return None
            
        # Ratio of Ratios
        r = (ac_red / dc_red) / (ac_ir / dc_ir)
        
        # Ecuación empírica estándar del sensor MAX30102 con calibración de piel:
        idx = self.skin_combo.currentIndex()
        cal = self.skin_calibration_data[idx]
        spo2 = 104.0 - 17.0 * r + cal["spo2_offset"]
        
        # Límites fisiológicos razonables para SpO2
        spo2 = max(80.0, min(100.0, spo2))
        return spo2
        
    def stop_recording_auto(self):
        self.is_recording = False
        self.is_previewing = False
        total_samples = len(self.recording_time)
        duration = total_samples * DT_SAMPLE
        loss_rate = (self.lost_packets / max(1, self.received_packets)) * 100
        self.mode_label.setText("⏹️ DETENIDO")
        self.log_message(f"Grabación de estudio completada con {total_samples} muestras", "SUCCESS")
        
        # Custom QMessageBox Style
        box = QMessageBox(self)
        box.setWindowTitle("Estudio Finalizado")
        box.setText("✅ GRABACIÓN COMPLETADA")
        box.setInformativeText(f"📊 Muestras: {total_samples}\n"
                               f"⏱️ Duración: {duration:.2f} s\n"
                               f"📈 Ritmo de muestreo: {total_samples / duration:.1f} Hz\n"
                               f"📉 Paquetes perdidos: {self.lost_packets}\n"
                               f"📉 Tasa de pérdida: {loss_rate:.1f}%")
        box.setStyleSheet("""
            QMessageBox { background-color: #FFFFFF; color: #4E232E; }
            QLabel { color: #4E232E; }
            QPushButton { background-color: #621132; color: #FFFFFF; border: 1px solid #B38E5D; padding: 6px; border-radius: 4px; font-weight: bold; }
        """)
        box.exec()
        
        self.update_ui_state()
        self.save_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        
    def update_frequency(self):
        if not self.is_previewing:
            self.bpm_value_label.setText("--")
            self.heart_label.setStyleSheet("color: #888888;")
            self.bp_value_label.setText("-- / --")
            self.spo2_value_label.setText("--")
            return
            
        current_time = time.time()
        elapsed = current_time - self.last_freq_time
        current_samples = self.global_sample_index
        diff_samples = current_samples - self.last_freq_samples
        if elapsed > 0:
            self.actual_freq = diff_samples / elapsed
        self.last_freq_samples = current_samples
        self.last_freq_time = current_time
        self.freq_label.setText(f"📊 Muestreo: {self.actual_freq:.1f} Hz")
        self.samples_label.setText(f"Muestras recolectadas: {self.global_sample_index}")
        
        # Detector de contacto físico (evita cálculos y reinicia a '--' si el sensor se retira)
        if len(self.raw_ir_buffer) > 0:
            recent_raw_ir = list(self.raw_ir_buffer)[-50:]
            avg_raw_ir = np.mean(recent_raw_ir)
            if avg_raw_ir < 20000: # Umbral de contacto en la muñeca (20,000 unidades)
                self.bpm_value_label.setText("--")
                self.heart_label.setStyleSheet("color: #888888;")
                self.bp_value_label.setText("-- / --")
                self.spo2_value_label.setText("--")
                self.quality_label.setText("📶 Calidad: Sin contacto")
                self.quality_label.setStyleSheet("color: #9D2449;")
                return
        
        bpm = self.calculate_bpm()
        if bpm is not None:
            self.bpm_value_label.setText(f"{int(round(bpm))}")
            self.heart_label.setStyleSheet("color: #9D2449;")
            
            # Estimación de Presión Arterial en tiempo real (basada en el canal IR)
            y = np.array(self.filtered_ir_buffer)
            window_size = 5
            smoothed = np.convolve(y, np.ones(window_size)/window_size, mode='same')
            
            # Detectar picos
            peaks = []
            min_dist = 40
            ymin, ymax = np.min(smoothed), np.max(smoothed)
            yrange = ymax - ymin
            threshold = ymin + yrange * 0.4
            last_peak_idx = -min_dist
            for i in range(1, len(smoothed) - 1):
                if smoothed[i] > smoothed[i-1] and smoothed[i] > smoothed[i+1]:
                    if smoothed[i] > threshold:
                        if (i - last_peak_idx) >= min_dist:
                            peaks.append(i)
                            last_peak_idx = i
            
            sbp, dbp = self.estimate_blood_pressure(bpm, peaks, smoothed)
            if sbp is not None and dbp is not None:
                self.bp_value_label.setText(f"{sbp} / {dbp}")
            else:
                self.bp_value_label.setText("-- / --")
        else:
            self.bpm_value_label.setText("--")
            self.heart_label.setStyleSheet("color: #888888;")
            self.bp_value_label.setText("-- / --")
            
        # Calcular SpO2
        spo2 = self.calculate_spo2()
        if spo2 is not None:
            self.spo2_value_label.setText(f"{int(round(spo2))}")
        else:
            self.spo2_value_label.setText("--")
            
    def toggle_invert(self):
        self.invert_signal = self.invert_checkbox.isChecked()
        estado = "activada" if self.invert_signal else "desactivada"
        self.log_message(f"Inversión de onda visual {estado}", "INFO")
        
    def analyze_skin_photo(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, 
            "Seleccionar Foto de la Piel", 
            "", 
            "Imágenes (*.png *.jpg *.jpeg *.bmp)"
        )
        if not filename:
            return
            
        image = QImage(filename)
        if image.isNull():
            QMessageBox.warning(self, "Error de Lectura", "No se pudo cargar la imagen seleccionada.")
            return
            
        w = image.width()
        h = image.height()
        cx = w // 2
        cy = h // 2
        rx = max(0, cx - 50)
        ry = max(0, cy - 50)
        rw = min(100, w - rx)
        rh = min(100, h - ry)
        
        if rw <= 0 or rh <= 0:
            QMessageBox.warning(self, "Error de Dimensiones", "La resolución de la imagen es demasiado baja.")
            return
            
        sum_r = 0.0
        sum_g = 0.0
        sum_b = 0.0
        pixel_count = 0
        
        for py in range(ry, ry + rh):
            for px in range(rx, rx + rw):
                color_val = image.pixelColor(px, py)
                sum_r += color_val.red()
                sum_g += color_val.green()
                sum_b += color_val.blue()
                pixel_count += 1
                
        avg_r = sum_r / pixel_count
        avg_g = sum_g / pixel_count
        avg_b = sum_b / pixel_count
        
        r_norm = avg_r / 255.0
        g_norm = avg_g / 255.0
        b_norm = avg_b / 255.0
        
        def pivot(v):
            return ((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92
            
        r_p = pivot(r_norm)
        g_p = pivot(g_norm)
        b_p = pivot(b_norm)
        
        x = r_p * 0.4124564 + g_p * 0.3575761 + b_p * 0.1804375
        y = r_p * 0.2126729 + g_p * 0.7151522 + b_p * 0.0721750
        z = r_p * 0.0193339 + g_p * 0.1191920 + b_p * 0.9503041
        
        x /= 0.950489
        y /= 1.000000
        z /= 1.088840
        
        def f(t):
            return t ** (1/3) if t > 0.008856 else 7.787 * t + 16/116
            
        fx = f(x)
        fy = f(y)
        fz = f(z)
        
        l_star = 116 * fy - 16
        b_star = 200 * (fy - fz)
        
        if b_star == 0:
            b_star = 0.001
            
        ita = math.atan((l_star - 50) / b_star) * (180 / math.pi)
        
        if ita > 55:
            idx = 0
            cat = "Muy clara"
        elif ita > 41:
            idx = 1
            cat = "Clara"
        elif ita > 28:
            idx = 2
            cat = "Intermedia"
        elif ita > 10:
            idx = 3
            cat = "Morena"
        elif ita > -30:
            idx = 4
            cat = "Oscura"
        else:
            idx = 5
            cat = "Muy oscura"
            
        self.skin_combo.setCurrentIndex(idx)
        QMessageBox.information(
            self, 
            "Tono de Piel Detectado", 
            f"Análisis completado en la zona central de la foto:\n\n"
            f"• L* (Luminosidad): {l_star:.2f}\n"
            f"• b* (Amarillez): {b_star:.2f}\n"
            f"• Ángulo ITA: {ita:.2f}°\n"
            f"• Categoría: {cat}\n\n"
            f"La calibración para piel '{cat}' ha sido aplicada automáticamente."
        )
        self.log_message(f"Calibración de tono de piel detectada por foto: {cat} (ITA={ita:.1f}°)", "INFO")
        
    def update_plot(self):
        if not self.is_previewing or len(self.time_buffer) == 0:
            return
            
        if self.dc_filter_enabled:
            y_data = np.array(self.filtered_ir_buffer)
            base_title = "REGISTRO DE PULSO - PPG IR FILTRADA (DC Blocker)"
        else:
            y_data = np.array(self.raw_ir_buffer)
            base_title = "REGISTRO DE PULSO - PPG IR CRUDA (18 bits)"
            
        if self.invert_signal:
            y_data = MAX_18BIT - y_data
            title = f"{base_title} - MODO ESPEJO"
        else:
            title = f"{base_title}"
            
        t_data = np.array(self.time_buffer)
        t_max = t_data[-1]
        t_min = max(0, t_max - 5)
        mask = (t_data >= t_min) & (t_data <= t_max)
        
        self.data_line.set_data(t_data[mask], y_data[mask])
        self.ax.set_xlim(t_min, t_max)
        self.ax.set_title(title, color="#621132", fontsize=11, fontweight="bold")
        
        if len(y_data[mask]) > 0:
            ymin = np.min(y_data[mask])
            ymax = np.max(y_data[mask])
            margin = (ymax - ymin) * 0.1 + 1
            self.ax.set_ylim(ymin - margin, ymax + margin)
            
        self.canvas.draw_idle()
        
        if self.is_recording:
            if len(self.recording_time) > 0:
                duration = len(self.recording_time) * DT_SAMPLE
                target = self.duration_spin.value()
                self.progress_bar.setVisible(True)
                self.progress_bar.setMaximum(target)
                self.progress_bar.setValue(int(min(duration, target)))
                self.progress_bar.setFormat(f"GRABANDO ESTUDIO: {duration:.1f}s / {target}s")
                self.mode_label.setText(f"💾 {self.progress_bar.format()}")
                
    def start_preview(self):
        self.is_previewing = True
        self.start_time = None # Reset de reloj de tiempo real
        self.mode_label.setText("📊 MODO: VISTA PREVIA ACTIVA")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.log_message("Visualización de flujo de ondas iniciada", "INFO")
        self.update_ui_state()
        
    def start_recording(self):
        if not self.is_previewing:
            QMessageBox.warning(self, "Error", "Debe iniciar primero la vista previa para registrar datos")
            return
            
        self.recording_time.clear()
        self.recording_raw.clear()
        self.recording_filtered.clear()
        self.recording_start_index = self.global_sample_index
        self.is_recording = True
        self.mode_label.setText(f"💾 GRABANDO - {self.duration_spin.value()}s")
        self.start_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.log_message(f"Grabación de estudio iniciada - Duración: {self.duration_spin.value()}s", "INFO")
        
    def stop_all(self):
        self.is_previewing = False
        self.is_recording = False
        self.mode_label.setText("⏹️ MODO: DETENIDO")
        self.progress_bar.setVisible(False)
        self.save_button.setEnabled(len(self.recording_time) > 0)
        self.update_ui_state()
        self.log_message("Registro y captura de datos detenidos por el usuario", "INFO")
        
    def reset_all(self):
        self.stop_all()
        self.time_buffer.clear()
        self.raw_red_buffer.clear()
        self.raw_ir_buffer.clear()
        self.filtered_red_buffer.clear()
        self.filtered_ir_buffer.clear()
        self.recording_time.clear()
        self.recording_raw_red.clear()
        self.recording_raw_ir.clear()
        self.recording_filtered_red.clear()
        self.recording_filtered_ir.clear()
        self.global_sample_index = 0
        self.expected_packet_seq = 0
        self.received_packets = 0
        self.lost_packets = 0
        self.duplicate_packets = 0
        self.start_time = None
        self.recording_start_time = 0.0
        self.filter_red.reset()
        self.filter_ir.reset()
        self.data_line.set_data([], [])
        self.canvas.draw_idle()
        self.save_button.setEnabled(False)
        self.mode_label.setText("⚪ MODO: REINICIADO")
        self.log_message("Todos los buffers del osciloscopio y variables han sido reiniciados", "INFO")
        
    def toggle_filter(self):
        self.dc_filter_enabled = self.filter_checkbox.isChecked()
        self.filter_red.reset()
        self.filter_ir.reset()
        status = "activado" if self.dc_filter_enabled else "desactivado"
        self.log_message(f"Filtro pasabanda Butterworth 0.5-8Hz {status}", "INFO")
        
    def update_ui_state(self):
        self.start_button.setEnabled(self.is_previewing and not self.is_recording)
        self.preview_button.setEnabled(self.is_connected and not self.is_previewing)
        self.stop_button.setEnabled(self.is_previewing)
        self.connect_button.setEnabled(not self.is_connected)
        self.disconnect_button.setEnabled(self.is_connected)
        
    def save_csv(self):
        if len(self.recording_time) == 0:
            QMessageBox.warning(self, "Error", "No existen registros en memoria para exportar")
            return
            
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Exportación de Datos")
        msg_box.setText("¿Qué tipo de señal filtrada (DC Blocker) deseas exportar?")
        msg_box.setInformativeText("• Señal cruda: valores leídos del sensor (rango 0-262143)\n"
                                   "• Señal invertida: valores procesados en espejo positivo (262143 - valor)\n\n"
                                   "Nota: Los valores sin filtrar siempre se exportarán sin alteración.")
        msg_box.setStyleSheet("""
            QMessageBox { background-color: #FFFFFF; color: #4E232E; }
            QLabel { color: #4E232E; }
            QPushButton { background-color: #621132; color: #FFFFFF; border: 1px solid #B38E5D; padding: 6px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #9D2449; color: #FFFFFF; }
        """)
        
        btn_cruda = msg_box.addButton("Señal cruda", QMessageBox.ButtonRole.YesRole)
        btn_invertida = msg_box.addButton("Señal invertida", QMessageBox.ButtonRole.NoRole)
        msg_box.setDefaultButton(btn_cruda)
        msg_box.exec()
        
        if msg_box.clickedButton() == btn_cruda:
            invert_save = False
            tipo = "CRUDA"
        elif msg_box.clickedButton() == btn_invertida:
            invert_save = True
            tipo = "INVERTIDA (espejo positivo)"
        else:
            return
            
        filename, _ = QFileDialog.getSaveFileName(self, "Guardar Archivo CSV", f"tensiometro_{time.strftime('%Y%m%d_%H%M%S')}.csv", "Archivos CSV (*.csv)")
        if not filename:
            return
            
        try:
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Tiempo(s)", "PPG_Rojo_Cruda", "PPG_IR_Cruda", "PPG_Rojo_Filtrada", "PPG_IR_Filtrada", "Notas"])
                writer.writerow(["", "Rango: 0-262143", "Rango: 0-262143", "Filtro: Butterworth 0.5-8.0 Hz", "Filtro: Butterworth 0.5-8.0 Hz", f"Señal guardada: {tipo}"])
                for t, rr, ri, fr, fi in zip(self.recording_time, self.recording_raw_red, self.recording_raw_ir, self.recording_filtered_red, self.recording_filtered_ir):
                    if invert_save:
                        fr_guardado = MAX_18BIT - fr
                        fi_guardado = MAX_18BIT - fi
                    else:
                        fr_guardado = fr
                        fi_guardado = fi
                    writer.writerow([f"{t:.6f}", f"{int(rr)}", f"{int(ri)}", f"{int(fr_guardado)}", f"{int(fi_guardado)}", ""])
                    
            duration = len(self.recording_time) * DT_SAMPLE
            loss_rate = (self.lost_packets / max(1, self.received_packets)) * 100
            
            box = QMessageBox(self)
            box.setWindowTitle("Exportación Exitosa")
            box.setText("✅ EXPORTACIÓN COMPLETADA")
            box.setInformativeText(f"Archivo guardado:\n{filename}\n\n"
                                   f"📊 Muestras: {len(self.recording_time)}\n"
                                   f"⏱️ Duración: {duration:.2f} s\n"
                                   f"📉 Pérdida: {loss_rate:.1f}%\n"
                                   f"🔄 Señal guardada: {tipo}")
            box.setStyleSheet("""
                QMessageBox { background-color: #FFFFFF; color: #4E232E; }
                QLabel { color: #4E232E; }
                QPushButton { background-color: #621132; color: #FFFFFF; border: 1px solid #B38E5D; padding: 6px; border-radius: 4px; font-weight: bold; }
            """)
            box.exec()
            self.log_message(f"Estudio CSV exportado ({tipo}): {filename}", "SUCCESS")
        except Exception as e:
            QMessageBox.critical(self, "Error de Exportación", f"No se pudo guardar el archivo:\n{str(e)}")
            self.log_message(f"Error exportando CSV: {str(e)}", "ERROR")
            
    def log_message(self, msg, level):
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {msg}")
        
    def closeEvent(self, event):
        if self.ble_worker:
            self.ble_worker.stop()
            self.ble_worker.wait(2000)
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    window = MainWindow()
    window.showMaximized()
    with loop:
        loop.run_forever()
