#!/usr/bin/env python

from __future__ import print_function

PROGRAM = 'condense.py'
VERSION = '1.809.150'
CONTACT = 'bright.tiger@mail.com' # michael nagy

#==============================================================================
# migrate point data from the epoch table to consolidated data in the quarter
# table.  also allow for re-migration of any quarters with less than 4 epochs
# aggregated.
#==============================================================================

import os, sys, psycopg2, psycopg2.extras, time

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
# database support.  we are currently using postgresql, but with minor
# modifications to this single block of code we could alternately run
# with sqlite3 or possibly other database engines.
#----------------------------------------------------------------------

def DbExecute(Note, Sql):
  try:
    db = psycopg2.connect('dbname=weather')
    cursor = db.cursor()
    cursor.execute(Sql)
    db.commit()
    db.close()
  except psycopg2.Error as er:
    Print('db %s error: %s' % (Note, er.message))
    os._exit(1)

def DbInit():
  sql = 'CREATE TABLE IF NOT EXISTS quarter ('
  sql += 'id             INT PRIMARY KEY,'
  sql += 'boot_count     INT ,' # consolidated, maximum
  sql += 'uptime_minutes INT ,' # consolidated, maximum
  sql += 'temp_f         REAL,' # consolidated, average
  sql += 'dewpoint_f     REAL,' # consolidated, average
  sql += 'humidity_pct   INT ,' # consolidated, average
  sql += 'pressure_inhg  REAL,' # consolidated, average
  sql += 'wind_mph       INT ,' # consolidated, maximum
  sql += 'wind_direction REAL,' # consolidated, final
  sql += 'rain_in        REAL,' # consolidated, maximum
  sql += 'rain_day_in    REAL,' # consolidated, maximum
  sql += 'tau_status     INT ,' # consolidated, maximum
  sql += 'tau_queries    INT ,' # consolidated, maximum
  sql += 'tau_replies    INT ,' # consolidated, maximum
  sql += 'log_next       INT ,' # consolidated, maximum
  sql += 'log_full       INT ,' # consolidated, maximum
  sql += 'reported_mask  INT ,' # consolidated, bitwise or
  sql += 'log_mask       INT ,' # log data needed or missing
  sql += 'epochs         INT)'  # number of epoch records consolidated
  DbExecute('init', sql)

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
# if oldepochs is -1 then condense all available datasets in the epoch
# table into a new quarter dataset, otherwise overwrite a previously
# condensed quarter if more epochs are now available then were when
# the quarter was previously condensed. 
#----------------------------------------------------------------------

def CondenseQuarter(DbCursor, Quarter, OldEpochs=-1):
  EpochMin = QuarterToEpoch(Quarter  )
  EpochMax = QuarterToEpoch(Quarter+1)-1
  DbCursor.execute(
    'SELECT * FROM epoch WHERE id BETWEEN %d AND %d' % (EpochMin, EpochMax))
  if DbCursor.rowcount > OldEpochs:
    BootCount     = 0
    UptimeMinutes = 0
    TempF         = 0.0
    DewpointF     = 0.0
    HumidityPct   = 0
    PressureInhg  = 0.0
    WindMph       = 0
    WindDirection = 0.0
    RainIn        = 0.0
    RainDayIn     = 0.0
    TauStatus     = 0
    TauQueries    = 0
    TauReplies    = 0
    LogNext       = 0
    LogFull       = 0
    ReportedMask  = 0
    LogMask       = 0
    Epochs        = 0
    for Row in DbCursor.fetchall():
      BootCount     = max(BootCount    , Row['boot_count'    ])
      UptimeMinutes = max(UptimeMinutes, Row['uptime_minutes'])
      TempF        +=                    Row['temp_f'        ]
      DewpointF    +=                    Row['dewpoint_f'    ]
      HumidityPct  +=                    Row['humidity_pct'  ]
      PressureInhg +=                    Row['pressure_inhg' ]
      WindMph       = max(WindMph      , Row['wind_mph'      ])
      WindDirection =                    Row['wind_direction']
      RainIn        = max(RainIn       , Row['rain_in'       ])
      RainInDay     = max(RainDayIn    , Row['rain_day_in'   ])
      TauStatus     = max(TauStatus    , Row['tau_status'    ])
      TauQueries    = max(TauQueries   , Row['tau_queries'   ])
      TauReplies    = max(TauReplies   , Row['tau_replies'   ])
      LogNext       = max(LogNext      , Row['log_next'      ])
      LogFull       = max(LogFull      , Row['log_full'      ])
      ReportedMask |=                    Row['reported_mask' ]
      Epochs       += 1
    if Epochs:
      TempF        /= Epochs
      DewpointF    /= Epochs
      HumidityPct  /= Epochs
      PressureInhg /= Epochs
    if OldEpochs > -1:
      Print('  recondense quarter %s from %d epoch records (was %d records)' % (LocalTimeStr(EpochMin), Epochs, OldEpochs))
      DbCursor.execute('DELETE FROM quarter WHERE id = %d' % (Quarter))
    else:
      Print('  condense quarter %s from %d epoch records' % (LocalTimeStr(EpochMin), Epochs))
    sql = 'INSERT INTO quarter ('
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
    sql += 'tau_status,'
    sql += 'tau_queries,'
    sql += 'tau_replies,'
    sql += 'log_next,'
    sql += 'log_full,'
    sql += 'reported_mask,'
    sql += 'log_mask,'
    sql += 'epochs) VALUES ('
    sql += '%d,'    % (Quarter      )
    sql += '%d,'    % (BootCount    )
    sql += '%d,'    % (UptimeMinutes)
    sql += '%3.1f,' % (TempF        )
    sql += '%3.1f,' % (DewpointF    )
    sql += '%d,'    % (HumidityPct  )
    sql += '%0.3f,' % (PressureInhg )
    sql += '%d,'    % (WindMph      )
    sql += '%1.0f,' % (WindDirection)
    sql += '%4.2f,' % (RainIn       )
    sql += '%4.2f,' % (RainDayIn    )
    sql += '%d,'    % (TauStatus    )
    sql += '%d,'    % (TauQueries   )
    sql += '%d,'    % (TauReplies   )
    sql += '%d,'    % (LogNext      )
    sql += '%d,'    % (LogFull      )
    sql += '%d,'    % (ReportedMask )
    sql += '%d,'    % (LogMask      )
    sql += '%d)'    % (Epochs       )
    DbCursor.execute(sql)
    return True
  return False

#----------------------------------------------------------------------
# find the min and max epoch values in the epoch table and create any
# missing quarter records needed to cover that range of epochs.  we
# may assume that quarter records have previously been created without
# gaps, and we maintain that assumption.  offer the option of re-
# condensing any existing records with fewer than 10 epochs.
#----------------------------------------------------------------------

print()
Print('%s %s' % (PROGRAM, VERSION))
print()
AutoYes = False
for arg in sys.argv:
  if arg.lower().startswith('-y'):
    AutoYes = True
DbInit()
DbConnection = psycopg2.connect('dbname=weather')
DbCursor1 = DbConnection.cursor(cursor_factory=psycopg2.extras.DictCursor)
DbCursor2 = DbConnection.cursor(cursor_factory=psycopg2.extras.DictCursor)
DbCursor1.execute('SELECT min(id),max(id) from epoch')
Row1 = DbCursor1.fetchone()
EpochMin = Row1['min']
EpochMax = Row1['max']
DbCursor1.execute('SELECT min(id),max(id) from quarter')
Row1 = DbCursor1.fetchone()
QuarterMin = Row1['min']
QuarterMax = Row1['max']
if not QuarterMin:
  QuarterMin = QuarterMax = 0
Print('existing epoch range')
print()
Print('   %s' % (LocalTimeStr(EpochMin)))
Print('   %s' % (LocalTimeStr(EpochMax)))
print()
Print('existing quarter range')
print()
Print('   %s' % (LocalTimeStr(QuarterToEpoch(QuarterMin))))
Print('   %s' % (LocalTimeStr(QuarterToEpoch(QuarterMax))))
print()
Print('condensing quarters')
print()
Quarter = NewQuarters = 0
for Epoch in range(EpochMin, EpochMax+1):
  if Quarter != EpochToQuarter(Epoch):
    Quarter = EpochToQuarter(Epoch)
    if Quarter < QuarterMin or Quarter > QuarterMax:
      CondenseQuarter(DbCursor1, Quarter)
      NewQuarters += 1
DbConnection.commit()
if NewQuarters:
  Print('  condensed %d new quarters' % (NewQuarters))
else:
  Print('  no new quarters to condense')
print()
if AutoYes or raw_input("try to recondense quarters with missing data? (y/n): ").lower().strip()[:1] == "y":
  if not AutoYes:
    print()
  NoChange = Recondensed = 0
  DbCursor1.execute('SELECT * FROM quarter ORDER BY id ASC')
  for Row1 in DbCursor1.fetchall():
    if Row1['epochs'] < 13:
      if CondenseQuarter(DbCursor2, Row1['id'], Row1['epochs']):
        Recondensed += 1
      else:
        NoChange += 1
    else:
      NoChange += 1
  if Recondensed:
    print()
  Print('  recondensed %d quarters, %d quarters unchanged' % (Recondensed, NoChange))
  print()
else:
  print()
DbConnection.commit()
DbConnection.close()
Print('done')
print()

#==============================================================================
# end
#==============================================================================
