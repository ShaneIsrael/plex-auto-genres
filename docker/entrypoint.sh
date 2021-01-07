#!/bin/sh

# setup initial config file
if [ ! -f /config/config.json ]
then
    echo "No config.json file found at /config/config.json. Did you volume mount the config directory?"
fi

# generate collections once on container start
python3 /automate.py

# start crond in foreground
exec crond -f