import os
import json
from time import sleep
from re import search
from datetime import datetime
from src.colors import bcolors
from src.args import DRY_RUN, TYPE
from src.setup import jikan, movie, tv
from src.genres import getGenres, sanitizeTitle

class QueryObj:
    def __init__(self, title):
        self.title = title
   
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
    
def query(q):
    q = QueryObj(q)
    if TYPE == 'anime':
        title = q.title.split(' [')[0]
        if len(title.split()) > 10:
            title = " ".join(title.split()[0:10])

        query = jikan.search('anime', title, page=1) # search result

        if not query['results']:
            print(f'Found 0 results on MyAnimeList for {bcolors.OKCYAN}{q.title}{bcolors.ENDC}')
            return
        totalResults = len(query['results'])
        for i, r in enumerate(query['results'], 0):
            if r['title'].lower() == title.lower():
                match = r
                query['results'].pop(i)
                break
        else:
            match = query['results'][0]
            query['results'].pop(0)

        
        print(f'Found {bcolors.WARNING}{totalResults} result(s){bcolors.ENDC} for {bcolors.OKCYAN}{q.title}{bcolors.ENDC}')
        print(f'Top result: {bcolors.OKGREEN}{match["title"]}{bcolors.ENDC} Released: {match["start_date"].split("-")[0]}')
        animeId = match['mal_id'] # anime's MyAnimeList ID
        anime = jikan.anime(animeId) # all of the anime's info
        genres = [ e['name'] for e in anime['genres'] ] # list comprehension
        print(f'Genres: {bcolors.OKGREEN}{", ".join(genres)}{bcolors.ENDC}')
        if len(query['results']) > 1:
            print(f'\nNext highest matching results...')
            for i, r in enumerate(query['results'], 1):
                print(f'{bcolors.WARNING}{r["title"]}{bcolors.ENDC} Released: {r["start_date"].split("-")[0]}')
                if i == 5:
                    break
    else:
        db = movie if TYPE == 'standard-movie' else tv
        results = db.search(sanitizeTitle(q.title))
        if (len(results) > 0):
            print(f'Found {bcolors.WARNING}{len(results)} result(s){bcolors.ENDC} for {bcolors.OKCYAN}{q.title}{bcolors.ENDC}')
            if (TYPE == 'standard-tv'):
                results[0] = db.details(results[0].id)
                genres = [ y[0] for y in [x['name'].split(' & ') for x in results[0].genres] ]
                print(f'Top result: {bcolors.OKGREEN}{results[0].name}{bcolors.ENDC} Released: {results[0].first_air_date.split("-")[0]}')
            else:
                print(f'Top result: {bcolors.OKGREEN}{results[0].title}{bcolors.ENDC} Released: {results[0].release_date.split("-")[0]}')
                genres = getGenres(q, TYPE)
            print(f'Genres: {bcolors.OKGREEN}{", ".join(genres)}{bcolors.ENDC}')
            if len(results) > 1:
                print(f'\nNext highest matching results...')
                for i,  r in enumerate(results[1:], 0):
                    if (TYPE == 'standard-tv'):
                        r = db.details(r.id)
                        print(f'{bcolors.WARNING}{r.name}{bcolors.ENDC} Released: {r.first_air_date.split("-")[0]}')
                    else:
                        print(f'{bcolors.WARNING}{r.title}{bcolors.ENDC} Released: {r.release_date.split("-")[0]}')
                    if i == 5:
                        break
        else:
            print(f'Found 0 results on TMDB for {bcolors.OKCYAN}{q.title}{bcolors.ENDC}')

class LoadConfig:
    def __init__(self):
        self.ignore = None
        self.replace = None
        self.sortedPrefix = None
        self.sortedCollections = None
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
                        if (config['sortedPrefix']):
                            self.sortedPrefix = config['sortedPrefix']
                        if (config['sortedCollections']):
                            self.sortedCollections = config['sortedCollections']
                except KeyError as e: 
                    print(f'{bcolors.WARNING}WARNING: Invalid or Missing key: {str(e)}{bcolors.ENDC} some functionality may fail, see config/config.json.example')


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
