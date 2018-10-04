// ============================================================================
// weatherbox3.ino             bright.tiger@gmail.com              Michael Nagy
// ============================================================================

#define PROJECT  "weatherbox3"
#define VERSION    "1.809.301"
#define EMAIL1   "bright.tiger"
#define EMAIL2     "@gmail.com"

// ============================================================================
// Arduino IDE configuration:
//
//    - tools -> board = Arduino/Genuine Uno
//    - tools -> port  = /dev/ttyACM0
//    - tools -> serial monitor (optional, 115000 baud)
// ============================================================================
// The WeatherBox is built around a Freetronics EtherTen, which is essentially
// an Arduino Uno with an integrated Ethernet Shield w/PoE support.  It has
// the following outboard components connected:
//
//     - a switchdoc weatherboard sensor interface with integrated bmp280
//         barometric pressure sensor
//     - a switchdoc weatherrack sensor cluster (via rj11's on weatherboard)
//         rain gauge
//         wind speed sensor
//         wind direction sensor
//     - a ds3231 rtc (via dedicated header on weatherboard)
//         realtime clock module
//     - an adafruit 32KB i2c fram memory module (via dedicated header on weatherboard)
//         nonvolatile memory for logging
//     - an am2315 sensor (via grove/i2c on weatherboard)
//         temperature sensor
//         humidity sensor
//     - a 4x20 character display (via grove/i2c on weatherboard)
//         lcd display (http://wiki.sunfounder.cc/index.php?title=I2C_LCD2004)
//
// Specs for the weatherboard and weatherrack are at:
//
//   http://www.switchdoc.com/wp-content/uploads/2016/07/WeatherBoard_CurrentSpecification.pdf
//   http://www.switchdoc.com/wp-content/uploads/2015/01/WeatherRack-011514-V1.0.pdf
//
// External libraries you will need to compile this:
//
//   http://downloads.arduino.cc/libraries/github.com/adafruit/Adafruit_Unified_Sensor-1.0.2.zip
//   http://downloads.arduino.cc/libraries/github.com/adafruit/Adafruit_BMP280_Library-1.0.2.zip
//   https://github.com/adafruit/Adafruit_FRAM_I2C/archive/master.zip
//   http://wiki.sunfounder.cc/images/7/7e/LiquidCrystal_I2C.zip
//
// The EtherTen runs at 5v from a 7-12v supply input via the Ethernet cable
// using a passive PoE injector connected to a 9v PSU.  The display and sensor
// peripherals run at 5v.  All peripherals are connected via a shared I2C
// interface.  The EtherTen includes an SD Card interface, but we don't use it
// as the required filesystem support eats way too much RAM that could be better
// used elsewhere, and the FRAM chip handles our logging requirements nicely.
//
// The system implements a REST API which delivers a JSON data package which
// includes all weather data along with various diagnostics.  Provision for
// verifying and setting the realtime clock are included in the REST API, and
// the realtime clock drives the daily aggregate rain reset algorithm.  The REST
// API also allows you to register the address of a Tornado Alert Unit (TAU)
// monitor, which if present will be queried periodically and its status included
// with the other weather data (as UV value).
//
// The 32KB FRAM memory is used to implement data logging, and can keep a history
// of over two weeks of weather datasets recorded every 15 minutes.
//
// External and internal connections to etherten board:
//
//    power+data - ethernet with 9v PoE
//
//    weatherboard - analog input
//      a2  orange . . . . wind direction - analog voltage
//
//    weatherboard - digital i/o
//      a4  gray . . . . . i2c sda
//      a5  white  . . . . i2c scl
//
//    weatherboard - digital interrupt inputs
//      d2  green  . . . . wind speed - 1.492 MPH per pulse low
//      d3  yellow . . . . rainfall - 0.011" per pulse low
//
//    digital - to ethernet shield
//      d4  internal . . . spi ss sd card (pulled down 3K2, unused)
//
//    digital - to ethernet shield
//      d10 internal . . . spi ss wiznet w5100 ethernet controller
//      dll internal . . . spi mosi
//      d12 internal . . . spi miso
//      d13 internal . . . spi sck
//
//    power - to weatherboard
//      gnd black  . . . . digital ground
//      5v  red  . . . . . digital power
//
// The i2c devices have the following addresses:
//
//      0x77  bmp280 barometric pressure sensor
//      0x68  ds3231 realtime clock
//      0x5c  am2315 temperature and humidity sensor
//      0x50  MB85RC256V 32KB FRAM nonvolatile memory
//      0x27  sunfounder 2004 (4x20) lcd display (alternate 0x3f)
//
// All i2c devices are either integrated directly on the weatherboard or
// plugged into it via grove connectors.
// ============================================================================

typedef unsigned long  int    bit32;
typedef   signed long  int    int32;
typedef unsigned short int    bit16;
typedef   signed short int    int16;
typedef                byte   bit8 ;
typedef                char * cptr ;

#define BUFFER_MAX 60

char Buffer[BUFFER_MAX];
bit8 BufferLen = 0;

bit16 BootCount     = 0;
bit16 UptimeMins = 0;

// ============================================================================
// Ethernet parameters.
// ============================================================================

#include "Ethernet.h"

const bit8 EnetMac[] = {0x18, 0xfe, 0x34, 0x9f, 0xaf, 0x75}; // semirandom

IPAddress EnetServerIPv4(192, 168, 18, 107); // our static local network address

#define ENET_PORT 80
#define TAU_PORT  80

EthernetServer EnetServer(ENET_PORT);
EthernetClient EnetClient           ;

// ============================================================================
// Tornado Alert Unit parameters.
// ============================================================================

char TauAddress[16];

bit8 LastTau    = 0; // most recent query value
bit8 LastTauMax = 0; // max over this log period

// ============================================================================
// Handle i2c devices.
// ============================================================================

#include "Wire.h" // i2c interface

//      0x77  bmp280 barometric pressure sensor
//      0x68  ds3231 realtime clock
//      0x5c  am2315 temperature and humidity sensor
//      0x50  MB85RC256V 32KB FRAM nonvolatile memory
//      0x27  sunfounder 2004 20-char 4-line lcd display (alternate 0x3f)

#define WPI_I2C_FRAM     0x50 // ferromagnetic random-access memory
#define WPI_I2C_BMP280   0x77 // barometric pressure sensor
#define WPI_I2C_DS3231   0x68 // realtime clock
#define WPI_I2C_AM2315   0x5C // humidity/temperature sensor
#define WPI_I2C_OLED     0x27 // oled display

// ============================================================================
// DS3231 realtime clock interface.
// ============================================================================

bit8  LastSecond  = 0; // 0..59 second of minute
bit8  LastMinute  = 0; // 0..59 minute of hour
bit8  LastHour    = 0; // 0..23 hour of day
bit8  LastWeekday = 1; // 1..07 day of week
bit8  LastDay     = 1; // 1..31 day of month
bit8  LastMonth   = 1; // 1..12 month of year
bit16 LastYear    = 0; // 0..99 year of century (assumed to be 21st)

// Convert normal decimal numbers to binary coded decimal.

bit8 DecToBcd(bit8 val) {
  return ((val / 10 * 16) + (val % 10));
}

// Convert binary coded decimal to normal decimal numbers.

bit8 BcdToDec(bit8 val) {
  return ((val / 16 * 10) + (val % 16));
}

// Write the current values of the global variables to the RTC hardware.
// Assume twenty-first century (20xx year) and set 24-hour clock mode.

void WriteTime() {
  delay(10);
  Wire.beginTransmission(WPI_I2C_DS3231);
  Wire.write(0);
  Wire.write(DecToBcd(LastSecond ));
  Wire.write(DecToBcd(LastMinute ));
  Wire.write(DecToBcd(LastHour   )); // also sets 24-hour mode
  Wire.write(DecToBcd(LastWeekday));
  Wire.write(DecToBcd(LastDay    ));
  Wire.write(DecToBcd(LastMonth  ));
  Wire.write(DecToBcd(LastYear   )); // 20xx
  Wire.endTransmission();
  delay(10);
}

// Read the current time from the RTC into global variables.

void ReadTime() {
  delay(10);
  Wire.beginTransmission(WPI_I2C_DS3231);
  Wire.write(0);
  Wire.endTransmission();
  Wire.requestFrom(WPI_I2C_DS3231, 7);
  delay(10);
  LastSecond  = BcdToDec(Wire.read());
  LastMinute  = BcdToDec(Wire.read());
  LastHour    = BcdToDec(Wire.read());
  LastWeekday = BcdToDec(Wire.read());
  LastDay     = BcdToDec(Wire.read());
  LastMonth   = BcdToDec(Wire.read());
  LastYear    = BcdToDec(Wire.read()); // 20xx
}

// ============================================================================
// Return humidity in percent 0-100 and temperature (celsius).  Although there
// are two temperature sensors, one on the WeatherBoard (along with the
// barometric pressure sensor), and one on the AM2315 (along with the humidity
// sensor), we use the one on the AM2315 as it appears more accurate.
// ============================================================================

bit8  LastHumi     = 0; // percent
int16 LastTemp     = 0; // unit = 0.1 celsius
int16 LastDewpoint = 0; // unit = 0.1 celsius

void ReadHumi() { // and Temp
  bit8 Reply[8];
  delay(10);
  Wire.beginTransmission(WPI_I2C_AM2315);
  delay(10);
  Wire.endTransmission();
  delay(10);
  Wire.beginTransmission(WPI_I2C_AM2315);
  Wire.write(0x03); // read register
  Wire.write(0x00); // start at address zero
  Wire.write(0x04); // request 4 data byes
  Wire.endTransmission();
  delay(10);
  Wire.requestFrom(WPI_I2C_AM2315, 8);
  for (bit8 i = 0; i < 8; i++) {
    Reply[i] = Wire.read();
  }
  delay(10);
  if ((Reply[0] == 0x03) && (Reply[1] == 0x04)) {
    bit16 Val = Reply[2];
    Val *= 256;
    Val += Reply[3];
    LastHumi = (bit8) (Val / 10);
    LastTemp = Reply[4] & 0x7F;
    LastTemp *= 256;
    LastTemp += Reply[5];
    if (Reply[4] & 0x80) {
      LastTemp = -LastTemp;
    }
  } else {
    Serial.print(F("humi.error..."));
} }

// ============================================================================
// Return barometric pressure in 0.001 inches of mercury units.
// ============================================================================

#include "Adafruit_Sensor.h"
#include "Adafruit_BMP280.h"

Adafruit_BMP280 bmp;

void InitPres() {
  Serial.print(F("  pres..."));
  bmp.begin(WPI_I2C_BMP280);
  Serial.println(F("done"));
}

bit16 LastPres = 0; // in units of 0.001 inches of mercury

void ReadPres() {
  LastPres = round(bmp.readPressure() * 0.2953);
}

// ============================================================================
// Set up interrupt-enabled digital inputs with pullups enabled for the
// anemometer and the rain gauge.
// ============================================================================

#define WPI_VANE  A2 // orange - analog voltage
#define WPI_ANEM   2 // green  - digital pulses low on each click (dirty)
#define WPI_RAIN   3 // yellow - digital pulses low on each click (dirty)

void InitPins() { // and Rain
  pinMode(WPI_ANEM, INPUT); digitalWrite(WPI_ANEM, HIGH); attachInterrupt(digitalPinToInterrupt(WPI_ANEM), AnemInterrupt, RISING);
  pinMode(WPI_RAIN, INPUT); digitalWrite(WPI_RAIN, HIGH); attachInterrupt(digitalPinToInterrupt(WPI_RAIN), RainInterrupt, RISING);
}

// ============================================================================
// Return wind direction as an integer compass heading 0-360 degrees.  The
// sensor returns one of 16 bearings, voltage mapped to 0-1024 in a bizarre
// sequence.  Return the one that is closest.  If an unmappable reading is
// returned by the sensor, just return the last good wind direction.
// ============================================================================

const bit16 VaneMap[16][2] = {
  {  65,  5 }, // 112.5
  {  84,  3 }, //  67.5
  {  93,  4 }, //  90
  { 127,  7 }, // 157.5
  { 183,  6 }, // 135
  { 245,  9 }, // 202.5
  { 285,  8 }, // 180
  { 406,  1 }, //  22.5
  { 462,  2 }, //  45
  { 599, 11 }, // 247.5
  { 630, 10 }, // 225
  { 701, 15 }, // 337.5
  { 785,  0 }, //   0
  { 829, 13 }, // 295.5
  { 887, 14 }, // 315
  { 946, 12 }, // 270
};

bit8 LastVane = 0; // multiply by 22.5 to get compass bearing

void ReadVane() {
  bit16 Value = analogRead(WPI_VANE);
  for (bit8 i = 0; i < 16; i++) {
    bit16 Mid = VaneMap[i][0];
    bit16 Min = (i ==  0) ?  30 : (VaneMap[i - 1][0] + Mid) / 2;
    bit16 Max = (i == 15) ? 980 : (VaneMap[i + 1][0] + Mid) / 2;
    if ((Min < Value) && (Value < Max)) {
      LastVane = (bit8) VaneMap[i][1];
} } }

// ============================================================================
// Maintain anemometer counter (debounced interrupt).  Periodically calculate:
//
//   - wind speed (mph, average over last 2 seconds)
//
// The anemometer measures wind speed by closing a contact as a magnet moves
// past a switch. One contact closure a second indicates 1.492 MPH (2.4 km/h).
// ============================================================================

bit32 AnemEvent = 0; // time of last interrupt
bit32 AnemStart = 0; // time of first counted interrupt
int16 AnemCount = 0; // debounced interrupt count

int16 AnemTotalCount  = 0; // accumulated interrupt count
bit8  AnemTotalMinute = 0; // accumulator bucket

void AnemInterrupt() {
  bit32 Now = micros();
  if (Now - AnemEvent > 1000) { // 1 millisecond
    if (AnemCount == 0) {
      AnemStart = Now;
      AnemTotalMinute = LastMinute;
    }
    AnemCount++;
    AnemTotalCount++;
  }
  AnemEvent = Now;
}

// For our purposes, instantaneous wind speed is based on the average number
// of counts/second over the last 2+ seconds.

bit8 LastAnem    = 0; // in mph, instantaneous
bit8 LastAnemAvg = 0; // in mph, last minute average
bit8 LastAnemMax = 0; // in mph, maximum since midnight

#define AnemMicroSecs 2000000 // 2 seconds minimum measurement period

// We can count on being called around once every 5 seconds.  If it has been
// more than 2 seconds since we were last called, reset the interrupt count
// accumulator and update the instantaneous windspeed.  If a new minute is
// starting (based on realtime clock), calculate the average windspeed over
// the previous minute and reset the minute count accumulator.  Also update
// the max windspeed, which will be reset each day at midnight.

void ReadAnem() {
  bit32 Period, Now = micros();
  bit16 Count, Total = 0;
  noInterrupts();
  Period = Now - AnemStart;
  if (Period > AnemMicroSecs) {
    Count = AnemCount;
    AnemStart = Now;
    AnemCount = 0;
    if (AnemTotalMinute != LastMinute) {
      Total = AnemTotalCount;
      AnemTotalCount = 0;
    }
  } else {
    Period = 0;
  }
  interrupts();
  if (Period) {
    LastAnem = round((Count * 1.492 * AnemMicroSecs) / (float) Period);
    if (LastAnem > LastAnemMax) {
      LastAnemMax = LastAnem;
  } }
  if (Total) {
    LastAnemAvg = round((Total * 1.492) / 60.0);
} }

// ============================================================================
// Maintain rainbucket counter (debounced interrupt).  Periodically calculate:
//
//   - rain inches (inches in the last hour, sum of last 60 1-minute buckets)
//
// Each contact closure of the rain sensor indicates 0.011 inch (0.2794 mm).
// ============================================================================

bit32 RainEvent = 0; // time of last interrupt
bit32 RainStart = 0; // time of first counted interrupt
bit8  RainCount = 0; // debounced interrupt count

void RainInterrupt() {
  bit32 Now = micros();
  if (Now - RainEvent > 1000) {
    if (RainCount == 0) {
      RainStart = Now;
    }
    RainCount++;
  }
  RainEvent = Now;
}

// For our purposes, rain rate is based on the total number of counts
// in the last hour.

bit8 RainBucketCount[60];
bit8 RainBucketIndex = 100; // trigger auto-init

bit16 LastRain    = 0; // in units of 0.011 inches
bit16 LastRainDay = 0; // in units of 0.011 inches

#define RainBucketMicroSecs 60000000 // 60 seconds

void ReadRain() {
  bit32 Period, Now = micros();
  bit8 Count;
  if (RainBucketIndex == 100) { // one-time auto-initialization
    RainBucketIndex = 0;
    for (Count = 0; Count < 60; Count++) {
      RainBucketCount[Count] = 0;
  } }
  noInterrupts();
  Period = Now - RainStart;
  if (Period > RainBucketMicroSecs) {
    Count = RainCount;
    RainStart = Now;
    RainCount = 0;
  } else {
    Period = 0;
  }
  interrupts();
  if (Period) { // a new minute has elapsed

    LastRainDay += Count; // accumulate total rainfall

    // Track short-term rainfall over the last 60 minutes using a
    // circular buffer of 60 one-minute accumulators.

    RainBucketCount[RainBucketIndex++] = Count;
    if (RainBucketIndex == 60) {
      RainBucketIndex = 0;
    }
    LastRain = 0;
    for (Count = 0; Count < 60; Count++) {
      LastRain += RainBucketCount[Count];
} } }

// ============================================================================
// Clear daily accumulators for rainfall and windspeed data.
// ============================================================================

bit8 TauAlive = 0;
bit16 HttpGets, TauQueries, TauReplies;

void ClearTotals() {
  LastRainDay = LastAnemMax = HttpGets = TauQueries = TauReplies = 0;
}

// ============================================================================
// Data structures which define how we store things in FRAM.  The layout in
// FRAM is functionally:
//
//      LogHeader  Header
//      LogRecord  Working
//      LogRecord  Log[LogSize]
//
// The header keeps track of which records in the log are in use, while the
// working log record is an image of the next record which will be logged,
// updated each time sensors and the realtime clock are read.
// ============================================================================

#define FRAM_MAGIC 0x395c

typedef struct {
  bit16 Magic      ; // must be first two bytes on chip
  bit16 LogSize    ; // size of each log record
  bit16 LogNext    ; // index of next log entry to write [0..LogSize-1]
  bit16 BootCount  ; // number of boots since fram cleared
  bit8  Address[16]; // persistent tau address
  bit8  LogFull    ; // nonzero indicates log has wrapped
} LogHeader;

typedef struct {
  bit8  Year      ; // 00..99
  bit8  Month     ; // 00..11
  bit8  Day       ; // 00..31
  bit8  Hour      ; // 00..23
  bit8  Minute    ; // 00..59
  bit8  Second    ; // 00..59
  bit8  Humi      ; // percent
  bit8  Vane      ; // unit = 22.5 degrees
  bit8  Anem      ; // unit = mph
  bit8  AnemAvg   ; // unit = mph, last 60 minutes
  bit8  AnemMax   ; // unit = mph, since midnight
  bit8  TauMax    ; // 0..9
  bit8  TauSet    ; // 0..1
  bit16 Temp      ; // unit = 0.1 celsius
  bit16 Dewpoint  ; // unit = 0.1 celsius
  bit16 Rain      ; // unit = 0.011 inch, last 60 minutes
  bit16 RainDay   ; // unit = 0.011 inch, since midnight
  bit16 Pres      ; // unit = 0.001 inches of mercury
  bit16 BootCount ;
  bit16 UptimeMins;
} LogRecord;

#define LOG_HEADER_SIZE  sizeof(LogHeader)
#define LOG_RECORD_SIZE  sizeof(LogRecord)

#define FRAM_SIZE  32768 // 32KB

// Calculate the number of log records we can record, taking into account
// the available memory, the header and the extra working log record which
// follows it.

const bit16 LogSize = (FRAM_SIZE - LOG_HEADER_SIZE - LOG_RECORD_SIZE) / LOG_RECORD_SIZE;

// ============================================================================
// Nonvolatile FRAM (ferromagnetic random-access memory) interface.
// ============================================================================

#include "Adafruit_FRAM_I2C.h"

Adafruit_FRAM_I2C fram = Adafruit_FRAM_I2C();

void FramWriteByte(bit16 Address, bit8 Value) {
  delay(1);
  fram.write8(Address, Value);
}

void FramWriteWord(bit16 Address, bit16 Value) {
  FramWriteByte(Address    , Value >> 8  );
  FramWriteByte(Address + 1, Value & 0xff);
}

bit8 FramReadByte(bit16 Address) {
  delay(1);
  return fram.read8(Address);
}

bit16 FramReadWord(bit16 Address) {
  return (FramReadByte(Address) << 8) | FramReadByte(Address + 1);
}

void TauSave() {
  for (bit8 Index = 0; Index < sizeof(TauAddress); Index++) {
    FramWriteByte(offsetof(LogHeader, Address) + Index, TauAddress[Index]);
} }

void TauLoad() {
  for (bit8 Index = 0; Index < sizeof(TauAddress); Index++) {
    TauAddress[Index] = FramReadByte(offsetof(LogHeader, Address) + Index);
} }

void TauWipe() {
  LastTau = LastTauMax = TauAlive = TauQueries = TauReplies = 0;
  for (bit8 Index = 0; Index < sizeof(TauAddress); Index++) {
    TauAddress[Index] = 0;
} }

// If the FRAM_MAGIC signature isn't found in the log header as expected, we
// assume the FRAM chip has not been initialized and do so by clearing the
// entire chip to zeros and then setting the FRAM_MAGIC value in the header.
// Likewise if the LogSize value doesn't match what is currently calculated.
// Upon successful init, we restore the boot counter and TAU address.

void InitFram() {
  Serial.print(F("  fram..."));
  fram.begin(WPI_I2C_FRAM);
  delay(10);
  bit16 OffsetMagic = offsetof(LogHeader, Magic    );
  bit16 OffsetSize  = offsetof(LogHeader, LogSize  );
  bit16 OffsetBoot  = offsetof(LogHeader, BootCount);
  if ((FramReadWord(OffsetMagic) != FRAM_MAGIC) || FramReadWord(OffsetSize) != LogSize) {
    Serial.print(F("wipe..."));
    for (bit16 Address = 0; Address < 32768; Address++) {
      FramWriteByte(Address, 0);
    }
    FramWriteWord(OffsetMagic, FRAM_MAGIC);
    FramWriteWord(OffsetSize , LogSize   );
    if ((FramReadWord(OffsetMagic) != FRAM_MAGIC) || FramReadWord(OffsetSize) != LogSize) {
      Serial.print(F("error..."));
    } else {
      Serial.print(F("verified..."));
  } }
  BootCount = FramReadWord(OffsetBoot) + 1;
  FramWriteWord(OffsetBoot, BootCount);
  TauLoad();
  Serial.println(F("done"));
}

// ============================================================================
// Query a Tornado Alert Unit (TAU) if configured. The URL looks like:
//
//     http://192.168.18.101/
//
// And the response looks like:
//
//     HTTP/1.1 200 OK
//     Content-Type: application/json
//     Content-Length: 37
//     Connection: close
//
//     {alert=0,version="1.809.141",uptime_secs=125}
//
// The alert value will be either 0 or 1, which we will map to a LastTau value
// of 1 and 9 respectively.  If we get no response from the TAU we set LastTau
// to a value of 0.
// ============================================================================

void EnetTauQuery() {
  LastTau = 0; // assume failure
  if (TauAddress[0]) {
    TauQueries++;
    Serial.print(F("query tau..."));
    Serial.print(TauAddress);
    Serial.print(F("..."));
    IPAddress Target;
    if (Target.fromString(TauAddress)) {
      if (EnetClient.connect(Target, TAU_PORT)) {
        Serial.print(F("connected..."));
        EnetClient.println(F("GET / HTTP/1.1"));
        EnetClient.println(F("Connection: close"));
        EnetClient.println();
        while (EnetClient.connected() && !EnetClient.available()) {
          delay(10);
        }
        while (EnetClient.connected()) {
          int Available = EnetClient.available();
          if (Available > 0) {
            if (Available > BUFFER_MAX - 1) {
              Available = BUFFER_MAX - 1;
            }
            EnetClient.read(Buffer, Available);
            Buffer[Available] = 0;
            if (cptr Alert = strstr(Buffer, "alert=")) {
              LastTau = atoi(Alert+6) ? 9 : 1;
              TauReplies++;
          } }
          delay(10);
        }
        EnetClient.stop();
        Serial.print(F("done"));
      } else {
        Serial.print(F("connect.error"));
      }
    } else {
      Serial.print(F("address.error"));
  } }
  Serial.print(F("...lasttau="));
  Serial.println(itoa(LastTau, Buffer, 10));
  if (LastTauMax < LastTau) {
    LastTauMax = LastTau;
} }

// ============================================================================
// Copy the working log record into the permanent log and adjust the log
// header to reflect the newly logged record and the offset of the next
// record to write.  Also set the full flag if the log wraps.
// ============================================================================

void LogData() {
  Serial.print(F("logging data..."));
  bit16 LogNext = FramReadWord(offsetof(LogHeader, LogNext)) + 1;
  bit16 Source = LOG_HEADER_SIZE;
  bit16 Target = LOG_HEADER_SIZE + (LogNext * LOG_RECORD_SIZE);
  for (bit8 Offset = 0; Offset < LOG_RECORD_SIZE; Offset++) {
    FramWriteByte(Target + Offset, FramReadByte(Source + Offset));
  }
  if (LogNext == LogSize) {
    LogNext = 0;
    FramWriteByte(offsetof(LogHeader, LogFull), 1);
  }
  FramWriteWord(offsetof(LogHeader, LogNext), LogNext);
  LastTauMax = 0;
  Serial.println(F("done"));
}

// ============================================================================
// Read all sensors and update static measurement values.
// ============================================================================

void ReadSensors() {
  Serial.print(F("read sensors..."));
  ReadVane(); // update LastVane
  ReadAnem(); // update LastAnem, LastAnemAvg and LastAnemMax
  ReadRain(); // update LastRain and LastRainDay
  ReadHumi(); // update LastHumi and LastTemp
  ReadPres(); // update LastPres
  ReadTime(); // update RtcXX
  float Temp = LastTemp / 10.0;
  float Dewp = 243.04 * (         log(LastHumi / 100.0) + ((17.625 * Temp) / (243.04 + Temp))) /
               (17.625 - log(LastHumi / 100.0) - ((17.625 * Temp) / (243.04 + Temp)));
  LastDewpoint = round(Dewp * 10.0);
  Serial.print(F("update header..."));
  bit8 TauSet = 0;
  if (strlen(TauAddress)) {
    TauSet = 1;
  }
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, Year      ), LastYear    );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, Month     ), LastMonth   );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, Day       ), LastDay     );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, Hour      ), LastHour    );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, Minute    ), LastMinute  );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, Second    ), LastSecond  );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, Humi      ), LastHumi    );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, Vane      ), LastVane    );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, Anem      ), LastAnem    );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, AnemAvg   ), LastAnemAvg );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, AnemMax   ), LastAnemMax );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, TauMax    ), LastTauMax  );
  FramWriteByte(LOG_HEADER_SIZE + offsetof(LogRecord, TauSet    ), TauSet      );
  FramWriteWord(LOG_HEADER_SIZE + offsetof(LogRecord, Temp      ), LastTemp    );
  FramWriteWord(LOG_HEADER_SIZE + offsetof(LogRecord, Dewpoint  ), LastDewpoint);
  FramWriteWord(LOG_HEADER_SIZE + offsetof(LogRecord, Rain      ), LastRain    );
  FramWriteWord(LOG_HEADER_SIZE + offsetof(LogRecord, RainDay   ), LastRainDay );
  FramWriteWord(LOG_HEADER_SIZE + offsetof(LogRecord, Pres      ), LastPres    );
  FramWriteWord(LOG_HEADER_SIZE + offsetof(LogRecord, BootCount ), BootCount   );
  FramWriteWord(LOG_HEADER_SIZE + offsetof(LogRecord, UptimeMins), UptimeMins  );
  Serial.println(F("done"));
}

// ============================================================================
// Handle spi devices.
// ============================================================================

#include "SPI.h"

// ============================================================================
// Ethernet support.
// ============================================================================

// Start the Ethernet connection and the server.

void InitEnet() {
  Serial.print(F("  enet..."));
  delay(500); // assure ethernet reset complete
  Ethernet.begin(EnetMac, EnetServerIPv4);
  EnetServer.begin();
  Serial.print(F("server address "));
  Serial.print(Ethernet.localIP());
  Serial.println(F("...done"));
}

// ============================================================================
// JSON format helpers.  Call JsonFrame('{') to start a JSON block, then call
// JsonText|Bit16|Float in any order as many times as you like.  Finally, call
// JsonFrame('}') to close the JSON block.
// ============================================================================

bit8 JsonState = 0; // nonzero after initial key: value pair processed

cptr JsonFrame(char Clue) {
  switch (Clue) {
    case '{':
      JsonState = 0;
      return "{\n  ";
    case ',': // return prefix separator for next key: value pair
      if (JsonState) {
        return ",\n  ";
      }
      JsonState = 1;
      return "";
    case '}':
      JsonState = 0;
      return "\n}\n";
} }

void JsonText(EthernetClient &client, const __FlashStringHelper * Key, cptr Value) {
  client.print(JsonFrame(','));
  client.print(F("\""));
  client.print(Key);
  client.print(F("\": \""));
  client.print(Value);
  client.print(F("\""));
}

void JsonBit16(EthernetClient &client, const __FlashStringHelper * Key, bit16 Value) {
  client.print(JsonFrame(','));
  client.print(F("\""));
  client.print(Key);
  client.print(F("\": "));
  client.print(Value);
}

void JsonScale(EthernetClient &client, const __FlashStringHelper * Key, bit16 Value, float Unit, bit8 Decimals) {
  char Temp[10];
  client.print(JsonFrame(','));
  client.print(F("\""));
  client.print(Key);
  client.print(F("\": "));
  dtostrf(Value * Unit, 3, Decimals, Temp);
  client.print(Temp);
}

// ============================================================================
// Process HTTP GET requests.  We handle the following commands:
//
//    dump
//    reset                       clear log and reset accumulators
//    time?yyyy&mm&dd&hh&mm&ss    set realtime clock
//    tau                         get tau address
//    tau?                        clear tau address
//    tau?xxx.xxx.xxx.xxx         set tau address
//    now                         return current weather data
//    log?index                   return weather data history
// ============================================================================

// Send back a list of valid endpoints.

cptr DoHelp() {
  return 
    "\n"
    "dump [64 bytes fram]\n"
    "reset\n"
    "time?yyyy&mm&dd&hh&mm&ss [utc]\n"
    "tau [query status]\n"
    "tau? [disable]\n"
    "tau?xxx.xxx.xxx.xxx\n"
    "now\n"
    "log?index\n";
}

// Clear all that should be cleared.

cptr DoReset() {
  Serial.print(F("  reset..."));
  ClearTotals();
  RainBucketIndex = 100;
  for (bit16 Address = 0; Address < 32768; Address++) {
    FramWriteByte(Address, 0);
  }
  FramWriteWord(0, FRAM_MAGIC);
  FramWriteWord(2, LogSize   );
  TauWipe();
  Serial.println(F("ok"));
  return "reset.ok";
}

// Display the first 64 bytes of FRAM on the console.

cptr DoDump() {
  bit16 Address = 0;
  while (Address < 64) {
    strcpy(Buffer, " ");
    for (bit16 Index = 0; Index < 16; Index++, Address++) {
      sprintf(strchr(Buffer, 0), " %02.2x", FramReadByte(Address));
    }
    Serial.println(Buffer);
  }
  return "dump.ok";
}

// The Time pointer may not be null, and should point to a full timespec.

cptr DoTime(cptr Time) {
  Serial.print(F("  time..."));
  if (strlen(Time) == 17) {
    if ((Time[ 2] == '&') &&
        (Time[ 5] == '&') &&
        (Time[ 8] == '&') &&
        (Time[11] == '&') &&
        (Time[14] == '&')) {
      LastYear   = atoi(Time + 0);
      LastMonth  = atoi(Time + 3);
      LastDay    = atoi(Time + 6);
      LastHour   = atoi(Time + 9);
      LastMinute = atoi(Time + 12);
      LastSecond = atoi(Time + 15);
      WriteTime();
      Serial.println(F("set"));
      return "time.set";
  } }
  Serial.println(F("error"));
  return "error";
}

// The Tau pointer may be null, or may point to an empty string or a Tau
// address.  For null, return the current Tau address.  For empty, clear
// the Tau address, otherwise set the Tau address.

cptr DoTau(cptr Tau) {
  Serial.print(F("  tau..."));
  if (Tau) {
    if ((strlen(Tau) > 10) && (strlen(Tau) < 16)) {
      strcpy(TauAddress, Tau);
      TauSave();
      Serial.print(TauAddress);
      Serial.println(F("...enabled"));
      return "tau.enabled";
    } else {
      TauWipe();
      TauSave();
      Serial.println(F("disabled"));
      return "tau.disabled";
    }
    Serial.println(F("error"));
    return "error";
  }
  Serial.println(TauAddress);
  if (TauAddress[0]) {
    return TauAddress;
  }
  TauWipe();
  TauSave();
  return "tau.disabled";
}

// The Index value may be LOG_NOW or [0..LogSize-1].  The heavy-lifting is
// done in the calling routine, here we just check for problems.

#define LOG_NOW  20000 // reserved value to represent current header log record
#define LOG_NONE 20001 // reserved value to represent no log record

bit16 LogIndex = LOG_NONE;

cptr DoData(cptr Get) {
  Serial.print(F("  get..."));
  if (Get) {
    LogIndex = atoi(Get);
    if (FramReadByte(offsetof(LogHeader, LogFull)) == 1) {
      if (LogIndex < LogSize) {
        Serial.println(itoa(LogIndex, Buffer, 10));
        return "log";
      }
    } else {
      if (LogIndex < FramReadWord(offsetof(LogHeader, LogNext))) {
        Serial.println(itoa(LogIndex, Buffer, 10));
        return "log";
    } }
  } else {
    LogIndex = LOG_NOW;
    Serial.println(F("current"));
    return "now";
  }
  LogIndex = LOG_NONE;
  Serial.println(F("error"));
  return "error";
}

void EnetHandleServer() {
  if (EthernetClient client = EnetServer.available()) {
    Serial.println(F("server connection"));
    cptr Message = "bad.request";
    LogIndex = LOG_NONE;
    Buffer[BufferLen = 0] = 0;
    while (client.connected()) {
      if (client.available()) {
        char c = client.read();
        if (c != '\n') {
          if ((c != '\r') && (BufferLen < BUFFER_MAX - 1)) {
            Buffer[BufferLen++] = c;
            Buffer[BufferLen  ] = 0;
          }
        } else {
          if (BufferLen) {
            if (strncmp(Buffer, "GET /", 5) == 0) {
              cptr Get = Buffer + 5;
              if (cptr End = strchr(Get, ' ')) {
                *End = 0;
                if (strcmp (Get, ""          ) == 0) Message = DoHelp (       );
                if (strcmp (Get, "dump"      ) == 0) Message = DoDump (       );
                if (strcmp (Get, "reset"     ) == 0) Message = DoReset(       );
                if (strncmp(Get, "time?20", 7) == 0) Message = DoTime (Get + 7);
                if (strcmp (Get, "tau"       ) == 0) Message = DoTau  (NULL   );
                if (strncmp(Get, "tau?" ,   4) == 0) Message = DoTau  (Get + 4);
                if (strcmp (Get, "now"       ) == 0) Message = DoData (NULL   ); // also sets LogIndex
                if (strncmp(Get, "log?" ,   4) == 0) Message = DoData (Get + 4); // also sets LogIndex
            } }
            Buffer[BufferLen = 0] = 0;
          } else {
            HttpGets++;
            client.println(F("HTTP/1.1 200 OK"));
            client.println(F("Content-Type: application/json"));
            client.println(F("Connection: close"));  // close connection after response
            client.println();
            client.print(JsonFrame('{'));
            JsonText (client, F("http.message"    ), Message        );
            JsonText (client, F("software.date"   ), VERSION        );
            JsonBit16(client, F("http.gets"       ), HttpGets       );
            JsonBit16(client, F("log.size"        ), LogSize        );
            JsonBit16(client, F("fram.magic"      ), FRAM_MAGIC     );
            JsonBit16(client, F("log.header.bytes"), LOG_HEADER_SIZE);
            JsonBit16(client, F("log.record.bytes"), LOG_RECORD_SIZE);
            if (LogIndex != LOG_NONE) {
              bit16 LogOffset = LOG_HEADER_SIZE;
              if (LogIndex > LogSize) {
                LogIndex = LOG_NOW;
              } else {
                LogOffset += LOG_RECORD_SIZE * LogIndex;
              }
              JsonBit16(client, F("log.index"     ), LogIndex );
              JsonBit16(client, F("log.offset"    ), LogOffset);
              JsonBit16(client, F("log.full"      ), FramReadByte(offsetof(LogHeader, LogFull)));
              JsonBit16(client, F("log.next"      ), FramReadWord(offsetof(LogHeader, LogNext)));
              JsonBit16(client, F("boot.count"    ), FramReadWord(LogOffset + offsetof(LogRecord, BootCount ))          );
              JsonBit16(client, F("uptime.minutes"), FramReadWord(LogOffset + offsetof(LogRecord, UptimeMins))          );
              JsonBit16(client, F("time.year"     ), FramReadByte(LogOffset + offsetof(LogRecord, Year      )) + 2000   );
              JsonBit16(client, F("time.month"    ), FramReadByte(LogOffset + offsetof(LogRecord, Month     ))          );
              JsonBit16(client, F("time.day"      ), FramReadByte(LogOffset + offsetof(LogRecord, Day       ))          );
              JsonBit16(client, F("time.hour"     ), FramReadByte(LogOffset + offsetof(LogRecord, Hour      ))          );
              JsonBit16(client, F("time.minute"   ), FramReadByte(LogOffset + offsetof(LogRecord, Minute    ))          );
              JsonBit16(client, F("time.second"   ), FramReadByte(LogOffset + offsetof(LogRecord, Second    ))          );
              JsonBit16(client, F("humidity.pct"  ), FramReadByte(LogOffset + offsetof(LogRecord, Humi      ))          );
              JsonScale(client, F("wind.direction"), FramReadByte(LogOffset + offsetof(LogRecord, Vane      )), 22.5 , 1);
              JsonBit16(client, F("wind.mph"      ), FramReadByte(LogOffset + offsetof(LogRecord, Anem      ))          );
              JsonBit16(client, F("wind.avg.mph"  ), FramReadByte(LogOffset + offsetof(LogRecord, AnemAvg   ))          );
              JsonBit16(client, F("wind.max.mph"  ), FramReadByte(LogOffset + offsetof(LogRecord, AnemMax   ))          );
              JsonScale(client, F("temp.c"        ), FramReadWord(LogOffset + offsetof(LogRecord, Temp      )), 0.1  , 1);
              JsonScale(client, F("dewpoint.c"    ), FramReadWord(LogOffset + offsetof(LogRecord, Dewpoint  )), 0.1  , 1);
              JsonScale(client, F("rain.in"       ), FramReadWord(LogOffset + offsetof(LogRecord, Rain      )), 0.011, 2);
              JsonScale(client, F("rain.day.in"   ), FramReadWord(LogOffset + offsetof(LogRecord, RainDay   )), 0.011, 2);
              JsonScale(client, F("pressure.inhg" ), FramReadWord(LogOffset + offsetof(LogRecord, Pres      )), 0.001, 3);
              JsonBit16(client, F("tau.set"       ), FramReadByte(LogOffset + offsetof(LogRecord, TauSet    ))          );
              JsonBit16(client, F("tau.status"    ), FramReadByte(LogOffset + offsetof(LogRecord, TauMax    ))          );
              JsonText (client, F("tau.address"   ), TauAddress);
              JsonBit16(client, F("tau.queries"   ), TauQueries);
              JsonBit16(client, F("tau.replies"   ), TauReplies);
            }
            client.print(JsonFrame('}'));
            Serial.println(F("  sending response"));
            client.stop();
    } } } }
    delay(10); // give the web browser time to receive the data
    client.stop(); // close the connection:
    Serial.println(F("  disconnected"));
} }

// ============================================================================
// LCD display.
// ============================================================================

#include "LiquidCrystal_I2C.h"

LiquidCrystal_I2C lcd(WPI_I2C_OLED, 20, 4);

void InitLcd() {
  Serial.print(F("  lcd..."));
  lcd.init();
  lcd.backlight();
  Serial.println(F("done"));
  lcd.setCursor(0, 0); lcd.print(F(PROJECT));
  lcd.setCursor(2, 1); lcd.print(F(EMAIL1 ));
  lcd.setCursor(4, 2); lcd.print(F(EMAIL2 ));
  lcd.setCursor(6, 3); lcd.print(F(VERSION));
  delay(2000);
}

#define CtoF(C) round((C*0.18)+32)

// +--------------------+
// |YYYY-MM-DD  HH:MM:SS|
// |Press  Temp/DewP  RH|
// |Rain/Total Wind/Peak|
// |Log/Limit   TAU Wind|
// +--------------------+

void ShowLcdLegend() {
  lcd.setCursor(0, 0); lcd.print(F("YYYY-MM-DD  HH:MM:SS"));
  lcd.setCursor(0, 1); lcd.print(F("Press  Temp Dewp  RH"));
  lcd.setCursor(0, 2); lcd.print(F("Rain Total Wind Peak"));
  lcd.setCursor(0, 3); lcd.print(F("Log Limit   TAU Wind"));
}

// +--------------------+
// |2018-09-01  10:15:03|
// |30.001 102F  93F 98%|
// | 1.23 14.50   20  24|
// |  34 1230    0 180.0|
// +--------------------+

void ShowLcdWeather() {
  sprintf_P(       Buffer,     PSTR("20%02d-%02d-%02d"), LastYear, LastMonth , LastDay   );
  sprintf_P(strchr(Buffer, 0), PSTR("  %02d:%02d:%02d"), LastHour, LastMinute, LastSecond);
  lcd.setCursor(0, 0); lcd.print(Buffer);
  dtostrf(LastPres / 1000.0, 6, 3, Buffer);
  sprintf_P(strchr(Buffer, 0), PSTR(" %3dF "), CtoF(LastTemp    ));
  sprintf_P(strchr(Buffer, 0), PSTR("%3dF " ), CtoF(LastDewpoint));
  sprintf_P(strchr(Buffer, 0), PSTR("%2d%%" ), LastHumi          );
  lcd.setCursor(0, 1); lcd.print(Buffer);
  dtostrf(LastRain    * 0.011, 5, 2,        Buffer    ); strcat(Buffer, " ");
  dtostrf(LastRainDay * 0.011, 5, 2, strchr(Buffer, 0));
  sprintf_P(strchr(Buffer, 0), PSTR("  %3d %3d"), LastAnem, LastAnemMax);
  lcd.setCursor(0, 2); lcd.print(Buffer);
  sprintf_P(Buffer, PSTR("%4d %4d   %2d "), FramReadWord(offsetof(LogHeader, LogNext)), LogSize, LastTau);
  dtostrf(LastVane * 22.5, 5, 1, strchr(Buffer, 0));
  lcd.setCursor(0, 3); lcd.print(Buffer);
}

// +--------------------+
// | 192.168.18.107  80 |
// | 192.168.18.102  80 |
// |   Get  Query Reply |
// |   123     12    11 |
// +--------------------+

void ShowLcdNetwork() {
  sprintf_P(Buffer, PSTR(" %d.%d.%d.%d %3d "), EnetServerIPv4[0], EnetServerIPv4[1], EnetServerIPv4[2], EnetServerIPv4[3], ENET_PORT);
  lcd.setCursor(0, 0); lcd.print(Buffer);
  if (TauAddress[0]) {
    sprintf_P(Buffer, PSTR(" %14s %3d "), TauAddress, TAU_PORT);
  } else {
    sprintf_P(Buffer, PSTR(" ---.---.--.--- --- "));
  }
  lcd.setCursor(0, 1); lcd.print(Buffer);
  sprintf_P(Buffer, PSTR("   Get  Query/Reply "));
  lcd.setCursor(0, 2); lcd.print(Buffer);
  sprintf_P(Buffer, PSTR(" %5d  %5d %5d "), HttpGets, TauQueries, TauReplies);
  lcd.setCursor(0, 3); lcd.print(Buffer);
}

// ============================================================================
// Initialize all peripherals.
// ============================================================================

void setup() {
  Serial.begin(115200);
  while (!Serial) {
    delay(10);
  }
  Serial.println();
  Serial.print(F(PROJECT));
  Serial.print(F(" "));
  Serial.println(F(VERSION));
  Serial.print(F("  "));
  Serial.print  (F(EMAIL1));
  Serial.println(F(EMAIL2));
  Serial.println(F("initializing..."));
  TauWipe();
  InitPins(); // Wind, Rain, Buttons
  InitPres();
  InitLcd();
  InitFram();
  InitEnet();
  sprintf_P(Buffer, PSTR("  log header, entry, size = %d, %d, %d"), LOG_HEADER_SIZE, LOG_RECORD_SIZE, LogSize);
  Serial.println(Buffer);
  sprintf_P(Buffer, PSTR("  tau address = %s"), TauAddress);
  Serial.println(Buffer);
  Serial.println(F("done"));
}

// ============================================================================
// Handle web requests and periodically update sensor values.  Don't update
// sensor values more often than once every 2 seconds to avoid violating the
// AM2315 spec (we do it every 5 seconds).
// ============================================================================

bit32 LastReadTime  = 0; // read sensors every 5 seconds
bit32 LastLcdTime   = 0; // update lcd screen every 1 seconds
bit32 LastLcdAuto   = 0; // display next lcd screen every 3 seconds
bit8  LastTauMinute = 0; // query tau every minute
bit8  LastLogMinute = 0; // log data every 15 minutes
bit8  LastClearDay  = 0; // clear daily accumulators at midnight
bit8  DataReady     = 0; // true when data is available
bit8  LcdScreen     = 0; // which of the lcd screens is displayed

void loop() {
  if (millis() - LastReadTime > 5000UL) {
    ReadSensors();
    DataReady = 1;
    LastReadTime = millis();
    if (LastMinute != LastLogMinute) {
      if (LastMinute % 15 == 0) {
        LogData();
        LastLogMinute = LastMinute;
    } }
    if (LastMinute != LastTauMinute) {
      UptimeMins += 1;
      EnetTauQuery();
      LastTauMinute = LastMinute;
    }
    if (LastDay != LastClearDay) {
      ClearTotals();
      LastClearDay = LastDay;
  } }
  if (millis() - LastLcdTime > 1000UL) {
    LastLcdTime = millis();
    switch (LcdScreen) {
      case 0: ShowLcdLegend (); break;
      case 1:
      case 2: ShowLcdWeather(); break;
      case 3: ShowLcdNetwork(); break;
  } }
  if (millis() - LastLcdAuto > 3000UL) {
    LastLcdAuto = millis();
    LcdScreen += 1;
    LcdScreen %= 4;
  }
  if (DataReady) {
    EnetHandleServer();
} }

// ============================================================================
// End.
// ============================================================================

