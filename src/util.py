import os
import json
from re import search
from src.colors import bcolors
from src.args import DRY_RUN, TYPE

# tmdb doesn't have a rate limit, but we sleep for 0.5 anyways
# Jikan fetch requires 2 request with a 4 second sleep on each request
def getSleepTime(mediaType):
    return 1 if search('^\S*show$|^\S*movie$', mediaType) else 8

def confirm():
    while True:
        response = input(bcolors.WARNING + "Continue? y/n... " + bcolors.ENDC)

        if response.lower() == 'y':
            return True
        if response.lower() == 'n':
            return False

class LoadConfig:
    def __init__(self):
        self.ignore = None
        self.replace = None
        if (os.path.isfile(f'config/config.json')):
            with open('config/config.json') as f:
                configJson = json.load(f)
                try:
                    if (configJson['general_settings']):
                        config = configJson['general_settings']['genres'][TYPE]
                        if (config['ignore']):
                            self.ignore = config['ignore']
                        if (config['replace']):
                            self.replace = config['replace']
                except KeyError as e: 
                    print(f'{bcolors.WARNING}Could not load config due to invalid/missing key: {str(e)}{bcolors.ENDC}, see config/config.json.example')


class LoadProgress:
    def __init__(self):
        self.successfulMedia = []
        self.failedMedia = []
        if not DRY_RUN:
            if not os.path.isdir('./logs'):
                os.mkdir('./logs')
            if os.path.isfile(f'logs/plex-{TYPE}-successful.txt'):
                with open(f'logs/plex-{TYPE}-successful.txt', 'r') as f:
                    self.successfulMedia = json.load(f)
            if os.path.isfile(f'logs/plex-{TYPE}-failures.txt'):
                with open(f'logs/plex-{TYPE}-failures.txt', 'r') as f:
                    self.failedMedia = json.load(f)

class SaveProgress:
    def __init__(self, successfulMedia, failedMedia):
        if not DRY_RUN:
            if not os.path.isdir('./logs'):
                os.mkdir('./logs')
            if successfulMedia:
                with open(f'logs/plex-{TYPE}-successful.txt', 'w') as f:
                    json.dump(successfulMedia, f)
            if failedMedia:
                with open(f'logs/plex-{TYPE}-failures.txt', 'w') as f:
                    json.dump(failedMedia, f)
