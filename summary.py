#!/usr/bin/env python

from __future__ import print_function

PROGRAM = 'summary.py'
VERSION = '1.809.180'
CONTACT = 'bright.tiger@mail.com' # michael nagy

#==============================================================================
# display a summary of the current database content
#==============================================================================

import time

#----------------------------------------------------------------------
# bitmask values which indicate publication to weather underground and
# aeris was successfull
#----------------------------------------------------------------------

MASK_REPORTED_WU = 0x01
MASK_REPORTED_PS = 0x02

#----------------------------------------------------------------------
# the standard utc and local time string format we use throughout
#----------------------------------------------------------------------

TimePattern = '%a %Y-%m-%d %H:%M:%S'

def LocalTimeStr(Quarter):
  Epoch = Quarter * 900
  return time.strftime(TimePattern, time.localtime(Epoch))

#----------------------------------------------------------------------
# main
#----------------------------------------------------------------------

import psycopg2, psycopg2.extras

try:
  DbConnection = psycopg2.connect('dbname=weather')
  DbCursor = DbConnection.cursor(cursor_factory=psycopg2.extras.DictCursor)
  DbCursor.execute('SELECT min(id), max(id) FROM quarter')
  Row = DbCursor.fetchone()
  QuarterMin = Row[0]
  QuarterMax = Row[1]
  print()
  print('Quarter [%d..%d]' % (QuarterMin, QuarterMax))
  print()
  print('Quarter %d is %s' % (QuarterMin, LocalTimeStr(QuarterMin)))
  print('Quarter %d is %s' % (QuarterMax, LocalTimeStr(QuarterMax)))
  print()
  DbCursor.execute('SELECT * FROM quarter WHERE id < %d ORDER BY id ASC' % (QuarterMax))
  Data = {}
  Quarters = 0
  Gaps = []
  GapLength = 0
  for Row in DbCursor.fetchall():
    Quarters += 1
    Epochs = Row['epochs']
    if Epochs:
      if GapLength:
        Gaps.append(GapLength)
        GapLength = 0
    else:
      GapLength += 1
    Reported = Row['reported_mask']
    if not Epochs in Data:
      Data[Epochs] = {'count': 0, 'wu': 0, 'ps': 0}
    Stats = Data[Epochs]
    Stats['count'] += 1
    if Reported & MASK_REPORTED_WU:
      Stats['wu'] += 1
    if Reported & MASK_REPORTED_PS:
      Stats['ps'] += 1
  DbConnection.close()
  print('Epochs  Datasets  Wunderground  Aeris')
  for Epochs in sorted(Data):
    Stats = Data[Epochs]
    print('%6d  %8d  %12d  %5d' % (Epochs, Stats['count'], Stats['wu'], Stats['ps']))
  print('        --------')  
  print('        %8d, %s gaps' % (Quarters, str(Gaps).replace(' ','')))
  print()
except psycopg2.Error as er:
  print('db error: %s' % (er.message))


#==============================================================================
# end
#==============================================================================
