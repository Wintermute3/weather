#!/usr/bin/env python

from __future__ import print_function

PROGRAM = 'backfill.py'
VERSION = '1.809.301'
CONTACT = 'bright.tiger@mail.com' # michael nagy

#==============================================================================
# report any unreported database entries (maximum of one per unreported
# quarter hour) and update the database to reflect the report if successful.
# directly query the wb3 system for log data as needed to backfill missing
# quarters, and note if log data is unavailable.
#==============================================================================

import os, sys, requests, time, calendar

DebugFlag = False

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
# write a message to the console and the permanent log file
#----------------------------------------------------------------------

def Print(Text):
  print('%s' % (Text))
  PermaLog(Text.strip())

#----------------------------------------------------------------------
# bitmask values which indicate publication to weather underground and
# aeris was successfull, and for quarters if data is present or
# unavailable
#----------------------------------------------------------------------

MASK_REPORTED_WU = 0x01 # epoch.reported_mask, weather underground
MASK_REPORTED_PS = 0x02 # epoch.reported_mask, aeris / pwsweather

MASK_LOG_SUCCESS = 0x01 # quarter.log_mask, log data recovered
MASK_LOG_MISSING = 0x02 # quarter.log_mask, log data unavailable

#----------------------------------------------------------------------
# database support.  we are currently using postgresql, but with minor
# modifications to this single block of code we could alternately run
# with sqlite3 or possibly other database engines.
#----------------------------------------------------------------------

import psycopg2, psycopg2.extras

#----------------------------------------------------------------------
# weatherbox3 base url
#----------------------------------------------------------------------

WB_URL_JSON = 'http://192.168.18.107'

#----------------------------------------------------------------------
# weather underground parameters
#----------------------------------------------------------------------

WU_STATION_ID = 'KFLMYAKK20'
WU_PASSWORD   = 'h1i2nqhw'
WU_URL_GET    = 'http://weatherstation.wunderground.com/weatherstation/updateweatherstation.php'

#----------------------------------------------------------------------
# aeris parameters
#----------------------------------------------------------------------

PS_STATION_ID = 'KFLMYAKK20'
PS_PASSWORD   = 'micro123'
PS_URL_GET    = 'https://www.pwsweather.com/pwsupdate/pwsupdate.php'

#----------------------------------------------------------------------
# given a unix epoch value in seconds, return a quarter value, which
# is just the epoch value divided by 900 (the number of seconds in 15
# minutes), and vice versa
#----------------------------------------------------------------------

def EpochToQuarter(Epoch):
  return Epoch / 900

def QuarterToEpoch(Quarter):
  return Quarter * 900

#----------------------------------------------------------------------
# report a dataset to one of the weather services and return true if
# successful.
#----------------------------------------------------------------------

def Report(DbCursor, Quarter, Code, Url, StationId, Password):
  try:
    DbCursor.execute('SELECT * FROM quarter WHERE id = %d' % (Quarter))
    Row = DbCursor.fetchone()
    try:
      Epoch = QuarterToEpoch(Quarter) + 450 # center of quarter
      UtcTime = time.strftime(TimePattern, time.gmtime(time.time()))
      Data = {
        'ID'          :      StationId       ,
        'PASSWORD'    :      Password        ,
        'dateutc'     :      UtcTime         ,
        'winddir'     : Row['wind_direction'],
        'windspeedmph': Row['wind_mph'      ],
        'rainin'      : Row['rain_in'       ],
        'dailyrainin' : Row['rain_day_in'   ],
        'humidity'    : Row['humidity_pct'  ],
        'dewptf'      : Row['dewpoint_f'    ],
        'UV'          : Row['tau_status'    ],
        'tempf'       : Row['temp_f'        ],
        'baromin'     : Row['pressure_inhg' ],
        'action'      :     'updateraw'
      }
      if DebugFlag:
        Print('  quarter %d %s update skip' % (Quarter, Code))
        return True
      r = requests.get(Url, params=Data)
      if r.status_code == 200:
        Print('    quarter %d %s update ok' % (Quarter, Code))
        return True
      Print('    quarter %d %s update bad %d' % (Quarter, Code, r.status_code))
    except Exception as er:
      Print('    quarter %d %s update exception: %s' % (Quarter, Code, er.message))
  except psycopg2.Error as er:
    Print('    quarter %d %s db read error: %s' % (Quarter, Code, er.message))
    os._exit(1)
  except Exception as er:
    Print('    quarter %d %s db write exception 1: %s' % (Quarter, Code, er.message))
  return False

#----------------------------------------------------------------------
# report any unreported quarters which have available data to both
# services and update quarter status to reflect success.
#----------------------------------------------------------------------

def ReportNewData():
  Print('reporting newly available data')
  DbConnection = psycopg2.connect('dbname=weather')
  DbCursor = DbConnection.cursor(cursor_factory=psycopg2.extras.DictCursor)
  DbCursor.execute('SELECT count(*) FROM quarter WHERE reported_mask = 3 and epochs > 0')
  ReportedCount = DbCursor.fetchone()['count']
  Print('  reported %d quarters previously' % (ReportedCount))
  DbCursor.execute('SELECT * FROM quarter WHERE reported_mask <> 3 and epochs > 0')
  for Row in DbCursor.fetchall():
    Quarter      = Row['id'           ]
    ReportedMask = Row['reported_mask']
    if ReportedMask & MASK_REPORTED_WU == 0:
      if Report(DbCursor, Quarter, 'wu', WU_URL_GET, WU_STATION_ID, WU_PASSWORD):
        ReportedMask |= MASK_REPORTED_WU
    if ReportedMask & MASK_REPORTED_PS == 0:
      if Report(DbCursor, Quarter, 'ps', PS_URL_GET, PS_STATION_ID, PS_PASSWORD):
        ReportedMask |= MASK_REPORTED_PS
    if ReportedMask != Row['reported_mask']:
      try:
        sql = 'UPDATE quarter SET '
        sql += 'reported_mask = %d WHERE id = %d' % (ReportedMask, Quarter)
        DbCursor.execute(sql)
      except psycopg2.Error as er:
        Print('    quarter %d db write error: %s' % (Quarter, er.message))
      except Exception as er:
        Print('    quarter %d db write exception 2: %s' % (Quarter, er.message))
  DbConnection.commit()
  DbConnection.close()

#----------------------------------------------------------------------
# attempt to pull data for the specified quarter from the wb3 log.  if
# successful, return a dictionary with the data.  if anything blows up
# with the web requests, allow the exception to bubble up to the
# routine that called us.  we do a binary search by epoch time to
# find any matching records.
#----------------------------------------------------------------------

LogDict = {}

def Wb3LogPull(Quarter):
  Target = QuarterToEpoch(Quarter)
  Print('  seeking data for quarter %d (epoch %d)' % (Quarter, Target))
  r = requests.get('%s/now' % (WB_URL_JSON))
  if r.status_code == 200:
    wb = r.json()
    LogSize = wb['log.size']
    if wb['log.full']:
      LogFirst = wb['log.next']
      LogNext  = wb['log.next'] + LogSize
    else:
      LogFirst = 0 
      LogNext  = wb['log.next']
    while LogFirst < LogNext:
      LogIndex = (LogFirst + LogNext) / 2
      WrapIndex = LogIndex % LogSize
      if WrapIndex in LogDict:
        Print('    cache log %d [%d,%d]' % (WrapIndex, LogFirst, LogNext))
        wb = LogDict[WrapIndex]
      else:
        time.sleep(0.5)
        Print('    query log %d [%d,%d]' % (WrapIndex, LogFirst, LogNext))
        r = requests.get('%s/log?%d' % (WB_URL_JSON, WrapIndex))
        if r.status_code == 200:
          wb = r.json()
          LogDict[WrapIndex] = wb
        else:
          raise Exception('unable to query wb3 log')
      TimeUtc = '%04d-%02d-%02d %02d:%02d:%02d' % (
        wb['time.year'],wb['time.month' ],wb['time.day'   ],
        wb['time.hour'],wb['time.minute'],wb['time.second']
      )
      Epoch = calendar.timegm(time.strptime(TimeUtc, TimePattern))
      Delta = Epoch - Target
      if abs(Delta) > 480: # more than 8 minutes off target
        if Delta > 0:
          if LogNext == LogIndex:
            return None
          LogNext = LogIndex # seek backward in log
        else:
          if LogFirst == LogIndex:
            return None
          LogFirst = LogIndex # seek foreward in log
      else:
        wb['dewpoint.f'] = round((wb['dewpoint.c'] * 1.8) + 32.0, 1)
        wb['temp.f'    ] = round((wb['temp.c'    ] * 1.8) + 32.0, 1)
        Data = {
          'boot_count'    : wb['boot.count'    ],
          'uptime_minutes': wb['uptime.minutes'],
          'temp_f'        : wb['temp.f'        ],
          'dewpoint_f'    : wb['dewpoint.f'    ],
          'humidity_pct'  : wb['humidity.pct'  ],
          'pressure_inhg' : wb['pressure.inhg' ],
          'wind_mph'      : wb['wind.mph'      ],
          'wind_direction': wb['wind.direction'],
          'rain_in'       : wb['rain.in'       ],
          'rain_day_in'   : wb['rain.day.in'   ],
          'tau_status'    : wb['tau.status'    ],
          'tau_queries'   : wb['tau.queries'   ],
          'tau_replies'   : wb['tau.replies'   ],
          'log_next'      : wb['log.next'      ],
          'log_full'      : wb['log.full'      ],
        }
        return Data
  return None # requested data is not available

#----------------------------------------------------------------------
# for any quarters with no data which have not already been marked as
# hopeless, attempt to fetch log data from the wb3 system.
#----------------------------------------------------------------------

def QueryWb3Log():
  Print('query missing data from wb3 log')
  DbConnection = psycopg2.connect('dbname=weather')
  DbCursor = DbConnection.cursor(cursor_factory=psycopg2.extras.DictCursor)
  if DebugFlag:
    DbCursor.execute('SELECT * FROM quarter WHERE epochs < 14')
  else:
    DbCursor.execute('SELECT * FROM quarter WHERE log_mask = 0 and epochs = 0')
  QueryCount = 0
  for Row in DbCursor.fetchall():
    QueryCount += 1
    Quarter = Row['id']
    try:
      Data = Wb3LogPull(Quarter)
      try:
        if Data:
          Print('    quarter %d data pulled from wb3 log' % (Quarter))
          sql = 'UPDATE quarter SET '
          sql += 'boot_count = %d,'        % (Data['boot_count'    ])
          sql += 'uptime_minutes = %d,'    % (Data['uptime_minutes'])
          sql += 'temp_f = %3.1f,'         % (Data['temp_f'        ])
          sql += 'dewpoint_f = %3.1f,'     % (Data['dewpoint_f'    ])
          sql += 'humidity_pct = %d,'      % (Data['humidity_pct'  ])
          sql += 'pressure_inhg = %0.3f,'  % (Data['pressure_inhg' ])
          sql += 'wind_mph = %d,'          % (Data['wind_mph'      ])
          sql += 'wind_direction = %1.0f,' % (Data['wind_direction'])
          sql += 'rain_in = %4.2f,'        % (Data['rain_in'       ])
          sql += 'rain_day_in = %4.2f,'    % (Data['rain_day_in'   ])
          sql += 'tau_status = %d,'        % (Data['tau_status'    ])
          sql += 'tau_queries = %d,'       % (Data['tau_queries'   ])
          sql += 'tau_replies = %d,'       % (Data['tau_replies'   ])
          sql += 'log_next = %d,'          % (Data['log_next'      ])
          sql += 'log_full = %d,'          % (Data['log_full'      ])
          sql += 'reported_mask = 0,'
          if DebugFlag:
            sql += 'log_mask = 0,'
            sql += 'epochs = 0'
          else:
            sql += 'log_mask = %d,' % (MASK_LOG_SUCCESS)
            sql += 'epochs = 1'
          sql += ' WHERE id = %d' % (Quarter)
          DbCursor.execute(sql)
        else:
          Print('    quarter %d data not found in wb3 log' % (Quarter))
          sql = 'UPDATE quarter SET '
          sql += 'log_mask = %d WHERE id = %d' % (MASK_LOG_MISSING, Quarter)
          DbCursor.execute(sql)
      except psycopg2.Error as er:
        Print('    quarter %d db write error: %s' % (Quarter, er.message))
      except Exception as er:
        Print('    quarter %d db write exception 3: %s' % (Quarter, er.message))
    except Exception as er:
      Print('    quarter %d exception querying wb3 log: %s' % (Quarter, er.message))
  if QueryCount == 0:
    Print('  no new quarters are missing data')
  DbConnection.commit()
  DbConnection.close()

#----------------------------------------------------------------------
# main.  for any quarter with unreported data, report the data and
# update the database to indicate that it has been reported.
#----------------------------------------------------------------------

print()
Print('%s %s' % (PROGRAM, VERSION))
print()
for arg in sys.argv:
  if arg.lower().startswith('-d'):
    DebugFlag = True
    print('  debug mode')
    print()
QueryWb3Log()
print()
ReportNewData()
print()
print('done')
print()

#==============================================================================
# end
#==============================================================================
