# infield-fly
An ffmpeg wrapper for media file container conversion

## Description
This repository contains a set of Python scripts that wrap the `ffmpeg` set of
media file tools to convert media files from the Matroska container to the mp4
container, commonly used by Apple devices. It handles single-file and full
directory operations. It performs stream copies where possible, and converts to
common formats (audio to AAC and AC-3, or subtitle to txg3) when required so as
to play back natively on Apple devices without transcoding.

## Requirements
* Python 3.9 or later
* `ffmpeg` and `ffprobe`, either on the `PATH` or in the directory named by the
  `ffmpeg_location` setting

Some features also depend on external services:

* Mapping file names to episode titles, and finding episodes to download, uses
  episode metadata from [TheTVDB](https://thetvdb.com), which requires a v4 API
  key.
* Processing download jobs uses a [Deluge](https://deluge-torrent.org) daemon.
* SMS notification of completed conversions uses [Twilio](https://www.twilio.com).

## Installation
Install into a virtual environment from a clone of this repository:

```
git clone https://github.com/jimevans/infield-fly.git
cd infield-fly
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools
.venv/bin/pip install -e .
```

This installs an `infield-fly` command at `.venv/bin/infield-fly`. Activate the
virtual environment (`source .venv/bin/activate`) to run it as `infield-fly`, or
run it as `python -m infield_fly`. Because the install is editable, a `git pull`
takes effect immediately; rerun `pip install -e .` if the dependencies in
`pyproject.toml` change.

To also install the test and lint tools, use `pip install -e ".[dev]"`.

## Configuration
Settings are read from a JSON file, located by the first of the following that
is set:

1. The `--config` command line option
2. The `INFIELD_FLY_CONFIG` environment variable
3. `~/.config/infield-fly/settings.json`

If no settings file is found, Infield Fly runs with default settings. Unless
overridden in the settings file, the working directories (`staging`,
`completed`, `logs`, `.jobs`) and the episode database cache (`.dbcache`) are
created in the same directory as the settings file.

All sections and settings are optional. An example:

```json
{
  "conversion": {
    "ffmpeg_location": "/usr/local/bin",
    "final_directory": "/media/tv",
    "substitutions": { ":": " -", "?": "" },
    "deluge_host": "localhost",
    "deluge_port": 58846,
    "deluge_user_name": "user",
    "deluge_password": "password",
    "log_level": "info"
  },
  "metadata": {
    "api_key": "<TheTVDB API key>",
    "pin": "<TheTVDB subscriber PIN>",
    "tracked_series": {
      "the show": {
        "id": 123456,
        "description": "The Show",
        "keywords": ["show"],
        "enable_search": true,
        "search_configs": [
          { "search_terms": ["1080p"] },
          { "search_terms": ["2160p"], "download_only": true }
        ]
      }
    }
  },
  "notification": {
    "sid": "<Twilio account SID>",
    "auth_token": "<Twilio auth token>",
    "sending_number": "+15555550100",
    "receiving_number": "+15555550101"
  }
}
```

The `conversion` section also accepts `staging_directory`, `database_directory`,
`database_cache_file`, `log_directory`, and `job_directory` to relocate the
working files. `substitutions` maps characters in episode titles to
replacements when naming converted files.

Each entry in `tracked_series` is keyed by its main keyword, and `id` is the
series ID on TheTVDB. The main keyword (when a `description` is given) and any
additional `keywords` are matched against file names to identify the series.
When `enable_search` is true, each entry in `search_configs` produces a search
for each new episode, made of the `primary_search_term` (defaulting to the main
keyword), the entry's `search_terms`, and the episode number (for example,
`the show 1080p s01e02`). Downloads from `download_only` searches are copied
to the final directory without conversion.

## Usage
```
infield-fly [-u] [-c CONFIG] <command> ...
```

The global options must come before the command:

* `-c`, `--config`: path to the settings file
* `-u`, `--unattended`: log to a rotating file in the log directory instead of
  the console, and suppress progress output; use this when running from a
  scheduler

Run `infield-fly <command> --help` for the full options of any command.

### Converting files
```
infield-fly convert [options] source destination
```

Converts a file to the mp4 container. By default, the video stream is copied;
the audio stream is copied or converted to stereo AAC as the default track,
with an AC-3 track added alongside it; and an English text subtitle stream is
converted to `mov_text`. A forced English subtitle stream, if present, is
written next to the output file as `<name>.eng.forced.srt`.

* If `source` is a file, it is converted to `destination`. If `destination` is
  a directory, the output keeps the source file's base name.
* If `source` is a directory, each episode file in it (named like
  `Show.Name.S01E02.mkv`) is converted into `destination`, named after the
  episode's title in the episode database (for example,
  `The Show - s01e02 - Episode Title.mp4`). The series is identified from the
  file name using the tracked series keywords, or explicitly with `-k KEYWORD`.
  Run `infield-fly database update` first so the episode titles are available.

Options:

* `--no-convert-audio`, `--no-convert-subtitles`: copy the audio stream only,
  or omit subtitles
* `--convert-video`: re-encode the video with libx264, scaling down to 1080p if
  larger; `--crf` (0–51, default 17) sets the quality, and `-a`/`--aspect-ratio`
  (for example, `2.40`) crops the center of the frame to that ratio
* `-x`, `--dry-run`: print the `ffmpeg` arguments without converting
* `-n`, `--notify`: send an SMS to the `receiving_number` when finished

Examples:

```
infield-fly convert movie.mkv /media/movies
infield-fly convert --convert-video -a 2.40 --crf 18 movie.mkv movie.mp4
infield-fly convert -k "the show" ~/Downloads/The.Show.S01 /media/tv/The\ Show
```

### Managing the episode database
Episode metadata for tracked series is cached locally and refreshed from
TheTVDB:

```
infield-fly database update      # update continuing and upcoming series, and fetch new ones
infield-fly database update -f   # update all tracked series, including ended ones
infield-fly database list show   # list the cached episodes of the series with keyword "show"
```

### Finding episodes
```
infield-fly search [options] [fromdate] [todate]
```

Finds episodes of tracked series that aired between `fromdate` and `todate`
(`YYYY-MM-DD`, defaulting to yesterday and today), and builds the search
strings for them from each series' `search_configs`.

* `-u`, `--update-metadata`: update the episode database first
* `-x`, `--dry-run`: print the search strings without searching
* `-c`, `--create-jobs`: add a job to the job queue for each search string
  instead of searching

Note that `-u` and `-c` mean something different here than before the command.

### Processing jobs
Jobs track an episode from search through download to conversion. A job moves
through the statuses `waiting`, `searching`, `adding`, `downloading`, `pending`,
`converting`, and `completed`.

```
infield-fly jobs add <keyword> <search string>   # add a job
infield-fly jobs list [status]                   # list jobs, optionally only those with a status
infield-fly jobs show <id>                       # show the details of a job
infield-fly jobs update <id> <status> [data]     # set the status of a job
infield-fly jobs remove <id>                     # remove a job
infield-fly jobs clear [status]                  # remove all jobs, or those with a status
infield-fly jobs process                         # run the processing phases
```

`jobs update` accepts additional data for two statuses:

* `adding` with a magnet link queues that download for the job
* `pending` with the path of a downloaded file queues that file for conversion

`jobs process` runs each of the following phases, which can be skipped with
`--no-search`, `--no-add-downloads`, `--no-query-downloads`, or `--no-convert`:

1. **Search:** creates jobs for episodes of tracked series airing today, and
   removes completed jobs from earlier days.
2. **Add downloads:** adds the magnet links of `adding` jobs to Deluge.
3. **Query downloads:** marks finished Deluge downloads as `pending`.
4. **Convert:** converts `pending` downloads into the final directory (or copies
   them, for download-only jobs).

To process jobs on a schedule, run the command from the virtual environment
with the unattended and config options, for example with this crontab entry:

```
*/30 * * * * /path/to/infield-fly/.venv/bin/infield-fly -u -c /path/to/settings.json jobs process
```

## Development
Run the tests with coverage, and lint the source and tests, using:

```
.venv/bin/pytest --cov
.venv/bin/pylint src
.venv/bin/pylint --rcfile=tests/pylintrc tests
```

## Support
This library is provided as-is, and will be updated only at the whim of the
author. It is provided with no promises of suppport or maintenance. It is designed
to solve a specific problem for a specific individual, so issue reports and
Pull Requests are unlikely to be given much attention, and may be closed without
comment or justification. The code is licensed under the MIT license, should it
not entirely meet your needs and require modification to do so.

## About the Name
I am a fan of the sport of [baseball](https://en.wikipedia.org/wiki/Baseball).
My experience with the game is related to my family, and comes to me from my
late grandfather. He played the game at a semi-professional level in the 1940s,
and he and I bonded over it when I was a child. Because of my love for the game,
I've taken to naming small, one-off utilities after various terms in the game.
In baseball, there is a rule known as the "infield fly rule," which a fly ball
in fair territory that can be caught by an infielder with ordinary effort, when
there are runners on first and second, or on first, second, and third base, with
no outs or one out in an inning. The intent of the rule is to not allow the 
fielding team to produce more outs (greater than one out) than would be produced
by simply expending the "ordinary effort" to catch the fly ball (one out), as the
baserunners could be thrown out if the ball were to land in play and not be caught.
It's a quirky rule that often confuses casual or new fans of the game. There is
nothing more or less significant to the name than it's simply a term from a sport
I enjoy.
