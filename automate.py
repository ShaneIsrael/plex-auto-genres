import os
import sys
import json
import time
import datetime
from subprocess import Popen, PIPE, STDOUT
from src.colors import bcolors

if not os.path.isfile('config/config.json'):
    print(f'{bcolors.FAIL}No config.json found at {os.getcwd()}/config/config.json')
    sys.exit(1)

with open('config/config.json') as f:
    config = json.load(f)


if not os.path.isdir('logs'):
    print(f'Could not find logs directory, expected {os.getcwd()}/logs')
    sys.exit(1)

LOGFILE = 'logs/plex-auto-genres-automate.log'
executions = config['run']

for i, run in enumerate(executions, 1):
    LIBRARY     = run['library']
    TYPE        = run['type']
    SET_POSTERS = run['setPosters']
    timestamp = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')
    print(f'{timestamp}\t{bcolors.OKCYAN}Running [{i}/{len(executions)}] -- library={LIBRARY}, type={TYPE}, setPosters={SET_POSTERS}{bcolors.ENDC}')

    argumentList = ['python', 'plex-auto-genres.py', '--library', f'{LIBRARY}', '--type', f'{TYPE}', '--yes', '--no-progress']

    with Popen(argumentList, stdout=PIPE, stderr=STDOUT) as p, open(LOGFILE, 'ab') as file:
        file.write(str.encode(f'--- Execution {timestamp} ---\n'))
        for line in p.stdout:
            file.write(line)
        file.close()

    if SET_POSTERS:
        argumentList.append('--set-posters')
        with Popen(argumentList, stdout=PIPE, stderr=STDOUT) as p, open(LOGFILE, 'ab') as file:
            for line in p.stdout:
                file.write(line)
            file.write(str.encode('\n'))
            file.close()
print()