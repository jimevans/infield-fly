"""Updates the metadata database"""

import argparse
from config_settings import Configuration
from episode_database import EpisodeDatabase

parser = argparse.ArgumentParser()
parser.add_argument("-f", "--force-updates", action="store_true", default=False,
                    help="Forces updates of metadata of all tracked series, including ended ones")

args = parser.parse_args()

config = Configuration()
episode_db = EpisodeDatabase.load_from_cache(config.metadata)
episode_db.update_all_tracked_series(force_updates=args.force_updates)
episode_db.save_to_cache()
