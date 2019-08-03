#!/usr/bin/env python

from __future__ import print_function

PROGRAM = 'proxy-logger.py'
VERSION = '1.908.021'
CONTACT = 'bright.tiger@mail.com' # michael nagy

#==============================================================================
# proxy-logger - periodically query data from a weatherbox4 system (which in
# turn will be querying a tornado alert unit), and report it to both weather
# underground and aeris.  if the realtime clock in the weatherbox4 is off by
# more than 60 seconds, reset it to the current time.  also log the weather
# data to a postgresql database.
#
# we expect to have an oled shield installed with an sh1106 display and a set
# of gpio buttons.  pressing joystick down will cause a program exit (which,
# if running as an autorestart service, will immediately run us again).  if
# the bottom button is pressed before joystick down, the system will do a
# clean poweroff.  this mode may be canceled by pressing the top button.
#
# when configured as a service, the systemd unit file can be found at:
#
#   /etc/systemd/system/proxy-logger.service
#
# we also log to /var/log/syslog as the proxy-logger.py process.  to allow
# for some debugging on a system other than the target raspberry pi, we
# inhibit the gpio, watchdog and upstream reporting functions if the oled
# display is not present.
#==============================================================================

import os, requests, json, time, calendar, logging, subprocess
from syslog import syslog
from time import sleep
from datetime import datetime

try:
  import serial
except:
  print("*** unable to import 'serial' - install with:")
  print()
  print('  sudo apt-get install python-serial')
  print()
  os._exit(1)

#----------------------------------------------------------------------
# Externalize passwords for weather apis.
#----------------------------------------------------------------------

Password = json.load(open('/home/pi/weather/.passwords.json'))

#----------------------------------------------------------------------
# two-digit cyclic counter to provide a warm fuzzy progress indicator
#----------------------------------------------------------------------

LoopCount = 0 # 00,01..99

#----------------------------------------------------------------------
# oled display terminal device, if available
#----------------------------------------------------------------------

Oled = None

#----------------------------------------------------------------------
# if the oled display is available enable the gpio buttons
#----------------------------------------------------------------------

BCM_BUTTON_TOP      = 16
BCM_BUTTON_MIDDLE   = 20
BCM_BUTTON_BOTTOM   = 21

BCM_JOYSTICK_DOWN   =  6
BCM_JOYSTICK_UP     = 19
BCM_JOYSTICK_RIGHT  =  5
BCM_JOYSTICK_LEFT   = 26
BCM_JOYSTICK_CENTER = 13

try:
  import RPi.GPIO as GPIO
except:
  pass

def GpioInit():
  if Oled:
    GPIO.setmode(GPIO.BCM)
    for Button in (
        BCM_BUTTON_TOP , BCM_BUTTON_MIDDLE , BCM_BUTTON_BOTTOM, BCM_JOYSTICK_DOWN,
        BCM_JOYSTICK_UP, BCM_JOYSTICK_RIGHT, BCM_JOYSTICK_LEFT, BCM_JOYSTICK_CENTER):
      GpioSetup(Button)
    print('[00] gpio setup complete')

def GpioSetup(Button):
  if Oled:
    GPIO.setup(Button, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def GpioInput(Button):
  if Oled:
    return GPIO.input(Button) == GPIO.LOW
  return False

def GpioCleanup():
  if Oled:
    GPIO.cleanup()

#----------------------------------------------------------------------
# if the oled display is available configure it and enable the gpio
# buttons
#----------------------------------------------------------------------

try:
  from demo_opts import get_device
  from luma.core.virtual import terminal
  from PIL import ImageFont
  def make_font(name, size):
    font_path = os.path.abspath(os.path.join(
      os.path.dirname(__file__), 'fonts', name))
    return ImageFont.truetype(font_path, size)
  Oled = terminal(
    get_device(['-i','spi','-d','sh1106']),
    make_font("ProggyTiny.ttf", 16)
  )
  GpioInit()
  print('[00] oled enabled')
except:
  print('[00] oled disabled')

#----------------------------------------------------------------------
# the watchdog must be reset at least once every 5 minutes or systemd
# will, as configured in our unit file, restart us, and actually
# reboot the system if it has to restart us too often.  we reset the
# watchdog on every successful query of the weatherbox4 system, which
# is about the most reliable indicator of health we have available.
# note that for getpid() to work, the systemd unit file must specify:
#
#   [Service]
#     Type=simple
#
# or else our pid won't be the one systemd expects and the watchdog
# won't get reset as required.
#----------------------------------------------------------------------

WatchDogCmd = '/bin/systemd-notify --pid=%d WATCHDOG=1' % (os.getpid())

def WatchdogReset():
  if Oled:
    subprocess.call(WatchDogCmd, shell=True)

#----------------------------------------------------------------------
# disable annoying info logging from requests package
#----------------------------------------------------------------------

logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(logging.ERROR)

#----------------------------------------------------------------------
# bitmask values which indicate publication to weather underground and
# aeris was successfull
#----------------------------------------------------------------------

MASK_REPORTED_WU = 0x01
MASK_REPORTED_PS = 0x02

#----------------------------------------------------------------------
# the standard utc and local time string format we use throughout
#----------------------------------------------------------------------

TimePattern = '%Y-%m-%d %H:%M:%S'

def LocalTimeStr(Epoch):
  return time.strftime(TimePattern, time.localtime(Epoch))

#----------------------------------------------------------------------
# write a timestamped message to the permanent log file
#----------------------------------------------------------------------

def PermaLog(Text):
  Time = LocalTimeStr(time.time())
  with open('/home/pi/weather/permanent.log', 'a') as f:
    f.write('%s %s %s\n' % (Time, PROGRAM, Text))

#----------------------------------------------------------------------
# print messages on the oled (if available), the console, and depending
# on the Log option, the syslog and possibly also the permanent log.
# for all except the oled, squeeze runs of spaces down to a single
# space (because of the odd way we format things for the oled).
#----------------------------------------------------------------------

def Print(Text='', Log=None):
  if Oled:
    if Text:
      Oled.println()
      Oled.puts(Text)
  Text = ' '.join(Text.split())
  print('%s' % (Text))
  if Log:
    syslog(Text)
    if Log == 'permalog':
      PermaLog(Text)

#----------------------------------------------------------------------
# database support.  we are currently using postgresql, but with minor
# modifications to this single block of code we could alternately run
# with sqlite3 or possibly other database engines.
#----------------------------------------------------------------------

import psycopg2

def DbExecute(Note5, Sql):
  try:
    db = psycopg2.connect('dbname=weather')
    cursor = db.cursor()
    cursor.execute(Sql)
    db.commit()
    db.close()
    Print('[%02d] %s ok' % (LoopCount, Note5), 'syslog')
  except psycopg2.Error as er:
    Print('[%02d] %s error: %s' % (LoopCount, Note5, er.message), 'permalog')

def DbInit():
  sql = 'CREATE TABLE IF NOT EXISTS epoch ('
  sql += 'id             INT PRIMARY KEY,'
  sql += 'boot_count     INT ,'
  sql += 'uptime_minutes INT ,'
  sql += 'temp_f         REAL,'
  sql += 'dewpoint_f     REAL,'
  sql += 'humidity_pct   INT ,'
  sql += 'pressure_inhg  REAL,'
  sql += 'wind_mph       INT ,'
  sql += 'wind_direction REAL,'
  sql += 'rain_in        REAL,'
  sql += 'rain_day_in    REAL,'
  sql += 'power_volt     REAL,'
  sql += 'tau_status     INT ,'
  sql += 'log_next       INT ,'
  sql += 'log_full       INT ,'
  sql += 'reported_mask  INT);'
  DbExecute('init ', sql)

#----------------------------------------------------------------------
# serial port - half duplex, 19200 bps, return decoded json
#----------------------------------------------------------------------

Wb4Port = None

def Wb4Json(Command='', Trace=False):
  Command += '\r'
  for Character in Command:
    Wb4Port.write(Character)
    sleep(0.1)
  Response = Wb4Port.read(4000)
  if Trace:
    print('%s' % (Response))
  try:
    return json.loads('{%s}' % Response.split('{')[1].split('}')[0])
  except:
    return None

def Wb4Init():
  global Wb4Port
  try:
    Wb4Port = serial.Serial(WB4_PORT, baudrate=WB4_BAUD, timeout=1.0)
    Wb4Json()
  except:
    Print('[00] serial error', 'permalog')
    sleep(2)
    os._exit(1)

#----------------------------------------------------------------------
# report no more than once per minute
#----------------------------------------------------------------------

LOOP_TIME_SECS = 60

#----------------------------------------------------------------------
# serial port parameters for weatherbox4 rs485 interface
#----------------------------------------------------------------------

WB4_PORT = '/dev/ttyUSB0'
WB4_BAUD = 19200

#----------------------------------------------------------------------
# weather underground parameters
#----------------------------------------------------------------------

WU_STATION_ID = 'KFLMYAKK20'
WU_URL_GET    = 'http://weatherstation.wunderground.com/weatherstation/updateweatherstation.php'

#----------------------------------------------------------------------
# aeris parameters
#----------------------------------------------------------------------

PS_STATION_ID = 'KFLMYAKK20'
PS_URL_GET    = 'https://www.pwsweather.com/pwsupdate/pwsupdate.php'

#----------------------------------------------------------------------
# main
#----------------------------------------------------------------------

PermaLog('%s %s' % (PROGRAM, VERSION))
Print('[00] pid %d' % (os.getpid()), 'permalog')

DbInit()
Wb4Init()

FAILSAFE_MAX = 5 # minutes without wb4 msx before auto-exit

FailSafe     = 0 # runcount of bad wb4 queries
RebootsTotal = 0 # wb4 reboot count
RebootsShow  = 0 # wb4 reboots since cleared

ReportedMask = 0x00 # MASK_REPORTED_PS | MASK_REPORTED_WU

try:
  ExitLoop = False
  PowerOff = False
  while not ExitLoop:
    if LoopCount > 98:
      LoopCount = 0 # keep loopcount 2 digits 01..99
    LoopCount += 1

    try:
      wb = Wb4Json('now')
      if wb:
        FailSafe = 0
        Print('[%02d] wb4   ok %d' % (LoopCount, wb['tau.status']), 'syslog')
        WatchdogReset() # only on the raspberry pi

        #------------------------------------------------------------------
        # determine if the weatherbox4 system has rebooted
        #------------------------------------------------------------------

        BootCount = wb['boot.count']
        if RebootsTotal != BootCount:
          if RebootsTotal:
            Print('[%02d] wb4 reboot %d' % (LoopCount, BootCount), 'permalog')
          RebootsTotal = BootCount
          RebootsShow += 1

        #------------------------------------------------------------------
        # convert celsius to fahrenheit with one decimal precision
        #------------------------------------------------------------------

        wb['dewpoint.f'] = round((wb['dewpoint.c'] * 1.8) + 32.0, 1)
        wb['temp.f'    ] = round((wb['temp.c'    ] * 1.8) + 32.0, 1)

        #------------------------------------------------------------------
        # get the actual utc time and convert to epoch
        #------------------------------------------------------------------

        wb['actual.utc'  ] = str(datetime.utcnow())[:-7]
        wb['actual.epoch'] = calendar.timegm(time.strptime(wb['actual.utc'], TimePattern))

        #------------------------------------------------------------------
        # get the weatherbox4 utc time and convert to epoch
        #------------------------------------------------------------------

        wb['time.utc'] = '%4d-%02d-%02d %02d:%02d:%02d' % (
          wb['time.year'], wb['time.month' ], wb['time.day'   ],
          wb['time.hour'], wb['time.minute'], wb['time.second']
        )
        wb['time.epoch'] = calendar.timegm(time.strptime(wb['time.utc'], TimePattern))

        #------------------------------------------------------------------
        # compare the actual and weatherbox4 epochs.  if they differ by
        # more than 60 seconds, reset the weatherbox4 time to the current
        # time
        #------------------------------------------------------------------

        TimeError = abs(wb['time.epoch'] - wb['actual.epoch'])
        if TimeError > 60:
          TimeSetCmd = 'time=' + wb['actual.utc'].replace(' ',',')
          Print('[%02d] wb4 time set' % (LoopCount), 'permalog')
          try:
            if Wb4Json(TimeSetCmd, True):
              Print('[%02d] wb4   ok' % (LoopCount), 'syslog')
            else:
              Print('[%02d] wb4 bad' % (LoopCount), 'permalog')
          except:
            Print('[%02d] wb4 err' % (LoopCount), 'permalog')

        #------------------------------------------------------------------
        # display the current json dictionary on the console
        #------------------------------------------------------------------

        if not Oled:
          Print(json.dumps(wb, indent=2, sort_keys=True))

        #------------------------------------------------------------------
        # report data to weather underground
        #------------------------------------------------------------------

        ReportedMask &= ~MASK_REPORTED_WU
        try:
          Data = {
            'ID'          :                WU_STATION_ID    ,
            'PASSWORD'    :      Password['WU_PASSWORD'   ] ,
            'dateutc'     : '%s'    % (wb['actual.utc'    ]),
            'winddir'     : '%1.0f' % (wb['wind.direction']),
            'windspeedmph': '%d'    % (wb['wind.mph'      ]),
            'rainin'      : '%4.2f' % (wb['rain.in'       ]),
            'dailyrainin' : '%4.2f' % (wb['rain.day.in'   ]),
            'humidity'    : '%d'    % (wb['humidity.pct'  ]),
            'dewptf'      : '%3.1f' % (wb['dewpoint.f'    ]),
            'UV'          : '%d'    % (wb['tau.status'    ]),
            'tempf'       : '%3.1f' % (wb['temp.f'        ]),
            'baromin'     : '%0.3f' % (wb['pressure.inhg' ]),
            'action'      :               'updateraw'
          }
          if not Oled:
            Print('[%02d] %s' % (LoopCount, WU_URL_GET))
          if Oled:
            r = requests.get(WU_URL_GET, params=Data)
            if r.status_code == 200:
              Print('[%02d] wu    ok' % (LoopCount), 'syslog')
              ReportedMask |= MASK_REPORTED_WU
            else:
              Print('[%02d] wu bad %d' % (LoopCount, r.status_code), 'permalog')
        except:
          Print('[%02d] wu err' % (LoopCount), 'permalog')

        #------------------------------------------------------------------
        # report data to aeris
        #------------------------------------------------------------------

        ReportedMask &= ~MASK_REPORTED_PS
        try:
          Data = {
            'ID'          :                PS_STATION_ID    ,
            'PASSWORD'    :      Password['PS_PASSWORD'   ] ,
            'dateutc'     : '%s'    % (wb['actual.utc'    ]),
            'winddir'     : '%1.0f' % (wb['wind.direction']),
            'windspeedmph': '%d'    % (wb['wind.mph'      ]),
            'rainin'      : '%4.2f' % (wb['rain.in'       ]),
            'dailyrainin' : '%4.2f' % (wb['rain.day.in'   ]),
            'humidity'    : '%d'    % (wb['humidity.pct'  ]),
            'dewptf'      : '%3.1f' % (wb['dewpoint.f'    ]),
            'UV'          : '%d'    % (wb['tau.status'    ]),
            'tempf'       : '%3.1f' % (wb['temp.f'        ]),
            'baromin'     : '%0.3f' % (wb['pressure.inhg' ]),
            'action'      :               'updateraw'
          }
          if not Oled:
            Print('[%02d] %s' % (LoopCount, PS_URL_GET))
          if Oled:
            r = requests.get(PS_URL_GET, params=Data)
            if r.status_code == 200:
              Print('[%02d] aeris ok' % (LoopCount), 'syslog')
              ReportedMask |= MASK_REPORTED_PS
            else:
              Print('[%02d] aeris bad %d' % (LoopCount, r.status_code), 'permalog')
        except:
          Print('[%02d] aeris err' % (LoopCount), 'permalog')

        #------------------------------------------------------------------
        # record the current data in the database
        #------------------------------------------------------------------

        sql = 'INSERT INTO epoch ('
        sql += 'id,'
        sql += 'boot_count,'
        sql += 'uptime_minutes,'
        sql += 'temp_f,'
        sql += 'dewpoint_f,'
        sql += 'humidity_pct,'
        sql += 'pressure_inhg,'
        sql += 'wind_mph,'
        sql += 'wind_direction,'
        sql += 'rain_in,'
        sql += 'rain_day_in,'
        sql += 'power_volt,'
        sql += 'tau_status,'
        sql += 'log_next,'
        sql += 'log_full,'
        sql += 'reported_mask) VALUES ('
        sql += '%d,'    % wb['actual.epoch'   ]
        sql += '%d,'    % wb['boot.count'     ]
        sql += '%d,'    % wb['uptime.minutes' ]
        sql += '%3.1f,' % wb['temp.f'         ]
        sql += '%3.1f,' % wb['dewpoint.f'     ]
        sql += '%d,'    % wb['humidity.pct'   ]
        sql += '%0.3f,' % wb['pressure.inhg'  ]
        sql += '%d,'    % wb['wind.mph'       ]
        sql += '%1.0f,' % wb['wind.direction' ]
        sql += '%4.2f,' % wb['rain.in'        ]
        sql += '%4.2f,' % wb['rain.day.in'    ]
        sql += '%6.3f,' % wb['power.volt'     ]
        sql += '%d,'    % wb['tau.status'     ]
        sql += '%d,'    % wb['log.next'       ]
        sql += '%d,'    % wb['log.full'       ]
        sql += '%d);'   % ReportedMask
        DbExecute('write', sql)

        Print('[%02d] dt=%02d %d' % (LoopCount, TimeError, RebootsShow), 'syslog')

      else:
        Print('[%02d] wb4 bad %d' % (LoopCount, r.status_code), 'permalog')
    except:
      Print('[%02d] wb4 err' % (LoopCount), 'permalog')

    Print('[%02d] sleep %d' % (LoopCount, LOOP_TIME_SECS), 'syslog')

    FailSafe += 1
    if FailSafe > FAILSAFE_MAX:
      Print('[%02d] failsafe' % (LoopCount), 'permalog') # we expect to exit and be auto-restarted
      ExitLoop = True
    else:
      for Second in range(LOOP_TIME_SECS):
        for Tick in range(10):
          sleep(0.1)
          if Oled:
            if GpioInput(BCM_BUTTON_TOP):
              if PowerOff:
                PowerOff = False
                Print('[%02d] poweroff false' % (LoopCount), 'permalog')
            if GpioInput(BCM_BUTTON_MIDDLE):
              Print('[%02d] middle' % (LoopCount), 'syslog')
            if GpioInput(BCM_BUTTON_BOTTOM):
              if not PowerOff:
                PowerOff = True
                Print('[%02d] poweroff true' % (LoopCount), 'permalog')
            if GpioInput(BCM_JOYSTICK_UP):
              Print('[%02d] reset status' % (LoopCount), 'permalog')
              RebootsShow = 0
            if GpioInput(BCM_JOYSTICK_DOWN):
              if not ExitLoop:
                Print('[%02d] exitloop true' % (LoopCount), 'permalog')
                ExitLoop = True
                break
            if GpioInput(BCM_JOYSTICK_LEFT):
              Print('[%02d] left' % (LoopCount), 'syslog')
            if GpioInput(BCM_JOYSTICK_RIGHT):
              Print('[%02d] right' % (LoopCount), 'syslog')
            if GpioInput(BCM_JOYSTICK_CENTER):
              Print('[%02d] center' % (LoopCount), 'permalog')
        if ExitLoop:
          break
except:
  Print('[%02d] exception' % (LoopCount), 'permalog')

if PowerOff:
  if Oled:
    Print('[%02d] poweroff' % (LoopCount), 'permalog')
    sleep(2)
    GpioCleanup()
    os.system('sudo poweroff')
    while True:
      sleep(1)
  else:
    Print('[%02d] poweroff flag was set' % (LoopCount), 'permalog')
Print('[%02d] exit' % (LoopCount), 'permalog')
GpioCleanup()

#==============================================================================
# end
#==============================================================================
