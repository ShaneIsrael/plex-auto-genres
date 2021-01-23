import os
import sys
import json
import time
import datetime
from subprocess import Popen, PIPE, STDOUT
from src.colors import bcolors

def getTimestamp():
    return datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')

def printWithTimestamp(text):
    print(f'{getTimestamp()}\t{text}')

if not os.path.isfile('config/config.json'):
    print(f'{bcolors.FAIL}No config.json found at {os.getcwd()}/config/config.json')
    sys.exit(1)

with open('config/config.json') as f:
    config = json.load(f)


if not os.path.isdir('logs'):
    print(f'Could not find logs directory, expected {os.getcwd()}/logs')
    sys.exit(1)

LOGFILE = 'logs/plex-auto-genres-automate.log'
executions = config['automation_settings']['run']

for i, run in enumerate(executions, 1):
    LIBRARY          = run['library']
    TYPE             = run['type']
    SET_POSTERS      = run['setPosters']
    SORT_COLLECTIONS = run['sortCollections']

    
    printWithTimestamp(f'{bcolors.OKCYAN}Running [{i}/{len(executions)}] -- library={LIBRARY}, type={TYPE}, setPosters={SET_POSTERS}, sortCollections={SORT_COLLECTIONS} {bcolors.ENDC}')

    argumentList = ['python', 'plex-auto-genres.py', '--library', f'{LIBRARY}', '--type', f'{TYPE}', '--yes', '--no-progress']

    printWithTimestamp(f'\t> Checking for new media and fetching genres...')
    with Popen(argumentList, stdout=PIPE, stderr=STDOUT) as p, open(LOGFILE, 'ab') as file:
        file.write(str.encode(f'--- Execution {getTimestamp()} ---\n'))
        for line in p.stdout:
            file.write(line)
        file.close()

    if SET_POSTERS:
        argumentList.append('--set-posters')

    if SORT_COLLECTIONS:
        argumentList.append('--sort')
    
    if SET_POSTERS or SORT_COLLECTIONS:
        printWithTimestamp(f'\t> Running post process command(s): setPosters={SET_POSTERS} sortCollections={SORT_COLLECTIONS}...')
        with Popen(argumentList, stdout=PIPE, stderr=STDOUT) as p, open(LOGFILE, 'ab') as file:
            for line in p.stdout:
                file.write(line)
            file.write(str.encode('\n'))
            file.close()
        printWithTimestamp('\t> Post process command(s) finished...')
    else:
        printWithTimestamp('\t> No post process commands to run, finished...')

print()