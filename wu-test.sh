#!/bin/bash

export WU_STATION_ID=KFLMYAKK20
export WU_URL_GET=https://weatherstation.wunderground.com/weatherstation/updateweatherstation.php
export WU_PASSWORD=h1i2nqhw

echo curl -v "$WU_URL_GET?ID=$WU_STATION_ID&PASSWORD=$WU_PASSWORD"
     curl -v "$WU_URL_GET?ID=$WU_STATION_ID&PASSWORD=$WU_PASSWORD"
