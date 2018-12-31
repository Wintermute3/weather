#!/usr/bin/env python

from __future__ import print_function

PROGRAM = 'voltage.py'
VERSION = '1.812.301'
CONTACT = 'bright.tiger@mail.com' # michael nagy

#==============================================================================
# display battery voltage by date/time                                                                                                                          
#==============================================================================                                                                                 
                                                                                                                                                                
import psycopg2, psycopg2.extras, time                                                                                                                          
import datetime as dt                                                                                                                                           
import numpy as np                                                                                                                                              
import matplotlib.pyplot as plt
import matplotlib.dates as md

#----------------------------------------------------------------------
# collect data from weather database epoch table and display
#----------------------------------------------------------------------

print()
print('%s %s' % (PROGRAM, VERSION))
print()

DbConnection = psycopg2.connect('dbname=weather')
DbCursor = DbConnection.cursor(cursor_factory=psycopg2.extras.DictCursor)
DbCursor.execute('SELECT id,power_volt FROM epoch ORDER BY id')
Dates = []
Volts = []
LastTime = 0
for Row in DbCursor.fetchall():
  Time    = Row['id'        ]
  Voltage = Row['power_volt']
  if Voltage > 1.0:
    if LastTime and (Time > LastTime + 7200):
      Dates.append(dt.datetime.fromtimestamp(LastTime+900))
      Volts.append(8.0) # np.nan)
      Dates.append(dt.datetime.fromtimestamp(Time-900))
      Volts.append(8.0) # np.nan)
    LastTime = Time
    Dates.append(dt.datetime.fromtimestamp(Time))
    Volts.append(Voltage)

plt.plot(Dates, Volts)
plt.xlabel('time')
plt.ylabel('voltage')
plt.show()

#==============================================================================
# end
#==============================================================================
