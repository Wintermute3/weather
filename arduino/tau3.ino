// ============================================================================
// tau3.ino             Michael Nagy, bright.tiger@gmail.com          1.809.141
// ============================================================================
// Using a NodeMCU, publish the dry-contact relay output status from a Tornado
// Alert Unit (TAU) via a simple web interface.  Upon query of the URL:
//
//     http://tau.local/
//
// an integer value of 1 or 0 will be returned as the value of a field named
// 'tau', encoded in a json block.  A value of 1 indicates the tornado alert
// unit is alerting (contact-closure output is closed), while a value of 0
// indicates it is quiescent (contact-closure output is open).
// ============================================================================
//       Arduino IDE configuration: Board = NodeMCU 1.0 (ESP-12E Module)
//                                  Port  = /dev/ttyUSB0
// ============================================================================
// Configure the Arduino IDE for 'NodeMCU 1.0 (ESP-11E Module)'.  On Kubuntu,
// the cp210x serial driver is required, and presents /dev/ttyUSB0 as the serial
// port.  Internal debugging output is configured for 115,200 baud, so set that
// speed if you bring up the serial monitor.
//
// Notes:
//     - the D0 output is the led near the USB connector (red or blue)
//     - the D4 output is the led on the ESP8266 submodule (blue)
//     - the D7 input is pulled up (short to ground to signal)
// ============================================================================
// On the ModeMCU module, the following components are added and connected
// as indicated:
//
//     - 470uF and 0.15uF capacitors are connected between Vin and Gnd
//     - a 1K resistor is connected between 3V3 and D7
//     - a 0.15uF capacitor is connected between D7 and Gnd
//
// Power from the external 5V linear regulated PSU comes in via Vin and Gnd.
// The PSU voltage needs to be at least 5V, and could go up to around 12V with
// no issue, however the PSU needs to be either an unregulated or linear
// regulated one.  Switching PSUs generate too much static and interfere with
// the function of the TAU itself.  I am using a Jameco 168605 5v 1A regulated
// linear adapter.
//
// The TAU normally-open switch connection comes in between D7 and Gnd.
// ============================================================================
// Upon initialization, the USB led will blink twice a second until the WiFi
// connection is established, then the ESP led will blink once.  After that
// initial sequence, both leds will normally be off.  The ESP led will light
// whenever a relay closure is detected, and the USB led will blink once each
// time another network device queries the status url.
// ============================================================================
// The ESP led will also pulse every 5 seconds (heartbeat) to let you know
// things are running.
// ============================================================================

#define VERSION "1.810.031"

#include <ESP8266WiFi.h>
#include <WiFiClient.h>
#include <ESP8266WebServer.h>
#include <elapsedMillis.h>

#include "passwords.h"

ESP8266WebServer server(80);

#define LED_USB    16 // D0 of nodemcu, usb led (red or blue)
#define LED_ESP     2 // D4 of nodemcu, esp led (blue)
#define SWITCH_IN  13 // D7 of nodemcu, pulled high

void Blink(int LED, int Count, int Delay) {
  while (Count--) {
    digitalWrite(LED, LOW);
    yield();
    delay(Delay);
    yield();
    digitalWrite(LED, HIGH);
    yield();
    delay(Delay);
    yield();
} }

int TauAlert   = 1; // switch closure status
int UptimeSecs = 0; // seconds since boot

void setup(void){
  pinMode(LED_USB, OUTPUT);
  pinMode(LED_ESP, OUTPUT);
  digitalWrite(LED_USB, HIGH);
  digitalWrite(LED_ESP, HIGH);
  pinMode(SWITCH_IN, INPUT_PULLUP);
  Serial.begin(115200);
  Serial.println();
  delay(5000);
  Serial.println("setting station mode...");
  WiFi.mode(WIFI_STA);
  Serial.print("connecting...");
  WiFi.begin(ssid, password);
  Serial.println("");
  while (WiFi.status() != WL_CONNECTED) {
    Blink(LED_USB, 1, 1);
    Serial.print(".");
    yield();
    delay(500);
    yield();
  }
  Serial.println("");
  Serial.print("connected to ");
  Serial.println(ssid);
  Serial.print("ip address: ");
  Serial.println(WiFi.localIP());
  Blink(LED_ESP, 1, 1);
  server.on("/", [](){
    String Message = "{alert=";
    Message += TauAlert;
    Message += ",version=\"";
    Message += VERSION;
    Message += "\",uptime_secs=";
    Message += UptimeSecs;
    Message += "}\n";
    Serial.print("/ ");
    Serial.print(Message);
    server.send(200, "application/json", Message);
    yield();
    Blink(LED_USB, 2, 250);
  });
  server.begin();
  Serial.println("http server started");
}

elapsedMillis  OneSecondTimer = 0; // auto-increments every millisecond
elapsedMillis FiveSecondTimer = 0; // auto-increments every millisecond

void loop(void){
  yield();
  if (digitalRead(SWITCH_IN) == LOW) {
    Blink(LED_ESP, 1, 1);
    if (TauAlert == 0) {
      TauAlert = 1;
      Serial.print("   alert=");
      Serial.println(TauAlert);
    }
  } else {
    if (TauAlert == 1) {
      TauAlert = 0;
      Serial.print("   alert=");
      Serial.println(TauAlert);
  } }
  yield();
  server.handleClient();
  yield();
  if (OneSecondTimer > 1000) { // five-second heartbeat
    OneSecondTimer -= 1000;
    UptimeSecs += 1;
  }
  if (FiveSecondTimer > 5000) { // five-second heartbeat
    FiveSecondTimer -= 5000;
    Blink(LED_ESP, 1, 1);
} }

// ============================================================================
// End.
// ============================================================================

