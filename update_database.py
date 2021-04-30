"""Updates the metadata database"""

from config_settings import Configuration
from episode_database import EpisodeDatabase


config = Configuration()
episode_db = EpisodeDatabase.load_from_cache(config.metadata)
episode_db.update_all_tracked_series()
episode_db.save_to_cache()
