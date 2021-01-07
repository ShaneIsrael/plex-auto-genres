![](https://img.shields.io/docker/build/shaneisrael/plex-auto-genres)

# Plex Auto Genres

Plex Auto Genres is a simple script that will add genre collection tags to your media making it much easier to search for genre specific content

1. [Requirements](#requirements)
2. [Optimal Setup](#optimal)
3. [Getting Started](#getting_started)
4. [Automating](#automating)
5. [Troubleshooting](#troubleshooting)
6. [Docker Usage](#docker_usage)

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
  --no-progress         Do not display the live updating progress bar
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

## <a id="automating"></a>Automating
I have conveniently included a script to help with automating the process of running plex-auto-genres when combined with any number of cron scheduling tools such as `contab`, `task scheduler`, etc. 

**If you have experience with Docker I reccommend using my docker image which will run on a schedule.**

1. Copy `.env.example` to `.env` and update the values
2. Copy `config.json.example` to `config.json` and update the values
4. Each entry in the `run` list will be executed when you run this script
5. Have some cron/scheduling process execute `python3 automate.py`, I suggest running it manually first to test that its working.

**Note:** *The first run of this script may take a long time (minutes to hours) depending on your library sizes.*

**Note:** *Don't be alarmed if you do not see any text output. The terminal output you normally see when running `main.py` is redirected to the log file **after** each executed `run` in your `config`.*

## <a id="docker_usage"></a>Docker Usage

1. **[Install Docker](https://docs.docker.com/get-docker/)**
2. **[Install Docker Compose](https://docs.docker.com/compose/install/)**
3. Clone or Download this repository
4. Edit `docker/docker-compose.yml` 
    1. Update the `volumes:` paths to point to the `config`,`logs`,`posters` directories in this repo.
    2. Update the `environment:` variables. See [Getting Started](#getting_started).
5. Copy `config/config.json.example` to `config/config.json`
    1. Edit the `run` array examples to match your needs. When the script runs, each library entry in this array will be updated on your Plex server. 
6. Run `docker-compose up -d`, **the script will run immediately then proceed to run on a schedule every night at 1am UTC.** Logs will be located at `logs/plex-auto-genres-automate.log`

 Another Docker option of this tool can be **[found here.](https://github.com/fdarveau/plex-auto-genres-docker)**


## Troubleshooting
1. If you are not seeing any new collections close your plex client and re-open it.
2. Delete the generated `plex-*-successful.txt`  and `plex-*-failures.txt` files if you want the script to generate collections from the beginning. You may want to do this if you delete your collections and need them re-created.
3. Having the release year in the title of a tv show or movie can cause the lookup to fail in some instances. For example `Battlestar Galactica (2003)` will fail, but `Battlestar Galactica` will not.
