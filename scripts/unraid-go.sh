#!/bin/bash
# Add these lines to /boot/config/go on the Unraid flash drive.
# They load the drivers this dashboard reads. They do not change fan speeds.

modprobe nct6775 || true
modprobe coretemp || true
modprobe drivetemp || true
modprobe i2c-dev || true
modprobe i2c-i801 || true
