from time import sleep
from src.setup import jikan

def getAnime(title):
    title = title.split(' [')[0]
    if len(title.split()) > 10:
        title = " ".join(title.split()[0:10])

    sleep(4)
    query = jikan.search('anime', title)
    if not query['results']:
        return None
    for r in query['results']:
        if r['title'].lower() == title.lower():
            match = r
            break
    else:
        match = query['results'][0]

    return match

def getAnimeDetails(malId):
    sleep(4)
    return jikan.anime(malId)