#!/bin/sh

# setup initial config file
if [ ! -f /config/config.json ]
then
    echo "No config.json file found at /config/config.json. Did you volume mount the config directory?"
    exit
fi

# generate collections once on container start
python3 -u /automate.py

echo "Creating cron process, this script will now run nightly while this container is up."
# # start crond in foreground
exec crond -f