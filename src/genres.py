#pylint: disable=no-member
from logging import error
import re
from time import sleep
from src.setup import movie, tv
from src.anime import getAnime, getAnimeDetails


def sanitizeTitle(title):
    # remove the year from the title if found. e.x. some movie (1991) would return: some movie
    return re.sub('\\s(\\(\\d{4}\\))', '', title)

def getAnimeGenres(title):
    anime = getAnime(title)
    if anime is not None:
        animeId = anime['mal_id'] # anime's MyAnimeList ID
        animeDetails = getAnimeDetails(animeId) # all of the anime's info
        genres = [ e['name'] for e in animeDetails['genres'] ] # list comprehension
        return genres
    return []

def getStandardGenres(title, mediaType):
    try:
        db = movie if re.search('^\S*movie$', mediaType) else tv

        sleep(0.5)

        query = db.search(title)
        if len(query) == 0:
            return []

        sleep(0.5)

        details = db.details(query[0].id)

        genres = [ y[0] for y in [x['name'].split(' & ') for x in details.genres] ]

        return genres

    except Exception as e:
        if ('Invalid API key' in str(e)):
            raise Exception('Invalid API key: Your TMDB API Key is invalid. ' +
                'See README for info on setting your TMDB key.') from e
        print(f'\n\n{str(e)}, when searching for entry: {title}, of type {mediaType}.')
        print('This entry has been added to the failures.txt - once the issue is corrected in your library remove it from there and try again.')
        return []

def getGenres(media, mediaType):
    if mediaType == 'anime':
        genres = getAnimeGenres(sanitizeTitle(media.title))
    else:
        genres = getStandardGenres(sanitizeTitle(media.title), mediaType)

    return genres
