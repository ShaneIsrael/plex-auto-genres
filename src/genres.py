#pylint: disable=no-member
import re
from time import sleep

from src.setup import jikan, movie, tv


def sanitizeTitle(title):
    # remove the year from the title if found. e.x. some movie (1991) would return: some movie
    return re.sub('\\s(\\(\\d{4}\\))', '', title)

def getAnimeGenres(title):
    title = title.split(' [')[0]
    if len(title.split()) > 10:
        title = " ".join(title.split()[0:10])

    sleep(4) # sleeps 4s
    query = jikan.search('anime', title, page=1) # search result

    if not query['results']:
        return []
    animeId = query['results'][0]['mal_id'] # anime's MyAnimeList ID

    sleep(4)

    anime = jikan.anime(animeId) # all of the anime's info
    genres = [ e['name'] for e in anime['genres'] ] # list comprehension

    return genres

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
        print(f'\n\n{str(e)}, when searching for entry: {title}, of type {mediaType}.')
        print('This entry has been added to the failures.txt - once the issue is corrected in your library remove it from there and try again.')
        return []

def getGenres(media, mediaType):
    if mediaType == 'anime':
        genres = getAnimeGenres(sanitizeTitle(media.title))
    else:
        genres = getStandardGenres(sanitizeTitle(media.title), mediaType)

    return genres
