#include <Wire.h>
#include "MAX30105.h" // Librería SparkFun MAX3010x
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

MAX30105 particleSensor;

// UUIDs del Servicio de Diagnóstico y Características
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define DATA_CHAR_UUID      "beb5483e-36e1-4688-b7f5-ea07361b26a8" // Notificaciones Telemetría (14 bytes)
#define CONTROL_CHAR_UUID   "12345678-1234-1234-1234-123456789abc" // Comandos START/STOP

BLEServer* pServer = NULL;
BLECharacteristic* pDataChar = NULL;
BLECharacteristic* pControlChar = NULL;
bool deviceConnected = false;
bool streamingActive = false;
uint16_t packetSequence = 0;

// Variables para acumular las muestras de doble canal
#define SAMPLE_PAIRS_PER_PACKET 2
uint32_t redBuffer[SAMPLE_PAIRS_PER_PACKET];
uint32_t irBuffer[SAMPLE_PAIRS_PER_PACKET];
uint8_t bufferIndex = 0;

class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
        deviceConnected = true;
        Serial.println("[BLE v1] Dispositivo conectado.");
    };
    void onDisconnect(BLEServer* pServer) {
        deviceConnected = false;
        streamingActive = false;
        packetSequence = 0;
        bufferIndex = 0;
        Serial.println("[BLE v1] Desconectado. Reiniciando anuncios...");
        pServer->startAdvertising();
    }
};

class ControlCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
        String rxValue = pCharacteristic->getValue().c_str(); 
        if (rxValue.length() > 0) {
            if (rxValue == "START") {
                streamingActive = true;
                packetSequence = 0;
                bufferIndex = 0;
                Serial.println("[BLE v1] Comando START recibido.");
            } else if (rxValue == "STOP") {
                streamingActive = false;
                Serial.println("[BLE v1] Comando STOP recibido.");
            }
        }
    }
};

void setup() {
    Serial.begin(115200);
    Wire.begin(21, 22, 400000); // Fast Mode 400kHz

    if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
        Serial.println("[ERROR] Sensor MAX30102 no encontrado.");
        while (1);
    }

    // Configuración óptica de muñeca (100 Hz, 18 bits, corriente 12 mA)
    byte ledBrightness = 60;
    byte sampleAverage = 1;
    byte ledMode = 2; // Red + IR
    int sampleRate = 100;
    int pulseWidth = 411;
    int adcRange = 4096;

    particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);

    BLEDevice::init("Tensiometro_Pulsera");
    pServer = BLEDevice::createServer();
    pServer->setCallbacks(new MyServerCallbacks());

    BLEService *pService = pServer->createService(SERVICE_UUID);

    pDataChar = pService->createCharacteristic(
        DATA_CHAR_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    pDataChar->addDescriptor(new BLE2902());

    pControlChar = pService->createCharacteristic(
        CONTROL_UUID,
        BLECharacteristic::PROPERTY_WRITE
    );
    pControlChar->setCallbacks(new ControlCallbacks());

    pService->start();

    BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
    pAdvertising->addServiceUUID(SERVICE_UUID);
    pAdvertising->setScanResponse(true);
    pAdvertising->setMinPreferred(0x06);  
    pAdvertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();

    Serial.println("[OK] Firmware v1 listo y anunciando.");
}

void loop() {
    if (deviceConnected && streamingActive) {
        particleSensor.check();

        while (particleSensor.available()) {
            redBuffer[bufferIndex] = particleSensor.getRed();
            irBuffer[bufferIndex] = particleSensor.getIR();
            particleSensor.nextSample();
            bufferIndex++;

            if (bufferIndex >= SAMPLE_PAIRS_PER_PACKET) {
                uint8_t payload[14];

                // Muestra 1: Rojo (3 bytes, Big Endian)
                payload[0] = (redBuffer[0] >> 16) & 0xFF;
                payload[1] = (redBuffer[0] >> 8) & 0xFF;
                payload[2] = redBuffer[0] & 0xFF;

                // Muestra 1: Infrarrojo (3 bytes, Big Endian)
                payload[3] = (irBuffer[0] >> 16) & 0xFF;
                payload[4] = (irBuffer[0] >> 8) & 0xFF;
                payload[5] = irBuffer[0] & 0xFF;

                // Muestra 2: Rojo (3 bytes, Big Endian)
                payload[6] = (redBuffer[1] >> 16) & 0xFF;
                payload[7] = (redBuffer[1] >> 8) & 0xFF;
                payload[8] = redBuffer[1] & 0xFF;

                // Muestra 2: Infrarrojo (3 bytes, Big Endian)
                payload[9] = (irBuffer[1] >> 16) & 0xFF;
                payload[10] = (irBuffer[1] >> 8) & 0xFF;
                payload[11] = irBuffer[1] & 0xFF;

                // Secuencia (2 bytes, Little Endian)
                payload[12] = packetSequence & 0xFF;
                payload[13] = (packetSequence >> 8) & 0xFF;

                pDataChar->setValue(payload, 14);
                pDataChar->notify();

                packetSequence++;
                bufferIndex = 0;
            }
        }
    }
    delay(1);
}
