// Lógica de interacción y Web Bluetooth para la Landing Page de la Estación Médica

document.addEventListener('DOMContentLoaded', () => {
    // ---------------------------------------------------------
    // 1. Lógica del Botón Copiar Código Arduino (Existente)
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
                console.error('Error al copiar el código al portapapeles: ', err);
                const textArea = document.createElement('textarea');
                textArea.value = codeText;
                textArea.style.position = 'fixed';
                document.body.appendChild(textArea);
                textArea.focus();
                textArea.select();
                try {
                    const successful = document.execCommand('copy');
                    if (successful) {
                        copyBtn.textContent = '✓ ¡Copiado!';
                        copyBtn.classList.add('copied');
                        setTimeout(() => {
                            copyBtn.textContent = '📋 Copiar Código';
                            copyBtn.classList.remove('copied');
                        }, 2500);
                    } else {
                        copyBtn.textContent = '❌ Error al copiar';
                    }
                } catch (fallbackErr) {
                    console.error('Fallback fallido:', fallbackErr);
                    copyBtn.textContent = '❌ No soportado';
                }
                document.body.removeChild(textArea);
            }
        });
    }

    // ---------------------------------------------------------
    // 2. Control Spy en Barra de Navegación (Existente)
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
    const DATA_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"; // Notificaciones
    const CONTROL_CHAR_UUID = "12345678-1234-1234-1234-123456789abc"; // Control START/STOP
    const EXPECTED_FREQ = 100.0;
    const BUFFER_LIMIT = 500; // Mantener últimos 5 segundos de datos

    // ---------------------------------------------------------
    // 4. Elementos del DOM del Monitor
    // ---------------------------------------------------------
    const connectBleBtn = document.getElementById('connectBleBtn');
    const disconnectBleBtn = document.getElementById('disconnectBleBtn');
    const startStudyBtn = document.getElementById('startStudyBtn');
    const exportCsvBtn = document.getElementById('exportCsvBtn');
    const connectionStatus = document.getElementById('connectionStatus');
    const skinContact = document.getElementById('skinContact');
    const deviceSampling = document.getElementById('deviceSampling');
    const packetLoss = document.getElementById('packetLoss');
    const samplesCollected = document.getElementById('samplesCollected');
    const studyDuration = document.getElementById('studyDuration');
    const progressBarContainer = document.getElementById('progressBarContainer');
    const progressBar = document.getElementById('progressBar');
    
    // Checkboxes del canvas
    const invertSignal = document.getElementById('invertSignal');
    const filterSignal = document.getElementById('filterSignal');
    
    // Displays de variables clínicas en las tarjetas
    const bpmValue = document.getElementById('bpmValue');
    const bpValue = document.getElementById('bpValue');
    const spo2Value = document.getElementById('spo2Value');
    const bpmCardIcon = document.querySelector('.vital-card .bpm');

    // Canvas del Osciloscopio
    const canvas = document.getElementById('ppgCanvas');
    const ctx = canvas.getContext('2d');

    // Calibración de Piel
    const skinToneSelect = document.getElementById('skinToneSelect');
    const skinPhotoBtn = document.getElementById('skinPhotoBtn');
    const skinPhotoUpload = document.getElementById('skinPhotoUpload');

    const skinCalibrationData = [
        { name: "Muy clara", spo2Offset: 0.0, sbpOffset: 0.0, dbpOffset: 0.0 },
        { name: "Clara", spo2Offset: 0.0, sbpOffset: 0.0, dbpOffset: 0.0 },
        { name: "Intermedia", spo2Offset: 0.2, sbpOffset: -0.5, dbpOffset: -0.2 },
        { name: "Morena", spo2Offset: 0.6, sbpOffset: -1.0, dbpOffset: -0.5 },
        { name: "Oscura", spo2Offset: 1.2, sbpOffset: -2.0, dbpOffset: -1.0 },
        { name: "Muy oscura", spo2Offset: 2.0, sbpOffset: -3.5, dbpOffset: -1.8 }
    ];

    // ---------------------------------------------------------
    // 5. Variables de Estado de la Sesión
    // ---------------------------------------------------------
    let bleDevice = null;
    let bleServer = null;
    let bleService = null;
    let dataChar = null;
    let controlChar = null;
    
    let isConnected = false;
    let isPreviewing = false;
    let isRecording = false;

    // Búferes de datos de la sesión
    let rawRedBuffer = [];
    let rawIrBuffer = [];
    let filteredRedBuffer = [];
    let filteredIrBuffer = [];
    let timeBuffer = [];

    // Búferes especiales de grabación
    let recordingTime = [];
    let recordingRawRed = [];
    let recordingRawIr = [];
    let recordingFilteredRed = [];
    let recordingFilteredIr = [];
    let recordingStartTime = 0;

    // Métricas de paquetes
    let expectedPacketSeq = 0;
    let receivedPackets = 0;
    let lostPackets = 0;
    let duplicatePackets = 0;
    
    // Mediciones de frecuencia y tiempos
    let globalSampleIndex = 0;
    let startTime = null;
    let actualFreq = EXPECTED_FREQ;
    let lastFreqTime = 0;
    let lastFreqSamples = 0;

    // ---------------------------------------------------------
    // 6. Clase Filtro Butterworth (Real-Time IIR Bandpass 0.5 - 8.0 Hz)
    // ---------------------------------------------------------
    class ButterworthFilter {
        constructor() {
            // Coeficientes SOS para fs=100Hz, f_low=0.5Hz, f_high=8.0Hz
            this.sos = [
                [1.78260999e-03, 3.56521998e-03, 1.78260999e-03, -1.29176642e+00, 4.35717576e-01],
                [1.00000000e+00, 2.00000000e+00, 1.00000000e+00, -1.51260719e+00, 7.19524086e-01],
                [1.00000000e+00, -2.00000000e+00, 1.00000000e+00, -1.93751823e+00, 9.38713070e-01],
                [1.00000000e+00, -2.00000000e+00, 1.00000000e+00, -1.97736736e+00, 9.78372608e-01]
            ];
            this.reset();
        }

        reset() {
            this.states = [
                [0.0, 0.0],
                [0.0, 0.0],
                [0.0, 0.0],
                [0.0, 0.0]
            ];
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

    const butterFilterRed = new ButterworthFilter();
    const butterFilterIr = new ButterworthFilter();

    // ---------------------------------------------------------
    // 7. Configuración del Canvas (Osciloscopio)
    // ---------------------------------------------------------
    function drawGrid() {
        ctx.strokeStyle = 'rgba(22, 29, 26, 0.5)';
        ctx.lineWidth = 1;
        
        // Dibujar líneas verticales
        for (let x = 0; x < canvas.width; x += 40) {
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, canvas.height);
            ctx.stroke();
        }
        
        // Dibujar líneas horizontales
        for (let y = 0; y < canvas.height; y += 40) {
            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(canvas.width, y);
            ctx.stroke();
        }
    }

    function renderPlot() {
        // Limpiar el canvas
        ctx.fillStyle = '#060907';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        
        // Dibujar cuadrícula médica fluorescente
        drawGrid();

        if (!isPreviewing || timeBuffer.length === 0) {
            // Si está desconectado, dibujar línea plana de estado de espera
            ctx.strokeStyle = '#555555';
            ctx.lineWidth = 2;
            ctx.shadowBlur = 0;
            ctx.beginPath();
            ctx.moveTo(0, canvas.height / 2);
            ctx.lineTo(canvas.width, canvas.height / 2);
            ctx.stroke();
            return;
        }

        // Obtener búfer a dibujar (IR filtrado o crudo)
        const isFilterEnabled = filterSignal.checked;
        const sourceBuffer = isFilterEnabled ? filteredIrBuffer : rawIrBuffer;
        
        if (sourceBuffer.length < 2) return;

        // Calcular mínimos y máximos para el escalado dinámico
        let yMin = sourceBuffer[0];
        let yMax = sourceBuffer[0];
        for (let i = 1; i < sourceBuffer.length; i++) {
            if (sourceBuffer[i] < yMin) yMin = sourceBuffer[i];
            if (sourceBuffer[i] > yMax) yMax = sourceBuffer[i];
        }
        let yRange = yMax - yMin;
        if (yRange < 1.0) yRange = 1.0;

        // Inversión visual de la señal si está seleccionado
        const isInvert = invertSignal.checked;

        // Trazado de la onda
        ctx.strokeStyle = '#88C0A0'; // Verde brillante osciloscopio
        ctx.lineWidth = 2.5;
        ctx.shadowColor = '#88C0A0';
        ctx.shadowBlur = 8;
        ctx.beginPath();

        const stepX = canvas.width / BUFFER_LIMIT;
        
        for (let i = 0; i < sourceBuffer.length; i++) {
            const val = sourceBuffer[i];
            // Normalizar entre 0 y 1
            let normY = (val - yMin) / yRange;
            
            // Si se invierte
            if (isInvert) {
                normY = 1.0 - normY;
            }

            // Mapear al canvas (dejando 15% de margen superior e inferior)
            const margin = canvas.height * 0.15;
            const drawY = canvas.height - (normY * (canvas.height - 2 * margin) + margin);
            const drawX = i * stepX;

            if (i === 0) {
                ctx.moveTo(drawX, drawY);
            } else {
                ctx.lineTo(drawX, drawY);
            }
        }
        ctx.stroke();
        
        // Resetear sombra para que no afecte a otros elementos
        ctx.shadowBlur = 0;
    }

    // Ejecutar render de osciloscopio constantemente
    function animationLoop() {
        renderPlot();
        requestAnimationFrame(animationLoop);
    }
    animationLoop(); // Iniciar bucle del canvas

    // ---------------------------------------------------------
    // 8. Algoritmos de Medición Cardiovascular
    // ---------------------------------------------------------

    // Función promedio auxiliar
    function getAverage(arr) {
        if (arr.length === 0) return 0;
        let sum = 0;
        for (let v of arr) sum += v;
        return sum / arr.length;
    }

    // Algoritmo de ritmo cardíaco (BPM)
    function calculateBPM() {
        if (filteredIrBuffer.length < 300 || timeBuffer.length < 300) {
            return null;
        }

        // Suavizado dinámico (paso bajo con ventana móvil de ~200ms)
        const windowSize = Math.max(5, Math.floor(actualFreq * 0.20)) | 1; // Asegurar impar
        const smoothed = [];
        const y = filteredIrBuffer;

        for (let i = 0; i < y.length; i++) {
            let sum = 0;
            let count = 0;
            const half = Math.floor(windowSize / 2);
            for (let w = -half; w <= half; w++) {
                const idx = i + w;
                if (idx >= 0 && idx < y.length) {
                    sum += y[idx];
                    count++;
                }
            }
            smoothed.push(sum / count);
        }

        // Encontrar mínimo, máximo y umbral adaptativo
        let sMin = smoothed[0];
        let sMax = smoothed[0];
        for (let v of smoothed) {
            if (v < sMin) sMin = v;
            if (v > sMax) sMax = v;
        }
        const sRange = sMax - sMin;
        if (sRange < 50) return null; // Señal demasiado débil o ruido plano

        const threshold = sMin + sRange * 0.50;
        const minDistance = Math.floor(actualFreq * 0.40); // 400 ms entre latidos (límite superior 150 BPM)

        const peaks = [];
        let lastPeakIdx = -minDistance;

        for (let i = 1; i < smoothed.length - 1; i++) {
            if (smoothed[i] > smoothed[i-1] && smoothed[i] > smoothed[i+1]) {
                if (smoothed[i] > threshold) {
                    if ((i - lastPeakIdx) >= minDistance) {
                        peaks.push(i);
                        lastPeakIdx = i;
                    }
                }
            }
        }

        if (peaks.length < 3) return null; // Al menos 3 latidos requeridos

        // Extraer los intervalos entre latidos en segundos reales de la PC
        const peakTimes = peaks.map(p => timeBuffer[p]);
        const intervals = [];
        for (let i = 1; i < peakTimes.length; i++) {
            intervals.push(peakTimes[i] - peakTimes[i-1]);
        }

        // Filtrar intervalos fisiológicos locos (reposo: 40 BPM a 180 BPM -> 0.33s a 1.5s)
        const validIntervals = intervals.filter(t => t >= 0.33 && t <= 1.5);
        if (validIntervals.length < 2) return null;

        // Calcular mediana de los intervalos para eliminar artefactos
        validIntervals.sort((a, b) => a - b);
        let medianIntervalSec = 0;
        const mid = Math.floor(validIntervals.length / 2);
        if (validIntervals.length % 2 === 0) {
            medianIntervalSec = (validIntervals[mid - 1] + validIntervals[mid]) / 2;
        } else {
            medianIntervalSec = validIntervals[mid];
        }

        if (medianIntervalSec === 0) return null;
        
        const bpm = 60.0 / medianIntervalSec;
        if (bpm >= 40 && bpm <= 180) {
            return { bpm, peaks, smoothed };
        }
        return null;
    }

    // Estimación de presión arterial sistólica/diastólica por PWA
    function estimateBloodPressure(bpm, peaks, smoothed) {
        if (!bpm || peaks.length < 3 || timeBuffer.length < Math.max(...peaks)) {
            return null;
        }

        // Buscar los valles de inicio (onsets) locales entre picos
        const valleys = [];
        for (let idx = 0; idx < peaks.length - 1; idx++) {
            const start = peaks[idx];
            const end = peaks[idx + 1];
            if (start >= end) continue;
            
            // Buscar índice del mínimo
            let minVal = smoothed[start];
            let minIndex = start;
            for (let s = start; s < end; s++) {
                if (smoothed[s] < minVal) {
                    minVal = smoothed[s];
                    minIndex = s;
                }
            }
            valleys.push(minIndex);
        }

        if (valleys.length < 2) return null;

        const riseTimes = [];
        const fallTimes = [];

        for (let v of valleys) {
            // Siguiente pico posterior al valle
            const postPeaks = peaks.filter(p => p > v);
            if (postPeaks.length === 0) continue;
            const p = postPeaks[0];

            // Siguiente valle posterior al pico
            const postValleys = valleys.filter(nv => nv > p);
            if (postValleys.length === 0) continue;
            const nv = postValleys[0];

            // Calcular duraciones en segundos
            const riseTime = timeBuffer[p] - timeBuffer[v];
            const fallTime = timeBuffer[nv] - timeBuffer[p];
            
            riseTimes.push(riseTime);
            fallTimes.push(fallTime);
        }

        if (riseTimes.length === 0 || fallTimes.length === 0) return null;

        // Promedios
        const avgRise = getAverage(riseTimes);
        const avgFall = getAverage(fallTimes);

        // Modelo de regresión lineal empírica con calibración de piel
        const skinIdx = parseInt(skinToneSelect.value) || 2;
        const cal = skinCalibrationData[skinIdx];
        let sbp = 120.0 + 0.15 * (bpm - 70.0) - 75.0 * (avgRise - 0.12) + cal.sbpOffset;
        let dbp = 80.0 + 0.08 * (bpm - 70.0) - 25.0 * (avgFall - 0.35) + cal.dbpOffset;

        // Clampear a límites lógicos
        sbp = Math.max(95.0, Math.min(145.0, sbp));
        dbp = Math.max(60.0, Math.min(95.0, dbp));

        // Diferencial mínimo de presión diferencial
        if (sbp <= dbp + 25) {
            sbp = dbp + 30;
        }

        return {
            sbp: Math.round(sbp),
            dbp: Math.round(dbp)
        };
    }

    // Estimación de Oxígeno en Sangre (SpO2)
    function calculateSpO2() {
        if (rawRedBuffer.length < 300 || rawIrBuffer.length < 300) {
            return null;
        }

        // Obtener la ventana de los últimos 3 segundos (300 muestras a 100Hz)
        const redRaw = rawRedBuffer.slice(-300);
        const irRaw = rawIrBuffer.slice(-300);
        
        const redFilt = filteredRedBuffer.slice(-300);
        const irFilt = filteredIrBuffer.slice(-300);

        // DC = media del canal crudo
        const dcRed = getAverage(redRaw);
        const dcIr = getAverage(irRaw);

        if (dcRed === 0 || dcIr === 0) return null;

        // Suavizar las señales filtradas con ventana móvil de 5
        const smoothSignal = (arr) => {
            const res = [];
            for (let i = 0; i < arr.length; i++) {
                let sum = 0;
                let count = 0;
                for (let w = -2; w <= 2; w++) {
                    if (i+w >= 0 && i+w < arr.length) {
                        sum += arr[i+w];
                        count++;
                    }
                }
                res.push(sum / count);
            }
            return res;
        };

        const redSmooth = smoothSignal(redFilt);
        const irSmooth = smoothSignal(irFilt);

        // AC = Amplitud pico a pico (max - min) de la señal suavizada
        const acRed = Math.max(...redSmooth) - Math.min(...redSmooth);
        const acIr = Math.max(...irSmooth) - Math.min(...irSmooth);

        if (acIr === 0) return null;

        // Ratio of Ratios (R)
        const r = (acRed / dcRed) / (acIr / dcIr);

        // Fórmula empírica estándar calibrada para el chip MAX30102 con calibración de piel
        const skinIdx = parseInt(skinToneSelect.value) || 2;
        const cal = skinCalibrationData[skinIdx];
        let spo2 = 104.0 - 17.0 * r + cal.spo2Offset;

        // Clampear a rango fisiológico seguro
        spo2 = Math.max(80.0, Math.min(100.0, spo2));
        return spo2;
    }

    // Limpia las lecturas de los indicadores en las tarjetas e iconos
    function clearDiagnosticDisplays() {
        bpmValue.textContent = '--';
        bpValue.textContent = '-- / --';
        spo2Value.textContent = '--';
        bpmCardIcon.classList.remove('heart-beating');
    }

    // Actualización periódica de cálculos cada segundo
    function runDiagnosticAlgorithms() {
        if (!isPreviewing) {
            clearDiagnosticDisplays();
            return;
        }

        // 1. Detección de contacto de piel
        if (rawIrBuffer.length > 0) {
            const recentIr = rawIrBuffer.slice(-50);
            const avgIr = getAverage(recentIr);
            
            if (avgIr < 20000) {
                // Sin contacto
                skinContact.textContent = '⚠️ Sin contacto';
                skinContact.className = 'status-badge status-warning';
                clearDiagnosticDisplays();
                return;
            } else {
                skinContact.textContent = '✔️ Con piel';
                skinContact.className = 'status-badge status-contact';
            }
        }

        // 2. Calcular BPM y Presión Arterial
        const bpmResult = calculateBPM();
        if (bpmResult) {
            const roundedBpm = Math.round(bpmResult.bpm);
            bpmValue.textContent = roundedBpm;
            bpmCardIcon.classList.add('heart-beating');

            // Estimación de Presión
            const bpResult = estimateBloodPressure(roundedBpm, bpmResult.peaks, bpmResult.smoothed);
            if (bpResult) {
                bpValue.textContent = `${bpResult.sbp} / ${bpResult.dbp}`;
            } else {
                bpValue.textContent = '-- / --';
            }
        } else {
            bpmValue.textContent = '--';
            bpValue.textContent = '-- / --';
            bpmCardIcon.classList.remove('heart-beating');
        }

        // 3. Calcular saturación de oxígeno (SpO2)
        const spo2 = calculateSpO2();
        if (spo2 !== null) {
            spo2Value.textContent = Math.round(spo2);
        } else {
            spo2Value.textContent = '--';
        }
    }

    // Temporizador de algoritmos clínicos cada 1000ms
    setInterval(runDiagnosticAlgorithms, 1000);

    // ---------------------------------------------------------
    // 9. Procesamiento de Notificaciones y Recepción de Muestras
    // ---------------------------------------------------------
    function processIncomingSamples(redSamples, irSamples, packetSeq) {
        if (!isPreviewing) return;

        if (startTime === null) {
            startTime = performance.now();
        }

        const packetTime = (performance.now() - startTime) / 1000;

        // Medir pérdida de paquetes por número de secuencia
        if (expectedPacketSeq === 0) {
            expectedPacketSeq = (packetSeq + 1) & 0xFFFF;
        } else if (packetSeq > expectedPacketSeq) {
            const lost = packetSeq - expectedPacketSeq;
            lostPackets += lost;
        } else if (packetSeq < expectedPacketSeq && expectedPacketSeq - packetSeq > 30000) {
            // Desbordamiento (uint16 max es 65535)
            const lost = (packetSeq + 65536) - expectedPacketSeq;
            lostPackets += lost;
        }
        expectedPacketSeq = (packetSeq + 1) & 0xFFFF;
        receivedPackets++;

        // Actualizar tasa de pérdida en interfaz
        const lossRate = (lostPackets / (receivedPackets + lostPackets)) * 100;
        packetLoss.textContent = `📉 Pérdida: ${lossRate.toFixed(1)}%`;

        // Medir la frecuencia de muestreo real en base al reloj del navegador
        const nowTime = performance.now();
        const elapsed = (nowTime - lastFreqTime) / 1000;
        if (elapsed >= 1.0) {
            const diff = globalSampleIndex - lastFreqSamples;
            actualFreq = diff / elapsed;
            lastFreqTime = nowTime;
            lastFreqSamples = globalSampleIndex;
            deviceSampling.textContent = `📊 Muestreo: ${actualFreq.toFixed(1)} Hz`;
        }

        const dt = 1.0 / (actualFreq > 10 ? actualFreq : EXPECTED_FREQ);
        const numSamples = redSamples.length;

        for (let idx = 0; idx < numSamples; idx++) {
            const redRaw = redSamples[idx];
            const irRaw = irSamples[idx];

            // Aplicar filtro pasabanda Butterworth
            const redFilt = butterFilterRed.filter(redRaw);
            const irFilt = butterFilterIr.filter(irRaw);

            // Guardar en búferes locales con tamaño máximo
            rawRedBuffer.push(redRaw);
            rawIrBuffer.push(irRaw);
            filteredRedBuffer.push(redFilt);
            filteredIrBuffer.push(irFilt);

            // Calcular el timestamp relativo individual de la muestra
            const t = packetTime - (numSamples - 1 - idx) * dt;
            timeBuffer.push(t);

            // Clampear buffers a un límite para evitar fugas de memoria
            if (rawRedBuffer.length > BUFFER_LIMIT) {
                rawRedBuffer.shift();
                rawIrBuffer.shift();
                filteredRedBuffer.shift();
                filteredIrBuffer.shift();
                timeBuffer.shift();
            }

            // Si la grabación del estudio está activa, registrar datos
            if (isRecording) {
                if (recordingTime.length === 0) {
                    recordingStartTime = t;
                }
                const recT = t - recordingStartTime;
                recordingTime.push(recT);
                recordingRawRed.push(redRaw);
                recordingRawIr.push(irRaw);
                recordingFilteredRed.push(redFilt);
                recordingFilteredIr.push(irFilt);

                // Incrementar contador de muestras grabadas
                samplesCollected.textContent = `📋 Muestras: ${recordingTime.length}`;

                // Controlar barra de progreso y duración del estudio
                const maxDur = parseFloat(studyDuration.value) || 10.0;
                const percent = (recT / maxDur) * 100;
                progressBar.style.width = `${Math.min(100, percent)}%`;

                if (recT >= maxDur) {
                    stopStudyRecording();
                }
            }

            globalSampleIndex++;
        }
    }

    // Callback de notificación de Web Bluetooth
    function onNotificationReceived(event) {
        const view = event.target.value; // DataView
        if (view.byteLength < 14) return;

        // Decodificación de muestras de 3 bytes (uint24) Big Endian
        const red1 = (view.getUint8(0) << 16) | (view.getUint8(1) << 8) | view.getUint8(2);
        const ir1 = (view.getUint8(3) << 16) | (view.getUint8(4) << 8) | view.getUint8(5);
        const red2 = (view.getUint8(6) << 16) | (view.getUint8(7) << 8) | view.getUint8(8);
        const ir2 = (view.getUint8(9) << 16) | (view.getUint8(10) << 8) | view.getUint8(11);
        
        // Secuencia en Little Endian de 2 bytes
        const seq = view.getUint16(12, true);

        processIncomingSamples([red1, red2], [ir1, ir2], seq);
    }

    // ---------------------------------------------------------
    // 10. Conectividad Web Bluetooth (GATT)
    // ---------------------------------------------------------
    async function connectToDevice() {
        connectionStatus.textContent = '🟡 Vinculando...';
        connectionStatus.className = 'status-badge status-connecting';
        
        try {
            console.log('Solicitando dispositivo Bluetooth...');
            bleDevice = await navigator.bluetooth.requestDevice({
                filters: [{ name: 'Tensiometro_Pulsera' }],
                optionalServices: [SERVICE_UUID]
            });

            console.log('Conectando a servidor GATT...');
            bleServer = await bleDevice.gatt.connect();
            
            // Detectar desconexión inesperada por hardware
            bleDevice.addEventListener('gattserverdisconnected', onDeviceDisconnectedUnexpectedly);

            console.log('Obteniendo servicio...');
            bleService = await bleServer.getPrimaryService(SERVICE_UUID);

            console.log('Obteniendo características...');
            dataChar = await bleService.getCharacteristic(DATA_CHAR_UUID);
            controlChar = await bleService.getCharacteristic(CONTROL_CHAR_UUID);

            // Escuchar notificaciones del sensor
            await dataChar.startNotifications();
            dataChar.addEventListener('characteristicvaluechanged', onNotificationReceived);

            // Enviar orden START para comenzar transmisiones
            const encoder = new TextEncoder();
            await controlChar.writeValue(encoder.encode("START"));

            console.log('Vinculado con éxito. Recepción iniciada.');
            
            // Resetear métricas y filtros
            butterFilterRed.reset();
            butterFilterIr.reset();
            startTime = null;
            expectedPacketSeq = 0;
            receivedPackets = 0;
            lostPackets = 0;
            globalSampleIndex = 0;
            lastFreqTime = performance.now();
            lastFreqSamples = 0;

            isConnected = true;
            isPreviewing = true;

            // UI
            connectionStatus.textContent = '🟢 Conectado';
            connectionStatus.className = 'status-badge status-connected';
            
            connectBleBtn.disabled = true;
            disconnectBleBtn.disabled = false;
            startStudyBtn.disabled = false;
            exportCsvBtn.disabled = true; // Deshabilitar exportar hasta que haya un estudio grabado

        } catch (error) {
            console.error('Error de vinculación:', error);
            connectionStatus.textContent = '🔴 Desconectado';
            connectionStatus.className = 'status-badge status-disconnected';
            alert(`Fallo de conexión: ${error.message || error}`);
        }
    }

    // Apagar y resetear toda la conexión de forma segura
    async function disconnectFromDevice() {
        if (!bleDevice) return;
        
        console.log('Desconectando...');
        
        try {
            if (controlChar) {
                const encoder = new TextEncoder();
                await controlChar.writeValue(encoder.encode("STOP"));
            }
            if (dataChar) {
                await dataChar.stopNotifications();
            }
        } catch (err) {
            console.log('Error al enviar STOP durante desconexión:', err);
        }

        if (bleServer && bleServer.connected) {
            bleServer.disconnect();
        }

        cleanupBLEState();
    }

    // Desconexión inesperada (el usuario se alejó, apagó la pulsera, etc.)
    function onDeviceDisconnectedUnexpectedly() {
        console.log('Se perdió la conexión física Bluetooth.');
        alert('Se ha perdido la conexión con la pulsera.');
        cleanupBLEState();
    }

    // Restaurar buffers e interfaz
    function cleanupBLEState() {
        bleDevice = null;
        bleServer = null;
        bleService = null;
        dataChar = null;
        controlChar = null;

        isConnected = false;
        isPreviewing = false;
        isRecording = false;

        // Resetear filtros
        butterFilterRed.reset();
        butterFilterIr.reset();

        // Limpiar buffers
        rawRedBuffer = [];
        rawIrBuffer = [];
        filteredRedBuffer = [];
        filteredIrBuffer = [];
        timeBuffer = [];

        // UI
        connectionStatus.textContent = '🔴 Desconectado';
        connectionStatus.className = 'status-badge status-disconnected';
        skinContact.textContent = '⚠️ Sin contacto';
        skinContact.className = 'status-badge status-warning';
        deviceSampling.textContent = '📊 Muestreo: -- Hz';
        packetLoss.textContent = '📉 Pérdida: --%';
        
        connectBleBtn.disabled = false;
        disconnectBleBtn.disabled = true;
        startStudyBtn.disabled = true;
        startStudyBtn.textContent = '▶️ Iniciar Grabación';
        progressBarContainer.style.display = 'none';

        clearDiagnosticDisplays();
    }

    // ---------------------------------------------------------
    // 11. Grabación de Estudios y Descarga de CSV
    // ---------------------------------------------------------
    function startStudyRecording() {
        if (!isConnected) return;

        console.log('Iniciando grabación de estudio...');
        
        // Resetear buffers de grabación
        recordingTime = [];
        recordingRawRed = [];
        recordingRawIr = [];
        recordingFilteredRed = [];
        recordingFilteredIr = [];

        isRecording = true;

        // UI
        startStudyBtn.disabled = true;
        startStudyBtn.textContent = '🔴 Grabando...';
        studyDuration.disabled = true;
        exportCsvBtn.disabled = true;
        
        progressBarContainer.style.display = 'block';
        progressBar.style.width = '0%';
    }

    function stopStudyRecording() {
        isRecording = false;
        console.log(`Grabación completada: ${recordingTime.length} muestras.`);

        // UI
        startStudyBtn.disabled = false;
        startStudyBtn.textContent = '▶️ Iniciar Grabación';
        studyDuration.disabled = false;
        exportCsvBtn.disabled = false;

        alert(`✅ Grabación de estudio finalizada con éxito.\nTotal de muestras: ${recordingTime.length}\nDuración: ${studyDuration.value}s\nYa puedes exportar tu reporte en formato CSV.`);
    }

    function exportToCSV() {
        if (recordingTime.length === 0) {
            alert('No hay datos grabados para exportar.');
            return;
        }

        // Construir el encabezado del archivo CSV
        let csvContent = "data:text/csv;charset=utf-8,";
        csvContent += "Tiempo_s,PPG_Rojo_Cruda,PPG_IR_Cruda,PPG_Rojo_Filtrada,PPG_IR_Filtrada\n";

        // Escribir los registros línea por línea
        for (let i = 0; i < recordingTime.length; i++) {
            const row = [
                recordingTime[i].toFixed(4),
                recordingRawRed[i],
                recordingRawIr[i],
                recordingFilteredRed[i].toFixed(1),
                recordingFilteredIr[i].toFixed(1)
            ];
            csvContent += row.join(",") + "\n";
        }

        // Codificar el URI del archivo y descargarlo automáticamente
        const encodedUri = encodeURI(csvContent);
        const link = document.createElement("a");
        link.setAttribute("href", encodedUri);
        
        // Formatear el nombre con la fecha actual
        const now = new Date();
        const dateStr = now.toISOString().slice(0,10) + "_" + now.getHours() + "-" + now.getMinutes();
        link.setAttribute("download", `estudio_cardiovascular_${dateStr}.csv`);
        
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    // ---------------------------------------------------------
    // 12. Asignación de Listeners a los Botones
    // ---------------------------------------------------------
    if (connectBleBtn) connectBleBtn.addEventListener('click', connectToDevice);
    if (disconnectBleBtn) disconnectBleBtn.addEventListener('click', disconnectFromDevice);
    if (startStudyBtn) startStudyBtn.addEventListener('click', startStudyRecording);
    if (exportCsvBtn) exportCsvBtn.addEventListener('click', exportToCSV);

    // Manejar el botón de subir foto y análisis de piel
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
                    
                    const w = img.width;
                    const h = img.height;
                    tempCanvas.width = w;
                    tempCanvas.height = h;
                    
                    tempCtx.drawImage(img, 0, 0, w, h);
                    
                    const cx = Math.floor(w / 2);
                    const cy = Math.floor(h / 2);
                    const rx = Math.max(0, cx - 50);
                    const ry = Math.max(0, cy - 50);
                    const rw = Math.min(100, w - rx);
                    const rh = Math.min(100, h - ry);
                    
                    if (rw <= 0 || rh <= 0) {
                        alert("La resolución de la imagen es demasiado baja.");
                        return;
                    }
                    
                    const imgData = tempCtx.getImageData(rx, ry, rw, rh);
                    const pixels = imgData.data;
                    
                    let sumR = 0, sumG = 0, sumB = 0;
                    let count = 0;
                    
                    for (let i = 0; i < pixels.length; i += 4) {
                        sumR += pixels[i];
                        sumG += pixels[i+1];
                        sumB += pixels[i+2];
                        count++;
                    }
                    
                    const avgR = sumR / count;
                    const avgG = sumG / count;
                    const avgB = sumB / count;
                    
                    const rNorm = avgR / 255.0;
                    const gNorm = avgG / 255.0;
                    const bNorm = avgB / 255.0;
                    
                    const pivot = (v) => {
                        return v > 0.04045 ? Math.pow((v + 0.055) / 1.055, 2.4) : v / 12.92;
                    };
                    
                    const rp = pivot(rNorm);
                    const gp = pivot(gNorm);
                    const bp = pivot(bNorm);
                    
                    let x = rp * 0.4124564 + gp * 0.3575761 + bp * 0.1804375;
                    let y = rp * 0.2126729 + gp * 0.7151522 + bp * 0.0721750;
                    let z = rp * 0.0193339 + gp * 0.1191920 + bp * 0.9503041;
                    
                    x /= 0.950489;
                    y /= 1.000000;
                    z /= 1.088840;
                    
                    const f = (t) => {
                        return t > 0.008856 ? Math.pow(t, 1/3) : 7.787 * t + 16/116;
                    };
                    
                    const fx = f(x);
                    const fy = f(y);
                    const fz = f(z);
                    
                    const lStar = 116 * fy - 16;
                    let bStar = 200 * (fy - fz);
                    
                    if (bStar === 0) bStar = 0.001;
                    
                    const ita = Math.atan((lStar - 50) / bStar) * (180 / Math.PI);
                    
                    let idx = 2;
                    let cat = "Intermedia";
                    
                    if (ita > 55) {
                        idx = 0;
                        cat = "Muy clara";
                    } else if (ita > 41) {
                        idx = 1;
                        cat = "Clara";
                    } else if (ita > 28) {
                        idx = 2;
                        cat = "Intermedia";
                    } else if (ita > 10) {
                        idx = 3;
                        cat = "Morena";
                    } else if (ita > -30) {
                        idx = 4;
                        cat = "Oscura";
                    } else {
                        idx = 5;
                        cat = "Muy oscura";
                    }
                    
                    if (skinToneSelect) {
                        skinToneSelect.value = idx.toString();
                    }
                    
                    alert(`Tono de Piel Detectado\n\n` +
                          `• L* (Luminosidad): ${lStar.toFixed(2)}\n` +
                          `• b* (Amarillez): ${bStar.toFixed(2)}\n` +
                          `• Ángulo ITA: ${ita.toFixed(2)}°\n` +
                          `• Categoría: ${cat}\n\n` +
                          `La calibración para piel '${cat}' ha sido aplicada automáticamente.`);
                    console.log(`Calibración de tono de piel detectada por foto: ${cat} (ITA=${ita.toFixed(1)}°)`);
                };
                img.src = event.target.result;
            };
            reader.readAsDataURL(file);
        });
    }
});
