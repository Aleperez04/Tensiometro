import os
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from scipy import signal

output_dir = r"C:\Users\alepe\.gemini\antigravity\scratch\tensiometro_extract\docs_img"
os.makedirs(output_dir, exist_ok=True)

plt.rcParams['font.sans-serif'] = 'DejaVu Sans'
plt.rcParams['axes.edgecolor'] = '#cbd5e0'
plt.rcParams['axes.linewidth'] = 1.0

# -------------------------------------------------------------
# 1. BODE PLOT & TIME-DOMAIN FILTERING (Butterworth 4th Order)
# -------------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), dpi=200)

fs = 100.0
nyq = 0.5 * fs
low = 0.5 / nyq
high = 8.0 / nyq
b, a = signal.butter(4, [low, high], btype='band')
w, h = signal.freqz(b, a, worN=2000, fs=fs)

# Subplot 1: Respuesta en Frecuencia (Bode)
ax1.plot(w, 20 * np.log10(np.maximum(np.abs(h), 1e-5)), color='#2b6cb0', lw=2.2, label='Filtro Butterworth 4.º Orden')
ax1.axvline(0.5, color='#e53e3e', ls='--', lw=1.5, label='Corte Inferior: 0.5 Hz (-3 dB)')
ax1.axvline(8.0, color='#dd6b20', ls='--', lw=1.5, label='Corte Superior: 8.0 Hz (-3 dB)')
ax1.axhline(-3, color='#718096', ls=':', lw=1.2)
ax1.fill_between(w, -60, 20 * np.log10(np.maximum(np.abs(h), 1e-5)), where=(w >= 0.5) & (w <= 8.0), color='#bee3f8', alpha=0.35, label='Banda de Paso Útil (PPG)')
ax1.set_xscale('log')
ax1.set_xlim(0.1, 50)
ax1.set_ylim(-50, 5)
ax1.set_title('Respuesta en Frecuencia del Filtro Paso-Banda (Magnitud en dB)', fontsize=12, fontweight='bold', color='#1a365d')
ax1.set_xlabel('Frecuencia (Hz)', fontsize=10)
ax1.set_ylabel('Atenuación (dB)', fontsize=10)
ax1.grid(True, which='both', ls='-', alpha=0.3)
ax1.legend(loc='lower left', fontsize=9, framealpha=0.9)

# Subplot 2: Señal en el Dominio del Tiempo (Cruda vs Filtrada)
t = np.linspace(0, 4, 400)
# Pulso simulado
hr_freq = 1.25 # 75 BPM
pulse = np.sin(2 * np.pi * hr_freq * t) + 0.35 * np.sin(4 * np.pi * hr_freq * t + 0.5)
baseline_drift = 8.0 * np.sin(2 * np.pi * 0.15 * t) + 120.0 # Deriva respiratoria
noise = 0.45 * np.random.normal(size=len(t))
raw_signal = baseline_drift + 2.5 * pulse + noise
# Filtrada
sos = signal.butter(4, [0.5, 8.0], btype='band', fs=fs, output='sos')
filt_signal = signal.sosfilt(sos, raw_signal)

ax2.plot(t, raw_signal, color='#a0aec0', lw=1.3, alpha=0.8, label='Señal Cruda IR (Con Deriva Respiratoria y Offset DC)')
ax2_twin = ax2.twinx()
ax2_twin.plot(t[50:], filt_signal[50:], color='#2b6cb0', lw=2.0, label='Señal Filtrada Butterworth (Centrada en 0, 0.5-8Hz)')
ax2.set_title('Efecto del Filtro en el Tiempo: Eliminación de Offset DC y Supresión de Ruido', fontsize=12, fontweight='bold', color='#1a365d')
ax2.set_xlabel('Tiempo (segundos)', fontsize=10)
ax2.set_ylabel('Amplitud Cruda (Cuentas)', color='#718096', fontsize=10)
ax2_twin.set_ylabel('Amplitud Filtrada (u.a.)', color='#2b6cb0', fontsize=10)
ax2.grid(True, ls='-', alpha=0.3)

lines1, labels1 = ax2.get_legend_handles_labels()
lines2, labels2 = ax2_twin.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc='upper right', fontsize=8.5, framealpha=0.9)

plt.tight_layout()
plt.savefig(os.path.join(output_dir, 'dsp_butterworth_bode.png'), dpi=200)
plt.close()
print("dsp_butterworth_bode.png generado.")

# -------------------------------------------------------------
# 2. PWA PULSE WAVEFORM MORPHOLOGY & TIMINGS
# -------------------------------------------------------------
fig, ax = plt.subplots(figsize=(9, 5), dpi=200)

t_single = np.linspace(0, 0.85, 300)
# Curva modelo de latido
p_up = np.sin(np.pi * t_single / 0.14)**2 * (t_single < 0.14)
p_down = np.exp(-(t_single - 0.14)/0.18) * np.cos(np.pi * (t_single - 0.14)/0.7) * (t_single >= 0.14)
pulse_morph = p_up + p_down
# Muesca dicrota
dicrotic = 0.22 * np.exp(-((t_single - 0.32)/0.04)**2)
single_wave = pulse_morph + dicrotic
single_wave = (single_wave - np.min(single_wave)) / (np.max(single_wave) - np.min(single_wave))

ax.plot(t_single, single_wave, color='#2b6cb0', lw=3.0, label='Onda Pletismográfica Arterial (PPG)')

# Puntos clave
onset_t, onset_v = 0.0, single_wave[0]
peak_t, peak_v = 0.14, single_wave[np.argmin(np.abs(t_single - 0.14))]
notch_t, notch_v = 0.30, single_wave[np.argmin(np.abs(t_single - 0.30))]
diast_t, diast_v = 0.35, single_wave[np.argmin(np.abs(t_single - 0.35))]
end_t, end_v = 0.82, single_wave[np.argmin(np.abs(t_single - 0.82))]

ax.scatter([onset_t, peak_t, notch_t, diast_t, end_t], 
           [onset_v, peak_v, notch_v, diast_v, end_v], 
           color=['#3182ce', '#e53e3e', '#805ad5', '#319795', '#3182ce'], s=80, zorder=5)

ax.annotate('Valle Onset Inicial\n(Inicio Sístole)', xy=(onset_t, onset_v), xytext=(onset_t+0.02, 0.15),
            arrowprops=dict(arrowstyle="->", color='#3182ce', lw=1.5), fontsize=8.5, fontweight='bold', color='#2b6cb0')

ax.annotate('Pico Sistólico Máximo\n(Eyección Ventricular)', xy=(peak_t, peak_v), xytext=(peak_t+0.04, 0.95),
            arrowprops=dict(arrowstyle="->", color='#e53e3e', lw=1.5), fontsize=8.5, fontweight='bold', color='#c53030')

ax.annotate('Muesca Dícrota\n(Cierre Válvula Aórtica)', xy=(notch_t, notch_v), xytext=(notch_t+0.06, 0.65),
            arrowprops=dict(arrowstyle="->", color='#805ad5', lw=1.5), fontsize=8.5, fontweight='bold', color='#6b46c1')

ax.annotate('Valle Final\n(Fin Diástole)', xy=(end_t, end_v), xytext=(end_t-0.15, 0.18),
            arrowprops=dict(arrowstyle="->", color='#3182ce', lw=1.5), fontsize=8.5, fontweight='bold', color='#2b6cb0')

# Intervalos Ts y Td
ax.annotate('', xy=(peak_t, -0.08), xytext=(onset_t, -0.08),
            arrowprops=dict(arrowstyle="<->", color='#e53e3e', lw=2.0))
ax.text((onset_t + peak_t)/2, -0.15, 'Ts (Tiempo de Subida)\n~120 ms', ha='center', fontsize=8.5, fontweight='bold', color='#c53030')

ax.annotate('', xy=(end_t, -0.08), xytext=(peak_t, -0.08),
            arrowprops=dict(arrowstyle="<->", color='#3182ce', lw=2.0))
ax.text((peak_t + end_t)/2, -0.15, 'Td (Tiempo de Bajada)\n~350 ms', ha='center', fontsize=8.5, fontweight='bold', color='#2b6cb0')

ax.set_ylim(-0.25, 1.15)
ax.set_xlim(-0.05, 0.90)
ax.set_title('Morfología de la Onda de Pulso Arterial y Parámetros Temporales PWA', fontsize=12, fontweight='bold', color='#1a365d')
ax.set_xlabel('Tiempo del Ciclo Cardíaco (segundos)', fontsize=10)
ax.set_ylabel('Amplitud Normalizada', fontsize=10)
ax.grid(True, ls='--', alpha=0.4)
ax.axhline(0, color='gray', lw=0.8, ls=':')

plt.tight_layout()
plt.savefig(os.path.join(output_dir, 'pwa_pulse_waveform.png'), dpi=200)
plt.close()
print("pwa_pulse_waveform.png generado.")

# -------------------------------------------------------------
# 3. CIELab & ITA COLOR SPACE MODEL
# -------------------------------------------------------------
fig, (ax_ita, ax_swatches) = plt.subplots(1, 2, figsize=(11, 5), dpi=200, gridspec_kw={'width_ratios': [1.3, 1]})

# Gráfico polar/cartesiano del espacio L* vs b*
b_star = np.linspace(10, 45, 200)
l_star = np.linspace(20, 80, 200)
B, L = np.meshgrid(b_star, l_star)
ITA = np.arctan((L - 50) / B) * (180 / np.pi)

contour = ax_ita.contourf(B, L, ITA, levels=30, cmap='YlOrBr_r', alpha=0.85)
cbar = fig.colorbar(contour, ax=ax_ita)
cbar.set_label('Ángulo ITA (grados sexagesimales °)', fontsize=9, fontweight='bold')

# Líneas de contorno de las 6 categorías
categories_angles = [55, 41, 28, 10, -30]
line_colors = ['#1a365d', '#2b6cb0', '#319795', '#d69e2e', '#c53030']
for angle, col in zip(categories_angles, line_colors):
    # L = 50 + B * tan(angle)
    b_line = np.linspace(10, 45, 100)
    l_line = 50 + b_line * np.tan(angle * np.pi / 180)
    valid = (l_line >= 20) & (l_line <= 80)
    ax_ita.plot(b_line[valid], l_line[valid], ls='--', lw=1.8, color=col)
    mid_idx = len(b_line[valid]) // 2
    if mid_idx > 0:
        ax_ita.text(b_line[valid][mid_idx], l_line[valid][mid_idx], f' {angle}°', color=col, fontweight='bold', fontsize=8)

ax_ita.set_title('Espacio Perceptual CIELab: Plano L* vs b* e Índice ITA', fontsize=11, fontweight='bold', color='#1a365d')
ax_ita.set_xlabel('Coordenada b* (Amarillez / Carotenos)', fontsize=9.5)
ax_ita.set_ylabel('Coordenada L* (Luminosidad Perceptual)', fontsize=9.5)
ax_ita.set_xlim(10, 45)
ax_ita.set_ylim(20, 80)
ax_ita.grid(True, ls=':', alpha=0.5)

# Swatches y Tabla de Calibración
swatches_data = [
    ("Muy clara", "> 55°", "+0.0%", "0.0 mmHg", "#f7ecd5"),
    ("Clara", "41° a 55°", "+0.0%", "0.0 mmHg", "#f0d5b2"),
    ("Intermedia", "28° a 41°", "+0.2%", "-0.5 mmHg", "#dfb88e"),
    ("Morena", "10° a 28°", "+0.6%", "-1.0 mmHg", "#b88358"),
    ("Oscura", "-30° a 10°", "+1.2%", "-2.0 mmHg", "#82502f"),
    ("Muy oscura", "< -30°", "+2.0%", "-3.5 mmHg", "#4a2c1d"),
]

ax_swatches.axis('off')
ax_swatches.set_title('Categorías ITA y Offsets de Compensación', fontsize=11, fontweight='bold', color='#1a365d')

y_pos = 0.90
for name, ita_range, spo2_off, bp_off, hex_col in swatches_data:
    # Muestra de color
    rect = patches.FancyBboxPatch((0.02, y_pos - 0.09), 0.18, 0.10, boxstyle="round,pad=0.02",
                                  facecolor=hex_col, edgecolor='#718096', lw=1.2)
    ax_swatches.add_patch(rect)
    
    # Texto
    ax_swatches.text(0.24, y_pos - 0.02, f"{name} (ITA: {ita_range})", fontsize=9.5, fontweight='bold', color='#1a202c')
    ax_swatches.text(0.24, y_pos - 0.07, f"ΔSpO2: {spo2_off}  |  ΔSBP: {bp_off}", fontsize=8.5, color='#4a5568')
    y_pos -= 0.155

plt.tight_layout()
plt.savefig(os.path.join(output_dir, 'cielab_ita_gamut.png'), dpi=200)
plt.close()
print("cielab_ita_gamut.png generado.")

# -------------------------------------------------------------
# 4. BLE 14-BYTE TELEMETRY FRAME STRUCTURE
# -------------------------------------------------------------
fig, ax = plt.subplots(figsize=(10, 3.2), dpi=200)
ax.axis('off')

# Dibujar bloques de bytes
blocks = [
    ("Bytes 0-2\n(3 Bytes)", "Muestra 1: Rojo\nuint24 (Big-Endian)\n0 a 262,143", "#fed7d7", "#9b2c2c"),
    ("Bytes 3-5\n(3 Bytes)", "Muestra 1: IR\nuint24 (Big-Endian)\n0 a 262,143", "#feebc8", "#9c4221"),
    ("Bytes 6-8\n(3 Bytes)", "Muestra 2: Rojo\nuint24 (Big-Endian)\n0 a 262,143", "#fed7d7", "#9b2c2c"),
    ("Bytes 9-11\n(3 Bytes)", "Muestra 2: IR\nuint24 (Big-Endian)\n0 a 262,143", "#feebc8", "#9c4221"),
    ("Bytes 12-13\n(2 Bytes)", "Secuencia ID\nuint16 (Little-Endian)\n0 a 65,535", "#e9d8fd", "#553c9a")
]

x_start = 0.02
total_w = 0.96
block_w = total_w / len(blocks)

for i, (byte_label, desc, bg_col, text_col) in enumerate(blocks):
    x = x_start + i * block_w
    rect = patches.FancyBboxPatch((x + 0.005, 0.15), block_w - 0.01, 0.65,
                                  boxstyle="round,pad=0.02", facecolor=bg_col, edgecolor=text_col, lw=1.8)
    ax.add_patch(rect)
    ax.text(x + block_w / 2, 0.62, byte_label, ha='center', va='center', fontsize=9.5, fontweight='bold', color=text_col)
    ax.text(x + block_w / 2, 0.36, desc, ha='center', va='center', fontsize=8.0, color='#2d3748')

ax.set_title('Estructura de la Trama Binaria de Telemetría BLE GATT (Total: 14 Bytes por Notificación)', 
             fontsize=11.5, fontweight='bold', color='#1a365d', y=0.95)

plt.tight_layout()
plt.savefig(os.path.join(output_dir, 'ble_packet_frame.png'), dpi=200)
plt.close()
print("ble_packet_frame.png generado.")
