#include <RadioLib.h>
#include "Arduino_RouterBridge.h"

#define LORA_CS    10  
#define LORA_DIO0  2   
#define LORA_RESET 9   
#define LORA_BUSY  RADIOLIB_NC

SX1278 radio = new Module(LORA_CS, LORA_DIO0, LORA_RESET, LORA_BUSY);

// Global variable to hold the latest valid JSON payload
String latestJsonPayload = "{}";

// Function to return the latest JSON payload when called over RPC/Bridge
String getLatestPayload() {
  return latestJsonPayload;
}

void setup() {
  Monitor.begin();
  Bridge.begin(); // Initialize bridge communication
  delay(3000);

  // Expose the function over the Bridge RPC interface
  Bridge.provide("getLatestPayload", getLatestPayload);

  int state = radio.begin(433.0, 125.0, 7, 5, 0x12, 10, 8);
  if (state == RADIOLIB_ERR_NONE) {
    radio.setSyncWord(0x12);
    radio.setPreambleLength(8);
    radio.explicitHeader();
    radio.setCRC(true);
    radio.startReceive();
  } else {
    while (1);
  }
}

void loop() {
  Bridge.update(); // Maintain bridge communication

  if (digitalRead(LORA_DIO0) == HIGH) {
    String payload = "";
    int state = radio.readData(payload);

    if (state != RADIOLIB_ERR_NONE) {
      uint8_t buffer[16] = {0};
      if (radio.readData(buffer, 16) == RADIOLIB_ERR_NONE) {
        payload = "";
        for (int i = 0; i < 16; i++) {
          if (buffer[i] >= 32 && buffer[i] <= 126) payload += (char)buffer[i];
        }
      }
    }

    float snr = radio.getSNR();
    float rssi = radio.getRSSI();

    if (payload.length() > 0 && snr > -12.0) {
      // Build string first to avoid RPC type mismatches
      String rssiStr = String(rssi, 2);
      String snrStr = String(snr, 2);
      String jsonOut = "{\"payload\":\"" + payload + "\",\"rssi\":" + rssiStr + ",\"snr\":" + snrStr + "}";

      // Save to global string for remote calls
      latestJsonPayload = jsonOut;

      // Print to monitor serial stream
      Monitor.println(jsonOut);

      // Send ACK back
      delay(50);
      radio.transmit("ok");
      radio.explicitHeader();
      radio.setCRC(true);
    }

    radio.startReceive();
  }
  delay(20);
}