import os
import sys
import json
import time
import datetime
from subprocess import Popen, PIPE, STDOUT
from src.colors import bcolors

if not os.path.isfile('config.json'):
    print(f'{bcolors.FAIL}No config.json found at {os.getcwd()}/config.json')
    sys.exit(1)

with open('config.json') as f:
    config = json.load(f)

LOG_DIR = config['logfileRoot']

if not os.path.isdir(LOG_DIR):
    print(f'{LOG_DIR} is not a valid directory')
    sys.exit(1)

logfile = f'{LOG_DIR}/plex-auto-genres-automate.log'

for run in config['run']:
    LIBRARY     = run['library']
    TYPE        = run['type']
    SET_POSTERS = run['setPosters']

    print(f'{bcolors.OKCYAN}Running via Automation -- library={LIBRARY}, type={TYPE}, setPosters={SET_POSTERS}{bcolors.ENDC}')

    argumentList = ['python', 'main.py', '--library', f'{LIBRARY}', '--type', f'{TYPE}', '--yes', '--no-progress']

    with Popen(argumentList, stdout=PIPE, stderr=STDOUT) as p, open(logfile, 'ab') as file:
        timestamp = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')
        file.write(str.encode(f'--- Execution {timestamp} ---\n'))
        for line in p.stdout:
            file.write(line)
        file.close()

    if SET_POSTERS:
        argumentList.append('--set-posters')
        with Popen(argumentList, stdout=PIPE, stderr=STDOUT) as p, open(logfile, 'ab') as file:
            for line in p.stdout:
                file.write(line)
            file.write(str.encode('\n'))
            file.close()
