import os
import sys
import json
import math
import time
import datetime
from timeit import default_timer as timer
from subprocess import Popen, PIPE, STDOUT
from src.colors import bcolors

WAIT_TIME=45

def getTimestamp():
    return datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')

def rateLimitSleep(timeDelta):
    if timeDelta < WAIT_TIME:
        printWithTimestamp(f'\t> Sleeping for {math.ceil(WAIT_TIME - timeDelta)} seconds to avoid Plex connection rate limit...')
        time.sleep(math.ceil(WAIT_TIME - timeDelta))
    return

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
    RATE_ANIME       = run['rateAnime']
    RATING_COLS      = run['createRatingCollections']

    start = timer()

    printWithTimestamp(f'{bcolors.OKCYAN}Running [{i}/{len(executions)}] -- library={LIBRARY}, type={TYPE}, setPosters={SET_POSTERS}, sortCollections={SORT_COLLECTIONS} {bcolors.ENDC}')

    argumentList = ['python', 'plex-auto-genres.py', '--library', f'{LIBRARY}', '--type', f'{TYPE}', '--yes', '--no-progress']

    printWithTimestamp(f'\t> Checking for new media and fetching genres...')
    with Popen(argumentList, stdout=PIPE, stderr=STDOUT) as p, open(LOGFILE, 'ab') as file:
        file.write(str.encode(f'--- Execution {getTimestamp()} ---\n'))
        for line in p.stdout:
            file.write(line)
        file.close()

    postProcess = []
    if SET_POSTERS:
        postProcess.append('--set-posters')

    if SORT_COLLECTIONS:
        postProcess.append('--sort')
    
    if RATE_ANIME:
        postProcess.append('--rate-anime')

    if RATING_COLS:
        postProcess.append('--create-rating-collections')

    if postProcess:
        for command in postProcess:
            rateLimitSleep(timer() - start)
            start = timer()
            printWithTimestamp(f'\t> Running post process command: {command}')
            plist = argumentList
            plist.append(command)
            with Popen(plist, stdout=PIPE, stderr=STDOUT) as p, open(LOGFILE, 'ab') as file:
                for line in p.stdout:
                    file.write(line)
                file.write(str.encode('\n'))
                file.close()
        printWithTimestamp('\t> Post process command(s) finished...')
    else:
        printWithTimestamp('\t> No post process commands to run, finished...')

print()