"""
===================================================================================
ESTACIÓN MÉDICA DE DIAGNÓSTICO CARDIOVASCULAR - VERSIÓN 2.0
Tensiómetro Digital de Arteria Radial con Cancelación de Movimiento (NLMS),
Monitoreo de Batería, Calibración Clínica Individual y Tono de Piel Automático
===================================================================================
"""

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
                             QProgressBar, QGroupBox, QGridLayout, QFrame,
                             QDialog, QLineEdit, QDoubleSpinBox)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QImage
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from qasync import QEventLoop

# UUIDs de Servicios y Características Bluetooth Low Energy
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"        # Telemetría (14 o 20 bytes)
CONTROL_UUID = "12345678-1234-1234-1234-123456789abc"     # Control START/STOP
BATTERY_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb" # Característica Batería (mV, %, flags)

DEVICE_NAME_PREFIX = "Tensiometro_"
EXPECTED_FREQ = 100
DT_SAMPLE = 1.0 / EXPECTED_FREQ
MAX_18BIT = 262143

# =============================================================================
# 1. FILTRO DIGITAL IIR BUTTERWORTH (PASABANDA 0.5 - 8.0 Hz, SOS BIQUAD)
# =============================================================================
class ButterworthFilter:
    """Filtro IIR Butterworth pasabanda de orden 4 (0.5 - 8.0 Hz a fs=100Hz) usando secciones de segundo orden (SOS)."""
    def __init__(self):
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

# =============================================================================
# 2. FILTRO ADAPTATIVO NLMS (CANCELACIÓN DE ARTEFACTOS DE MOVIMIENTO)
# =============================================================================
class NLMSFilter:
    """Filtro Adaptativo NLMS (Normalized Least Mean Squares)
       Limpia el canal PPG tomando como referencia la aceleración física del MPU6050.
       num_taps: 24 coeficientes
       mu: factor de aprendizaje (0.02 en [0.005, 0.05])
       epsilon: constante de estabilización energética
    """
    def __init__(self, num_taps=24, mu=0.02, epsilon=1e-5):
        self.num_taps = num_taps
        self.mu = mu
        self.epsilon = epsilon
        self.weights = np.zeros(num_taps)
        self.buffer = np.zeros(num_taps)
        self.lock = Lock()
        
    def filter(self, desired, noise_ref, adapt=True):
        with self.lock:
            # Desplazar buffer de ruido con la nueva muestra
            self.buffer[1:] = self.buffer[:-1]
            self.buffer[0] = noise_ref
            
            # Estimación del ruido acoplado en la onda PPG
            noise_est = float(np.dot(self.weights, self.buffer))
            clean_signal = desired - noise_est
            
            # Actualización de pesos normalizada por la potencia del ruido
            if adapt:
                power = float(np.dot(self.buffer, self.buffer)) + self.epsilon
                step = (self.mu / power) * clean_signal
                self.weights += step * self.buffer
                
            return clean_signal
            
    def reset(self):
        with self.lock:
            self.weights = np.zeros(self.num_taps)
            self.buffer = np.zeros(self.num_taps)

# =============================================================================
# 3. TRABAJADOR ASÍNCRONO BLE (RECEPCIÓN DUAL 14/20 BYTES Y BATERÍA)
# =============================================================================
class BLEWorker(QThread):
    """Maneja la conexión Bluetooth Low Energy de forma asíncrona y segura."""
    # Señales: (red_samples, ir_samples, seq, accel_xyz_g)
    data_received = pyqtSignal(list, list, int, list)
    battery_received = pyqtSignal(int, int, int) # pct, mv, flags
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
            while self.should_reconnect and self.running:
                await self._connect()
                while self.connected and self.should_reconnect:
                    await asyncio.sleep(1)
                    if not (self.client and self.client.is_connected):
                        self.connected = False
                        self.log_message.emit("⚠️ Conexión perdida - Reconectando automáticamente...", "WARNING")
                        break
                else:
                    if not self.should_reconnect:
                        break
                    self.reconnect_attempts += 1
                    if self.reconnect_attempts > 10:
                        self.log_message.emit("❌ Límite de reconexiones alcanzado", "ERROR")
                        self.connection_status.emit(False, "No se pudo restablecer el enlace")
                        return
                    await asyncio.sleep(2)
        except Exception as e:
            self.log_message.emit(f"Error en ciclo BLE: {str(e)}", "ERROR")
            await asyncio.sleep(2)
    
    async def _connect(self):
        try:
            if self.client and self.client.is_connected:
                await self._cleanup_client()
            self.client = BleakClient(self.address)
            await self.client.connect()
            
            # Iniciar notificaciones de telemetría (14 o 20 bytes)
            await self.client.start_notify(CHAR_UUID, self._notification_handler)
            
            # Suscribirse a la característica de batería si está disponible
            try:
                await self.client.start_notify(BATTERY_CHAR_UUID, self._battery_handler)
            except Exception:
                pass
                
            # Enviar comando START para que el firmware comience el muestreo
            await self.client.write_gatt_char(CONTROL_UUID, b"START", response=False)
            
            self.connected = True
            self.reconnect_attempts = 0
            self.connection_status.emit(True, f"Conectado a {self.address}")
            self.log_message.emit("✅ Enlace BLE establecido y adquisición iniciada", "SUCCESS")
        except Exception as e:
            self.connection_status.emit(False, str(e))
            self.log_message.emit(f"❌ Error de enlace BLE: {str(e)}", "ERROR")
    
    async def _cleanup_client(self):
        if self.client:
            try:
                await self.client.stop_notify(CHAR_UUID)
            except Exception:
                pass
            try:
                await self.client.stop_notify(BATTERY_CHAR_UUID)
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
            self.log_message.emit("🔴 Enlace cerrado por el usuario", "INFO")
        except Exception:
            pass
    
    def _notification_handler(self, sender, data):
        try:
            length = len(data)
            if length in (14, 20):
                red_samples = []
                ir_samples = []
                for i in range(2):
                    offset = i * 6
                    red_val = (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]
                    ir_val = (data[offset + 3] << 16) | (data[offset + 4] << 8) | data[offset + 5]
                    red_samples.append(red_val)
                    ir_samples.append(ir_val)
                packet_seq = (data[13] << 8) | data[12]
                
                # Decodificar aceleración triaxial (si es trama extendida de 20 bytes)
                if length == 20:
                    ax_raw = int.from_bytes(data[14:16], byteorder='little', signed=True)
                    ay_raw = int.from_bytes(data[16:18], byteorder='little', signed=True)
                    az_raw = int.from_bytes(data[18:20], byteorder='little', signed=True)
                    # Escala ±2g (1g = 16384 LSB)
                    ax_g = ax_raw / 16384.0
                    ay_g = ay_raw / 16384.0
                    az_g = az_raw / 16384.0
                else:
                    ax_g, ay_g, az_g = 0.0, 0.0, 1.0 # Nominal 1g en reposo para v1
                    
                self.data_received.emit(red_samples, ir_samples, packet_seq, [ax_g, ay_g, az_g])
            else:
                self.log_message.emit(f"⚠️ Paquete anómalo: {length} bytes", "WARNING")
        except Exception as e:
            self.log_message.emit(f"Error procesando trama BLE: {str(e)}", "ERROR")
            
    def _battery_handler(self, sender, data):
        try:
            if len(data) >= 4:
                pct = data[0]
                mv = data[1] | (data[2] << 8)
                flags = data[3]
                self.battery_received.emit(pct, mv, flags)
            elif len(data) == 1:
                self.battery_received.emit(data[0], int(3400 + data[0] * 8), 0)
        except Exception as e:
            self.log_message.emit(f"Error procesando batería: {str(e)}", "ERROR")

# =============================================================================
# 4. DIÁLOGO DE CALIBRACIÓN CLÍNICA (3 MEDICIONES CON ESFIGMOMANÓMETRO)
# =============================================================================
class ClinicalCalibrationDialog(QDialog):
    """Permite registrar 3 lecturas previas de esfigmomanómetro para calibrar el modelo PWA."""
    def __init__(self, parent=None, subject_id="SUJETO-01", sbp_ref=120.0, dbp_ref=80.0):
        super().__init__(parent)
        self.setWindowTitle("Calibración Clínica Individual (Esfigmomanómetro)")
        self.resize(420, 320)
        self.setStyleSheet("""
            QDialog { background-color: #FFFFFF; color: #4E232E; }
            QLabel { color: #4E232E; font-size: 11px; }
            QLineEdit, QDoubleSpinBox {
                background-color: #F8FAFC; border: 1px solid #B38E5D;
                padding: 4px; border-radius: 4px; font-weight: bold;
            }
            QPushButton {
                background-color: #621132; color: #FFFFFF; font-weight: bold;
                border: 1px solid #B38E5D; padding: 6px 12px; border-radius: 4px;
            }
            QPushButton:hover { background-color: #9D2449; }
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        
        lbl_info = QLabel("<b>Ingrese 3 mediciones previas tomadas con esfigmomanómetro:</b><br>"
                          "El sistema calculará el promedio de referencia para eliminar el truncamiento rígido "
                          "y personalizar las constantes vasculares del paciente.")
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)
        
        form_grid = QGridLayout()
        form_grid.addWidget(QLabel("ID del Sujeto:"), 0, 0)
        self.txt_id = QLineEdit(subject_id)
        form_grid.addWidget(self.txt_id, 0, 1, 1, 2)
        
        form_grid.addWidget(QLabel("Medición 1 (Sist / Diast):"), 1, 0)
        self.sbp1 = QDoubleSpinBox(); self.sbp1.setRange(70, 220); self.sbp1.setValue(sbp_ref)
        self.dbp1 = QDoubleSpinBox(); self.dbp1.setRange(40, 140); self.dbp1.setValue(dbp_ref)
        form_grid.addWidget(self.sbp1, 1, 1)
        form_grid.addWidget(self.dbp1, 1, 2)
        
        form_grid.addWidget(QLabel("Medición 2 (Sist / Diast):"), 2, 0)
        self.sbp2 = QDoubleSpinBox(); self.sbp2.setRange(70, 220); self.sbp2.setValue(sbp_ref)
        self.dbp2 = QDoubleSpinBox(); self.dbp2.setRange(40, 140); self.dbp2.setValue(dbp_ref)
        form_grid.addWidget(self.sbp2, 2, 1)
        form_grid.addWidget(self.dbp2, 2, 2)
        
        form_grid.addWidget(QLabel("Medición 3 (Sist / Diast):"), 3, 0)
        self.sbp3 = QDoubleSpinBox(); self.sbp3.setRange(70, 220); self.sbp3.setValue(sbp_ref)
        self.dbp3 = QDoubleSpinBox(); self.dbp3.setRange(40, 140); self.dbp3.setValue(dbp_ref)
        form_grid.addWidget(self.sbp3, 3, 1)
        form_grid.addWidget(self.dbp3, 3, 2)
        
        layout.addLayout(form_grid)
        
        btn_box = QHBoxLayout()
        self.btn_save = QPushButton("💾 APLICAR CALIBRACIÓN")
        self.btn_cancel = QPushButton("CANCELAR")
        self.btn_save.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_save)
        btn_box.addWidget(self.btn_cancel)
        layout.addLayout(btn_box)
        
    def get_calibration(self):
        sbp_avg = (self.sbp1.value() + self.sbp2.value() + self.sbp3.value()) / 3.0
        dbp_avg = (self.dbp1.value() + self.dbp2.value() + self.dbp3.value()) / 3.0
        return self.txt_id.text().strip(), sbp_avg, dbp_avg

# =============================================================================
# 5. VENTANA PRINCIPAL (ESTACIÓN CLÍNICA HUD v2.0)
# =============================================================================
class MainWindow(QMainWindow):
    """Interfaz gráfica HUD institucional con filtrado adaptativo NLMS y monitoreo de batería."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESTACIÓN DE MONITOREO CARDIACO - GOBIERNO DE MÉXICO (v2.0)")
        self.resize(1450, 930)
        self.ble_worker = None
        self.is_connected = False
        self.is_previewing = False
        self.is_recording = False
        self.global_sample_index = 0
        self.actual_freq = 0
        self.last_freq_time = time.time()
        self.last_freq_samples = 0
        
        # Filtros Digitales y Adaptativos
        self.dc_filter_enabled = True # ACTIVO POR DEFECTO SEGÚN REQUERIMIENTO
        self.filter_red = ButterworthFilter()
        self.filter_ir = ButterworthFilter()
        self.filter_accel = ButterworthFilter()
        self.nlms_red = NLMSFilter(num_taps=24, mu=0.02)
        self.nlms_ir = NLMSFilter(num_taps=24, mu=0.02)
        
        # Acelerometría y Máquina de Estados de Movimiento
        self.latest_accel = [0.0, 0.0, 1.0]
        self.latest_accel_mag = 0.0
        self.motion_state = "REPOSO" # "REPOSO", "MOVIMIENTO_LEVE", "MOVIMIENTO_FUERTE"
        
        # Gestión y Monitoreo de Batería con Histéresis
        self.battery_pct = 100
        self.battery_mv = 4200
        self.battery_low = False
        
        # Tono de piel automático por DCraw
        self.auto_skin_enabled = True
        self.dc_red_avg = 100000.0
        self.dc_ir_avg = 100000.0
        self.stable_contact_counter = 0
        
        # Parámetros de Calibración de Piel
        self.skin_calibration_data = [
            {"name": "Muy clara", "spo2_offset": 0.0, "sbp_offset": 0.0, "dbp_offset": 0.0},
            {"name": "Clara", "spo2_offset": 0.0, "sbp_offset": 0.0, "dbp_offset": 0.0},
            {"name": "Intermedia", "spo2_offset": 0.2, "sbp_offset": -0.5, "dbp_offset": -0.2},
            {"name": "Morena", "spo2_offset": 0.6, "sbp_offset": -1.0, "dbp_offset": -0.5},
            {"name": "Oscura", "spo2_offset": 1.2, "sbp_offset": -2.0, "dbp_offset": -1.0},
            {"name": "Muy oscura", "spo2_offset": 2.0, "sbp_offset": -3.5, "dbp_offset": -1.8}
        ]
        
        # Calibración Clínica Individual
        self.subject_id = "SUJETO-01"
        self.sbp_reference = 120.0
        self.dbp_reference = 80.0
        self.is_clinically_calibrated = False
        self.calib_sbp_offset = 0.0
        self.calib_dbp_offset = 0.0
        self.last_valid_sbp = None
        self.last_valid_dbp = None
        
        self.recording_start_index = 0
        self.invert_signal = False
        self.expected_packet_seq = 0
        self.received_packets = 0
        self.lost_packets = 0
        self.duplicate_packets = 0
        self.start_time = None
        self.recording_start_time = 0.0
        
        # Búferes circulares para visualización dinámica
        self.time_buffer = deque(maxlen=800)
        self.raw_red_buffer = deque(maxlen=800)
        self.raw_ir_buffer = deque(maxlen=800)
        self.filtered_red_buffer = deque(maxlen=800)
        self.filtered_ir_buffer = deque(maxlen=800)
        self.nlms_ir_buffer = deque(maxlen=800)
        self.accel_mag_buffer = deque(maxlen=800)
        
        # Búferes completos para exportación CSV (N=30)
        self.recording_time = []
        self.recording_raw_red = []
        self.recording_raw_ir = []
        self.recording_filtered_red = []
        self.recording_filtered_ir = []
        self.recording_nlms_red = []
        self.recording_nlms_ir = []
        self.recording_accel_x = []
        self.recording_accel_y = []
        self.recording_accel_z = []
        self.recording_accel_mag = []
        self.recording_motion_state = []
        self.recording_battery_pct = []
        self.recording_bpm = []
        self.recording_spo2 = []
        self.recording_sbp = []
        self.recording_dbp = []
        
        self.setup_ui()
        self.setup_timers()
    
    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        
        central.setStyleSheet("""
            QWidget {
                background-color: #D4C19C;
                color: #4E232E;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            #Header {
                background-color: #621132;
                border-bottom: 2px solid #B38E5D;
            }
            #Header QLabel {
                color: #D4C19C;
                background-color: #621132;
            }
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
                font-size: 78px;
                font-weight: bold;
            }
            #VitalsUnit {
                color: #B38E5D;
                font-size: 10px;
                font-weight: bold;
            }
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
            QPushButton {
                background-color: #621132;
                color: #FFFFFF;
                border: 1px solid #B38E5D;
                padding: 7px 10px;
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
            #AlertBanner {
                background-color: #F8FAFC;
                border: 1px solid #B38E5D;
                border-radius: 4px;
                padding: 4px;
                font-weight: bold;
                font-size: 11px;
            }
        """)
        
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)
        
        # 1. ENCABEZADO CON TELEMETRÍA DE ENERGÍA Y ESTADO
        header = QWidget()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        
        lbl_logo = QLabel("🇲🇽 GOBIERNO DE MÉXICO")
        lbl_logo.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        lbl_title = QLabel("|   ESTACIÓN DE DIAGNÓSTICO CARDIOVASCULAR v2.0")
        lbl_title.setFont(QFont("Arial", 10))
        lbl_title.setStyleSheet("color: #D4C19C;")
        
        self.battery_label = QLabel("🔋 BATERÍA: --% (0.00 V)")
        self.battery_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.battery_label.setStyleSheet("color: #D4C19C;")
        
        self.status_label = QLabel("⚫ SENSOR: DESCONECTADO")
        self.status_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.status_label.setStyleSheet("color: #D4C19C;")
        
        header_layout.addWidget(lbl_logo)
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()
        header_layout.addWidget(self.battery_label)
        header_layout.addWidget(QLabel("   "))
        header_layout.addWidget(self.status_label)
        main_layout.addWidget(header)
        
        # Banner de alerta jerarquizada (Batería baja / Movimiento / Contacto)
        self.alert_banner = QLabel("🟢 SISTEMA EN ESPERA - VINCULE EL SENSOR")
        self.alert_banner.setObjectName("AlertBanner")
        self.alert_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.alert_banner)
        
        # 2. FILA CENTRAL: Osciloscopio (70%) | Monitor de Signos (30%)
        fila_central = QHBoxLayout()
        fila_central.setSpacing(12)
        
        # A) Gráfica Matplotlib con trazo dinámico
        self.figure = Figure(figsize=(9, 4.8), facecolor="#D4C19C")
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor("#FFFFFF")
        self.ax.grid(True, alpha=0.3, color="#B38E5D")
        self.ax.set_xlabel("Tiempo (segundos)", color="#621132", fontsize=9)
        self.ax.set_ylabel("Amplitud PPG", color="#621132", fontsize=9)
        self.ax.tick_params(colors="#621132", labelsize=8)
        self.data_line, = self.ax.plot([], [], linewidth=2.2, color="#9D2449", label="PPG IR (Limpia NLMS)")
        
        # B) Panel de Signos Vitales
        vitals_panel = QWidget()
        vitals_panel.setObjectName("VitalsPanel")
        vitals_layout = QVBoxLayout(vitals_panel)
        vitals_layout.setContentsMargins(15, 12, 15, 12)
        vitals_layout.setSpacing(6)
        
        lbl_bpm_title = QLabel("RITMO CARDÍACO")
        lbl_bpm_title.setObjectName("VitalsTitle")
        lbl_bpm_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bpm_value_label = QLabel("--")
        self.bpm_value_label.setObjectName("VitalsValue")
        self.bpm_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_bpm_unit = QLabel("LATIDOS POR MINUTO (LPM)")
        lbl_bpm_unit.setObjectName("VitalsUnit")
        lbl_bpm_unit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        div_bp = QFrame(); div_bp.setFrameShape(QFrame.Shape.HLine); div_bp.setStyleSheet("background-color: #B38E5D; max-height: 1px;")
        lbl_bp_title = QLabel("PRESIÓN ARTERIAL ESTIMADA")
        lbl_bp_title.setObjectName("VitalsTitle")
        lbl_bp_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bp_value_label = QLabel("-- / --")
        self.bp_value_label.setFont(QFont("Arial", 26, QFont.Weight.Bold))
        self.bp_value_label.setStyleSheet("color: #621132; background-color: #FFFFFF;")
        self.bp_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_bp_unit = QLabel("mmHg (SISTÓLICA / DIASTÓLICA)")
        lbl_bp_unit.setObjectName("VitalsUnit")
        lbl_bp_unit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        div_spo2 = QFrame(); div_spo2.setFrameShape(QFrame.Shape.HLine); div_spo2.setStyleSheet("background-color: #B38E5D; max-height: 1px;")
        lbl_spo2_title = QLabel("OXÍGENO EN SANGRE")
        lbl_spo2_title.setObjectName("VitalsTitle")
        lbl_spo2_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spo2_value_label = QLabel("--")
        self.spo2_value_label.setFont(QFont("Arial", 26, QFont.Weight.Bold))
        self.spo2_value_label.setStyleSheet("color: #9D2449; background-color: #FFFFFF;")
        self.spo2_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_spo2_unit = QLabel("% SpO2 (SATURACIÓN)")
        lbl_spo2_unit.setObjectName("VitalsUnit")
        lbl_spo2_unit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.heart_label = QLabel("❤️")
        self.heart_label.setFont(QFont("Arial", 26))
        self.heart_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.heart_label.setStyleSheet("color: #888888;")
        
        div_inf = QFrame(); div_inf.setFrameShape(QFrame.Shape.HLine); div_inf.setStyleSheet("background-color: #B38E5D; max-height: 1px;")
        self.motion_label = QLabel("🏃 Movimiento: Reposo")
        self.motion_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.quality_label = QLabel("📶 Calidad: --")
        self.quality_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        vitals_layout.addWidget(div_inf)
        vitals_layout.addWidget(self.motion_label)
        vitals_layout.addWidget(self.quality_label)
        vitals_layout.addWidget(self.freq_label)
        vitals_layout.addStretch()
        
        fila_central.addWidget(vitals_panel, stretch=3)
        fila_central.addWidget(self.canvas, stretch=7)
        main_layout.addLayout(fila_central)
        
        # Barra de progreso
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(16)
        main_layout.addWidget(self.progress_bar)
        
        # 3. CONSOLA INFERIOR DE CONTROL
        console_panel = QWidget()
        console_panel.setObjectName("ConsolePanel")
        console_layout = QHBoxLayout(console_panel)
        console_layout.setContentsMargins(12, 8, 12, 10)
        console_layout.setSpacing(15)
        
        # Grupo 1: Enlace BLE
        grp_ble = QGroupBox("🔗 COMUNICACIÓN BLE")
        lay_ble = QGridLayout(grp_ble)
        lay_ble.setSpacing(6)
        self.device_combo = QComboBox()
        self.scan_button = QPushButton("🔍 ESCANEAR")
        self.connect_button = QPushButton("🔌 VINCULAR")
        self.disconnect_button = QPushButton("❌ SOLTAR")
        self.disconnect_button.setEnabled(False)
        lay_ble.addWidget(QLabel("Dispositivo:"), 0, 0)
        lay_ble.addWidget(self.device_combo, 0, 1, 1, 2)
        lay_ble.addWidget(self.scan_button, 1, 0)
        lay_ble.addWidget(self.connect_button, 1, 1)
        lay_ble.addWidget(self.disconnect_button, 1, 2)
        console_layout.addWidget(grp_ble, stretch=4)
        
        # Grupo 2: Parámetros del Canal y Calibración
        grp_params = QGroupBox("🔧 DSP & CALIBRACIÓN")
        lay_params = QVBoxLayout(grp_params)
        lay_params.setSpacing(4)
        
        # Checkbox Butterworth ACTIVO POR DEFECTO
        self.filter_checkbox = QCheckBox("Filtro Butterworth (0.5-8Hz)")
        self.filter_checkbox.setChecked(True)
        self.nlms_checkbox = QCheckBox("Cancelación NLMS de Movimiento")
        self.nlms_checkbox.setChecked(True)
        self.invert_checkbox = QCheckBox("Invertir Espejo PPG")
        
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
        self.skin_combo.setCurrentIndex(2)
        self.auto_skin_checkbox = QCheckBox("Auto DCraw")
        self.auto_skin_checkbox.setChecked(True)
        self.skin_photo_btn = QPushButton("📷 Foto")
        self.skin_photo_btn.setStyleSheet("max-width: 50px;")
        
        skin_layout.addWidget(QLabel("Piel:"))
        skin_layout.addWidget(self.skin_combo)
        skin_layout.addWidget(self.auto_skin_checkbox)
        skin_layout.addWidget(self.skin_photo_btn)
        
        lay_params.addWidget(self.filter_checkbox)
        lay_params.addWidget(self.nlms_checkbox)
        lay_params.addWidget(self.invert_checkbox)
        lay_params.addLayout(skin_layout)
        console_layout.addWidget(grp_params, stretch=4)
        
        # Grupo 3: Comandos de Diagnóstico y Validación
        grp_actions = QGroupBox("🎮 COMANDOS DE DIAGNÓSTICO")
        lay_actions = QGridLayout(grp_actions)
        lay_actions.setSpacing(6)
        
        self.preview_button = QPushButton("📊 VISTA PREVIA")
        self.preview_button.setEnabled(False)
        self.start_button = QPushButton("💾 GRABAR")
        self.start_button.setEnabled(False)
        self.stop_button = QPushButton("⏹️ DETENER")
        self.stop_button.setEnabled(False)
        self.calib_button = QPushButton("⚖️ CALIBRACIÓN CLÍNICA")
        self.save_button = QPushButton("💿 EXPORTAR CSV")
        self.save_button.setEnabled(False)
        self.reset_button = QPushButton("🔄 REINICIAR")
        
        duration_h = QHBoxLayout()
        duration_h.addWidget(QLabel("Duración:"))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 300)
        self.duration_spin.setValue(10)
        self.duration_spin.setSuffix(" seg")
        duration_h.addWidget(self.duration_spin)
        
        lay_actions.addWidget(self.preview_button, 0, 0)
        lay_actions.addWidget(self.start_button, 0, 1)
        lay_actions.addWidget(self.stop_button, 0, 2)
        lay_actions.addWidget(self.calib_button, 1, 0)
        lay_actions.addWidget(self.save_button, 1, 1)
        lay_actions.addWidget(self.reset_button, 1, 2)
        lay_actions.addLayout(duration_h, 2, 0, 1, 3)
        console_layout.addWidget(grp_actions, stretch=4)
        
        main_layout.addWidget(console_panel)
        
        # Pie de página con contador
        self.samples_label = QLabel("Muestras recolectadas: 0  |  Sujeto: PAC-001  |  Calibración: ESTÁNDAR (PWA base)")
        self.samples_label.setFont(QFont("Arial", 8))
        self.samples_label.setStyleSheet("color: #4E232E;")
        main_layout.addWidget(self.samples_label)
        
        # Conexiones de Eventos
        self.scan_button.clicked.connect(self.scan_devices)
        self.connect_button.clicked.connect(self.connect_device)
        self.disconnect_button.clicked.connect(self.disconnect_device)
        self.preview_button.clicked.connect(self.start_preview)
        self.start_button.clicked.connect(self.start_recording) # ERROR CORREGIDO
        self.stop_button.clicked.connect(self.stop_all)
        self.reset_button.clicked.connect(self.reset_all)
        self.save_button.clicked.connect(self.save_csv)
        self.calib_button.clicked.connect(self.open_clinical_calibration)
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
        self.ble_worker.battery_received.connect(self.on_battery_received)
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
        QTimer.singleShot(1500, self.cleanup_ble_worker)
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
            self.log_message("Enlace con la pulsera establecido exitosamente", "SUCCESS")
        else:
            self.is_connected = False
            self.status_label.setText("🔴 ENLACE SUSPENDIDO")
            self.status_label.setStyleSheet("color: #9D2449; font-weight: bold;")
            self.connect_button.setEnabled(True)
            self.disconnect_button.setEnabled(False)
        self.update_ui_state()
        
    def on_battery_received(self, pct, mv, flags):
        self.battery_pct = pct
        self.battery_mv = mv
        voltage = mv / 1000.0
        self.battery_label.setText(f"🔋 BATERÍA: {pct}% ({voltage:.2f} V)")
        
        # Histéresis de batería baja: Activa si < 3.4V, desactiva solo si >= 3.5V
        if voltage < 3.40:
            self.battery_low = True
            self.battery_label.setStyleSheet("color: #FF5555; font-weight: bold;")
        elif voltage >= 3.50:
            self.battery_low = False
            self.battery_label.setStyleSheet("color: #D4C19C; font-weight: bold;")
            
    def open_clinical_calibration(self):
        dlg = ClinicalCalibrationDialog(self, self.subject_id, self.sbp_reference, self.dbp_reference)
        if dlg.exec():
            sub_id, sbp_avg, dbp_avg = dlg.get_calibration()
            self.subject_id = sub_id
            self.sbp_reference = sbp_avg
            self.dbp_reference = dbp_avg
            self.is_clinically_calibrated = True
            
            # Offset individual respecto al baseline nominal (120/80)
            self.calib_sbp_offset = sbp_avg - 120.0
            self.calib_dbp_offset = dbp_avg - 80.0
            
            self.samples_label.setText(
                f"Sujeto: {self.subject_id}  |  Ref SBP: {self.sbp_reference:.1f} mmHg, DBP: {self.dbp_reference:.1f} mmHg  |  "
                f"Calibración: PERSONALIZADA (Offsets: SBP={self.calib_sbp_offset:+.1f}, DBP={self.calib_dbp_offset:+.1f})"
            )
            self.log_message(f"Calibración clínica actualizada para {self.subject_id}: {sbp_avg:.1f}/{dbp_avg:.1f} mmHg", "SUCCESS")
            
    def on_data_received(self, red_samples, ir_samples, packet_seq, accel_xyz_g):
        if not self.is_previewing:
            return
            
        if self.start_time is None:
            self.start_time = time.time()
            
        packet_time = time.time() - self.start_time
        
        # Medición de pérdidas de paquetes
        if self.expected_packet_seq == 0:
            self.expected_packet_seq = (packet_seq + 1) & 0xFFFF
        elif packet_seq > self.expected_packet_seq:
            lost = packet_seq - self.expected_packet_seq
            self.lost_packets += lost
        self.expected_packet_seq = (packet_seq + 1) & 0xFFFF
        self.received_packets += 1
        
        loss_rate = (self.lost_packets / max(1, self.received_packets)) * 100
        self.quality_label.setText(f"📶 Pérdida: {loss_rate:.1f}%")
        
        # Aceleración física del MPU6050
        self.latest_accel = accel_xyz_g
        ax, ay, az = accel_xyz_g
        accel_mag = math.sqrt(ax**2 + ay**2 + az**2) - 1.0 # Sustraer gravedad estática 1g
        accel_filt = self.filter_accel.filter(accel_mag)
        self.latest_accel_mag = accel_filt
        
        # Máquina de estados de movimiento:
        # Reposo: |accel| < 0.08g  -> mu=0 (pesos congelados)
        # Movimiento leve: 0.08g a 0.35g -> NLMS activo adaptándose
        # Movimiento fuerte: > 0.35g -> suspender presión y alertar
        abs_accel = abs(accel_filt)
        if abs_accel < 0.08:
            self.motion_state = "REPOSO"
            adapt_nlms = False
        elif abs_accel <= 0.35:
            self.motion_state = "MOVIMIENTO_LEVE"
            adapt_nlms = self.nlms_checkbox.isChecked()
        else:
            self.motion_state = "MOVIMIENTO_FUERTE"
            adapt_nlms = False
            
        num_samples = len(red_samples)
        freq = self.actual_freq if self.actual_freq > 10 else EXPECTED_FREQ
        dt = 1.0 / freq
        
        for idx, (red_raw, ir_raw) in enumerate(zip(red_samples, ir_samples)):
            red_float = float(red_raw)
            ir_float = float(ir_raw)
            
            # Filtro Butterworth (0.5-8.0 Hz)
            if self.dc_filter_enabled:
                red_filt = self.filter_red.filter(red_float)
                ir_filt = self.filter_ir.filter(ir_float)
            else:
                red_filt = red_float
                ir_filt = ir_float
                
            # Filtro Adaptativo NLMS (Cancelación de movimiento con referencia del MPU6050)
            if self.nlms_checkbox.isChecked():
                red_nlms = self.nlms_red.filter(red_filt, accel_filt, adapt=adapt_nlms)
                ir_nlms = self.nlms_ir.filter(ir_filt, accel_filt, adapt=adapt_nlms)
            else:
                red_nlms = red_filt
                ir_nlms = ir_filt
                
            self.raw_red_buffer.append(red_float)
            self.raw_ir_buffer.append(ir_float)
            self.filtered_red_buffer.append(red_filt)
            self.filtered_ir_buffer.append(ir_filt)
            self.nlms_ir_buffer.append(ir_nlms)
            self.accel_mag_buffer.append(accel_filt)
            
            t = packet_time - (num_samples - 1 - idx) * dt
            self.time_buffer.append(t)
            
            # Calibración continua de piel por nivel DCraw (promedio exponencial)
            if ir_raw > 20000 and self.motion_state == "REPOSO":
                self.dc_red_avg = 0.998 * self.dc_red_avg + 0.002 * red_float
                self.dc_ir_avg = 0.998 * self.dc_ir_avg + 0.002 * ir_float
                self.stable_contact_counter += 1
                if self.stable_contact_counter >= 500 and self.auto_skin_enabled:
                    self._update_auto_skin_tone()
            else:
                self.stable_contact_counter = 0
                
            # Grabación de estudio clínico
            if self.is_recording:
                if len(self.recording_time) == 0:
                    self.recording_start_time = t
                rec_t = t - self.recording_start_time
                self.recording_time.append(rec_t)
                self.recording_raw_red.append(red_float)
                self.recording_raw_ir.append(ir_float)
                self.recording_filtered_red.append(red_filt)
                self.recording_filtered_ir.append(ir_filt)
                self.recording_nlms_red.append(red_nlms)
                self.recording_nlms_ir.append(ir_nlms)
                self.recording_accel_x.append(ax)
                self.recording_accel_y.append(ay)
                self.recording_accel_z.append(az)
                self.recording_accel_mag.append(accel_filt)
                self.recording_motion_state.append(self.motion_state)
                self.recording_battery_pct.append(self.battery_pct)
                
                # Valores instantáneos estimados
                bpm_val = float(self.bpm_value_label.text()) if self.bpm_value_label.text().replace('.','',1).isdigit() else 0.0
                spo2_val = float(self.spo2_value_label.text()) if self.spo2_value_label.text().replace('.','',1).isdigit() else 0.0
                self.recording_bpm.append(bpm_val)
                self.recording_spo2.append(spo2_val)
                self.recording_sbp.append(self.last_valid_sbp if self.last_valid_sbp else 0)
                self.recording_dbp.append(self.last_valid_dbp if self.last_valid_dbp else 0)
                
                if rec_t >= self.duration_spin.value():
                    self.stop_recording_auto()
            self.global_sample_index += 1

    def _update_auto_skin_tone(self):
        """Calcula el fototipo melánico automáticamente mediante el ratio continuo DC Rojo / DC IR."""
        if self.dc_ir_avg <= 0:
            return
        dc_ratio = self.dc_red_avg / self.dc_ir_avg
        
        # Clasificación cuantitativa según atenuación relativa por eumelanina (660nm vs 880nm)
        if dc_ratio > 1.25:
            cat_idx = 0 # Muy clara (ITA > 55°)
        elif dc_ratio > 1.10:
            cat_idx = 1 # Clara (ITA 41° - 55°)
        elif dc_ratio > 0.95:
            cat_idx = 2 # Intermedia (ITA 28° - 41°)
        elif dc_ratio > 0.80:
            cat_idx = 3 # Morena (ITA 10° - 28°)
        elif dc_ratio > 0.65:
            cat_idx = 4 # Oscura (ITA -30° - 10°)
        else:
            cat_idx = 5 # Muy oscura (ITA < -30°)
            
        if self.skin_combo.currentIndex() != cat_idx:
            self.skin_combo.setCurrentIndex(cat_idx)
            cat_name = self.skin_calibration_data[cat_idx]["name"]
            self.log_message(f"Tono de piel adaptado automáticamente por DCraw: {cat_name} (Ratio={dc_ratio:.2f})", "INFO")

    def calculate_bpm(self):
        """Detección de latidos sobre la señal IR limpia post-NLMS."""
        if len(self.nlms_ir_buffer) < 300 or len(self.time_buffer) < 300:
            return None
            
        y = np.array(self.nlms_ir_buffer)
        actual_freq = self.actual_freq if self.actual_freq > 10 else EXPECTED_FREQ
        
        # Suavizado de 200 ms (media móvil)
        window_size = max(5, int(actual_freq * 0.20))
        if window_size % 2 == 0:
            window_size += 1
        smoothed = np.convolve(y, np.ones(window_size)/window_size, mode='same')
        
        min_dist = int(actual_freq * 0.40) # Mínimo 400 ms entre latidos
        peaks = []
        ymin, ymax = np.min(smoothed), np.max(smoothed)
        yrange = ymax - ymin
        if yrange < 50:
            return None
            
        threshold = ymin + yrange * 0.50
        last_peak_idx = -min_dist
        for i in range(1, len(smoothed) - 1):
            if smoothed[i] > smoothed[i-1] and smoothed[i] > smoothed[i+1]:
                if smoothed[i] > threshold:
                    if (i - last_peak_idx) >= min_dist:
                        peaks.append(i)
                        last_peak_idx = i
                        
        if len(peaks) < 3:
            return None
            
        t_arr = np.array(self.time_buffer)
        peak_times = t_arr[peaks]
        intervals_sec = np.diff(peak_times)
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
        """Modelo hemodinámico PWA calibrado individualmente (sin truncamiento rígido)."""
        if bpm is None or len(peaks) < 3 or len(self.time_buffer) < max(peaks):
            return None, None
            
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
            if not post_peaks: continue
            p = post_peaks[0]
            post_valleys = [nv for nv in valleys if nv > p]
            if not post_valleys: continue
            nv = post_valleys[0]
            
            rise_times_sec.append(t_arr[p] - t_arr[v])
            fall_times_sec.append(t_arr[nv] - t_arr[p])
            
        if not rise_times_sec or not fall_times_sec:
            return None, None
            
        avg_rise_sec = np.mean(rise_times_sec)
        avg_fall_sec = np.mean(fall_times_sec)
        
        # Modelo base PWA con corrección de tono de piel y calibración clínica individual
        idx = self.skin_combo.currentIndex()
        cal = self.skin_calibration_data[idx]
        
        base_sbp = 120.0 + 0.15 * (bpm - 70.0) - 75.0 * (avg_rise_sec - 0.12) + cal["sbp_offset"]
        base_dbp = 80.0 + 0.08 * (bpm - 70.0) - 25.0 * (avg_fall_sec - 0.35) + cal["dbp_offset"]
        
        # Aplicar offset clínico individual si se ingresaron lecturas previas de esfigmomanómetro
        if self.is_clinically_calibrated:
            sbp = base_sbp + self.calib_sbp_offset
            dbp = base_dbp + self.calib_dbp_offset
        else:
            sbp = base_sbp
            dbp = base_dbp
            
        # Condición fisiológica: Presión diferencial mínima de 20 mmHg
        if sbp <= dbp + 20:
            sbp = dbp + 25
            
        return int(round(sbp)), int(round(dbp))

    def calculate_spo2(self):
        """Ratio of Ratios con compensación de melanina dérmica."""
        if len(self.raw_red_buffer) < 300 or len(self.raw_ir_buffer) < 300:
            return None
            
        red_raw = np.array(self.raw_red_buffer)[-300:]
        ir_raw = np.array(self.raw_ir_buffer)[-300:]
        red_filt = np.array(self.filtered_red_buffer)[-300:]
        ir_filt = np.array(self.filtered_ir_buffer)[-300:]
        
        dc_red = np.mean(red_raw)
        dc_ir = np.mean(ir_raw)
        if dc_red == 0 or dc_ir == 0:
            return None
            
        red_smooth = np.convolve(red_filt, np.ones(5)/5, mode='same')
        ir_smooth = np.convolve(ir_filt, np.ones(5)/5, mode='same')
        
        ac_red = np.max(red_smooth) - np.min(red_smooth)
        ac_ir = np.max(ir_smooth) - np.min(ir_smooth)
        if ac_ir == 0:
            return None
            
        r = (ac_red / dc_red) / (ac_ir / dc_ir)
        idx = self.skin_combo.currentIndex()
        cal = self.skin_calibration_data[idx]
        
        spo2 = 104.0 - 17.0 * r + cal["spo2_offset"]
        return max(75.0, min(100.0, spo2))

    def update_frequency(self):
        """Actualización periódica de signos y jerarquización de alertas de seguridad clínica."""
        if not self.is_previewing:
            self.bpm_value_label.setText("--")
            self.heart_label.setStyleSheet("color: #888888;")
            self.bp_value_label.setText("-- / --")
            self.spo2_value_label.setText("--")
            return
            
        current_time = time.time()
        elapsed = current_time - self.last_freq_time
        current_samples = self.global_sample_index
        if elapsed > 0:
            self.actual_freq = (current_samples - self.last_freq_samples) / elapsed
        self.last_freq_samples = current_samples
        self.last_freq_time = current_time
        self.freq_label.setText(f"📊 Muestreo: {self.actual_freq:.1f} Hz")
        self.samples_label.setText(
            f"Muestras: {self.global_sample_index}  |  Sujeto: {self.subject_id}  |  "
            f"Calibración: {'PERSONALIZADA' if self.is_clinically_calibrated else 'ESTÁNDAR PWA'}"
        )
        
        # -------------------------------------------------------------
        # JERARQUÍA DE ALERTAS CLÍNICAS (Nivel 1: Batería, Nivel 2: Piel, Nivel 3: Movimiento)
        # -------------------------------------------------------------
        # 1. Alerta de Batería Baja (< 3.4V)
        if self.battery_low:
            self.alert_banner.setText("⚠️ BATERÍA BAJA (<3.4V) - CARGUE EL DISPOSITIVO (Estimación en pausa)")
            self.alert_banner.setStyleSheet("background-color: #FFDDDD; color: #9D2449; font-weight: bold; border: 1px solid #9D2449;")
            self.bp_value_label.setText("PAUSA")
            return
            
        # 2. Detector de Contacto Físico (Umbral > 20,000 unidades en canal IR)
        if len(self.raw_ir_buffer) > 0:
            recent_raw_ir = list(self.raw_ir_buffer)[-50:]
            avg_raw_ir = np.mean(recent_raw_ir)
            if avg_raw_ir < 20000:
                self.alert_banner.setText("⚠️ SIN CONTACTO DE PIEL - COLOQUE EL SENSOR EN LA MUÑECA")
                self.alert_banner.setStyleSheet("background-color: #FFF3CD; color: #856404; font-weight: bold; border: 1px solid #FFEEBA;")
                self.bpm_value_label.setText("--")
                self.heart_label.setStyleSheet("color: #888888;")
                self.bp_value_label.setText("-- / --")
                self.spo2_value_label.setText("--")
                return

        # 3. Máquina de Estados de Movimiento
        if self.motion_state == "MOVIMIENTO_FUERTE":
            self.motion_label.setText("🏃 Movimiento: FUERTE (Artefacto)")
            self.motion_label.setStyleSheet("color: #9D2449; font-weight: bold;")
            self.alert_banner.setText("⚠️ MANTÉN LA MUÑECA QUIETA - ARTEFACTO DE MOVIMIENTO DETECTADO")
            self.alert_banner.setStyleSheet("background-color: #FFF3CD; color: #856404; font-weight: bold; border: 1px solid #FFEEBA;")
            # Retener último valor válido en pantalla sin recalcular
            if self.last_valid_sbp and self.last_valid_dbp:
                self.bp_value_label.setText(f"{self.last_valid_sbp} / {self.last_valid_dbp}*")
            return
        elif self.motion_state == "MOVIMIENTO_LEVE":
            self.motion_label.setText("🏃 Movimiento: Leve (NLMS Activo)")
            self.motion_label.setStyleSheet("color: #B38E5D; font-weight: bold;")
            self.alert_banner.setText("🟡 FILTRANDO MOVIMIENTO - CANCELACIÓN ADAPTATIVA NLMS ACTIVA")
            self.alert_banner.setStyleSheet("background-color: #F8FAFC; color: #1B365D; font-weight: bold; border: 1px solid #CBD5E1;")
        else:
            self.motion_label.setText("🏃 Movimiento: Reposo")
            self.motion_label.setStyleSheet("color: #4E232E;")
            self.alert_banner.setText("🟢 MONITOREO CONTINUO ACTIVO - ADQUISICIÓN ESTABLE")
            self.alert_banner.setStyleSheet("background-color: #E8F5E9; color: #2E7D32; font-weight: bold; border: 1px solid #A5D6A7;")

        # Procesamiento normal de ritmo cardíaco
        bpm = self.calculate_bpm()
        if bpm is not None:
            self.bpm_value_label.setText(f"{int(round(bpm))}")
            self.heart_label.setStyleSheet("color: #9D2449;")
            
            y = np.array(self.nlms_ir_buffer)
            smoothed = np.convolve(y, np.ones(5)/5, mode='same')
            peaks = []
            min_dist = 40
            ymin, ymax = np.min(smoothed), np.max(smoothed)
            threshold = ymin + (ymax - ymin) * 0.4
            last_p = -min_dist
            for i in range(1, len(smoothed) - 1):
                if smoothed[i] > smoothed[i-1] and smoothed[i] > smoothed[i+1]:
                    if smoothed[i] > threshold and (i - last_p) >= min_dist:
                        peaks.append(i)
                        last_p = i
                        
            sbp, dbp = self.estimate_blood_pressure(bpm, peaks, smoothed)
            if sbp is not None and dbp is not None:
                self.bp_value_label.setText(f"{sbp} / {dbp}")
                self.last_valid_sbp = sbp
                self.last_valid_dbp = dbp
        else:
            self.bpm_value_label.setText("--")
            self.heart_label.setStyleSheet("color: #888888;")
            
        spo2 = self.calculate_spo2()
        if spo2 is not None:
            self.spo2_value_label.setText(f"{int(round(spo2))}")
        else:
            self.spo2_value_label.setText("--")

    def toggle_filter(self):
        self.dc_filter_enabled = self.filter_checkbox.isChecked()
        self.filter_red.reset()
        self.filter_ir.reset()
        self.log_message(f"Filtro Butterworth {'activado' if self.dc_filter_enabled else 'desactivado'}", "INFO")

    def toggle_invert(self):
        self.invert_signal = self.invert_checkbox.isChecked()
        self.log_message(f"Inversión de onda visual {'activada' if self.invert_signal else 'desactivada'}", "INFO")

    def analyze_skin_photo(self):
        """Módulo de respaldo: Análisis fotométrico de fotografía local."""
        filename, _ = QFileDialog.getOpenFileName(self, "Seleccionar Foto de la Piel", "", "Imágenes (*.png *.jpg *.jpeg *.bmp)")
        if not filename: return
        image = QImage(filename)
        if image.isNull():
            QMessageBox.warning(self, "Error", "No se pudo leer la imagen")
            return
            
        w, h = image.width(), image.height()
        rx = max(0, w//2 - 50); ry = max(0, h//2 - 50)
        rw = min(100, w - rx); rh = min(100, h - ry)
        
        sum_r, sum_g, sum_b = 0.0, 0.0, 0.0
        count = 0
        for py in range(ry, ry + rh):
            for px in range(rx, rx + rw):
                c = image.pixelColor(px, py)
                sum_r += c.red(); sum_g += c.green(); sum_b += c.blue()
                count += 1
                
        r_norm, g_norm, b_norm = (sum_r/count)/255.0, (sum_g/count)/255.0, (sum_b/count)/255.0
        pivot = lambda v: ((v + 0.055) / 1.055)**2.4 if v > 0.04045 else v / 12.92
        rp, gp, bp = pivot(r_norm), pivot(g_norm), pivot(b_norm)
        
        x = rp * 0.4124564 + gp * 0.3575761 + bp * 0.1804375
        y = rp * 0.2126729 + gp * 0.7151522 + bp * 0.0721750
        z = rp * 0.0193339 + gp * 0.1191920 + bp * 0.9503041
        
        fx = (x/0.950489)**(1/3) if (x/0.950489) > 0.008856 else 7.787*(x/0.950489) + 16/116
        fy = (y/1.000000)**(1/3) if (y/1.000000) > 0.008856 else 7.787*(y/1.000000) + 16/116
        fz = (z/1.088840)**(1/3) if (z/1.088840) > 0.008856 else 7.787*(z/1.088840) + 16/116
        
        l_star = 116 * fy - 16
        b_star = 200 * (fy - fz)
        if b_star == 0: b_star = 0.001
        ita = math.atan((l_star - 50) / b_star) * (180 / math.pi)
        
        if ita > 55: idx = 0
        elif ita > 41: idx = 1
        elif ita > 28: idx = 2
        elif ita > 10: idx = 3
        elif ita > -30: idx = 4
        else: idx = 5
        
        self.skin_combo.setCurrentIndex(idx)
        self.auto_skin_checkbox.setChecked(False) # Priorizar selección del usuario
        cat_name = self.skin_calibration_data[idx]["name"]
        QMessageBox.information(self, "Tono de Piel Calibrado", 
                                f"Análisis CIELab completado:\nL*={l_star:.1f}, b*={b_star:.1f}, ITA={ita:.1f}°\n"
                                f"Categoría aplicada: '{cat_name}'")

    def update_plot(self):
        if not self.is_previewing or len(self.time_buffer) == 0:
            return
            
        if self.nlms_checkbox.isChecked():
            y_data = np.array(self.nlms_ir_buffer)
            title = "REGISTRO EN TIEMPO REAL - PPG IR FILTRADA (NLMS + Butterworth)"
        elif self.dc_filter_enabled:
            y_data = np.array(self.filtered_ir_buffer)
            title = "REGISTRO EN TIEMPO REAL - PPG IR (Filtro Butterworth 0.5-8Hz)"
        else:
            y_data = np.array(self.raw_ir_buffer)
            title = "REGISTRO EN TIEMPO REAL - PPG IR CRUDA (18 bits)"
            
        if self.invert_signal:
            y_data = MAX_18BIT - y_data
            title += " [MODO ESPEJO]"
            
        t_data = np.array(self.time_buffer)
        t_max = t_data[-1]
        t_min = max(0, t_max - 5)
        mask = (t_data >= t_min) & (t_data <= t_max)
        
        self.data_line.set_data(t_data[mask], y_data[mask])
        self.ax.set_xlim(t_min, t_max)
        self.ax.set_title(title, color="#621132", fontsize=10, fontweight="bold")
        
        if len(y_data[mask]) > 0:
            ymin, ymax = np.min(y_data[mask]), np.max(y_data[mask])
            margin = (ymax - ymin) * 0.1 + 1
            self.ax.set_ylim(ymin - margin, ymax + margin)
            
        self.canvas.draw_idle()
        
        if self.is_recording and len(self.recording_time) > 0:
            duration = len(self.recording_time) * DT_SAMPLE
            target = self.duration_spin.value()
            self.progress_bar.setVisible(True)
            self.progress_bar.setMaximum(target)
            self.progress_bar.setValue(int(min(duration, target)))
            self.progress_bar.setFormat(f"GRABANDO ESTUDIO: {duration:.1f}s / {target}s")

    def start_preview(self):
        self.is_previewing = True
        self.start_time = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.log_message("Flujo de visualización clínica iniciado", "INFO")
        self.update_ui_state()

    # =========================================================================
    # CORRECCIÓN TÉCNICA DEL ERROR EN EL BOTÓN DE GRABADO DE DATOS
    # =========================================================================
    def start_recording(self):
        if not self.is_previewing:
            QMessageBox.warning(self, "Error", "Inicie primero la vista previa para registrar datos")
            return
            
        # Limpieza correcta de los búferes extendidos
        self.recording_time.clear()
        self.recording_raw_red.clear()
        self.recording_raw_ir.clear()
        self.recording_filtered_red.clear()
        self.recording_filtered_ir.clear()
        self.recording_nlms_red.clear()
        self.recording_nlms_ir.clear()
        self.recording_accel_x.clear()
        self.recording_accel_y.clear()
        self.recording_accel_z.clear()
        self.recording_accel_mag.clear()
        self.recording_motion_state.clear()
        self.recording_battery_pct.clear()
        self.recording_bpm.clear()
        self.recording_spo2.clear()
        self.recording_sbp.clear()
        self.recording_dbp.clear()
        
        self.recording_start_index = self.global_sample_index
        self.is_recording = True
        self.start_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.log_message(f"Grabación de estudio iniciada ({self.duration_spin.value()}s)", "INFO")

    def stop_recording_auto(self):
        self.is_recording = False
        self.progress_bar.setVisible(False)
        self.save_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.preview_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        
        samples = len(self.recording_time)
        dur = samples * DT_SAMPLE
        QMessageBox.information(self, "Estudio Completado", 
                                f"✅ Grabación finalizada exitosamente:\n\n"
                                f"• Muestras: {samples}\n"
                                f"• Duración: {dur:.2f} s\n"
                                f"• Sujeto: {self.subject_id}\n\n"
                                f"Presione 'EXPORTAR CSV' para generar el archivo de validación clínica.")
        self.log_message(f"Estudio finalizado: {samples} muestras capturadas", "SUCCESS")

    def stop_all(self):
        self.is_previewing = False
        self.is_recording = False
        self.progress_bar.setVisible(False)
        self.save_button.setEnabled(len(self.recording_time) > 0)
        self.update_ui_state()

    def reset_all(self):
        self.stop_all()
        self.time_buffer.clear()
        self.raw_red_buffer.clear()
        self.raw_ir_buffer.clear()
        self.filtered_red_buffer.clear()
        self.filtered_ir_buffer.clear()
        self.nlms_ir_buffer.clear()
        self.accel_mag_buffer.clear()
        self.filter_red.reset()
        self.filter_ir.reset()
        self.filter_accel.reset()
        self.nlms_red.reset()
        self.nlms_ir.reset()
        self.data_line.set_data([], [])
        self.canvas.draw_idle()
        self.save_button.setEnabled(False)
        self.log_message("Búferes de señal y filtros reiniciados", "INFO")

    def update_ui_state(self):
        self.start_button.setEnabled(self.is_previewing and not self.is_recording)
        self.preview_button.setEnabled(self.is_connected and not self.is_previewing)
        self.stop_button.setEnabled(self.is_previewing)
        self.connect_button.setEnabled(not self.is_connected)
        self.disconnect_button.setEnabled(self.is_connected)

    # =========================================================================
    # EXPORTACIÓN CSV EXTENDIDA (N=30) CON BANDERAS DE CONFIGURACIÓN Y VALIDACIÓN
    # =========================================================================
    def save_csv(self):
        if len(self.recording_time) == 0:
            QMessageBox.warning(self, "Error", "No existen registros en memoria para exportar")
            return
            
        filename, _ = QFileDialog.getSaveFileName(
            self, "Guardar Reporte CSV Extendido",
            f"estudio_{self.subject_id}_{time.strftime('%Y%m%d_%H%M%S')}.csv",
            "Archivos CSV (*.csv)"
        )
        if not filename:
            return
            
        try:
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                
                # Bloque superior con banderas de configuración algorítmica
                writer.writerow(["# ====================================================================="])
                writer.writerow(["# REPORTE DE VALIDACIÓN CLÍNICA - TENSIÓMETRO DIGITAL v2.0 (ARCADIA)"])
                writer.writerow([f"# ID_Sujeto: {self.subject_id}", f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}"])
                writer.writerow([f"# SBP_Referencia_Esfigmomanómetro: {self.sbp_reference:.1f} mmHg", 
                                 f"DBP_Referencia_Esfigmomanómetro: {self.dbp_reference:.1f} mmHg"])
                writer.writerow([f"# Filtro_Butterworth: {'ACTIVO (0.5-8.0 Hz)' if self.dc_filter_enabled else 'DESACTIVADO'}",
                                 f"Filtro_NLMS: {'ACTIVO (24 taps, mu=0.02)' if self.nlms_checkbox.isChecked() else 'DESACTIVADO'}",
                                 f"Tono_Piel: {self.skin_combo.currentText()}"])
                writer.writerow(["# ====================================================================="])
                
                # Encabezados de columnas clínicas y físicas
                writer.writerow([
                    "Tiempo_s",
                    "PPG_Rojo_Cruda",
                    "PPG_IR_Cruda",
                    "PPG_Rojo_Butterworth",
                    "PPG_IR_Butterworth",
                    "PPG_Rojo_Limpia_NLMS",
                    "PPG_IR_Limpia_NLMS",
                    "Accel_X_g",
                    "Accel_Y_g",
                    "Accel_Z_g",
                    "Accel_Magnitud_g",
                    "Estado_Movimiento",
                    "BPM_Estimado",
                    "SpO2_Estimado_pct",
                    "SBP_Estimado_mmHg",
                    "DBP_Estimado_mmHg",
                    "Bateria_pct",
                    "ID_Sujeto",
                    "SBP_Referencia_mmHg",
                    "DBP_Referencia_mmHg"
                ])
                
                for i in range(len(self.recording_time)):
                    writer.writerow([
                        f"{self.recording_time[i]:.4f}",
                        int(self.recording_raw_red[i]),
                        int(self.recording_raw_ir[i]),
                        f"{self.recording_filtered_red[i]:.2f}",
                        f"{self.recording_filtered_ir[i]:.2f}",
                        f"{self.recording_nlms_red[i]:.2f}",
                        f"{self.recording_nlms_ir[i]:.2f}",
                        f"{self.recording_accel_x[i]:.4f}",
                        f"{self.recording_accel_y[i]:.4f}",
                        f"{self.recording_accel_z[i]:.4f}",
                        f"{self.recording_accel_mag[i]:.4f}",
                        self.recording_motion_state[i],
                        f"{self.recording_bpm[i]:.1f}",
                        f"{self.recording_spo2[i]:.1f}",
                        f"{self.recording_sbp[i]}",
                        f"{self.recording_dbp[i]}",
                        self.recording_battery_pct[i],
                        self.subject_id,
                        f"{self.sbp_reference:.1f}",
                        f"{self.dbp_reference:.1f}"
                    ])
                    
            QMessageBox.information(self, "Exportación Exitosa", 
                                    f"✅ Reporte CSV guardado correctamente:\n{filename}\n\n"
                                    f"Campos clínicos incluidos: ID_Sujeto, SBP/DBP Referencia, "
                                    f"Señales NLMS y Acelerometría MPU6050 para validación estadística.")
            self.log_message(f"Archivo CSV extendido exportado: {filename}", "SUCCESS")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo guardar el archivo: {str(e)}")

    def log_message(self, msg, level):
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {msg}")

    def closeEvent(self, event):
        if self.ble_worker:
            self.ble_worker.stop()
            self.ble_worker.wait(1500)
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    window = MainWindow()
    window.showMaximized()
    with loop:
        loop.run_forever()
