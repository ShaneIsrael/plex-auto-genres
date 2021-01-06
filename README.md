# Plex Auto Genres

Plex Auto Genres is a simple script that will add genre collection tags to your media making it much easier to search for genre specific content

1. [Requirements](#requirements)
2. [Optimal Setup](#optimal)
3. [Getting Started](#getting_started)
4. [Troubleshooting](#troubleshooting)
5. [Docker Usage](#docker_usage)

###### Movies example (with cover art set using --set-posters flag.)
![Movie Collections](/images/movies.png)

###### Anime example
![Anime Collections](/images/animes.png)

## Requirements
1. Python 3 - Instructions > [Windows / Mac / Linux](https://installpython3.com/)
2. [TMDB Api Key](https://developers.themoviedb.org/3/getting-started/introduction) (Only required for non-anime libraries)


## <a id="optimal"></a>Optimal Setup

1. Anime / Anime Movies are in their own library on your plex server. **_(Anime and Anime Movies can share the same library)_**
2. Standard TV Shows are in their own library on your plex server.
3. Standard Movies are in their own library on your plex server.
4. Proper titles for your media, this makes it easier to find the media. (see https://support.plex.tv/articles/naming-and-organizing-your-tv-show-files/)

For this to work well your plex library should be sorted. Meaning standard and non-standard media should not be in the same Plex library. Anime is an example of non-standard media.

If your anime shows and standard tv shows are in the same library, you can still use this script just choose (**standard**) as the type. However, doing this could cause incorrect genres added to some or all of your anime media entries.

###### Here is an example of my plex library setup
![Plex Library Example](/images/example-library-setup.png)

## <a id="getting_started"></a>Getting Started 
1. Read the **Optimal Setup** section above
2. Install the python dependencies listed in `requirements.txt`, if you have pip you can simply do `pip install -r requirements.txt`
3. Rename the `.env.example` file to `.env`
4. Edit the `.env` file and set your plex username, password, and server name. If you are generating collections for standard media (non anime) you will need to also obtain an [TMDB Api Key](https://developers.themoviedb.org/3/getting-started/introduction) (for movies and tv shows) 
    |Variable|Authentication method|Value|
    |---|---|---|
    |PLEX_USERNAME|Username and password|Your Plex Username|
    |PLEX_PASSWORD|Username and password|Your Plex Password|
    |PLEX_SERVER_NAME|Username and password|Your Plex Server Name|
    |PLEX_BASE_URL|Token|Your Plex Server base URL|
    |PLEX_TOKEN|Token|Your Plex Token|
    |PLEX_COLLECTION_PREFIX||(Optional) Prefix for the created Plex collections. For example, with a value of "\*", a collection named "Adventure", the name would instead be "*Adventure".<br><br>Default value : ""|
    |TMDB_API_KEY||Your TMDB api key (not required for anime library tagging)|
5. Optional, If you want to update the poster art of your collections. See `sample_posters/readme.txt`

You are now ready to run the script
```
usage: plex-auto-genres.py [-h] [--library LIBRARY] [--type {anime,standard}]

Adds genre tags (collections) to your Plex media.

optional arguments:
  -h, --help            show this help message and exit
  --library LIBRARY     The exact name of the Plex library to generate genre collections for.
  --type {anime,standard-movie,standard-tv}
                        The type of media contained in the library
  --set-posters         uploads posters located in posters/<type> of matching collections. Supports (.PNG)
  --dry                 Do not modify plex collections (debugging feature)
  -f, --force           Force proccess on all media (independently of proggress recorded in logs/).
  -y, --yes             Do not prompt.

example: 
python plex-auto-genres.py --library "Anime Movies" --type anime
python plex-auto-genres.py --library "Anime Shows" --type anime
python plex-auto-genres.py --library Movies --type standard-movie
python plex-auto-genres.py --library "TV Shows" --type standard-tv
python plex-auto-genres.py --library Movies --type standard-movie --set-posters
```

![Example Usage](/images/example-usage.gif)

## Troubleshooting
1. If you are not seeing any new collections close your plex client and re-open it.
2. Delete the generated `plex-*-finished.txt`  and `plex-*-failures.txt` files if you want the script to generate collections from the beginning. You may want to do this if you delete your collections and need them re-created.
3. Having the release year in the title of a tv show or movie can cause the lookup to fail in some instances. For example `Battlestar Galactica (2003)` will fail, but `Battlestar Galactica` will not.

## <a id="docker_usage"></a>Docker Usage
If you would like to run this via a docker container somebody has made that possible. [Follow their instructions here](https://github.com/fdarveau/plex-auto-genres-docker)
