# Plex Auto Genres

Plex Auto Genres is a simple script that will add genre collection tags to your media making it much easier to search for genre specific content

1. [Requirements](#requirements)
2. [Optimal Setup](#Setup)
3. [Getting Started](#getting_started)
4. [Troubleshooting](#troubleshooting)

###### Movies example
![Movie Collections](/images/movies.png)

###### Anime example
![Anime Collections](/images/animes.png)

## Requirements
1. Python 3
2. [TMDB Api Key](https://developers.themoviedb.org/3/getting-started/introduction) (Only required for non-anime libraries)

## Optimal Setup

1. Anime / Anime Movies are in their own library on your plex server. **_(Anime and Anime Movies can share the same library)_**
2. Standard TV Shows are in their own library on your plex server.
3. Standard Movies are in their own library on your plex server.
4. Proper titles for your media, this makes it easier to find the media. (see https://support.plex.tv/articles/naming-and-organizing-your-tv-show-files/)

For this to work well your plex library should be sorted. Meaning standard and non-standard media should not be in the same Plex library. Anime is an example of non-standard media.

If your anime shows and standard tv shows are in the same library, you can still use this script just choose (**standard**) as the type. However, doing this could cause incorrect genres added to some or all of your anime media entries.

###### Here is an example of my plex library setup
![Plex Library Example](/images/example-library-setup.png)

## Getting Started <a name="getting_started"></a>
1. Read the **Optimal Setup** section above
2. Install the python dependencies listed in `requirements.txt`, if you have pip you can simply do `pip install -r requirements.txt`
3. Rename the `.env.example` file to `.env`
4. Edit the `.env` file and set your plex username, password, and server name. If you are generating collections for standard media (non anime) you will need to also obtain an [TMDB Api Key](https://developers.themoviedb.org/3/getting-started/introduction) (for movies and tv shows) 

You are now ready to run the script
```
usage: plex-auto-genres.py [-h] [--library LIBRARY] [--type {anime,standard}]

Adds genre tags (collections) to your Plex media.

optional arguments:
  -h, --help            show this help message and exit
  --library LIBRARY     The exact name of the Plex library to generate genre collections for.
  --type {anime,standard-movie,standard-tv}
                        The type of media contained in the library

example: 
python plex-auto-genres.py --library "Anime Movies" --type anime
python plex-auto-genres.py --library "Anime Shows" --type anime
python plex-auto-genres.py --library Movies --type standard-movie
python plex-auto-genres.py --library "TV Shows" --type standard-tv
```

![Example Usage](/images/example-usage.gif)

## Troubleshooting
1. If you are not seeing any new collections close your plex client and re-open it.
2. Delete the generated `plex-*-finished.txt`  and `plex-*-failures.txt` files if you want the script to generate collections from the beginning. You may want to do this if you delete your collections and need them re-created.
3. Having the release year in the title of a tv show or movie can cause the lookup to fail in some instances. For example `Battlestar Galactica (2003)` will fail, but `Battlestar Galactica` will not.