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
