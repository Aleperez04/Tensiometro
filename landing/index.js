// ===================================================================================
// ESTACIÓN MÉDICA DE DIAGNÓSTICO CARDIOVASCULAR - VERSIÓN 2.0 (WEB BLUETOOTH)
// Cancelación de Movimiento Adaptativa (NLMS), Monitoreo de Batería,
// Calibración Clínica Individual y Tono de Piel Automático por DCraw
// ===================================================================================

document.addEventListener('DOMContentLoaded', () => {
    // ---------------------------------------------------------
    // 1. Botón Copiar Código Arduino (v2.0)
    // ---------------------------------------------------------
    const copyBtn = document.getElementById('copyBtn');
    const arduinoCode = document.getElementById('arduinoCode');

    if (copyBtn && arduinoCode) {
        copyBtn.addEventListener('click', async () => {
            const codeText = arduinoCode.textContent;
            try {
                await navigator.clipboard.writeText(codeText);
                copyBtn.textContent = '✓ ¡Copiado!';
                copyBtn.classList.add('copied');
                setTimeout(() => {
                    copyBtn.textContent = '📋 Copiar Código';
                    copyBtn.classList.remove('copied');
                }, 2500);
            } catch (err) {
                console.error('Error al copiar el código: ', err);
            }
        });
    }

    // ---------------------------------------------------------
    // 2. Control Spy en Barra de Navegación
    // ---------------------------------------------------------
    const sections = document.querySelectorAll('section');
    const navLinks = document.querySelectorAll('.nav-links a');

    window.addEventListener('scroll', () => {
        let currentSectionId = '';
        sections.forEach(section => {
            const sectionTop = section.offsetTop;
            if (window.scrollY >= (sectionTop - 120)) {
                currentSectionId = section.getAttribute('id');
            }
        });
        navLinks.forEach(link => {
            link.classList.remove('active');
            if (link.getAttribute('href') === `#${currentSectionId}`) {
                link.classList.add('active');
                link.style.color = '#B38E5D';
            } else {
                link.style.color = '';
            }
        });
    });

    // ---------------------------------------------------------
    // 3. Constantes de Bluetooth y Algoritmos
    // ---------------------------------------------------------
    const SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b";
    const DATA_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8";        // 20 o 14 bytes
    const CONTROL_CHAR_UUID = "12345678-1234-1234-1234-123456789abc";     // START / STOP
    const BATTERY_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb";     // Batería (mV, %, flags)
    const EXPECTED_FREQ = 100.0;
    const BUFFER_LIMIT = 500;

    // ---------------------------------------------------------
    // 4. Elementos del DOM del Monitor Clínico
    // ---------------------------------------------------------
    const connectBleBtn = document.getElementById('connectBleBtn');
    const disconnectBleBtn = document.getElementById('disconnectBleBtn');
    const startStudyBtn = document.getElementById('startStudyBtn');
    const exportCsvBtn = document.getElementById('exportCsvBtn');
    const calibModalBtn = document.getElementById('calibModalBtn');
    
    const connectionStatus = document.getElementById('connectionStatus');
    const batteryStatus = document.getElementById('batteryStatus');
    const skinContact = document.getElementById('skinContact');
    const motionStatus = document.getElementById('motionStatus');
    const alertBanner = document.getElementById('alertBanner');
    
    const deviceSampling = document.getElementById('deviceSampling');
    const packetLoss = document.getElementById('packetLoss');
    const samplesCollected = document.getElementById('samplesCollected');
    const studyDuration = document.getElementById('studyDuration');
    const progressBarContainer = document.getElementById('progressBarContainer');
    const progressBar = document.getElementById('progressBar');
    
    const invertSignal = document.getElementById('invertSignal');
    const filterSignal = document.getElementById('filterSignal');
    const nlmsSignal = document.getElementById('nlmsSignal');
    
    const bpmValue = document.getElementById('bpmValue');
    const bpValue = document.getElementById('bpValue');
    const spo2Value = document.getElementById('spo2Value');
    const bpmCardIcon = document.querySelector('.vital-card .bpm');

    const canvas = document.getElementById('ppgCanvas');
    const ctx = canvas.getContext('2d');

    const skinToneSelect = document.getElementById('skinToneSelect');
    const autoSkinCheckbox = document.getElementById('autoSkinCheckbox');
    const skinPhotoBtn = document.getElementById('skinPhotoBtn');
    const skinPhotoUpload = document.getElementById('skinPhotoUpload');

    // Modal de Calibración
    const calibModal = document.getElementById('calibModal');
    const saveCalibBtn = document.getElementById('saveCalibBtn');
    const closeCalibBtn = document.getElementById('closeCalibBtn');
    const calibSubjectId = document.getElementById('calibSubjectId');
    const calibS1 = document.getElementById('calibS1');
    const calibD1 = document.getElementById('calibD1');
    const calibS2 = document.getElementById('calibS2');
    const calibD2 = document.getElementById('calibD2');
    const calibS3 = document.getElementById('calibS3');
    const calibD3 = document.getElementById('calibD3');

    const skinCalibrationData = [
        { name: "Muy clara", spo2Offset: 0.0, sbpOffset: 0.0, dbpOffset: 0.0 },
        { name: "Clara", spo2Offset: 0.0, sbpOffset: 0.0, dbpOffset: 0.0 },
        { name: "Intermedia", spo2Offset: 0.2, sbpOffset: -0.5, dbpOffset: -0.2 },
        { name: "Morena", spo2Offset: 0.6, sbpOffset: -1.0, dbpOffset: -0.5 },
        { name: "Oscura", spo2Offset: 1.2, sbpOffset: -2.0, dbpOffset: -1.0 },
        { name: "Muy oscura", spo2Offset: 2.0, sbpOffset: -3.5, dbpOffset: -1.8 }
    ];

    // ---------------------------------------------------------
    // 5. Variables de Estado de la Sesión y Algoritmia
    // ---------------------------------------------------------
    let bleDevice = null;
    let bleServer = null;
    let bleService = null;
    let dataChar = null;
    let controlChar = null;
    let batteryChar = null;
    
    let isConnected = false;
    let isPreviewing = false;
    let isRecording = false;

    // Batería con Histéresis
    let batteryPct = 100;
    let batteryMv = 4200;
    let batteryLow = false;

    // Aceleración y Máquina de Estados
    let currentMotionState = "REPOSO";
    let lastValidSbp = null;
    let lastValidDbp = null;

    // Calibración Clínica Individual
    let subjectId = "SUJETO-01";
    let sbpReference = 120.0;
    let dbpReference = 80.0;
    let isClinicallyCalibrated = false;
    let calibSbpOffset = 0.0;
    let calibDbpOffset = 0.0;

    // Tono automático por DCraw
    let dcRedAvg = 100000.0;
    let dcIrAvg = 100000.0;
    let stableContactCount = 0;

    // Búferes de datos de la sesión
    let rawRedBuffer = [];
    let rawIrBuffer = [];
    let filteredRedBuffer = [];
    let filteredIrBuffer = [];
    let nlmsRedBuffer = [];
    let nlmsIrBuffer = [];
    let accelMagBuffer = [];
    let timeBuffer = [];

    // Búferes para exportación CSV (N=30)
    let recTime = [];
    let recRawRed = [];
    let recRawIr = [];
    let recFiltRed = [];
    let recFiltIr = [];
    let recNlmsRed = [];
    let recNlmsIr = [];
    let recAccelX = [];
    let recAccelY = [];
    let recAccelZ = [];
    let recAccelMag = [];
    let recMotion = [];
    let recBat = [];
    let recBpm = [];
    let recSpo2 = [];
    let recSbp = [];
    let recDbp = [];
    let recordingStartTime = 0;

    // Métricas de paquetes
    let expectedPacketSeq = 0;
    let receivedPackets = 0;
    let lostPackets = 0;
    let globalSampleIndex = 0;
    let startTime = null;
    let actualFreq = EXPECTED_FREQ;
    let lastFreqTime = 0;
    let lastFreqSamples = 0;

    // ---------------------------------------------------------
    // 6. Filtro Digital IIR Butterworth (0.5 - 8.0 Hz SOS)
    // ---------------------------------------------------------
    class ButterworthFilter {
        constructor() {
            this.sos = [
                [1.78260999e-03, 3.56521998e-03, 1.78260999e-03, -1.29176642e+00, 4.35717576e-01],
                [1.00000000e+00, 2.00000000e+00, 1.00000000e+00, -1.51260719e+00, 7.19524086e-01],
                [1.00000000e+00, -2.00000000e+00, 1.00000000e+00, -1.93751823e+00, 9.38713070e-01],
                [1.00000000e+00, -2.00000000e+00, 1.00000000e+00, -1.97736736e+00, 9.78372608e-01]
            ];
            this.reset();
        }

        reset() {
            this.states = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]];
        }

        filter(x) {
            let val = x;
            for (let i = 0; i < 4; i++) {
                const b = this.sos[i];
                const b0 = b[0], b1 = b[1], b2 = b[2], a1 = b[3], a2 = b[4];
                const w1 = this.states[i][0];
                const w2 = this.states[i][1];
                
                const w = val - a1 * w1 - a2 * w2;
                const y = b0 * w + b1 * w1 + b2 * w2;
                
                this.states[i] = [w, w1];
                val = y;
            }
            return val;
        }
    }

    // ---------------------------------------------------------
    // 7. Filtro Adaptativo NLMS (Cancelación de Ruido en JavaScript)
    // ---------------------------------------------------------
    class NLMSFilter {
        constructor(numTaps = 24, mu = 0.02, epsilon = 1e-5) {
            this.numTaps = numTaps;
            this.mu = mu;
            this.epsilon = epsilon;
            this.reset();
        }

        reset() {
            this.weights = new Float32Array(this.numTaps);
            this.buffer = new Float32Array(this.numTaps);
        }

        filter(desired, noiseRef, adapt = true) {
            // Desplazar buffer de ruido
            for (let i = this.numTaps - 1; i > 0; i--) {
                this.buffer[i] = this.buffer[i - 1];
            }
            this.buffer[0] = noiseRef;

            // Estimación del ruido acoplado
            let noiseEst = 0;
            for (let i = 0; i < this.numTaps; i++) {
                noiseEst += this.weights[i] * this.buffer[i];
            }
            const cleanSignal = desired - noiseEst;

            // Actualización de pesos adaptativos
            if (adapt) {
                let power = 0;
                for (let i = 0; i < this.numTaps; i++) {
                    power += this.buffer[i] * this.buffer[i];
                }
                power += this.epsilon;
                const step = (this.mu / power) * cleanSignal;
                for (let i = 0; i < this.numTaps; i++) {
                    this.weights[i] += step * this.buffer[i];
                }
            }
            return cleanSignal;
        }
    }

    const butterFilterRed = new ButterworthFilter();
    const butterFilterIr = new ButterworthFilter();
    const butterFilterAccel = new ButterworthFilter();
    const nlmsFilterRed = new NLMSFilter(24, 0.02);
    const nlmsFilterIr = new NLMSFilter(24, 0.02);

    // ---------------------------------------------------------
    // 8. Renderizado del Canvas (Osciloscopio a 60 FPS)
    // ---------------------------------------------------------
    function drawGrid() {
        ctx.strokeStyle = 'rgba(22, 29, 26, 0.5)';
        ctx.lineWidth = 1;
        for (let x = 0; x < canvas.width; x += 40) {
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
        }
        for (let y = 0; y < canvas.height; y += 40) {
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
        }
    }

    function renderPlot() {
        ctx.fillStyle = '#060907';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        drawGrid();

        if (!isPreviewing || timeBuffer.length === 0) {
            ctx.strokeStyle = '#555555';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(0, canvas.height / 2);
            ctx.lineTo(canvas.width, canvas.height / 2);
            ctx.stroke();
            return;
        }

        // Selección de canal a renderizar
        let displayBuffer = nlmsSignal.checked ? nlmsIrBuffer : (filterSignal.checked ? filteredIrBuffer : rawIrBuffer);
        if (displayBuffer.length === 0) return;

        const tMax = timeBuffer[timeBuffer.length - 1];
        const tMin = Math.max(0, tMax - 5);
        const activeIndices = [];
        for (let i = 0; i < timeBuffer.length; i++) {
            if (timeBuffer[i] >= tMin && timeBuffer[i] <= tMax) {
                activeIndices.push(i);
            }
        }
        if (activeIndices.length === 0) return;

        let yMin = Infinity, yMax = -Infinity;
        for (let idx of activeIndices) {
            const v = displayBuffer[idx];
            if (v < yMin) yMin = v;
            if (v > yMax) yMax = v;
        }
        const margin = (yMax - yMin) * 0.1 || 10;
        const scaleMin = yMin - margin;
        const scaleMax = yMax + margin;

        ctx.strokeStyle = nlmsSignal.checked ? '#00FFCC' : '#FF3366';
        ctx.lineWidth = 2.5;
        ctx.shadowColor = ctx.strokeStyle;
        ctx.shadowBlur = 8;
        ctx.beginPath();

        for (let i = 0; i < activeIndices.length; i++) {
            const idx = activeIndices[i];
            const t = timeBuffer[idx];
            let val = displayBuffer[idx];
            if (invertSignal.checked) val = (filterSignal.checked || nlmsSignal.checked) ? -val : (262143 - val);

            const drawX = ((t - tMin) / (tMax - tMin)) * canvas.width;
            const drawY = canvas.height - ((val - scaleMin) / (scaleMax - scaleMin)) * canvas.height;

            if (i === 0) ctx.moveTo(drawX, drawY);
            else ctx.lineTo(drawX, drawY);
        }
        ctx.stroke();
        ctx.shadowBlur = 0;
    }

    function animationLoop() {
        renderPlot();
        requestAnimationFrame(animationLoop);
    }
    animationLoop();

    // ---------------------------------------------------------
    // 9. Algoritmos de Medición y Jerarquización de Alertas
    // ---------------------------------------------------------
    function getAverage(arr) {
        if (arr.length === 0) return 0;
        let sum = 0;
        for (let v of arr) sum += v;
        return sum / arr.length;
    }

    // Ritmo Cardíaco (BPM)
    function calculateBPM() {
        const buffer = nlmsSignal.checked ? nlmsIrBuffer : filteredIrBuffer;
        if (buffer.length < 300 || timeBuffer.length < 300) return null;

        const windowSize = Math.max(5, Math.floor(actualFreq * 0.20)) | 1;
        const smoothed = [];
        for (let i = 0; i < buffer.length; i++) {
            let sum = 0, count = 0;
            const half = Math.floor(windowSize / 2);
            for (let w = -half; w <= half; w++) {
                const idx = i + w;
                if (idx >= 0 && idx < buffer.length) {
                    sum += buffer[idx]; count++;
                }
            }
            smoothed.push(sum / count);
        }

        let sMin = smoothed[0], sMax = smoothed[0];
        for (let v of smoothed) {
            if (v < sMin) sMin = v;
            if (v > sMax) sMax = v;
        }
        if (sMax - sMin < 50) return null;

        const threshold = sMin + (sMax - sMin) * 0.50;
        const minDistance = Math.floor(actualFreq * 0.40);
        const peaks = [];
        let lastPeakIdx = -minDistance;

        for (let i = 1; i < smoothed.length - 1; i++) {
            if (smoothed[i] > smoothed[i-1] && smoothed[i] > smoothed[i+1]) {
                if (smoothed[i] > threshold && (i - lastPeakIdx) >= minDistance) {
                    peaks.append ? peaks.append(i) : peaks.push(i);
                    lastPeakIdx = i;
                }
            }
        }
        if (peaks.length < 3) return null;

        const peakTimes = peaks.map(p => timeBuffer[p]);
        const intervals = [];
        for (let i = 1; i < peakTimes.length; i++) {
            intervals.push(peakTimes[i] - peakTimes[i-1]);
        }
        const validIntervals = intervals.filter(t => t >= 0.33 && t <= 1.5);
        if (validIntervals.length < 2) return null;

        validIntervals.sort((a, b) => a - b);
        const mid = Math.floor(validIntervals.length / 2);
        const medianIntervalSec = validIntervals.length % 2 === 0 ? (validIntervals[mid - 1] + validIntervals[mid]) / 2 : validIntervals[mid];
        if (medianIntervalSec === 0) return null;

        const bpm = 60.0 / medianIntervalSec;
        if (bpm >= 40 && bpm <= 180) return { bpm, peaks, smoothed };
        return null;
    }

    // =========================================================================
    // CORRECCIÓN TÉCNICA: INDEXACIÓN DE ARRAY EN SELECTOR DE PIEL DEL DOM
    // =========================================================================
    function getSelectedSkinIndex() {
        if (!skinToneSelect) return 2;
        const val = parseInt(skinToneSelect.value, 10);
        return isNaN(val) ? 2 : val; // PRESERVA EL ÍNDICE 0 ("Muy clara")
    }

    // Estimación de Presión Arterial (PWA con Calibración Clínica)
    function estimateBloodPressure(bpm, peaks, smoothed) {
        if (!bpm || peaks.length < 3 || timeBuffer.length < Math.max(...peaks)) return null;

        const valleys = [];
        for (let idx = 0; idx < peaks.length - 1; idx++) {
            const start = peaks[idx], end = peaks[idx + 1];
            if (start >= end) continue;
            let minVal = smoothed[start], minIndex = start;
            for (let s = start; s < end; s++) {
                if (smoothed[s] < minVal) { minVal = smoothed[s]; minIndex = s; }
            }
            valleys.push(minIndex);
        }
        if (valleys.length < 2) return null;

        const riseTimes = [], fallTimes = [];
        for (let v of valleys) {
            const postPeaks = peaks.filter(p => p > v);
            if (postPeaks.length === 0) continue;
            const p = postPeaks[0];
            const postValleys = valleys.filter(nv => nv > p);
            if (postValleys.length === 0) continue;
            const nv = postValleys[0];

            riseTimes.push(timeBuffer[p] - timeBuffer[v]);
            fallTimes.push(timeBuffer[nv] - timeBuffer[p]);
        }
        if (riseTimes.length === 0 || fallTimes.length === 0) return null;

        const avgRise = getAverage(riseTimes);
        const avgFall = getAverage(fallTimes);

        // Corrección de tono de piel con índice corregido
        const skinIdx = getSelectedSkinIndex();
        const cal = skinCalibrationData[skinIdx];
        
        let sbp = 120.0 + 0.15 * (bpm - 70.0) - 75.0 * (avgRise - 0.12) + cal.sbpOffset;
        let dbp = 80.0 + 0.08 * (bpm - 70.0) - 25.0 * (avgFall - 0.35) + cal.dbpOffset;

        // Aplicación del offset clínico individual de esfigmomanómetro
        if (isClinicallyCalibrated) {
            sbp += calibSbpOffset;
            dbp += calibDbpOffset;
        }

        // Diferencial mínimo seguro
        if (sbp <= dbp + 20) sbp = dbp + 25;

        return { sbp: Math.round(sbp), dbp: Math.round(dbp) };
    }

    // Saturación de Oxígeno (SpO2)
    function calculateSpO2() {
        if (rawRedBuffer.length < 300 || rawIrBuffer.length < 300) return null;
        const redRaw = rawRedBuffer.slice(-300);
        const irRaw = rawIrBuffer.slice(-300);
        const redFilt = filteredRedBuffer.slice(-300);
        const irFilt = filteredIrBuffer.slice(-300);

        const dcRed = getAverage(redRaw);
        const dcIr = getAverage(irRaw);
        if (dcRed === 0 || dcIr === 0) return null;

        const smooth = (arr) => {
            const res = [];
            for (let i = 0; i < arr.length; i++) {
                let sum = 0, count = 0;
                for (let w = -2; w <= 2; w++) {
                    if (i+w >= 0 && i+w < arr.length) { sum += arr[i+w]; count++; }
                }
                res.push(sum / count);
            }
            return res;
        };

        const redSmooth = smooth(redFilt);
        const irSmooth = smooth(irFilt);
        const acRed = Math.max(...redSmooth) - Math.min(...redSmooth);
        const acIr = Math.max(...irSmooth) - Math.min(...irSmooth);
        if (acIr === 0) return null;

        const r = (acRed / dcRed) / (acIr / dcIr);
        const skinIdx = getSelectedSkinIndex();
        const cal = skinCalibrationData[skinIdx];
        let spo2 = 104.0 - 17.0 * r + cal.spo2Offset;
        return Math.max(75.0, Math.min(100.0, spo2));
    }

    function clearDiagnosticDisplays() {
        bpmValue.textContent = '--';
        bpValue.textContent = '-- / --';
        spo2Value.textContent = '--';
        bpmCardIcon.classList.remove('heart-beating');
    }

    // Bucle Periódico de Diagnóstico y Jerarquía de Seguridad
    function runDiagnosticAlgorithms() {
        if (!isPreviewing) {
            clearDiagnosticDisplays();
            return;
        }

        // 1. Alerta de Batería Baja (< 3.4V con histéresis a 3.5V)
        if (batteryLow) {
            alertBanner.textContent = '⚠️ BATERÍA BAJA (<3.4V) - CARGUE EL DISPOSITIVO (Pausa de seguridad)';
            alertBanner.className = 'alert-banner alert-battery';
            bpValue.textContent = 'PAUSA';
            return;
        }

        // 2. Detección de Contacto Cutáneo (Umbral > 20,000 unidades en IR)
        if (rawIrBuffer.length > 0) {
            const recentIr = rawIrBuffer.slice(-50);
            const avgIr = getAverage(recentIr);
            if (avgIr < 20000) {
                skinContact.textContent = '⚠️ Sin contacto';
                skinContact.className = 'status-badge status-warning';
                alertBanner.textContent = '⚠️ SIN CONTACTO DE PIEL - COLOQUE EL SENSOR EN LA MUÑECA';
                alertBanner.className = 'alert-banner alert-warning';
                clearDiagnosticDisplays();
                return;
            } else {
                skinContact.textContent = '✔️ Con piel';
                skinContact.className = 'status-badge status-contact';
            }
        }

        // 3. Máquina de Estados de Movimiento
        if (currentMotionState === "MOVIMIENTO_FUERTE") {
            motionStatus.textContent = '🔴 Artefacto';
            motionStatus.className = 'status-badge status-warning';
            alertBanner.textContent = '⚠️ MANTÉN LA MUÑECA QUIETA - ARTEFACTO DE MOVIMIENTO DETECTADO';
            alertBanner.className = 'alert-banner alert-warning';
            if (lastValidSbp && lastValidDbp) {
                bpValue.textContent = `${lastValidSbp} / ${lastValidDbp}*`;
            }
            return;
        } else if (currentMotionState === "MOVIMIENTO_LEVE") {
            motionStatus.textContent = '🟡 Filtrando NLMS';
            motionStatus.className = 'status-badge status-contact';
            alertBanner.textContent = '🟡 FILTRANDO MOVIMIENTO - CANCELACIÓN ADAPTATIVA NLMS ACTIVA';
            alertBanner.className = 'alert-banner alert-nlms';
        } else {
            motionStatus.textContent = '🟢 Reposo';
            motionStatus.className = 'status-badge status-contact';
            alertBanner.textContent = '🟢 MONITOREO CONTINUO ACTIVO - ADQUISICIÓN ESTABLE';
            alertBanner.className = 'alert-banner alert-normal';
        }

        // Cálculos normales
        const bpmResult = calculateBPM();
        if (bpmResult) {
            const roundedBpm = Math.round(bpmResult.bpm);
            bpmValue.textContent = roundedBpm;
            bpmCardIcon.classList.add('heart-beating');

            const bpResult = estimateBloodPressure(roundedBpm, bpmResult.peaks, bpmResult.smoothed);
            if (bpResult) {
                bpValue.textContent = `${bpResult.sbp} / ${bpResult.dbp}`;
                lastValidSbp = bpResult.sbp;
                lastValidDbp = bpResult.dbp;
            } else {
                bpValue.textContent = '-- / --';
            }
        } else {
            bpmValue.textContent = '--';
            bpValue.textContent = '-- / --';
            bpmCardIcon.classList.remove('heart-beating');
        }

        const spo2 = calculateSpO2();
        if (spo2 !== null) {
            spo2Value.textContent = Math.round(spo2);
        } else {
            spo2Value.textContent = '--';
        }
    }
    setInterval(runDiagnosticAlgorithms, 1000);

    // ---------------------------------------------------------
    // 10. Desempaquetado de Telemetría (20 Bytes) y Filtrado
    // ---------------------------------------------------------
    function processIncomingSamples(redSamples, irSamples, packetSeq, accelX, accelY, accelZ) {
        if (!isPreviewing) return;
        if (startTime === null) startTime = performance.now();

        const packetTime = (performance.now() - startTime) / 1000;

        // Pérdida de paquetes
        if (expectedPacketSeq === 0) {
            expectedPacketSeq = (packetSeq + 1) & 0xFFFF;
        } else if (packetSeq > expectedPacketSeq) {
            lostPackets += (packetSeq - expectedPacketSeq);
        }
        expectedPacketSeq = (packetSeq + 1) & 0xFFFF;
        receivedPackets++;

        const lossRate = (lostPackets / (receivedPackets + lostPackets)) * 100;
        packetLoss.textContent = `📉 Pérdida: ${lossRate.toFixed(1)}%`;

        // Medición de frecuencia real
        const nowTime = performance.now();
        const elapsed = (nowTime - lastFreqTime) / 1000;
        if (elapsed >= 1.0) {
            actualFreq = (globalSampleIndex - lastFreqSamples) / elapsed;
            lastFreqTime = nowTime;
            lastFreqSamples = globalSampleIndex;
            deviceSampling.textContent = `📊 Muestreo: ${actualFreq.toFixed(1)} Hz`;
        }

        // Magnitud de Aceleración y Filtrado Butterworth Pasabanda
        const accelMag = Math.sqrt(accelX*accelX + accelY*accelY + accelZ*accelZ) - 1.0;
        const accelFilt = butterFilterAccel.filter(accelMag);

        // Máquina de estados de movimiento
        const absAccel = Math.abs(accelFilt);
        let adaptNLMS = false;
        if (absAccel < 0.08) {
            currentMotionState = "REPOSO";
            adaptNLMS = false; // Congelar pesos en reposo
        } else if (absAccel <= 0.35) {
            currentMotionState = "MOVIMIENTO_LEVE";
            adaptNLMS = nlmsSignal.checked;
        } else {
            currentMotionState = "MOVIMIENTO_FUERTE";
            adaptNLMS = false;
        }

        const dt = 1.0 / (actualFreq > 10 ? actualFreq : EXPECTED_FREQ);
        const numSamples = redSamples.length;

        for (let idx = 0; idx < numSamples; idx++) {
            const redRaw = redSamples[idx];
            const irRaw = irSamples[idx];

            const redFilt = filterSignal.checked ? butterFilterRed.filter(redRaw) : redRaw;
            const irFilt = filterSignal.checked ? butterFilterIr.filter(irRaw) : irRaw;

            // Filtro NLMS
            const redNlms = nlmsSignal.checked ? nlmsFilterRed.filter(redFilt, accelFilt, adaptNLMS) : redFilt;
            const irNlms = nlmsSignal.checked ? nlmsFilterIr.filter(irFilt, accelFilt, adaptNLMS) : irFilt;

            rawRedBuffer.push(redRaw);
            rawIrBuffer.push(irRaw);
            filteredRedBuffer.push(redFilt);
            filteredIrBuffer.push(irFilt);
            nlmsRedBuffer.push(redNlms);
            nlmsIrBuffer.push(irNlms);
            accelMagBuffer.push(accelFilt);

            const t = packetTime - (numSamples - 1 - idx) * dt;
            timeBuffer.push(t);

            if (rawRedBuffer.length > BUFFER_LIMIT) {
                rawRedBuffer.shift(); rawIrBuffer.shift();
                filteredRedBuffer.shift(); filteredIrBuffer.shift();
                nlmsRedBuffer.shift(); nlmsIrBuffer.shift();
                accelMagBuffer.shift(); timeBuffer.shift();
            }

            // Calibración automática de piel por DCraw
            if (irRaw > 20000 && currentMotionState === "REPOSO") {
                dcRedAvg = 0.998 * dcRedAvg + 0.002 * redRaw;
                dcIrAvg = 0.998 * dcIrAvg + 0.002 * irRaw;
                stableContactCount++;
                if (stableContactCount >= 500 && autoSkinCheckbox.checked) {
                    const ratio = dcRedAvg / dcIrAvg;
                    let autoIdx = 2;
                    if (ratio > 1.25) autoIdx = 0;
                    else if (ratio > 1.10) autoIdx = 1;
                    else if (ratio > 0.95) autoIdx = 2;
                    else if (ratio > 0.80) autoIdx = 3;
                    else if (ratio > 0.65) autoIdx = 4;
                    else autoIdx = 5;

                    if (skinToneSelect.value !== autoIdx.toString()) {
                        skinToneSelect.value = autoIdx.toString();
                        console.log(`Auto DCraw ajustó el tono de piel a: ${skinCalibrationData[autoIdx].name} (Ratio=${ratio.toFixed(2)})`);
                    }
                }
            } else {
                stableContactCount = 0;
            }

            // Grabación de estudio
            if (isRecording) {
                if (recTime.length === 0) recordingStartTime = t;
                const recT = t - recordingStartTime;
                recTime.push(recT);
                recRawRed.push(redRaw);
                recRawIr.push(irRaw);
                recFiltRed.push(redFilt);
                recFiltIr.push(irFilt);
                recNlmsRed.push(redNlms);
                recNlmsIr.push(irNlms);
                recAccelX.push(accelX);
                recAccelY.push(accelY);
                recAccelZ.push(accelZ);
                recAccelMag.push(accelFilt);
                recMotion.push(currentMotionState);
                recBat.push(batteryPct);
                recBpm.push(parseFloat(bpmValue.textContent) || 0);
                recSpo2.push(parseFloat(spo2Value.textContent) || 0);
                recSbp.push(lastValidSbp || 0);
                recDbp.push(lastValidDbp || 0);

                samplesCollected.textContent = `📋 Muestras: ${recTime.length}`;
                const maxDur = parseFloat(studyDuration.value) || 10.0;
                const percent = (recT / maxDur) * 100;
                progressBar.style.width = `${Math.min(100, percent)}%`;

                if (recT >= maxDur) stopStudyRecording();
            }
            globalSampleIndex++;
        }
    }

    // Callback de notificación GATT
    function onNotificationReceived(event) {
        const view = event.target.value;
        const len = view.byteLength;
        if (len < 14) return;

        const red1 = (view.getUint8(0) << 16) | (view.getUint8(1) << 8) | view.getUint8(2);
        const ir1 = (view.getUint8(3) << 16) | (view.getUint8(4) << 8) | view.getUint8(5);
        const red2 = (view.getUint8(6) << 16) | (view.getUint8(7) << 8) | view.getUint8(8);
        const ir2 = (view.getUint8(9) << 16) | (view.getUint8(10) << 8) | view.getUint8(11);
        const seq = view.getUint16(12, true);

        // Decodificación de Aceleración triaxial (int16 Little Endian, escala ±2g)
        let ax = 0.0, ay = 0.0, az = 1.0;
        if (len >= 20) {
            ax = view.getInt16(14, true) / 16384.0;
            ay = view.getInt16(16, true) / 16384.0;
            az = view.getInt16(18, true) / 16384.0;
        }

        processIncomingSamples([red1, red2], [ir1, ir2], seq, ax, ay, az);
    }

    function onBatteryNotification(event) {
        const view = event.target.value;
        if (view.byteLength >= 4) {
            batteryPct = view.getUint8(0);
            batteryMv = view.getUint16(1, true);
            const flags = view.getUint8(3);
            const voltage = batteryMv / 1000.0;
            batteryStatus.textContent = `🔋 Batería: ${batteryPct}% (${voltage.toFixed(2)} V)`;

            // Histéresis de seguridad
            if (voltage < 3.40) {
                batteryLow = true;
                batteryStatus.className = 'status-badge status-warning';
            } else if (voltage >= 3.50) {
                batteryLow = false;
                batteryStatus.className = 'status-badge status-battery';
            }
        }
    }

    // ---------------------------------------------------------
    // 11. Conexión Web Bluetooth (GATT) y Auto-Reconexión
    // ---------------------------------------------------------
    async function connectToDevice() {
        connectionStatus.textContent = '🟡 Vinculando...';
        connectionStatus.className = 'status-badge status-connecting';
        
        try {
            bleDevice = await navigator.bluetooth.requestDevice({
                filters: [{ namePrefix: 'Tensiometro_' }],
                optionalServices: [SERVICE_UUID, "battery_service"]
            });

            bleDevice.addEventListener('gattserverdisconnected', onDeviceDisconnected);

            bleServer = await bleDevice.gatt.connect();
            bleService = await bleServer.getPrimaryService(SERVICE_UUID);

            dataChar = await bleService.getCharacteristic(DATA_CHAR_UUID);
            controlChar = await bleService.getCharacteristic(CONTROL_CHAR_UUID);

            try {
                batteryChar = await bleService.getCharacteristic(BATTERY_CHAR_UUID);
                await batteryChar.startNotifications();
                batteryChar.addEventListener('characteristicvaluechanged', onBatteryNotification);
            } catch (bErr) {
                console.log('Característica de batería no disponible en este hardware:', bErr);
            }

            await dataChar.startNotifications();
            dataChar.addEventListener('characteristicvaluechanged', onNotificationReceived);

            const encoder = new TextEncoder();
            await controlChar.writeValue(encoder.encode("START"));

            butterFilterRed.reset();
            butterFilterIr.reset();
            butterFilterAccel.reset();
            nlmsFilterRed.reset();
            nlmsFilterIr.reset();

            startTime = null;
            expectedPacketSeq = 0;
            receivedPackets = 0;
            lostPackets = 0;
            globalSampleIndex = 0;
            lastFreqTime = performance.now();
            lastFreqSamples = 0;

            isConnected = true;
            isPreviewing = true;

            connectionStatus.textContent = '🟢 Conectado';
            connectionStatus.className = 'status-badge status-connected';
            connectBleBtn.disabled = true;
            disconnectBleBtn.disabled = false;
            startStudyBtn.disabled = false;
            exportCsvBtn.disabled = true;

        } catch (error) {
            console.error('Error de enlace Web Bluetooth:', error);
            cleanupBLEState();
            alert(`Fallo de conexión: ${error.message || error}`);
        }
    }

    function onDeviceDisconnected() {
        console.log('Enlace Bluetooth interrumpido. Reintentando o limpiando...');
        cleanupBLEState();
    }

    async function disconnectFromDevice() {
        if (!bleDevice) return;
        try {
            if (controlChar) {
                const encoder = new TextEncoder();
                await controlChar.writeValue(encoder.encode("STOP"));
            }
            if (dataChar) await dataChar.stopNotifications();
            if (batteryChar) await batteryChar.stopNotifications();
        } catch (e) {}
        if (bleServer && bleServer.connected) bleServer.disconnect();
        cleanupBLEState();
    }

    function cleanupBLEState() {
        bleDevice = null; bleServer = null; bleService = null; dataChar = null; controlChar = null; batteryChar = null;
        isConnected = false; isPreviewing = false; isRecording = false;

        butterFilterRed.reset(); butterFilterIr.reset(); nlmsFilterRed.reset(); nlmsFilterIr.reset();
        rawRedBuffer = []; rawIrBuffer = []; filteredRedBuffer = []; filteredIrBuffer = [];
        nlmsRedBuffer = []; nlmsIrBuffer = []; accelMagBuffer = []; timeBuffer = [];

        connectionStatus.textContent = '🔴 Desconectado';
        connectionStatus.className = 'status-badge status-disconnected';
        skinContact.textContent = '⚠️ Sin contacto';
        skinContact.className = 'status-badge status-warning';
        motionStatus.textContent = '🏃 Reposo';
        batteryStatus.textContent = '🔋 Batería: --%';
        deviceSampling.textContent = '📊 Muestreo: -- Hz';
        packetLoss.textContent = '📉 Pérdida: --%';
        
        connectBleBtn.disabled = false;
        disconnectBleBtn.disabled = true;
        startStudyBtn.disabled = true;
        progressBarContainer.style.display = 'none';
        clearDiagnosticDisplays();
    }

    // ---------------------------------------------------------
    // 12. Grabación y Exportación CSV Extendida (N=30)
    // ---------------------------------------------------------
    function startStudyRecording() {
        if (!isConnected) return;
        recTime = []; recRawRed = []; recRawIr = []; recFiltRed = []; recFiltIr = [];
        recNlmsRed = []; recNlmsIr = []; recAccelX = []; recAccelY = []; recAccelZ = [];
        recAccelMag = []; recMotion = []; recBat = []; recBpm = []; recSpo2 = [];
        recSbp = []; recDbp = [];

        isRecording = true;
        startStudyBtn.disabled = true;
        startStudyBtn.textContent = '🔴 Grabando...';
        studyDuration.disabled = true;
        exportCsvBtn.disabled = true;
        progressBarContainer.style.display = 'block';
        progressBar.style.width = '0%';
    }

    function stopStudyRecording() {
        isRecording = false;
        startStudyBtn.disabled = false;
        startStudyBtn.textContent = '▶️ Iniciar Grabación';
        studyDuration.disabled = false;
        exportCsvBtn.disabled = false;
        alert(`✅ Grabación de estudio completada (${recTime.length} muestras).\nPresione "Exportar CSV" para descargar el reporte.`);
    }

    function exportToCSV() {
        if (recTime.length === 0) {
            alert('No hay datos grabados para exportar.');
            return;
        }

        let csvContent = "data:text/csv;charset=utf-8,";
        // Banderas de configuración
        csvContent += "# =====================================================================\n";
        csvContent += "# REPORTE CLINICO TENSIOMETRO DIGITAL v2.0 (ARCADIA)\n";
        csvContent += `# ID_Sujeto: ${subjectId}, Fecha: ${new Date().toISOString()}\n`;
        csvContent += `# SBP_Referencia: ${sbpReference.toFixed(1)} mmHg, DBP_Referencia: ${dbpReference.toFixed(1)} mmHg\n`;
        csvContent += `# Filtro_Butterworth: ACTIVO (0.5-8Hz), Filtro_NLMS: ${nlmsSignal.checked ? 'ACTIVO (24 taps)' : 'DESACTIVADO'}, Tono_Piel: ${skinCalibrationData[getSelectedSkinIndex()].name}\n`;
        csvContent += "# =====================================================================\n";
        
        csvContent += "Tiempo_s,PPG_Rojo_Cruda,PPG_IR_Cruda,PPG_Rojo_Butterworth,PPG_IR_Butterworth,PPG_Rojo_Limpia_NLMS,PPG_IR_Limpia_NLMS,Accel_X_g,Accel_Y_g,Accel_Z_g,Accel_Mag_g,Estado_Movimiento,BPM_Estimado,SpO2_Estimado,SBP_Estimado,DBP_Estimado,Bateria_pct,ID_Sujeto,SBP_Referencia,DBP_Referencia\n";

        for (let i = 0; i < recTime.length; i++) {
            const row = [
                recTime[i].toFixed(4),
                recRawRed[i],
                recRawIr[i],
                recFiltRed[i].toFixed(2),
                recFiltIr[i].toFixed(2),
                recNlmsRed[i].toFixed(2),
                recNlmsIr[i].toFixed(2),
                recAccelX[i].toFixed(4),
                recAccelY[i].toFixed(4),
                recAccelZ[i].toFixed(4),
                recAccelMag[i].toFixed(4),
                recMotion[i],
                recBpm[i].toFixed(1),
                recSpo2[i].toFixed(1),
                recSbp[i],
                recDbp[i],
                recBat[i],
                subjectId,
                sbpReference.toFixed(1),
                dbpReference.toFixed(1)
            ];
            csvContent += row.join(",") + "\n";
        }

        const encodedUri = encodeURI(csvContent);
        const link = document.createElement("a");
        link.setAttribute("href", encodedUri);
        link.setAttribute("download", `estudio_${subjectId}_${new Date().toISOString().slice(0,10)}.csv`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    // ---------------------------------------------------------
    // 13. Modal de Calibración Clínica Individual
    // ---------------------------------------------------------
    if (calibModalBtn) {
        calibModalBtn.addEventListener('click', () => {
            calibModal.style.display = 'flex';
        });
    }

    if (closeCalibBtn) {
        closeCalibBtn.addEventListener('click', () => {
            calibModal.style.display = 'none';
        });
    }

    if (saveCalibBtn) {
        saveCalibBtn.addEventListener('click', () => {
            subjectId = calibSubjectId.value.trim() || "SUJETO-01";
            const s1 = parseFloat(calibS1.value) || 120;
            const d1 = parseFloat(calibD1.value) || 80;
            const s2 = parseFloat(calibS2.value) || 120;
            const d2 = parseFloat(calibD2.value) || 80;
            const s3 = parseFloat(calibS3.value) || 120;
            const d3 = parseFloat(calibD3.value) || 80;

            sbpReference = (s1 + s2 + s3) / 3.0;
            dbpReference = (d1 + d2 + d3) / 3.0;
            isClinicallyCalibrated = true;
            calibSbpOffset = sbpReference - 120.0;
            calibDbpOffset = dbpReference - 80.0;

            calibModal.style.display = 'none';
            alert(`✅ Calibración Clínica Aplicada:\n\nSujeto: ${subjectId}\nPromedio SBP Ref: ${sbpReference.toFixed(1)} mmHg\nPromedio DBP Ref: ${dbpReference.toFixed(1)} mmHg\nOffset SBP: ${calibSbpOffset.toFixed(1)} | Offset DBP: ${calibDbpOffset.toFixed(1)}`);
        });
    }

    // ---------------------------------------------------------
    // 14. Asignación de Listeners a los Controles
    // ---------------------------------------------------------
    if (connectBleBtn) connectBleBtn.addEventListener('click', connectToDevice);
    if (disconnectBleBtn) disconnectBleBtn.addEventListener('click', disconnectFromDevice);
    if (startStudyBtn) startStudyBtn.addEventListener('click', startStudyRecording);
    if (exportCsvBtn) exportCsvBtn.addEventListener('click', exportToCSV);

    if (skinPhotoBtn) {
        skinPhotoBtn.addEventListener('click', () => {
            if (skinPhotoUpload) skinPhotoUpload.click();
        });
    }

    if (skinPhotoUpload) {
        skinPhotoUpload.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = function(event) {
                const img = new Image();
                img.onload = function() {
                    const tempCanvas = document.createElement('canvas');
                    const tempCtx = tempCanvas.getContext('2d');
                    tempCanvas.width = img.width; tempCanvas.height = img.height;
                    tempCtx.drawImage(img, 0, 0);

                    const cx = Math.floor(img.width / 2), cy = Math.floor(img.height / 2);
                    const rx = Math.max(0, cx - 50), ry = Math.max(0, cy - 50);
                    const rw = Math.min(100, img.width - rx), rh = Math.min(100, img.height - ry);
                    const pixels = tempCtx.getImageData(rx, ry, rw, rh).data;

                    let sumR = 0, sumG = 0, sumB = 0, count = 0;
                    for (let i = 0; i < pixels.length; i += 4) {
                        sumR += pixels[i]; sumG += pixels[i+1]; sumB += pixels[i+2]; count++;
                    }
                    const rNorm = (sumR / count) / 255.0;
                    const gNorm = (sumG / count) / 255.0;
                    const bNorm = (sumB / count) / 255.0;

                    const pivot = v => v > 0.04045 ? Math.pow((v + 0.055) / 1.055, 2.4) : v / 12.92;
                    const rp = pivot(rNorm), gp = pivot(gNorm), bp = pivot(bNorm);

                    let x = rp * 0.4124564 + gp * 0.3575761 + bp * 0.1804375;
                    let y = rp * 0.2126729 + gp * 0.7151522 + bp * 0.0721750;
                    let z = rp * 0.0193339 + gp * 0.1191920 + bp * 0.9503041;
                    x /= 0.950489; y /= 1.000000; z /= 1.088840;

                    const f = t => t > 0.008856 ? Math.pow(t, 1/3) : 7.787 * t + 16/116;
                    const lStar = 116 * f(y) - 16;
                    const bStar = 200 * (f(y) - f(z)) || 0.001;
                    const ita = Math.atan((lStar - 50) / bStar) * (180 / Math.PI);

                    let idx = 2;
                    if (ita > 55) idx = 0;
                    else if (ita > 41) idx = 1;
                    else if (ita > 28) idx = 2;
                    else if (ita > 10) idx = 3;
                    else if (ita > -30) idx = 4;
                    else idx = 5;

                    skinToneSelect.value = idx.toString();
                    autoSkinCheckbox.checked = false; // Priorizar elección del usuario
                    alert(`Tono Detectado: ${skinCalibrationData[idx].name} (ITA=${ita.toFixed(1)}°)`);
                };
                img.src = event.target.result;
            };
            reader.readAsDataURL(file);
        });
    }
});
