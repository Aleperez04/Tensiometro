# Documentación Técnica Profesional: Estación Médica de Diagnóstico Cardiovascular (Versión 1.0 y Versión 2.0)

Este documento detalla exhaustivamente la arquitectura, ingeniería de hardware, procesamiento digital de señales (DSP), formulación hemodinámica y especificación de software de la **Estación Médica de Diagnóstico Cardiovascular**, cubriendo la evolución técnica completa desde la **Versión 1 (Línea Base)** hasta la **Versión 2 (Sistema Autónomo con Cancelación de Movimiento NLMS y Calibración Clínica)**.

---

## 1. Resumen Ejecutivo y Evolución del Sistema

### 1.1 Objetivo del Proyecto
El proyecto tiene por objetivo el diseño e implementación de un tensiómetro digital portátil no invasivo basado en fotopletismografía (PPG) de reflectancia en la arteria radial (cara ventral de la muñeca). El sistema captura las oscilaciones volumétricas arteriales para estimar en tiempo real:
1. **Frecuencia Cardíaca (BPM)**
2. **Presión Arterial Sistólica (SBP) y Diastólica (DBP)** mediante Análisis de Onda de Pulso (PWA)
3. **Saturación de Oxígeno en Sangre ($SpO_2$)** mediante Ratio espectral
4. **Calibración Cutánea Automática** para compensar el sesgo de absorción óptica por melanina
5. **Telemetría de Energía** y estado de carga de la batería Li-Po

### 1.2 Cuadro Comparativo: Versión 1 vs. Versión 2

| Característica / Parámetro | Versión 1.0 (Línea Base) | Versión 2.0 (Mejoras Implementadas) |
| :--- | :--- | :--- |
| **Alimentación Eléctrica** | Cable USB (Dependiente de PC / 5V). | **Batería Li-Po 3.7V autónoma** con cargador TP4056 USB-C. |
| **Regulación de Tensión** | Directa por USB con caídas y ruido de PC. | **LDO interno del ESP32 Mini (3.3V)** como filtro de rizado. Se descarta LM2596. |
| **Monitoreo de Energía** | No disponible (sin telemetría de batería). | **Monitoreo por divisor resistivo ($2\times 100\text{ k}\Omega$) en ADC1** con alerta a 3.4V e histéresis a 3.5V. |
| **Sensores en Bus I2C** | Únicamente MAX30102 (Dirección `0x57`). | **MAX30102 (`0x57`) + Acelerómetro MPU6050 (`0x68`)** a 400 kHz. |
| **Trama de Telemetría BLE** | 14 Bytes (2 muestras Rojo/IR + Secuencia). | **20 Bytes Extendida** (Muestras ópticas + Secuencia + Aceleración X, Y, Z). |
| **Filtrado de Movimiento** | Suavizado pasivo (media móvil 200 ms). | **Filtro Adaptativo NLMS** (24 taps) referenciado a la magnitud física de aceleración. |
| **Máquina de Estados** | Binaria (Con contacto / Sin contacto). | **3 Estados Dinámicos**: Reposo, Movimiento Leve (NLMS) y Movimiento Fuerte (Alerta visual). |
| **Calibración de Piel** | Captura fotográfica o selección manual. | **Detección Automática por nivel $DC_{raw}$** ($Ratio_{DC} = DC_{Rojo} / DC_{IR}$) en los primeros 5–10 s. |
| **Modelo de Presión (PWA)**| Truncamiento rígido ($95-145$ / $60-95\text{ mmHg}$). | **Calibración Individual por Esfigmomanómetro** (3 lecturas previas, eliminación de truncamiento). |
| **Exportación CSV** | Columnas básicas sin metadatos. | **Formato Extendido ($N=30$)** con cabecera de flags, señales post-NLMS y campos clínicos de error. |
| **Conectividad y Enlace** | BLE básico sin auto-reconexión robusta. | **Re-anuncio automático en ESP32**, reintentos automáticos y reinicio de filtros en clientes. |

---

## 2. Lo que se Conserva al 100% de la Primera Versión

Para garantizar la estabilidad hemodinámica demostrada en la primera versión, los siguientes núcleos algorítmicos permanecen activos e intactos en la Versión 2:

1. **Detección de Contacto Cutáneo**:
   * Evaluación continua del canal infrarrojo crudo ($IR_{crudo}$).
   * Si $IR_{crudo} < 20,000\text{ unidades ADC}$, el procesamiento se suspende de inmediato, los displays se limpian a `"--"` y se muestra la alerta *"Sin contacto"*.
2. **DC Blocker**:
   * Filtro IIR recursivo con coeficiente $R = 0.98$ para centrar la señal en cero, eliminando la absorción estática de tejidos óseos y piel.
3. **Filtro Pasabanda Butterworth (Orden 4, $0.5\text{ Hz}$ a $8.0\text{ Hz}$)**:
   * *Etapa Pasa Altas ($0.5\text{ Hz}$)*: Elimina la deriva de la línea base por respiración y variaciones térmicas lentas.
   * *Etapa Pasa Bajas ($8.0\text{ Hz}$)*: Suprime interferencia de la red eléctrica ($50/60\text{ Hz}$) y ruido electromiográfico.
   * Implementado en cascada Direct Form II mediante Secciones de Segundo Orden (SOS / Biquads).
4. **Suavizado de Señal ($200\text{ ms}$)**:
   * Filtro de media móvil para el acondicionado previo de la onda antes de buscar los picos sistólicos, suprimiendo falsos positivos generados por la muesca dícrota.
5. **Algoritmo PWA Básico y Tiempos Vasculares**:
   * Medición precisa de los tiempos de ascenso ($T_s$) y descenso ($T_d$) para la estimación de SBP, DBP y BPM.
6. **Servidor GATT BLE a $100\text{ Hz}$**:
   * Transmisión sincronizada de paquetes binarios para visualización a 60 fps en tiempo real.

---

## 3. Rediseño Eléctrico y Hardware Autónomo (Versión 2.0)

### 3.1 Esquema de Distribución de Energía
El dispositivo elimina la dependencia del cable USB para operar:
* **Batería Li-Po de $3.7\text{ V}$ ($500\text{ mAh}$)**: Fuente electroquímica principal con densidad energética suficiente para más de 6 horas de telemetría continua.
* **Módulo de Carga TP4056 USB-C**: Administra la carga en corriente constante / voltaje constante (CC/CV) e integra circuito de protección contra sobrecarga ($4.2\text{ V}$), sobredescarga ($2.5\text{ V}$) y sobrecorriente ($3\text{ A}$). El puerto USB queda reservado **únicamente para cargar la batería** y para reprogramar el microcontrolador.
* **Regulación y Descarte del LM2596**: La salida del módulo TP4056 se conecta directamente al pin de entrada principal (**VIN / 5V**) de la placa **ESP32 Mini**. El regulador interno de la placa (LDO de baja caída) reduce y estabiliza el voltaje a **$3.3\text{ V}$ nítidos**, filtrando el ruido eléctrico de la batería hacia los sensores MAX30102 y MPU6050. Se **descarta físicamente el convertidor LM2596** del inventario debido a que su topología reductora (*step-down*) requiere un diferencial de voltaje mínimo ($V_{in} - V_{out} \ge 1.5\text{ V}$), siendo totalmente incompatible con el rango útil de una celda Li-Po ($3.4\text{ V} - 4.2\text{ V}$).
* **Capacitores de Desacoplo**: Se incluyen condensadores de tántalo y cerámicos de $10\,\mu\text{F}$ y $100\,\text{nF}$ en paralelo entre $3.3\text{ V}$ y GND en la entrada del sensor óptico para suprimir transitorios de conmutación.

### 3.2 Monitoreo de Batería con Divisor Resistivo (ADC1)
Para reportar el estado de carga sin sobrepasar el límite de entrada del convertidor analógico-digital del ESP32 ($3.3\text{ V}$), se instaló un divisor de tensión simétrico formado por dos resistencias de precisión de $100\text{ k}\Omega$ ($1\%$ de tolerancia) conectadas entre el terminal positivo de la batería y GND:
$$V_{ADC} = V_{bat} \times \left(\frac{100\text{ k}\Omega}{100\text{ k}\Omega + 100\text{ k}\Omega}\right) = \frac{V_{bat}}{2}$$

* El pin central del divisor se conecta a un canal del **ADC1** (GPIO 34), evitando el ADC2 para que las lecturas no entren en conflicto con el módem de radiofrecuencia Wi-Fi/Bluetooth.
* **Cálculo de Tensión Real**:
  $$V_{bat} = \left(\frac{\text{Lectura ADC}}{4095}\right) \times 3.3\text{ V} \times 2.0$$
* **Lógica de Histéresis Clínica de Alerta**:
  * Si $V_{bat} < 3.40\text{ V}$, se activa la bandera de **Batería Baja** y se suspenden los cálculos de presión arterial para evitar distorsiones por atenuación del LED emisor.
  * La alerta se desactiva únicamente cuando la batería se conecta al cargador y el voltaje supera los **$3.50\text{ V}$** (histéresis de $100\text{ mV}$).

---

## 4. Mejoras Digitales y Algoritmos Nuevos

### 4.1 Integración del Acelerómetro MPU6050 y Trama de 20 Bytes
El circuito integrado MPU6050 se encuentra soldado sobre el mismo circuito impreso de la pulsera, montado inmediatamente adyacente al sensor MAX30102.
* Comparte el bus serie I2C en modo rápido ($400\text{ kHz}$) en la dirección `0x68`.
* Configurado a fondo de escala de $\pm 2g$, con una sensibilidad de $16384\text{ LSB}/g$.

#### Distribución Binaria de la Trama Extendida de 20 Bytes:
| Rango de Bytes | Parámetro Registrado | Tipo de Dato | Codificación |
| :--- | :--- | :--- | :--- |
| **0 – 2** | Muestra 1: PPG Canal Rojo | uint24 (3 bytes) | Big Endian |
| **3 – 5** | Muestra 1: PPG Canal Infrarrojo | uint24 (3 bytes) | Big Endian |
| **6 – 8** | Muestra 2: PPG Canal Rojo | uint24 (3 bytes) | Big Endian |
| **9 – 11** | Muestra 2: PPG Canal Infrarrojo | uint24 (3 bytes) | Big Endian |
| **12 – 13** | Contador Incremental de Secuencia | uint16 (2 bytes) | Little Endian |
| **14 – 15** | Aceleración en Eje X ($A_x$) | int16 (2 bytes) | Little Endian ($1g = 16384$) |
| **16 – 17** | Aceleración en Eje Y ($A_y$) | int16 (2 bytes) | Little Endian ($1g = 16384$) |
| **18 – 19** | Aceleración en Eje Z ($A_z$) | int16 (2 bytes) | Little Endian ($1g = 16384$) |

### 4.2 Filtro Adaptativo NLMS (Cancelación de Ruido de Movimiento)
El movimiento mecánico de la muñeca provoca desplazamientos relativos del lecho capilar respecto al fotodiodo, generando artefactos morfológicos de gran amplitud que deforman la onda PPG.

1. **Magnitud de Aceleración Física**:
   $$A_{raw} = \sqrt{A_x^2 + A_y^2 + A_z^2} - 1.0g$$
   La señal $A_{raw}$ se procesa mediante un filtro pasabanda Butterworth ($0.5 - 8.0\text{ Hz}$) para aislar exclusivamente las aceleraciones dinámicas ($x_{acc}$).
2. **Arquitectura NLMS (Normalized Least Mean Squares)**:
   Se mantiene un vector de pesos $\mathbf{w}[n]$ de longitud $M = 24$ coeficientes y un búfer de ruido de referencia $\mathbf{x}[n] = [x_{acc}[n], x_{acc}[n-1], \dots, x_{acc}[n-M+1]]^T$.
   * **Estimación del artefacto acoplado**:
     $$y[n] = \mathbf{w}^T[n] \mathbf{x}[n] = \sum_{k=0}^{M-1} w_k[n] x_{acc}[n-k]$$
   * **Señal limpia libre de movimiento (error a minimizar)**:
     $$e[n] = d[n] - y[n]$$
     Donde $d[n]$ es la señal PPG filtrada por Butterworth.
   * **Actualización con factor normalizado por energía**:
     $$\mathbf{w}[n+1] = \mathbf{w}[n] + \frac{\mu}{\|\mathbf{x}[n]\|^2 + \epsilon} e[n] \mathbf{x}[n]$$
     Con $\mu = 0.02$ y $\epsilon = 10^{-5}$ para evitar inestabilidad ante energía nula.
   * **Congelamiento de Coeficientes**: En estado de reposo, el factor de actualización se conmuta a $\mu = 0$, congelando los pesos para evitar que el filtro adapte componentes biológicas del pulso.

### 4.3 Máquina de Estados por Movimiento
El sistema evalúa continuamente la aceleración filtrada $|x_{acc}|$:
* **Estado Reposo ($|x_{acc}| < 0.08g$)**: Operación normal. Los pesos del NLMS se congelan ($\mu = 0$) y se estiman todos los signos vitales.
* **Estado Movimiento Leve ($0.08g \le |x_{acc}| \le 0.35g$)**: El filtro NLMS activa su aprendizaje ($\mu = 0.02$) y sustrae dinámicamente el componente inercial, entregando una señal limpia al algoritmo de picos.
* **Estado Movimiento Fuerte ($|x_{acc}| > 0.35g$)**: El desacoplamiento mecánico supera la capacidad de cancelación lineal. Se suspende temporalmente el cálculo de presión arterial, se retienen en pantalla los últimos valores válidos con un asterisco de congelamiento y se despliega la alerta visual: *"⚠️ Mantén la muñeca quieta"*.

### 4.4 Tono de Piel Automático por Nivel $DC_{raw}$
Se elimina la captura fotográfica como método obligatorio, reduciendo tiempos de consulta:
* La eumelanina absorbe la luz roja ($660\text{ nm}$) mucho más fuertemente que la infrarroja ($880\text{ nm}$).
* Tras 5 a 10 segundos ($500\text{ muestras}$) de contacto estable en reposo, el software calcula el cociente continuo:
  $$Ratio_{DC} = \frac{DC_{\text{Rojo}}}{DC_{\text{IR}}}$$
* **Matriz de Clasificación Automática**:
  * $Ratio_{DC} > 1.25$: Muy clara ($\Delta SpO_2 = 0.0\%$, $\Delta SBP = 0.0\text{ mmHg}$)
  * $1.10 < Ratio_{DC} \le 1.25$: Clara ($\Delta SpO_2 = 0.0\%$, $\Delta SBP = 0.0\text{ mmHg}$)
  * $0.95 < Ratio_{DC} \le 1.10$: Intermedia ($\Delta SpO_2 = +0.2\%$, $\Delta SBP = -0.5\text{ mmHg}$)
  * $0.80 < Ratio_{DC} \le 0.95$: Morena ($\Delta SpO_2 = +0.6\%$, $\Delta SBP = -1.0\text{ mmHg}$)
  * $0.65 < Ratio_{DC} \le 0.80$: Oscura ($\Delta SpO_2 = +1.2\%$, $\Delta SBP = -2.0\text{ mmHg}$)
  * $Ratio_{DC} \le 0.65$: Muy oscura ($\Delta SpO_2 = +2.0\%$, $\Delta SBP = -3.5\text{ mmHg}$)
* El selector manual se mantiene en la interfaz exclusivamente como mecanismo de respaldo.

### 4.5 Calibración Clínica Individual y Validación
* **Eliminación del Truncamiento Rígido**: Se suprimieron los límites estáticos artificiales ($95-145\text{ mmHg}$ sistólica y $60-95\text{ mmHg}$ diastólica).
* **Módulo de Calibración por Esfigmomanómetro**: El software permite ingresar 3 mediciones previas tomadas con manguito oscilométrico convencional. El sistema calcula el promedio de referencia ($SBP_{ref}, DBP_{ref}$) y deduce el offset elástico individual:
  $$\Delta SBP_{ind} = SBP_{ref} - 120.0, \quad \Delta DBP_{ind} = DBP_{ref} - 80.0$$
* **Exportación CSV Extendida ($N=30$)**: El archivo CSV incluye metadatos en las primeras líneas con las banderas de configuración algorítmica y columnas para aceleración ($X, Y, Z, Mag$), señales post-NLMS y los campos clínicos obligatorios `ID_Sujeto`, `SBP_Referencia` y `DBP_Referencia`, permitiendo calcular directamente:
  * Error cuadrático medio: $\text{RMSE} < 5.0\text{ mmHg}$
  * Análisis de concordancia de **Bland-Altman**
  * Coeficiente de correlación intraclase: $\text{ICC} > 0.85$

### 4.6 Corrección de Errores Técnicos Solucionados
1. **Filtro Butterworth en Python**: Se programó activo por defecto al iniciar la aplicación (`self.filter_checkbox.setChecked(True)` y `self.dc_filter_enabled = True`).
2. **Botón de Grabado de Datos en Python**: Se corrigió el error de ejecución de tiempo de ejecución (`AttributeError: 'MainWindow' object has no attribute 'recording_raw'`), implementando la limpieza y direccionamiento correcto a los búferes independientes de Rojo, Infrarrojo y Aceleración.
3. **Selector de Tonos de Piel en JavaScript**: Se corrigió la sentencia `parseInt(skinToneSelect.value) || 2`, la cual provocaba que el índice `0` ("Muy clara") fuera evaluado como valor *falsy* y forzara erróneamente el índice `2` ("Intermedia").

---

## 5. Especificación de la Interfaz Inalámbrica BLE GATT (v2.0)

* **Nombre de Red BLE**: `Tensiometro_Pulsera_v2`
* **UUID de Servicio Principal**: `4fafc201-1fb5-459e-8fcc-c5c9c331914b`

### Características del Servicio:
1. **Telemetría de Datos (UUID: `beb5483e-36e1-4688-b7f5-ea07361b26a8`)**:
   * Propiedad: `NOTIFY`
   * Tamaño: Fijo de 20 bytes a $50\text{ Hz}$ ($100\text{ muestras/s}$ reales de PPG y Aceleración triaxial).
2. **Control de Adquisición (UUID: `12345678-1234-1234-1234-123456789abc`)**:
   * Propiedad: `WRITE`
   * Comandos UTF-8: `"START"` para activar notificaciones y limpiar el FIFO; `"STOP"` para entrar en modo de suspensión energética.
3. **Telemetría de Batería (UUID: `00002a19-0000-1000-8000-00805f9b34fb`)**:
   * Propiedades: `READ` y `NOTIFY`
   * Payload (4 bytes):
     * Byte 0: Nivel porcentual de carga ($0 - 100\%$, uint8)
     * Bytes 1–2: Tensión en milivoltios ($3400 - 4200\text{ mV}$, uint16 Little Endian)
     * Byte 3: Banderas de estado (Bit 0 = Conectado, Bit 1 = Alerta Batería Baja activa)

---

## 6. Código del Firmware Embebido Documentado

### 6.1 Firmware Versión 1.0 (Línea Base Histórica)
El código original para ESP32 estándar con lectura del sensor MAX30102 y telemetría de 14 bytes se encuentra preservado en [firmware_v1.ino](file:///C:/Users/alepe/.gemini/antigravity/scratch/tensiometro_extract/firmware_v1/firmware_v1.ino).

### 6.2 Firmware Versión 2.0 (Producción Autónoma)
El código para el microcontrolador ESP32 Mini con bus I2C doble (MAX30102 + MPU6050), lectura de batería por ADC1 y trama extendida de 20 bytes se encuentra implementado en [firmware_v2.ino](file:///C:/Users/alepe/.gemini/antigravity/scratch/tensiometro_extract/firmware_v2/firmware_v2.ino).
