#!/usr/bin/env python
# coding=utf-8

from __future__ import print_function

VERSION = '1.809.171' # Y.YMM.DDn
PROGRAM = 'weather-plot.py'
CONTACT = 'bright.tiger@gmail.com' # michael nagy

# ===============================================================================
# create a png graphic with transparent background and write it to a file.  the
# graphic is designed to display against a light-colored background, and will
# display include the following graphs, aligned horizontally by timestamp:
#
#   temperature / dew point
#   humidity
#   wind speed
#   direction
#   rainfall (hourly and daily)
#   tornado alerts
#
# data to plot will be pulled from the postgresql weather database for the time
# span specified on the command line as start quarter and number of quarters.
# ===============================================================================

import os, sys, time

import matplotlib.pyplot  as plt
import matplotlib.patches as patches

Graphics = 3 # number of distinct graphics

HeightEach = 3 # inches - vertical height of each graphic
WidthEach  = 8 # inches - horizontal width of each graphic

#----------------------------------------------------------------------
# convert a quarter to the epoch of the start of the quarter.
#----------------------------------------------------------------------

def QuarterToEpoch(Quarter):
  return Quarter * 900

#----------------------------------------------------------------------
# call calibratetimeticks to set to reasonable values.
#----------------------------------------------------------------------

TickLimit = 0

TimePattern  = ''
LabelPattern = ''
LabelText    = ''

def DayExt(x):
  def DayMap(d):
    if d in ['01','21','31']:
      return 'st'
    if d in ['02','22']:
      return 'nd'
    if d in ['03','23']:
      return 'rd'
    return 'th'
  y = x.split('$')
  if len(y) == 2:
    x = y[0] + DayMap(y[0][-2:]) + y[1]
  return x

#----------------------------------------------------------------------
# adjust the ticklimit and timepattern as appropriate based on the
# number of quarters being displayed.
#----------------------------------------------------------------------

def CalibrateTimeTicks(Quarter, Quarters):
  global TimePattern, TickLimit, LabelPattern, LabelText
  if Quarters < 960: # 0-10 days
    TickLimit = 5
    TimePattern = '%a %b %d$\n%I:%M %P' # Mon Sep 30th^10:30 am
    LabelPattern = '' # '\n%Y' # 2018
  else: # 10+ days
    TickLimit = 6
    TimePattern = '%b %d$\n%Y' # Sep 30th^2018
    LabelPattern = ''
  if LabelPattern:
    LabelText = DayExt(time.strftime(LabelPattern, time.localtime(QuarterToEpoch(Quarter))))
    LabelText2 = DayExt(time.strftime(LabelPattern, time.localtime(QuarterToEpoch(Quarter+Quarters-1))))
    if LabelText != LabelText2:
      LabelText += '-' + LabelText2
  else:
    LabelText = ''

#----------------------------------------------------------------------
# format a timestamp with appropriate precision based on the number
# of quarters being displayed.
#----------------------------------------------------------------------

def LocalTimeStr(Quarter, Quarters):
  if Quarters < TickLimit:
    Labels = max(2, Quarters)
  else:
    Labels = TickLimit
  TickQuarters = (Quarters / Labels) + 1
  if Quarter % TickQuarters == TickQuarters / 2:
    return DayExt(time.strftime(TimePattern, time.localtime(QuarterToEpoch(Quarter))))
  return ''

#----------------------------------------------------------------------
# weather data to display
#----------------------------------------------------------------------

Time   = []
Labels = []

TimeMin = 0
TimeMax = 0

Temperature = []
DewPoint    = []
Humidity    = []
WindSpeed   = []
Direction   = []
Rain        = []
RainTotal   = []
Tau         = []

TempMin = 0
TempMax = 0
WindMax = 0
RainMax = 0

#----------------------------------------------------------------------
# database support.  we are currently using postgresql, but with minor
# modifications to this single block of code we could alternately run
# with sqlite3 or possibly other database engines.
#----------------------------------------------------------------------

import psycopg2, psycopg2.extras

DbName = 'weather'

#----------------------------------------------------------------------
# load the specified quarter and period in quarters from the database.
# if quarter is zero on entry, autoselect the most recent day.
#----------------------------------------------------------------------

def LoadData(Quarter, Quarters):
  global Labels, Time, Temperature, DewPoint, WindSpeed
  global Rain, RainTotal
  global TempMin, TempMax, RainMax, WindMax, TimeMin, TimeMax
  DbConnection = psycopg2.connect('dbname=weather')
  DbCursor = DbConnection.cursor(cursor_factory=psycopg2.extras.DictCursor)
  if Quarter < 1:
    Offset = ((Quarter - 1) * 96) + 16
    print('offset = %d' % (Offset))
    DbCursor.execute('SELECT max(id) FROM quarter')
    Row = DbCursor.fetchone()
    print('MAX=%d' % (Row[0]))
    Quarter  = ((Row[0] / 96) * 96) + Offset
    Quarters =                  96
  print('%d..%d' % (Quarter, Quarters))
  Labels      = []
  Time        = []
  Temperature = []
  DewPoint    = []
  WindSpeed   = []
  Rain        = []
  RainTotal   = []
  CalibrateTimeTicks(Quarter, Quarters)
  DbCursor.execute('SELECT min(id),max(id) FROM quarter WHERE id BETWEEN %d AND %d' % (
    Quarter, Quarter + Quarters - 1))
  Row = DbCursor.fetchone()
  QuarterMin = Row[0]
  QuarterMax = Row[1]
  print('%d..%d' % (QuarterMin, QuarterMax))
  for HotQuarter in range(Quarter,QuarterMin):
    Time.append(HotQuarter)
    Labels.append(LocalTimeStr(HotQuarter, Quarters))
    Temperature.append(0)
    DewPoint   .append(0)
    Humidity   .append(0)
    WindSpeed  .append(0)
    Direction  .append(0)
    Rain       .append(0)
    RainTotal  .append(0)
    Tau        .append(0)
  DbCursor.execute('SELECT * FROM quarter WHERE id BETWEEN %d AND %d ORDER BY id ASC' % (
    Quarter, Quarter + Quarters - 1))
  QueryCount = FirstIndex = 0
  for Row in DbCursor.fetchall():
    QueryCount += 1
    HotQuarter = Row['id']
    FirstIndex = len(Temperature)
    Time.append(HotQuarter)
    Labels.append(LocalTimeStr(HotQuarter, Quarters))
    Temperature.append(Row['temp_f'        ])
    DewPoint   .append(Row['dewpoint_f'    ])
    Humidity   .append(Row['humidity_pct'  ])
    WindSpeed  .append(Row['wind_mph'      ])
    Direction  .append(Row['wind_direction'])
    Rain       .append(Row['rain_in'       ])
    RainTotal  .append(Row['rain_day_in'   ])
    Tau        .append(Row['tau_status'    ])
  for HotQuarter in range(QuarterMax + 1,Quarter + Quarters):
    Time.append(HotQuarter)
    Labels.append(LocalTimeStr(HotQuarter, Quarters))
    Temperature.append(0)
    DewPoint   .append(0)
    Humidity   .append(0)
    WindSpeed  .append(0)
    Direction  .append(0)
    Rain       .append(0)
    RainTotal  .append(0)
    Tau        .append(0)
  if Temperature:
    TempMax = Temperature[FirstIndex]
    TempMin = DewPoint   [FirstIndex]
    WindMax = WindSpeed  [FirstIndex]
    RainMax = RainTotal  [FirstIndex]
    TimeMin = Quarter
    TimeMax = Quarter + Quarters - 1
    for i in range(1,len(Temperature)):
      if Temperature[i]:
        TempMax = max(TempMax, Temperature[i])
        TempMin = min(TempMin, DewPoint   [i])
        WindMax = max(WindMax, WindSpeed  [i])
        RainMax = max(RainMax, RainTotal  [i])
    TempMax = ((round(TempMax      ) / 5.0) + 1.0) * 5.0
    TempMin = ((round(TempMin      ) / 5.0) - 2.0) * 5.0
    WindMax = ((round(WindMax      ) / 5.0) + 1.0) * 5.0
    RainMax = ( round(RainMax * 2.0)        + 1.0) * 0.5
  else:
    print('no data in range')
  DbConnection.commit()
  DbConnection.close()

#----------------------------------------------------------------------
# plot the temperature and dew point
#----------------------------------------------------------------------

def PlotTemperature(PlotIndex):

  # logical grid has 1 column

  plt.subplot((Graphics * 100) + 11 + PlotIndex)

  # plot temperature in red and dew point in green.  Don't specify any
  # labels here, we will do that in the legend definitions below.

  plt.plot(Time, Temperature, 'r-', linewidth=1.0)
  plt.plot(Time, DewPoint   , 'g-', linewidth=1.0)

  # the xticks literals need to align with the time list (same
  # number of elements).  Used empty strings to skip labels.

  if Labels:
    plt.xticks(Time,Labels)

  if LabelText:
    plt.xlabel(LabelText)

  # Define the range of time and temperature axis.

  plt.axis([TimeMin, TimeMax, TempMin, TempMax])

  # make some graphics color blocks for use in the legend.

  patch1 = patches.Patch(color='red'  , label=u'Temperature (°F)')
  patch2 = patches.Patch(color='green', label=  u'Dew Point (°F)')

  # display the legend on one line in lower right with no frame using
  # the red and green color blocks we created above as markers instead
  # of the default lines.

  plt.legend(loc='lower right', frameon=False, ncol=2, handles=[patch1,patch2])

  # turn off the top and right borders of the figure.

  plt.gca().spines.values()[1].set_visible(False) # right
  plt.gca().spines.values()[3].set_visible(False) # top

  # turn off ticks on the bottom axis.

  plt.tick_params(bottom=False)

#----------------------------------------------------------------------
# plot the wind speed
#----------------------------------------------------------------------

def PlotWind(PlotIndex):

  # logical grid has 1 column

  plt.subplot((Graphics * 100) + 11 + PlotIndex)

  # plot temperature in red and dew point in green.  Don't specify any
  # labels here, we will do that in the legend definitions below.

  plt.plot(Time, WindSpeed, 'b-', linewidth=1.0)

  # the xticks literals need to align with the time list (same
  # number of elements).  Used empty strings to skip labels.

  if Labels:
    plt.xticks(Time,Labels)

  if LabelText:
    plt.xlabel(LabelText)

  # define the range of time and temperature axis

  plt.axis([TimeMin, TimeMax, 0, WindMax])

  # make a graphics color blocks for use in the legend

  patch1 = patches.Patch(color='blue', label=u'Wind Speed (mph)')

  # display the legend on one line in lower right with no frame using
  # the red and green color blocks we created above as markers instead
  # of the default lines.

  plt.legend(loc='lower right', frameon=False, ncol=2, handles=[patch1])

  # turn off the top and right borders of the figure

  plt.gca().spines.values()[1].set_visible(False) # right
  plt.gca().spines.values()[3].set_visible(False) # top

  # turn off ticks on the bottom axis

  plt.tick_params(bottom=False)

#----------------------------------------------------------------------
# plot the hourly and total rainfall
#----------------------------------------------------------------------

def PlotRain(PlotIndex):

  # logical grid has 1 column

  plt.subplot((Graphics * 100) + 11 + PlotIndex)

  # plot temperature in red and dew point in green.  Don't specify any
  # labels here, we will do that in the legend definitions below.

  plt.plot(Time, Rain     , 'g-', linewidth=1.0)
  plt.plot(Time, RainTotal, 'b-', linewidth=1.0)

  # the xticks literals need to align with the time list (same
  # number of elements).  Used empty strings to skip labels.

  if Labels:
    plt.xticks(Time,Labels)

  if LabelText:
    plt.xlabel(LabelText)

  # Define the range of time and temperature axis.

  plt.axis([TimeMin, TimeMax, 0, RainMax])

  # make some graphics color blocks for use in the legend.

  patch1 = patches.Patch(color='green', label=u'Rain Rate (in)')
  patch2 = patches.Patch(color='blue' , label=u'Rain Total (in)')

  # display the legend on one line in lower right with no frame using
  # the red and green color blocks we created above as markers instead
  # of the default lines.

  plt.legend(loc='lower right', frameon=False, ncol=2, handles=[patch1,patch2])

  # turn off the top and right borders of the figure.

  plt.gca().spines.values()[1].set_visible(False) # right
  plt.gca().spines.values()[3].set_visible(False) # top

  # turn off ticks on the bottom axis.

  plt.tick_params(bottom=False)

#----------------------------------------------------------------------
# main - pull time span from command line parameters, then pull supporting
# datasets from postgresql, then graph the various values.
#----------------------------------------------------------------------

Quarter = Quarters = 0
if len(sys.argv) > 1:
  try:
    Quarter = int(sys.argv[1])
    if Quarter > 0:
      Quarters = int(sys.argv[2])
  except:
    print('bad arguments - specify start quarter and quarter count')
    os._exit(1)

# load data over specified range, reducing range to cover actual data

LoadData(Quarter, Quarters)

if Temperature:
 #plt.ioff()
  plt.figure(figsize=(WidthEach, HeightEach * Graphics))
  PlotTemperature(0)
  PlotWind(1)
  PlotRain(2)
  plt.savefig('weather-plot.png', bbox_inches='tight', transparent=True)
  plt.show()
  plt.close()

# ===============================================================================
# end
# ===============================================================================
