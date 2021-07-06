"""Main module for listing data for a tracked series"""

import argparse
from config_settings import Configuration
from episode_database import EpisodeDatabase

parser = argparse.ArgumentParser()
parser.add_argument("keyword",
                    help="Keyword to select the series to display from the episode database")

args = parser.parse_args()

config = Configuration()
episode_db = EpisodeDatabase.load_from_cache(config.metadata)
series_metadata = episode_db.get_tracked_series_by_keyword(args.keyword)
for episode in series_metadata.episodes:
    airdate = "not aired" if episode.airdate is None else episode.airdate.strftime("%Y-%m-%d")
    print("s{:02d}e{:02d} (aired: {}) - {}".format(
        episode.season_number, episode.episode_number, airdate, episode.title))
