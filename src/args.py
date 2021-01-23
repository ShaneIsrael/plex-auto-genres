import argparse
import sys
import os

from src.colors import bcolors

example_text = '\nexample command: ' + bcolors.BOLD +  'python plex-auto-genres.py --library "Anime Shows" --type anime' + bcolors.ENDC

parser = argparse.ArgumentParser(description='Adds genre tags (collections) to your Plex media.', epilog=example_text)
parser.add_argument('--library', action='store', dest='library', nargs=1,
                    help='The exact name of the Plex library to generate genre collections for.')
parser.add_argument('--type', dest='type', action='store', choices=('anime', 'standard-movie', 'standard-tv'), nargs=1,
                    help='The type of media contained in the library')
parser.add_argument('--set-posters', help='uploads posters located in posters/<type> of matching collections. Supports (.PNG)', action='store_true')
parser.add_argument('--sort', help='sort collections by adding the sort prefix character to the collection sort title', action='store_true')
parser.add_argument('--dry', help='Do not modify plex collections (debugging feature)', action='store_true')
parser.add_argument('--no-progress', help='Do not display the live updating progress bar', action='store_true')
parser.add_argument('-f', '--force', help='Force proccess on all media (independently of proggress recorded in logs/).', action='store_true')
parser.add_argument('-y', '--yes', help='Do not prompt.', action='store_true')

if len(sys.argv)==1:
    parser.print_help(sys.stderr)
    sys.exit(1)

args = parser.parse_args()

if not args.type or not args.library:
    print(f'\n{bcolors.FAIL}The parameters {bcolors.BOLD}--library{bcolors.ENDC}{bcolors.FAIL} ' +
        f'and {bcolors.BOLD}--type{bcolors.ENDC}{bcolors.FAIL} are required.\n{bcolors.ENDC}')
    sys.exit(1)

LIBRARY     = args.library[0]
TYPE        = args.type[0]
DRY_RUN     = args.dry
SET_POSTERS = args.set_posters
SORT        = args.sort
FORCE       = args.force
NO_PROMPT   = args.yes
NO_PROGRESS = args.no_progress

if FORCE and not SET_POSTERS:
    if os.path.isfile(f'logs/plex-{TYPE}-successful.txt'):
        os.remove(f'logs/plex-{TYPE}-successful.txt')
    if os.path.isfile(f'logs/plex-{TYPE}-failures.txt'):
        os.remove(f'logs/plex-{TYPE}-failures.txt')
