import math
import os
from datetime import datetime
from re import sub

from plexapi.myplex import MyPlexAccount, PlexServer

from src.args import DRY_RUN, LIBRARY, TYPE
from src.colors import bcolors
from src.genres import getGenres, sanitizeTitle
from src.anime import getAnime
from src.progress import printProgressBar
from src.setup import (
    PLEX_BASE_URL,
    PLEX_COLLECTION_PREFIX,
    PLEX_PASSWORD,
    PLEX_SERVER_NAME,
    PLEX_TOKEN,
    PLEX_USERNAME,
    validateDotEnv
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

def setAnimeRatings(plex):
    try:
        library = plex.library.section(LIBRARY).all()
        total = len(library)
        printProgressBar(0, total, prefix='Progress:', suffix='Complete', length=50)
        for i, media in enumerate(library, 1):
            anime = getAnime(media.title)
            score = str(anime['score']) if anime['score'] else '0.0'
            media.rate(score)
            media.edit(**{'rating.value': score})
            printProgressBar(i, total, prefix='Progress:', suffix='Complete', length=50)
        print()
    except Exception as e:
        print(e)

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