#!/bin/bash

mkdir -p data
cd data
tar xf ../weather.tgz
psql weather < ./weather.db > /dev/null
../summary.py
