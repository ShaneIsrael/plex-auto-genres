import os
import sys
import json
import subprocess
from src.colors import bcolors

if (not os.path.isfile('config.json')):
    print(f'{bcolors.FAIL}No config.json found at {os.getcwd()}/config.json')
    sys.exit(1)
   
with open('config.json') as f:
    config = json.load(f)

for run in config['run']:
    LIBRARY     = run['library']
    TYPE        = run['type']
    SET_POSTERS = run['setPosters']
    
    print(f'{bcolors.OKCYAN}Running via Automation -- library={LIBRARY}, type={TYPE}, setPosters={SET_POSTERS}{bcolors.ENDC}')
    
    argumentList = ['python', f'main.py', f'--library', f'{LIBRARY}', f'--type', f'{TYPE}', f'--yes']
    
    subprocess.call(argumentList)
    if (SET_POSTERS):
        argumentList.append('--set-posters')
        subprocess.call(argumentList)
