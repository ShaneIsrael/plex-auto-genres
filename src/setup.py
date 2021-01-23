import os
import sys
import signal
from re import search
from dotenv import load_dotenv
from jikanpy import Jikan
from tmdbv3api import TV, Movie, TMDb
from src.colors import bcolors

load_dotenv()

PLEX_USERNAME = os.getenv("PLEX_USERNAME")
PLEX_PASSWORD = os.getenv("PLEX_PASSWORD")
PLEX_SERVER_NAME = os.getenv("PLEX_SERVER_NAME")
PLEX_BASE_URL = os.getenv("PLEX_BASE_URL")
PLEX_TOKEN = os.getenv("PLEX_TOKEN")
PLEX_COLLECTION_PREFIX = os.getenv("PLEX_COLLECTION_PREFIX", "")
PLEX_COLLECTION_SORT_PREFIX = os.getenv("PLEX_COLLECTION_SORT_PREFIX", "")
PLEX_COLLECTION_SORT_NAMES = os.getenv("PLEX_COLLECTION_SORT_NAMES", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

jikan = Jikan()
tmdb = TMDb()
movie = Movie()
tv = TV()

signal.signal(signal.SIGINT, signal.default_int_handler)

tmdb.api_key = TMDB_API_KEY

def validateDotEnv(mediaType):
    if not os.path.isfile('.env') and not (PLEX_USERNAME or PLEX_PASSWORD or PLEX_SERVER_NAME):
        print(bcolors.FAIL + 'No .env file detected. Please locate the .env.example file and copy' +
            ' the contents into a new file named .env placed next to this script.' + bcolors.ENDC)
        sys.exit(1)

    if not PLEX_USERNAME and not PLEX_TOKEN:
        print(bcolors.FAIL + 'PLEX_USERNAME is missing or not set. Please verify your .env file.' + bcolors.ENDC)
        sys.exit(1)

    if not PLEX_PASSWORD and not PLEX_TOKEN:
        print(bcolors.FAIL + 'PLEX_PASSWORD is missing or not set. Please verify your .env file.' + bcolors.ENDC)
        sys.exit(1)

    if (not PLEX_SERVER_NAME) and (not PLEX_TOKEN):
        print(bcolors.FAIL + 'PLEX_SERVER_NAME is missing or not set. Please verify your .env file.' + bcolors.ENDC)
        sys.exit(1)

    if PLEX_TOKEN and not PLEX_BASE_URL:
        print(bcolors.FAIL + 'Plex Token Auth requires PLEX_BASE_URL to be set. Please verify your .env file.' + bcolors.ENDC)
        sys.exit(1)

    if search('^\S+show$|^\S+movie$', mediaType) and not TMDB_API_KEY:
        print(bcolors.FAIL + 'TMDB_API_KEY must be set for non-anime. Please verify your .env file.' + bcolors.ENDC)
        sys.exit(1)
