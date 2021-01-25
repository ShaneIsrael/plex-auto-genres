import math
import os
from datetime import datetime
from re import sub

from plexapi.myplex import MyPlexAccount, PlexServer

from src.args import DRY_RUN, LIBRARY, TYPE
from src.colors import bcolors
from src.genres import getGenres, sanitizeTitle
from src.progress import printProgressBar
from src.setup import (
    PLEX_BASE_URL,
    PLEX_COLLECTION_PREFIX,
    PLEX_PASSWORD,
    PLEX_SERVER_NAME,
    PLEX_TOKEN,
    PLEX_USERNAME,
    validateDotEnv,
    jikan,
    movie,
    tv
)
from src.util import getSleepTime, LoadConfig, LoadProgress, SaveProgress

validateDotEnv(TYPE)


def connectToPlex():
    print('\nConnecting to Plex...')
    try:
        if PLEX_USERNAME is not None and PLEX_PASSWORD is not None and PLEX_SERVER_NAME is not None:
            account = MyPlexAccount(PLEX_USERNAME, PLEX_PASSWORD)
            plex = account.resource(PLEX_SERVER_NAME).connect()
        elif PLEX_BASE_URL is not None and PLEX_TOKEN is not None:
            plex = PlexServer(PLEX_BASE_URL, PLEX_TOKEN)
        else:
            raise Exception(
                "No valid credentials found. See the README for more details.")
    except Exception as e:
        raise Exception from e

    return plex



def generate(plex):

    dataLoad = LoadProgress()
    config = LoadConfig()
    successfulMedia = dataLoad.successfulMedia
    failedMedia = dataLoad.failedMedia


    try:
        # plex library
        library = plex.library.section(LIBRARY).all()

        # media counters
        totalCount = len(library)
        unfinishedCount = len(list(filter(
            lambda media: f'{media.title} ({media.year})' not in successfulMedia and f'{media.title} ({media.year})' not in failedMedia, library)))
        finishedCount = totalCount - unfinishedCount

        # estimated time "of arrival"
        eta = ((unfinishedCount * getSleepTime(TYPE)) / 60) * 2

        print(f'Found {totalCount} media entries under {LIBRARY} ({finishedCount}/{totalCount} completed).')
        print(f'Estimated time to completion: {math.ceil(eta)} minutes...\n')

        printProgressBar(0, totalCount, prefix='Progress:',
                         suffix='Complete', length=50)
        # i = current media's position
        updateCount = 0
        for i, media in enumerate(library, 1):
            mediaIdentifier = f'{media.title} ({media.year})'

            if mediaIdentifier not in successfulMedia and mediaIdentifier not in failedMedia:
                genres = getGenres(media, TYPE)

                if not genres:
                    failedMedia.append(mediaIdentifier)

                else:
                    if not DRY_RUN:
                        updateCount += 1
                        for genre in genres:
                            if (config.ignore and (genre.lower() in map(str.lower, config.ignore))):
                                continue
                            if (config.replace and (genre.lower() in map(str.lower, config.replace.keys()))):
                                genre = config.replace[genre.lower()]
                            genre = PLEX_COLLECTION_PREFIX + genre
                            media.addCollection(genre)

                    successfulMedia.append(mediaIdentifier)
            
            printProgressBar(i, totalCount, prefix='Progress:',
                             suffix='Complete', length=50)

        if failedMedia:
            print(bcolors.FAIL + f'\nFailed to get genre information for {len(failedMedia)} entries. ' +
                bcolors.ENDC + f'See logs/plex-{TYPE}-failures.txt.')
        else:
            print(bcolors.OKGREEN + '\nSuccessfully got genre information for all entries. ' +
                bcolors.ENDC + f'See logs/plex-{TYPE}-successful.txt.')

    except KeyboardInterrupt:
        print('\n\nOperation interupted, progress has been saved.')
    except KeyError as e:
        print(f'\n\nKeyError: {e}, if this is a replace value in your config, please make it lowercase.')
    except Exception as e:
        print(f'\n\nUncaught Exception: {e}')

    SaveProgress(successfulMedia=successfulMedia, failedMedia=failedMedia)
    return updateCount

def query(q):
    if TYPE == 'anime':
        title = q.title.split(' [')[0]
        if len(title.split()) > 10:
            title = " ".join(title.split()[0:10])

        query = jikan.search('anime', title, page=1) # search result

        if not query['results']:
            print(f'Found 0 results on MyAnimeList for {bcolors.OKCYAN}{q.title}{bcolors.ENDC}')
            return
        else:
            results = []
            for r in query['results']:
                if title.lower() in r['title'].lower():
                    results.append(r)
            if results:
                results = sorted(results, key = lambda i: i['mal_id'])
            else:
                results = query['results']

            print(f'Found {bcolors.WARNING}{len(results)} result(s){bcolors.ENDC} for {bcolors.OKCYAN}{q.title}{bcolors.ENDC}')
            print(f'Top result: {bcolors.OKGREEN}{results[0]["title"]}{bcolors.ENDC} Released: {results[0]["start_date"].split("-")[0]}')
            animeId = results[0]['mal_id'] # anime's MyAnimeList ID
            anime = jikan.anime(animeId) # all of the anime's info
            genres = [ e['name'] for e in anime['genres'] ] # list comprehension
            print(f'Genres: {bcolors.OKGREEN}{", ".join(genres)}{bcolors.ENDC}')
            if len(results) > 1:
                print(f'\nNext highest matching results...')
                for i, r in enumerate(results[1:], 0):
                    print(f'{bcolors.WARNING}{r["title"]}{bcolors.ENDC} Released: {r["start_date"].split("-")[0]}')
                    if i == 2:
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
                    if i == 2:
                        break
        else:
            print(f'Found 0 results on TMDB for {bcolors.OKCYAN}{q.title}{bcolors.ENDC}')

def uploadCollectionArt(plex):
    # complete path to the posters directory
    postersDir = os.getcwd() + f'/posters/{TYPE}'

    # if there is no posters/ directory
    if not os.path.isdir(postersDir):
        print(bcolors.FAIL + f'Could not find poster art directory. Expected location: {postersDir}.' + bcolors.ENDC)
        return

    collections = plex.library.section(LIBRARY).collection()

    # if there are no collections (same as len(collections) == 0)
    if not collections:
        print(f'{bcolors.FAIL}Could not find any Plex collections.{bcolors.ENDC}')
        return

    print('\nUploading collection artwork...')

    for c in collections:
        # remove prefix characters and replace spaces with dashes
        title = c.title

        if (title[0] == PLEX_COLLECTION_PREFIX):
            title = title.replace(PLEX_COLLECTION_PREFIX, '', 1)

        # path to the image
        posterPath = f'{postersDir}/' + \
            title.lower().replace(' ', '-') + '.png'

        # If the poster exists, upload it
        if os.path.isfile(posterPath):
            print(f'Uploading {title}...', end=' ')

            try:
                c.uploadPoster(filepath=posterPath)
            except Exception as e:
                print(f'{bcolors.FAIL}failed!{bcolors.ENDC} {bcolors.WARNING}See error:{bcolors.ENDC}{e}')
            else:
                print(f'{bcolors.OKGREEN}done!{bcolors.ENDC}')

        # If it doesn't, print 404
        else:
            print(f'No poster found for collection {bcolors.WARNING}{title}{bcolors.ENDC}, expected {bcolors.WARNING}'
                + sub(f'^{os.getcwd()}', '', posterPath) + f'{bcolors.ENDC}.')

    return

def sortCollections(plex, library):
    config = LoadConfig()
    prefix = config.sortedPrefix
    sortedCollections = config.sortedCollections

    if not prefix:
        print(f'{bcolors.FAIL}NO PREFIX SET.{bcolors.ENDC} Please set the {bcolors.WARNING}sortedPrefix{bcolors.ENDC} value in your config/config.json file.')
        return
    if not sortedCollections:
        print(f'{bcolors.FAIL}NO PREFIX COLLECTIONS SET.{bcolors.ENDC} Please set the {bcolors.WARNING}sortedCollections{bcolors.ENDC} list in your config/config.json file.')
        return

    for c in sortedCollections:
        c = c.strip().lower()
        collections = plex.library.section(library).collection(title=c)
        if (len(collections) > 0):
            collection = collections[0]
            sortTitle = f'{prefix}{collection.title}'
            collection.edit(**{'titleSort.value': sortTitle, 'titleSort.locked': '0'})
            print(f'Collection {bcolors.OKGREEN}{collection.title}{bcolors.ENDC} sort title updated to {bcolors.OKGREEN}{sortTitle}{bcolors.ENDC}')
        else:
            print(f'Could not find a collection with name {bcolors.WARNING}{c}{bcolors.ENDC} in your Plex {bcolors.WARNING}{library}{bcolors.ENDC} collections.')

    return