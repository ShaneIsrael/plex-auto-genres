from time import sleep
import requests

JIKAN_URL = 'https://api.jikan.moe/v4'

def searchAnime(title):
    query = requests.get(f'{JIKAN_URL}/anime', params={'q': title})
    return query.json()

def getAnimeById(id):
    query = requests.get(f'{JIKAN_URL}/anime/{id}')
    return query.json()['data']

def getAnime(title):
    title = title.split(' [')[0]
    if len(title.split()) > 10:
        title = " ".join(title.split()[0:10])

    sleep(4)
    query = searchAnime(title)
    if query['pagination']['items']['total'] == 0:
        return None
    for r in query['data']:
        if r['title'].lower() == title.lower():
            match = r
            break
    else:
        match = query['data'][0]

    return match

def getAnimeDetails(malId):
    sleep(4)
    return getAnimeById(malId)