"""Tests for the series form of the web interface"""

import pytest

from infield_fly.web.series_form import SearchConfigForm, SeriesForm, suggest_keyword

SERIES_SETTINGS = {
    "id": 12,
    "description": "The Show",
    "keywords": ["show", "the show us"],
    "enable_search": True,
    "primary_search_term": "the show us",
    "search_configs": [
        {"search_terms": ["1080p", "amzn"], "release_groups": ["flux", "ntb"]},
        {"download_only": True},
    ],
    "custom": "kept",
}


def form_data(**values):
    data = {"keyword": "the show", "series_id": "12", "description": "The Show",
            "config_count": "0"}
    data.update(values)
    return data


class TestSettings:

    def test_from_settings(self):
        form = SeriesForm.from_settings("the show", SERIES_SETTINGS)

        assert form == SeriesForm(
            keyword="the show", series_id="12", description="The Show",
            keywords="show, the show us", enable_search=True,
            primary_search_term="the show us",
            search_configs=[SearchConfigForm("1080p amzn", "flux, ntb", False),
                            SearchConfigForm("", "", True)])

    def test_round_trip_keeps_settings_and_unknown_keys(self):
        form = SeriesForm.from_settings("the show", SERIES_SETTINGS)

        assert form.to_settings(SERIES_SETTINGS) == SERIES_SETTINGS

    def test_defaults_for_minimal_settings(self):
        form = SeriesForm.from_settings("show", {})

        assert form == SeriesForm(keyword="show", enable_search=False)

    def test_emptied_values_are_removed(self):
        form = SeriesForm.from_settings("the show", SERIES_SETTINGS)
        form.keywords = " , "
        form.primary_search_term = ""
        form.search_configs = []
        form.enable_search = False

        assert form.to_settings(SERIES_SETTINGS) == {
            "id": 12, "description": "The Show", "enable_search": False, "custom": "kept"}

    def test_lists_are_split(self):
        form = SeriesForm(keyword="show", series_id="3", description="Show",
                          keywords="a, b c ,, d",
                          search_configs=[SearchConfigForm("  1080p   web ", "x,y z", False)])

        settings = form.to_settings()

        assert settings["keywords"] == ["a", "b c", "d"]
        assert settings["search_configs"] == [
            {"search_terms": ["1080p", "web"], "release_groups": ["x", "y", "z"]}]

    def test_new_series(self):
        form = SeriesForm.for_new_series(12, "The Show: Reloaded (2024)")

        assert form == SeriesForm(keyword="the show reloaded", series_id="12",
                                  description="The Show: Reloaded (2024)",
                                  search_configs=[SearchConfigForm()])


class TestFormData:

    def test_from_form_data(self):
        form = SeriesForm.from_form_data(form_data(**{
            "keyword": " the show ", "keywords": "a, b", "enable_search": "on",
            "primary_search_term": " term ", "config_count": "2",
            "config-0-search_terms": "1080p", "config-0-release_groups": "flux",
            "config-1-download_only": "on"}))

        assert form == SeriesForm(
            keyword="the show", series_id="12", description="The Show", keywords="a, b",
            enable_search=True, primary_search_term="term",
            search_configs=[SearchConfigForm("1080p", "flux", False),
                            SearchConfigForm("", "", True)])

    @pytest.mark.parametrize("count", ["bogus", "-3"])
    def test_invalid_config_count(self, count):
        form = SeriesForm.from_form_data(form_data(config_count=count))

        assert form.search_configs == []
        assert not form.enable_search


class TestActions:

    @staticmethod
    def make_form():
        return SeriesForm(search_configs=[SearchConfigForm("a"), SearchConfigForm("b"),
                                          SearchConfigForm("c")])

    @pytest.mark.parametrize("action, expected", [
        ("add-config", ["a", "b", "c", ""]),
        ("remove-config-1", ["a", "c"]),
        ("move-config-up-2", ["a", "c", "b"]),
        ("move-config-up-0", ["a", "b", "c"]),
    ])
    def test_actions(self, action, expected):
        form = self.make_form()

        assert form.apply_action(action)

        assert [x.search_terms for x in form.search_configs] == expected

    @pytest.mark.parametrize("action", ["remove-config-3", "move-config-up-x", "bogus", None])
    def test_unknown_actions(self, action):
        form = self.make_form()

        assert not form.apply_action(action)

        assert [x.search_terms for x in form.search_configs] == ["a", "b", "c"]


class TestValidate:

    TRACKED = {"the show": {"id": 12}, "other": {"id": 34}}

    def test_valid_edit_of_existing_series(self):
        form = SeriesForm(keyword="the show", series_id="12", description="The Show")

        assert form.validate(self.TRACKED, "the show") == []

    def test_valid_rename(self):
        form = SeriesForm(keyword="renamed", series_id="12", description="The Show")

        assert form.validate(self.TRACKED, "the show") == []

    @pytest.mark.parametrize("keyword, series_id, description, expected", [
        ("", "56", "D", "Enter the main keyword."),
        ("OTHER", "56", "D", "Another series already has the main keyword 'OTHER'."),
        ("new", "", "D", "TheTVDB ID must be a positive whole number."),
        ("new", "0", "D", "TheTVDB ID must be a positive whole number."),
        ("new", "1.5", "D", "TheTVDB ID must be a positive whole number."),
        ("new", "34", "D", "The series 'other' already has TheTVDB ID 34."),
        ("new", "56", "", "Enter a description."),
    ])
    def test_invalid_new_series(self, keyword, series_id, description, expected):
        form = SeriesForm(keyword=keyword, series_id=series_id, description=description)

        errors = form.validate(self.TRACKED)

        assert len(errors) == 1
        assert errors[0].startswith(expected)


class TestExampleSearches:

    def test_example_searches(self):
        form = SeriesForm.from_settings("the show", SERIES_SETTINGS)

        searches = form.get_example_searches(1, 2)

        assert [(x.queries, x.is_download_only, x.release_groups) for x in searches] == [
            (["the show us 1080p amzn s01e02"], False,
             {"the show us 1080p amzn s01e02": ["flux", "ntb"]}),
            (["the show us s01e02"], True, {}),
        ]

    @pytest.mark.parametrize("values", [
        {"keyword": ""}, {"description": ""}, {"series_id": "x"}, {"series_id": "0"}])
    def test_no_example_searches_for_invalid_form(self, values):
        form = SeriesForm.from_settings("the show", SERIES_SETTINGS)
        for name, value in values.items():
            setattr(form, name, value)

        assert form.get_example_searches() == []


@pytest.mark.parametrize("name, expected", [
    ("The Show", "the show"),
    ("Marvel’s Agents of S.H.I.E.L.D.", "marvels agents of s h i e l d"),
    ("Grey's Anatomy", "greys anatomy"),
    ("The Show (2024)", "the show"),
    ("1923", "1923"),
])
def test_suggest_keyword(name, expected):
    assert suggest_keyword(name) == expected
