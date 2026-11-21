"""Module containing the form with which a tracked series is edited in the web interface"""

import re
from dataclasses import dataclass, field
from typing import List

from infield_fly.configuration import MetadataSettings


@dataclass
class SearchConfigForm:

    """The fields of a search configuration of a tracked series"""

    search_terms: str = ""
    release_groups: str = ""
    download_only: bool = False


@dataclass
class SeriesForm:

    """
    The fields of a tracked series, as entered in the series editor. Lists, such as the
    additional keywords, are entered as text, and split into lists when saved.
    """

    keyword: str = ""
    series_id: str = ""
    description: str = ""
    keywords: str = ""
    enable_search: bool = True
    primary_search_term: str = ""
    search_configs: List[SearchConfigForm] = field(default_factory=list)

    @classmethod
    def from_settings(cls, keyword, series_dict):
        """Creates a form from the settings of a tracked series"""

        return cls(
            keyword=keyword,
            series_id=str(series_dict.get("id", "")),
            description=series_dict.get("description", ""),
            keywords=", ".join(series_dict.get("keywords", [])),
            enable_search=bool(series_dict.get("enable_search", False)),
            primary_search_term=series_dict.get("primary_search_term", ""),
            search_configs=[
                SearchConfigForm(" ".join(x.get("search_terms", [])),
                                 ", ".join(x.get("release_groups", [])),
                                 bool(x.get("download_only", False)))
                for x in series_dict.get("search_configs", [])])

    @classmethod
    def for_new_series(cls, series_id, name):
        """Creates a form for a new tracked series found on thetvdb.com"""

        return cls(keyword=suggest_keyword(name), series_id=str(series_id), description=name,
                   search_configs=[SearchConfigForm()])

    @classmethod
    def from_form_data(cls, form_data):
        """Creates a form from the data submitted from the series editor"""

        try:
            config_count = max(0, int(form_data.get("config_count", "0")))
        except ValueError:
            config_count = 0
        return cls(
            keyword=form_data.get("keyword", "").strip(),
            series_id=form_data.get("series_id", "").strip(),
            description=form_data.get("description", "").strip(),
            keywords=form_data.get("keywords", "").strip(),
            enable_search=form_data.get("enable_search") is not None,
            primary_search_term=form_data.get("primary_search_term", "").strip(),
            search_configs=[
                SearchConfigForm(
                    form_data.get(f"config-{index}-search_terms", "").strip(),
                    form_data.get(f"config-{index}-release_groups", "").strip(),
                    form_data.get(f"config-{index}-download_only") is not None)
                for index in range(config_count)])

    def apply_action(self, action):
        """
        Applies a change to the search configurations requested by a button in the series
        editor: "add-config", "remove-config-N", or "move-config-up-N", returning False for
        any other action
        """

        if action == "add-config":
            self.search_configs.append(SearchConfigForm())
            return True

        match = re.fullmatch(r"(remove-config|move-config-up)-([0-9]+)", action or "")
        if match is None or int(match.group(2)) >= len(self.search_configs):
            return False
        index = int(match.group(2))
        if match.group(1) == "remove-config":
            del self.search_configs[index]
        elif index > 0:
            self.search_configs[index - 1:index + 1] = [self.search_configs[index],
                                                        self.search_configs[index - 1]]
        return True

    def validate(self, tracked_series, original_keyword=None):
        """
        Validates the form against the other tracked series in the settings, returning a
        list of errors, which is empty if the form is valid
        """

        errors = []
        others = {keyword: series for keyword, series in tracked_series.items()
                  if keyword != original_keyword}
        if self.keyword == "":
            errors.append("Enter the main keyword.")
        elif self.keyword.lower() in (x.lower() for x in others):
            errors.append(f"Another series already has the main keyword '{self.keyword}'.")

        if not self.series_id.isdigit() or int(self.series_id) <= 0:
            errors.append("TheTVDB ID must be a positive whole number.")
        else:
            for keyword, series in others.items():
                if isinstance(series, dict) and series.get("id") == int(self.series_id):
                    errors.append(f"The series '{keyword}' already has TheTVDB ID "
                                  f"{self.series_id}.")

        if self.description == "":
            errors.append("Enter a description. Without one, the main keyword isn't used to "
                          "match file names to the series.")
        return errors

    def to_settings(self, series_dict=None):
        """
        Gets the settings of the tracked series, updating its existing settings, so that
        settings not edited with the form are kept
        """

        settings = dict(series_dict or {})
        settings["id"] = int(self.series_id)
        settings["description"] = self.description
        _set_or_remove(settings, "keywords", split_list(self.keywords))
        _set_or_remove(settings, "primary_search_term", self.primary_search_term)
        settings["enable_search"] = self.enable_search

        search_configs = []
        for search_config in self.search_configs:
            search_config_settings = {}
            _set_or_remove(search_config_settings, "search_terms",
                           search_config.search_terms.split())
            _set_or_remove(search_config_settings, "release_groups",
                           search_config.release_groups.replace(",", " ").split())
            if search_config.download_only:
                search_config_settings["download_only"] = True
            search_configs.append(search_config_settings)
        _set_or_remove(settings, "search_configs", search_configs)
        return settings

    def get_example_searches(self, season_number=1, episode_number=1):
        """
        Gets the searches that would be made for an episode with the settings in this form,
        or an empty list if they are not valid enough to say
        """

        if self.keyword == "" or self.description == "":
            return []
        try:
            settings = self.to_settings()
        except ValueError:
            return []
        metadata = MetadataSettings.from_dict({"tracked_series": {self.keyword: settings}})
        if not metadata.tracked_series:
            return []
        return metadata.tracked_series[0].get_episode_searches(season_number, episode_number)


def _set_or_remove(settings, name, value):
    if value:
        settings[name] = value
    elif name in settings:
        del settings[name]


def split_list(text):
    """Splits a list entered as text, separated by commas, into its non-empty items"""

    return [x.strip() for x in text.split(",") if x.strip() != ""]


def suggest_keyword(name):
    """
    Suggests the main keyword for a series from its name: the name in lower case, without
    apostrophes, other punctuation, or a trailing year, such as "the show part 2" for
    "The Show: Part 2 (2024)"
    """

    name = re.sub(r"\s*\((19|20)[0-9]{2}\)\s*$", "", name)
    # Release names drop apostrophes rather than separating words at them.
    name = re.sub(r"['\u2019]", "", name)
    return " ".join(re.sub(r"[^\w\s]", " ", name.lower()).split())
