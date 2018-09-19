#!/bin/bash

PROGRAM='snapshot.sh'
VERSION='1.809.151'
CONTACT='bright.tiger@gmail.com'

# This script executes both locally on the raspberry pi to collect
# a backup snapshot of the database and all support scripts and
# configuration info and remotely to save the resulting compressed
# backup snapshot archive.  It determines its environment via the
# userid, which is expected to be 'pi' on the raspberry pi system.
#
#   on raspbarry pi: generate backup snapshot archive and send
#     the resulting tar gzip'ed (.tgz) archive to stdout
#
#   on other system: run a copy of this script on the raspberry
#     pi and catch the resulting stdout traffic in a local .tgz
#     backup file

if [ `whoami` == 'pi' ]; then
  rm -rf snapshot/
  mkdir snapshot
  pg_dump -c --no-owner weather > snapshot/weather.db
  cp /etc/systemd/system/proxy-logger.service snapshot/
  cp weather/*.{py,sh,log} snapshot/
  cp -R weather/fonts/ snapshot/
  crontab -l > snapshot/crontab.list
  cd snapshot
  tar czf ../weather.tgz .
  rm -rf snapshot/
else
  echo
  echo -n 'Generating snapshot...'
  ssh pi@pi < ./snapshot.sh > /dev/null 2>&1
  echo -n 'fetching snapshot...'
  scp pi@pi:weather.tgz . > /dev/null
  echo 'done.'
  echo
  tar tvf ./weather.tgz
  echo
  echo "Snapshot 'weather.tgz' is a tar gzip compressed archive."
  echo
fi

#
# End
#