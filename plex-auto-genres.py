#pylint: disable=no-member, line-too-long
import sys
from src.args import SET_POSTERS, LIBRARY, TYPE, NO_PROMPT, SORT, QUERY, RATE_ANIME, RATING_COLS
from src.colors import bcolors
from src.setup import PLEX_COLLECTION_PREFIX, PLEX_SERVER_NAME, PLEX_BASE_URL
from src.util import confirm, query
from src.plex import connectToPlex, uploadCollectionArt, sortCollections, generate, createRatingCollections, setAnimeRatings



if __name__ == '__main__':
    if QUERY:
        query(QUERY)
        sys.exit()

    plex = connectToPlex()
    if not plex:
        sys.exit()
        
    if RATE_ANIME:
        print(f'\nYou are about to update your {bcolors.WARNING}[{LIBRARY}]{bcolors.ENDC} collection\'s ratings with ratings from MyAnimeList.')
        if NO_PROMPT or confirm():
            setAnimeRatings(plex)
        sys.exit()

    if RATING_COLS:
        print(f'\nYou are about to create [{bcolors.WARNING}{TYPE}{bcolors.ENDC}] rating collection tags for the library [{bcolors.WARNING}{LIBRARY}{bcolors.ENDC}] on your server [{bcolors.WARNING}{(PLEX_SERVER_NAME or PLEX_BASE_URL)}{bcolors.ENDC}].')
        if NO_PROMPT or confirm():
            createRatingCollections(plex)
        sys.exit()

    if SET_POSTERS:
        print(f'\nYou are about to update your {bcolors.WARNING}[{LIBRARY}]{bcolors.ENDC} collection\'s posters to any matching image titles located at {bcolors.WARNING}posters/{bcolors.ENDC}.')
        if NO_PROMPT or confirm():
            uploadCollectionArt(plex)

    if SORT:
        print(f'\nYou are about to sort {bcolors.WARNING}[{LIBRARY}]{bcolors.ENDC} collection\'s by prefixing their sort titles with our prefix character set in {bcolors.WARNING}config/config.json{bcolors.ENDC}.')
        if NO_PROMPT or confirm():
            sortCollections(plex, LIBRARY)

    if (not SET_POSTERS and not SORT):
        CONFIRMATION = f'\nYou are about to create [{bcolors.WARNING}{TYPE}{bcolors.ENDC}] genre collection tags for the library [{bcolors.WARNING}{LIBRARY}{bcolors.ENDC}] on your server [{bcolors.WARNING}{(PLEX_SERVER_NAME or PLEX_BASE_URL)}{bcolors.ENDC}].'
        if PLEX_COLLECTION_PREFIX:
            CONFIRMATION += ' With prefix ['+bcolors.WARNING+PLEX_COLLECTION_PREFIX+bcolors.ENDC+'].'

        print(CONFIRMATION)
        if NO_PROMPT or confirm():
            UPDATE_COUNT = generate(plex)
            print(f'Updated {UPDATE_COUNT} entrie(s) since last run.')
    
    print()