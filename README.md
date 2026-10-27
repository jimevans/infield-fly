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
* Searching for downloads uses a [Newznab](https://newznab.readthedocs.io)
  indexer API, such as [NZBPlanet](https://nzbplanet.net).
* Processing download jobs uses a [SABnzbd](https://sabnzbd.org) instance for
  NZB files, or a [Deluge](https://deluge-torrent.org) daemon for torrents.
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

### Shell completion
Infield Fly supports tab completion of its commands and options, job IDs, and
job statuses using [argcomplete](https://github.com/kislyuk/argcomplete), which
is installed with it. Completion applies when the command is run as
`infield-fly`, so the virtual environment must be activated (or `infield-fly`
otherwise on the `PATH`). To enable it, add the line for your shell to its
startup file:

* **bash** (`~/.bashrc`):
  `eval "$(register-python-argcomplete infield-fly)"`
* **zsh** (`~/.zshrc`, after `compinit` has run):
  `eval "$(register-python-argcomplete infield-fly)"`
* **PowerShell** (the file named by `$PROFILE`):
  `register-python-argcomplete --shell powershell infield-fly | Out-String | Invoke-Expression`

`register-python-argcomplete` is installed alongside `infield-fly` in the
virtual environment, so use its full path (for example,
`/path/to/infield-fly/.venv/bin/register-python-argcomplete`) if the virtual
environment is not activated when the startup file runs. Windows `cmd` does not
support completion of command arguments.

## Configuration
Settings are read from a JSON file, located by the first of the following that
is set:

1. The `--config` command line option
2. The `INFIELD_FLY_CONFIG` environment variable
3. `~/.config/infield-fly/settings.json`

A settings file named by `--config` or `INFIELD_FLY_CONFIG` must exist, or
Infield Fly exits with an error. If there is no settings file in the default
location, Infield Fly runs with default settings. Unless
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
          { "search_terms": ["1080p", "amzn"], "release_groups": ["flux", "ntb"] },
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
  },
  "search": {
    "newznab_url": "https://api.nzbplanet.net/api",
    "api_key": "<Newznab indexer API key>",
    "max_candidates": 5
  },
  "download": {
    "sabnzbd_url": "http://localhost:8080",
    "sabnzbd_api_key": "<SABnzbd API key>",
    "sabnzbd_category": "tv"
  }
}
```

The `conversion` section also accepts `staging_directory`, `database_directory`,
`database_cache_file`, `log_directory`, `job_directory`, and `nzb_directory` to
relocate the working files. `substitutions` maps characters in episode titles to
replacements when naming converted files.

Each entry in `tracked_series` is keyed by its main keyword, and `id` is the
series ID on TheTVDB. The main keyword (when a `description` is given) and any
additional `keywords` are matched against file names to identify the series.
When `enable_search` is true, each entry in `search_configs` produces a search
string for each new episode, made of the `primary_search_term` (defaulting to the
main keyword), the entry's `search_terms`, and the episode number (for example,
`the show 1080p s01e02`).

Only one download of each episode is converted. The search strings of the
entries that aren't `download_only` are combined into one job, which tries them
in the order the entries are listed, using the first that finds a usable
download. List the preferred entry first, and fallbacks after it. Each
`download_only` entry is a job of its own, whose download is copied to the
final directory without conversion. A download rejected for one job of an
episode is not tried by the episode's other jobs.

An entry's optional `release_groups` lists the release groups whose posts may
be downloaded for it, in order of preference. Its search string is searched for
once, and among the results, posts from the first listed group are tried first
(newest first), then posts from the next group, and so on. Posts from groups not
in the list are never downloaded, and when none of the results is from a listed
group, the next entry's search string is tried. A group matches when its name
appears as one or more whole words anywhere in a post's title, ignoring case and
punctuation, so `ntb` matches `...H.264-NTb` but not `...H.264-NTBX`. Avoid
group names that are also common release words, such as `web` or `amzn`, which
would match posts from any group. Without `release_groups`, posts from any group
may be downloaded.

The `search` section enables searching for downloads when `api_key` is set.
`newznab_url` is the indexer's API end point (defaulting to NZBPlanet's), and
`max_candidates` limits how many search results are downloaded and validated
for a job on each run. The `download` section names the SABnzbd instance to which
NZB files are added, and optionally the category under which to add them.

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
written next to the output file as `<name>.eng.forced.srt`. In that case, the
main English text subtitle stream is also written as `<name>.eng.srt`,
for remuxing later if ffmpeg has trouble muxing the forced subtitles.

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
(`YYYY-MM-DD`, defaulting to yesterday and today), and prints the search
strings for them built from each series' `search_configs`.

* `-u`, `--update-metadata`: update the episode database first
* `-c`, `--create-jobs`: add a job to the job queue for each search string
  instead of printing them
* `-x`, `--dry-run`: with `-c`, print the search strings without creating jobs

Note that `-u` and `-c` mean something different here than before the command.

### Processing jobs
Jobs track an episode from search through download to conversion. A job moves
through the statuses `waiting`, `searching`, `adding`, `downloading`, `pending`,
`converting`, and `completed`. A job whose download fails returns to `waiting`.

```
infield-fly jobs add <keyword> <search string>   # add a job
infield-fly jobs list [status]                   # list jobs, optionally only those with a status
infield-fly jobs show <id>                       # show the details of a job
infield-fly jobs update <id> <status> [data]     # set the status of a job
infield-fly jobs remove <id>                     # remove a job
infield-fly jobs clear [status]                  # remove all jobs, or those with a status
infield-fly jobs process                         # run the processing phases
```

To find a download for a job yourself, give the job the download with
`jobs update`, which accepts additional data for two statuses:

* `adding` with a magnet link, or the path or URL of an NZB file, queues that
  download for the job in Deluge or SABnzbd
* `pending` with the path of a downloaded file or directory queues it for
  conversion

`jobs process` runs each of the following phases, which can be skipped with
`--no-search`, `--no-search-downloads`, `--no-add-downloads`,
`--no-query-downloads`, or `--no-convert`:

1. **Search:** creates jobs for episodes of tracked series airing today, and
   removes completed jobs from earlier days.
2. **Search downloads:** searches the Newznab indexer for each `waiting` job,
   using a text search (`t=search`) for each of the job's search strings in turn
   until one finds a usable download. Results are used as the indexer returns
   them, without further filtering, so each search config's terms must be
   specific enough to find only the right posts. Results are tried newest first,
   up to `max_candidates` for the job in each run. The NZB file of each result is
   checked to ensure that it lists every part of every file it contains; the
   first that passes is used, and those that fail are rejected and not tried
   again. Jobs with no usable result stay `waiting` until the next run.
3. **Add downloads:** adds the NZB files of `adding` jobs to SABnzbd, and their
   magnet links to Deluge.
4. **Query downloads:** marks finished downloads as `pending`. A download that
   fails in SABnzbd is removed from its history and rejected, and its job
   returns to `waiting` to try the next search result.
5. **Convert:** converts `pending` downloads into the final directory (or copies
   them, for download-only jobs). When a download contains more than one episode
   file (`.mkv` or `.mp4`), such as a sample alongside the episode, only the
   largest is converted.

Infield Fly checks that an NZB file is complete, but not that its articles are
still available from your Usenet provider. Enable SABnzbd's
**Config → Switches → Check before download** and **Abort jobs that cannot be
completed** settings, so that downloads whose articles have been removed fail
quickly, and the next search result is tried.

To process jobs on a schedule, run the command from the virtual environment
with the unattended and config options, for example with this crontab entry:

```
*/30 * * * * /path/to/infield-fly/.venv/bin/infield-fly -u -c /path/to/settings.json jobs process
```

## Development
Each way of downloading episodes is a download track, in its own package under
`src/infield_fly`, with its tests in the matching directory under `tests`:

* `torrent`: magnet links (`magnet.py`), downloaded with Deluge (`downloader.py`)
* `usenet`: NZB files found on a Newznab indexer (`newznab.py`), checked for
  completeness (`validation.py`), and downloaded with SABnzbd (`sabnzbd.py`,
  `downloader.py`)

Each track's `downloader.py` implements the `Downloader` interface in
`downloader.py`, and `JobQueue` in `jobs.py` hands each track the jobs whose
`downloader` value matches the track's `name`. To add a track, implement the
interface in a new package and add its class to `DOWNLOADER_CLASSES` in
`jobs.py`.

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
