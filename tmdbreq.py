#!/usr/bin/env python3

"""
Author: Aidan Hopper
Date: 2/27/2026
"""

import shlex
import sys
import subprocess
import json
import requests
import os
import asyncio
from dotenv import load_dotenv
import argparse

def run(cmd):
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if proc.stdout != "":
        print(proc.stdout)
    if proc.stderr != "":
        print(proc.stderr)
    return proc.returncode
    

def dprint(d):
    print(json.dumps(d, indent=4))

def config_argparse():
    parser = argparse.ArgumentParser()
    parser.add_argument("type", choices=["movie", "tv"])
    parser.add_argument("--jobs", "-j", default=10, type=int)
    parser.add_argument("--seasons", "-s", default="all")
    parser.add_argument("tmdbid")
    return parser

# Request all data from tmdbid
# Once the file is downloaded it is possible to read metadata using ffprobe

class Episode:
    def __init__(self, episode_number: int, name: str):
        self.name = name
        self.episode_number = episode_number
        self.season = None

    def set_season(self, season):
        self.season = season

    def __str__(self):
        return json.dumps({
            "name": self.name,
            "episode_number": self.episode_number,
        }, indent=4)

class Season:
    def __init__(self, season_number, episodes: list[Episode]):
        self.season_number = season_number
        self.episodes = episodes
        self.show = None

    def set_show(self, show):
        self.show = show

    def __str__(self):
        return json.dumps({
            "season_number": self.season_number,
            "episodes": [json.loads(str(episode)) for episode in self.episodes],
        }, indent=4)

class TVShow:
    def __init__(self, name, year, tmdbid, tvdbid, seasons: list[Season]):
        self.name = name
        self.year = year
        self.tmdbid = tmdbid
        self.tvdbid = tvdbid
        self.seasons = seasons

    def __str__(self):
        return json.dumps({
            "name": self.name,
            "year": self.year,
            "tmdbid": self.tmdbid,
            "tvdbid": self.tvdbid,
            "seasons": [json.loads(str(season)) for season in self.seasons],
        }, indent=4)

class Movie:
    pass

class TMDBDataRequester:
    def __init__(self, tmdbid, api_key, media_type):
        self.tmdbid = tmdbid    
        self.api_key = api_key
        self.media_type = media_type
        self.api_url = "https://api.themoviedb.org"

    def _get(self, endpoint):
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        url = f"{self.api_url}{endpoint}"

        print(f"[INFO] GET {url}")

        response = requests.get(url, headers=headers)

        if not response.ok:
            print(f"[ERROR] TMDBDataRequester response returned status {response.status_code}")
            return response.ok, None

        return response.ok, json.loads(response.text)

    def _request_movie(self):
        status, res = self._get(f"/3/movie/{self.tmdbid}")
        if status != 200:
            return None
        return res

    def _request_tv(self):
        ok, series_res = self._get(f"/3/tv/{self.tmdbid}")
        if not ok:
            return None

        seasons: list[Season] = []

        for season in series_res["seasons"]:
            season_number = season["season_number"]

            ok, res = self._get(f"/3/tv/{self.tmdbid}/season/{season_number}")
            if not ok:
                return None

            episodes: list[Episode] = []

            for episode in res["episodes"]:
                episodes.append(Episode(
                    episode_number=episode["episode_number"],
                    name=episode["name"],
                ))

            seasons.append(Season(
                season_number=season_number,
                episodes=episodes,
            ))
            
            for episode in seasons[-1].episodes:
                episode.set_season(seasons[-1])

        ok, res = self._get(f"/3/tv/{self.tmdbid}/external_ids")
        if not ok:
            print(f"[ERROR] Cound not query exernal IDs for {self.tmdbid}")
            return None

        show = TVShow(
            name=series_res["name"],
            year=int(series_res["first_air_date"].split("-")[0]),
            tmdbid=self.tmdbid,
            tvdbid=res["tvdb_id"],
            seasons=seasons,
        )

        for season in show.seasons:
            season.set_show(show)

        return show


    def request(self):
        if self.media_type == "tv":
            return self._request_tv()
        elif self.media_type == "movie":
            return self._request_movie()
        return None


class TVDownloader:
    def __init__(self, api_url, tv_shows_dir, job_size, seasons):
        self.api_url = api_url
        self.tv_shows_dir = tv_shows_dir
        self.job_size = job_size
        self.seasons = seasons

    def _series_dir(self, show: TVShow):
        s = f"{shlex.quote(self.tv_shows_dir)}/"
        s += shlex.quote(f"{show.name} ({show.year}) [tvdbid-{show.tvdbid}]")
        return s

    def _season_dir(self, season: Season):
        season_num = str(season.season_number)
        if len(season_num) == 1:
            season_num = "0" + season_num
        return f"'Season {season.season_number}'"

    def _make_episode_dir(self, episode: Episode):
        run(f"mkdir -p {self._series_dir(episode.season.show)}/{self._season_dir(episode.season)}")

    def _episode_path_predownload(self, episode: Episode):
        path = f"{self._series_dir(episode.season.show)}/{self._season_dir(episode.season)}/"
        path += f"S{episode.season.season_number}E{episode.episode_number}.mkv"
        return path

    def _episode_download_url(self, episode: Episode):
        return (
            f"{self.api_url}/"
            f"{episode.season.show.tmdbid}/"
            f"{episode.season.season_number}/"
            f"{episode.episode_number}"
        )

    def _download_episode(self, episode: Episode):
        print(
            "[INFO] Downloading "
            f"S{episode.season.season_number}E{episode.episode_number} "
            f"of {episode.season.show.name}"
        )

        cmd = (
            f"curl {self._episode_download_url(episode)} "
            f"> {self._episode_path_predownload(episode)}"
        )

        print(f"[INFO] CMD {cmd}")

        rc = run(cmd)

        return rc == 0

    def _seasons_to_download(self):
        ret = set()
        season_ranges = self.seasons.split(",")
        for r in season_ranges:
            r = r.split("-")
            if len(r) == 1:
                ret.add(int(r[0]))
            elif len(r) > 1:
                left = int(r[0])
                right = int(r[1])
                for i in range(left, right + 1):
                    ret.add(i)
        return ret

    async def download(self, show: TVShow):
        seasons_to_download = self._seasons_to_download()

        episodes = []
        for season in show.seasons:
            print(self.seasons)
            if self.seasons != "all" and season.season_number not in seasons_to_download:
                continue
            for episode in season.episodes:
                episodes.append(episode)

        jobs = [[]]
        for episode in episodes:
            if len(jobs[-1]) == self.job_size:
                jobs.append([])
            jobs[-1].append(episode)

        for job in jobs:
            for episode in job:
                print(f"[INFO] Getting ready to download {episode.name}")
                self._make_episode_dir(episode)

            job_successes = await asyncio.gather(
                *[
                    asyncio.to_thread(self._download_episode, episode)
                    for episode in job
                ]
            )

            if job_successes.count(False) != 0:
                print("Failed")
                return

async def main():
    load_dotenv()

    parser = config_argparse()
    args = parser.parse_args()
    
    media_type = args.type
    tmdbid = args.tmdbid
    job_size = args.jobs
    seasons = args.seasons

    movies_dir = os.getenv("MOVIES_DIR")
    tv_shows_dir = os.getenv("TV_SHOWS_DIR")
    media_request_api = os.getenv("MEDIA_REQUEST_API")
    tmdb_api_key = os.getenv("TMDB_API_KEY")

    # TODO Add arg to turn this into a web API

    if media_type == "movie":
        print("[ERROR] movie not supported yet")
        return 1

    tmdb = TMDBDataRequester(tmdbid, tmdb_api_key, media_type)
    tvdownloader = TVDownloader(media_request_api, tv_shows_dir, job_size, seasons)

    show = tmdb.request()

    await tvdownloader.download(show)

    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
