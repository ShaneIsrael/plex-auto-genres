#pylint: disable=no-member, line-too-long
from src.args import SET_POSTERS, LIBRARY, TYPE, NO_PROMPT
from src.colors import bcolors
from src.setup import PLEX_COLLECTION_PREFIX, PLEX_SERVER_NAME, PLEX_BASE_URL
from src.util import confirm
from src.plex import connectToPlex, uploadCollectionArt, generate

plex = connectToPlex()

if __name__ == '__main__':
    if SET_POSTERS:
        print(f'\nYou are about to update your {bcolors.WARNING}[{LIBRARY}]{bcolors.ENDC} collection\'s posters to any matching image titles located at {bcolors.WARNING}posters/{bcolors.ENDC}.')
        if NO_PROMPT or confirm():
            uploadCollectionArt(plex)
    else:
        CONFIRMATION = f'\nYou are about to create [{bcolors.WARNING}{TYPE}{bcolors.ENDC}] genre collection tags for the library [{bcolors.WARNING}{LIBRARY}{bcolors.ENDC}] on your server [{bcolors.WARNING}{(PLEX_SERVER_NAME or PLEX_BASE_URL)}{bcolors.ENDC}].'
        if PLEX_COLLECTION_PREFIX:
            CONFIRMATION += 'With prefix ['+bcolors.WARNING+PLEX_COLLECTION_PREFIX+bcolors.ENDC+'].'

        print(CONFIRMATION)
        if NO_PROMPT or confirm():
            UPDATE_COUNT = generate(plex)
            print(f'Updated {UPDATE_COUNT} entrie(s) since last run.')

    print()
