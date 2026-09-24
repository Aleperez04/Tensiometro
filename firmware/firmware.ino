/*
  ===================================================================================
  ESTACIÓN MÉDICA DE DIAGNÓSTICO CARDIOVASCULAR - VERSIÓN 2.0
  Proyecto: Tensiómetro Digital de Arteria Radial con Compensación y Cancelación NLMS
  Plataforma: ESP32 Mini (Operación Autónoma a Batería Li-Po 3.7V con TP4056 USB-C)
  Sensores: MAX30102 (PPG Óptico) + MPU6050 (Acelerómetro Triaxial) en Bus I2C (400kHz)
  ===================================================================================
*/

#include <Wire.h>
#include "MAX30105.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// -----------------------------------------------------------------------------
// DEFINICIÓN DE IDENTIFICADORES UUID GATT (Estándar y Personalizados)
// -----------------------------------------------------------------------------
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define DATA_CHAR_UUID      "beb5483e-36e1-4688-b7f5-ea07361b26a8" // Notificaciones 20 bytes (PPG + Secuencia + Accel)
#define CONTROL_CHAR_UUID   "12345678-1234-1234-1234-123456789abc" // Control START/STOP
#define BATTERY_CHAR_UUID   "00002a19-0000-1000-8000-00805f9b34fb" // Característica Batería (mV, %, flags)

// -----------------------------------------------------------------------------
// DIRECCIONES I2C Y REGISTROS MPU6050
// -----------------------------------------------------------------------------
#define MPU6050_ADDR        0x68
#define MPU_PWR_MGMT_1      0x6B
#define MPU_ACCEL_CONFIG    0x1C
#define MPU_ACCEL_XOUT_H    0x3B

// -----------------------------------------------------------------------------
// PINES Y MONITOREO DE BATERÍA (ADC1)
// -----------------------------------------------------------------------------
#define BATTERY_ADC_PIN     34    // Entrada ADC1 (Divisor resistivo 100k + 100k = factor 2.0)
#define ADC_REF_VOLTAGE     3.3   // Tensión de referencia regulador LDO ESP32 Mini
#define ADC_RESOLUTION      4095.0
#define V_DIVIDER_RATIO     2.0   // Factor multiplicador por divisor 1:1
#define V_BAT_CRITICAL      3.40  // Umbral de activación de alerta de batería baja
#define V_BAT_HYSTERESIS    3.50  // Umbral de apagado de alerta por histéresis

// Instancias de hardware y BLE
MAX30105 particleSensor;
BLEServer* pServer = NULL;
BLECharacteristic* pDataChar = NULL;
BLECharacteristic* pControlChar = NULL;
BLECharacteristic* pBatteryChar = NULL;

bool deviceConnected = false;
bool streamingActive = false;
uint16_t packetSequence = 0;

// Variables para acumular las 2 muestras de ambos canales
#define SAMPLE_PAIRS_PER_PACKET 2
uint32_t redBuffer[SAMPLE_PAIRS_PER_PACKET];
uint32_t irBuffer[SAMPLE_PAIRS_PER_PACKET];
uint8_t bufferIndex = 0;

// Variables de aceleración física MPU6050 (±2g, 1g = 16384 LSB)
int16_t accelX = 0;
int16_t accelY = 0;
int16_t accelZ = 16384; // 1g inicial en reposo

// Monitoreo de energía
unsigned long lastBatteryCheck = 0;
bool lowBatteryActive = false;
uint8_t batteryPct = 100;
uint16_t batteryMv = 4200;

// -----------------------------------------------------------------------------
// FUNCIONES DE CONTROL DE BAJO NIVEL MPU6050 (I2C DIRECTO)
// -----------------------------------------------------------------------------
bool initMPU6050() {
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(MPU_PWR_MGMT_1);
    Wire.write(0x00); // Despertar oscilador interno
    if (Wire.endTransmission() != 0) {
        return false;
    }

    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(MPU_ACCEL_CONFIG);
    Wire.write(0x00); // Configurar escala a ±2g
    if (Wire.endTransmission() != 0) {
        return false;
    }
    return true;
}

void readMPU6050Accel() {
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(MPU_ACCEL_XOUT_H);
    Wire.endTransmission(false);
    Wire.requestFrom((uint8_t)MPU6050_ADDR, (size_t)6, true);

    if (Wire.available() >= 6) {
        accelX = (Wire.read() << 8) | Wire.read();
        accelY = (Wire.read() << 8) | Wire.read();
        accelZ = (Wire.read() << 8) | Wire.read();
    }
}

// -----------------------------------------------------------------------------
// MONITOREO DE NIVEL DE BATERÍA CON HISTÉRESIS
// -----------------------------------------------------------------------------
void checkBatteryStatus() {
    // Lectura promediada de 8 muestras para mitigar ruido térmico del ADC
    uint32_t adcSum = 0;
    for (int k = 0; k < 8; k++) {
        adcSum += analogRead(BATTERY_ADC_PIN);
        delayMicroseconds(50);
    }
    float rawAdc = (float)adcSum / 8.0f;
    float voltage = (rawAdc / ADC_RESOLUTION) * ADC_REF_VOLTAGE * V_DIVIDER_RATIO;
    batteryMv = (uint16_t)(voltage * 1000.0f);

    // Mapeo porcentual lineal en rango de batería Li-Po (3.4V = 0%, 4.2V = 100%)
    int pct = (int)(((voltage - V_BAT_CRITICAL) / (4.20f - V_BAT_CRITICAL)) * 100.0f);
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    batteryPct = (uint8_t)pct;

    // Lógica de histéresis clínica para la alerta
    if (voltage < V_BAT_CRITICAL) {
        lowBatteryActive = true;
    } else if (voltage >= V_BAT_HYSTERESIS) {
        lowBatteryActive = false;
    }

    // Banderas de estado: Bit 0 = Conexión activa, Bit 1 = Alerta Batería Baja (<3.4V)
    uint8_t flags = 0;
    if (deviceConnected) flags |= 0x01;
    if (lowBatteryActive) flags |= 0x02;

    // Notificar si hay suscriptores
    if (deviceConnected && pBatteryChar != NULL) {
        uint8_t batPayload[4];
        batPayload[0] = batteryPct;
        batPayload[1] = batteryMv & 0xFF;
        batPayload[2] = (batteryMv >> 8) & 0xFF;
        batPayload[3] = flags;
        pBatteryChar->setValue(batPayload, 4);
        pBatteryChar->notify();
    }
}

// -----------------------------------------------------------------------------
// CALLBACKS DEL SERVIDOR BLE (RECONEXIÓN AUTOMÁTICA Y COMANDOS)
// -----------------------------------------------------------------------------
class ServerCallbacksV2: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
        deviceConnected = true;
        Serial.println("[BLE v2] Enlace establecido con cliente receptor.");
    }
    void onDisconnect(BLEServer* pServer) {
        deviceConnected = false;
        streamingActive = false;
        packetSequence = 0;
        bufferIndex = 0;
        Serial.println("[BLE v2] Enlace interrumpido. Reanudando transmisiones de anuncio...");
        pServer->startAdvertising(); // Reconexión automática
    }
};

class ControlCallbacksV2: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
        String rxValue = pCharacteristic->getValue().c_str(); 
        if (rxValue.length() > 0) {
            if (rxValue == "START") {
                streamingActive = true;
                packetSequence = 0;
                bufferIndex = 0;
                particleSensor.clearFIFO();
                Serial.println("[BLE v2] Comando START recibido. Telemetría de 20 bytes iniciada.");
            } else if (rxValue == "STOP") {
                streamingActive = false;
                Serial.println("[BLE v2] Comando STOP recibido. Adquisición pausada.");
            }
        }
    }
};

// -----------------------------------------------------------------------------
// INICIALIZACIÓN DEL SISTEMA
// -----------------------------------------------------------------------------
void setup() {
    Serial.begin(115200);
    analogReadResolution(12); // ADC a 12 bits (0-4095)
    pinMode(BATTERY_ADC_PIN, INPUT);

    Serial.println("\n=======================================================");
    Serial.println("  ESTACIÓN MÉDICA CARDIOVASCULAR v2.0 - ESP32 MINI");
    Serial.println("  Telemetría PPG (MAX30102) + Aceleración Triaxial (MPU6050)");
    Serial.println("=======================================================");

    // 1. Inicialización de I2C a 400 kHz (Fast Mode)
    Wire.begin(21, 22, 400000);

    // 2. Inicialización del Acelerómetro MPU6050 (mismo bus I2C)
    if (!initMPU6050()) {
        Serial.println("[ALERTA] MPU6050 no detectado en 0x68. Continuando en modo fotométrico.");
    } else {
        Serial.println("[OK] MPU6050 inicializado correctamente a escala ±2g.");
    }

    // 3. Inicialización del Sensor Óptico MAX30102
    if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
        Serial.println("[ERROR CRÍTICO] MAX30102 no encontrado en 0x57. Revise conexiones.");
        while (1) {
            delay(1000);
        }
    }

    // Configuración óptica optimizada para la arteria radial (muñeca)
    byte ledBrightness = 60; // ~12 mA por diodo emisor
    byte sampleAverage = 1;  // Sin promedio (100 Hz puros)
    byte ledMode = 2;        // Modo 2: Rojo + Infrarrojo
    int sampleRate = 100;    // 100 muestras/segundo
    int pulseWidth = 411;    // 18 bits de resolución ADC
    int adcRange = 4096;     // 4096 nA
    particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
    particleSensor.clearFIFO();
    Serial.println("[OK] MAX30102 configurado a 100 Hz y 18 bits.");

    // 4. Inicialización del Stack BLE GATT
    BLEDevice::init("Tensiometro_Pulsera_v2");
    pServer = BLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacksV2());

    BLEService *pService = pServer->createService(SERVICE_UUID);

    // Característica de datos: 20 bytes por notificación
    pDataChar = pService->createCharacteristic(
        DATA_CHAR_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    pDataChar->addDescriptor(new BLE2902());

    // Característica de control (START / STOP)
    pControlChar = pService->createCharacteristic(
        CONTROL_UUID,
        BLECharacteristic::PROPERTY_WRITE
    );
    pControlChar->setCallbacks(new ControlCallbacksV2());

    // Característica de telemetría de batería (mV, %, flags)
    pBatteryChar = pService->createCharacteristic(
        BATTERY_CHAR_UUID,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY
    );
    pBatteryChar->addDescriptor(new BLE2902());

    pService->start();

    // Configurar anuncios BLE
    BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
    pAdvertising->addServiceUUID(SERVICE_UUID);
    pAdvertising->setScanResponse(true);
    pAdvertising->setMinPreferred(0x06);
    pAdvertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();

    Serial.println("[OK] Servidor BLE GATT iniciado. Esperando conexión del software...");
    checkBatteryStatus();
}

// -----------------------------------------------------------------------------
// BUCLE PRINCIPAL DE ADQUISICIÓN Y EMPAQUETAMIENTO (20 BYTES)
// -----------------------------------------------------------------------------
void loop() {
    // Comprobar batería cada 2 segundos
    if (millis() - lastBatteryCheck >= 2000) {
        lastBatteryCheck = millis();
        checkBatteryStatus();
    }

    if (deviceConnected && streamingActive) {
        particleSensor.check();

        while (particleSensor.available()) {
            redBuffer[bufferIndex] = particleSensor.getRed();
            irBuffer[bufferIndex] = particleSensor.getIR();
            particleSensor.nextSample();
            bufferIndex++;

            if (bufferIndex >= SAMPLE_PAIRS_PER_PACKET) {
                // Leer aceleración física triaxial más reciente del MPU6050
                readMPU6050Accel();

                // Construcción de la trama extendida de 20 bytes
                uint8_t payload[20];

                // Bytes 0–2: Muestra 1 Canal Rojo (uint24 Big Endian)
                payload[0] = (redBuffer[0] >> 16) & 0xFF;
                payload[1] = (redBuffer[0] >> 8)  & 0xFF;
                payload[2] = redBuffer[0] & 0xFF;

                // Bytes 3–5: Muestra 1 Canal Infrarrojo (uint24 Big Endian)
                payload[3] = (irBuffer[0] >> 16) & 0xFF;
                payload[4] = (irBuffer[0] >> 8)  & 0xFF;
                payload[5] = irBuffer[0] & 0xFF;

                // Bytes 6–8: Muestra 2 Canal Rojo (uint24 Big Endian)
                payload[6] = (redBuffer[1] >> 16) & 0xFF;
                payload[7] = (redBuffer[1] >> 8)  & 0xFF;
                payload[8] = redBuffer[1] & 0xFF;

                // Bytes 9–11: Muestra 2 Canal Infrarrojo (uint24 Big Endian)
                payload[9]  = (irBuffer[1] >> 16) & 0xFF;
                payload[10] = (irBuffer[1] >> 8)  & 0xFF;
                payload[11] = irBuffer[1] & 0xFF;

                // Bytes 12–13: Contador de secuencia (uint16 Little Endian)
                payload[12] = packetSequence & 0xFF;
                payload[13] = (packetSequence >> 8) & 0xFF;

                // Bytes 14–15: Aceleración en Eje X (int16 Little Endian)
                payload[14] = accelX & 0xFF;
                payload[15] = (accelX >> 8) & 0xFF;

                // Bytes 16–17: Aceleración en Eje Y (int16 Little Endian)
                payload[16] = accelY & 0xFF;
                payload[17] = (accelY >> 8) & 0xFF;

                // Bytes 18–19: Aceleración en Eje Z (int16 Little Endian)
                payload[18] = accelZ & 0xFF;
                payload[19] = (accelZ >> 8) & 0xFF;

                // Emisión de notificación BLE continua a 50 Hz (100 muestras/segundo reales)
                pDataChar->setValue(payload, 20);
                pDataChar->notify();

                packetSequence++;
                bufferIndex = 0;
            }
        }
    }
    delay(1);
}
