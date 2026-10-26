# infield-fly
An ffmpeg wrapper for media file container conversion

## Description
This repository contains a set of Python scripts that wrap the `ffmpeg` set of
media file tools to convert media files from the Matroska container to the mp4
container, commonly used by Apple devices. It handles single-file and full
directory operations. It performs stream copies where possible, and converts to
common formats (audio to AAC and AC-3, or subtitle to txg3) when required so as
to play back natively on Apple devices without transcoding.

## Installation
Infield Fly requires Python 3.9 or later. From a clone of this repository:

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

This installs an `infield-fly` command (also runnable as `python -m infield_fly`).

## Configuration
Settings are read from a JSON file, located by the first of the following that
is set:

1. The `--config` command line option
2. The `INFIELD_FLY_CONFIG` environment variable
3. `~/.config/infield-fly/settings.json`

Unless overridden in the settings file, the working directories (`staging`,
`completed`, `logs`, `.jobs`) and the episode database cache (`.dbcache`) are
created in the same directory as the settings file.

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
