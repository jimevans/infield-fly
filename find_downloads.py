"""Main module for finding downloadable artifacts"""

import argparse
import os
from config_settings import Configuration
from datetime import datetime
from episode_database import EpisodeDatabase, SeriesInfo, EpisodeInfo
from torrent_finder import TorrentDataProvider

parser = argparse.ArgumentParser()
parser.add_argument("fromdate", help="Start of date range withn which to search for episodes")
parser.add_argument("todate", nargs="?", default=datetime.now().strftime("%Y-%m-%d"),
                    help="End of date range within which to search for episodes")

parser.add_argument("-d", "--directory", help="Directory to which to write magnet links to files")
parser.add_argument("-x", "--dry-run", action="store_true",
                    help="Perform a dry run, printing data, but do not convert")

args = parser.parse_args()
from_date = datetime.strptime(args.fromdate, "%Y-%m-%d")
to_date = datetime.strptime(args.todate, "%Y-%m-%d")

config = Configuration()
found_episodes = []
searches_to_perform = []
episode_db = EpisodeDatabase.load_from_cache(config.metadata)
for tracked_series in config.metadata.tracked_series:
    series = episode_db.get_series(tracked_series.series_id)
    series_episodes_since_last_search = series.get_episodes_by_airdate(from_date, to_date)
    for series_episode in series_episodes_since_last_search:
        found_episodes.append(series_episode)
        for stored_search in tracked_series.stored_searches:
            search_string = " ".join(stored_search)
            search_string += " s{:02d}e{:02d}".format(
                series_episode.season_number, series_episode.episode_number)
            searches_to_perform.append(search_string)

print("Episodes found:")
for episode in found_episodes:
    print(episode.plex_title)

print("")
if args.dry_run:
    print("Dry run requested. Not performing searches. Searches to perform:")
    for search in searches_to_perform:
        print(search)
else:
    finder = TorrentDataProvider()
    print("Performing searches")
    for search in searches_to_perform:
        search_results = finder.search(search)
        if len(search_results) == 0:
            print("No results found after retries")
        for search_result in search_results:
            if args.directory is not None and os.path.isdir(args.directory):
                magnet_file_path = os.path.join(args.directory,search_result.title + ".magnet")
                print("Writing magnet link to {}".format(magnet_file_path))
                with open(magnet_file_path, "w") as magnet_file:
                    magnet_file.write(search_result.magnet_link)
                    magnet_file.flush()
            else:
                print("Torrent title: {}".format(search_result.title))
                print("Magnet link: {}".format(search_result.magnet_link))
