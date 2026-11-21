# Changelog
All notable changes to Infield Fly are recorded here, newest first. Versions follow
[semantic versioning](https://semver.org), and each is tagged in git as `v` followed by the
version, such as `v1.0.0`, from which the installed version is derived. See Releases in
README.md for how to make one.

## [Unreleased]

### Changed
* The command line and the web interface change jobs by the same rules:
  * Commands that change jobs refuse to while job processing is running, as the web
    interface does, since a run could overwrite the change.
  * `jobs update` to pending accepts a download's folder, as well as a file, and both
    check that the episode file converted is the job's episode.
  * A download given with a status other than adding or pending is refused, rather
    than ignored.
  * `jobs add` refuses a series that isn't tracked, and a job that already exists.

## [1.1.0] - 2026-11-21

### Added
* A Convert page in the web interface converts a file, such as a movie, or a folder of
  episodes, into the final directory, as the convert command does. For a file, it shows
  which source tracks become which tracks of the converted file, the tracks left out,
  and the ffmpeg commands used, before converting. A conversion shows its progress and
  time left, can be cancelled, and doesn't replace existing files unless asked to.
* The convert command's `--stage` option converts in the staging directory, moving the
  converted files to the destination when done, and deleting them if the conversion
  fails or is cancelled; `--overwrite` replaces existing converted files; and
  `--progress-file` writes a staged conversion's progress to a file.

### Changed
* In unattended mode, writing subtitles to separate files no longer prints ffmpeg's
  output.

### Fixed
* Deleting a converted SABnzbd download left its folder behind when SABnzbd recorded
  the download's file, rather than its folder, as its location; the folder is now
  deleted when it is named after the download.
* A finished download that SABnzbd no longer knew, as when it was removed from
  SABnzbd's history between checks, was rejected and downloaded again, leaving the
  first copy behind; it is now found in SABnzbd's completed directory and converted.

## [1.0.1] - 2026-11-21

### Fixed
* The Add job page lists tracked series by name, in alphabetical order, instead of by
  main keyword in the order of the settings file.

## [1.0.0] - 2026-11-21
The first versioned release, describing everything Infield Fly does as of this version.

### Converting
* Converts video files to the MP4 container for playback on Apple devices without
  transcoding: the video stream, including HEVC, is copied; audio is copied or converted
  to a stereo AAC track with an AC-3 track alongside it; and English text subtitles are
  converted to `mov_text`, with forced subtitles also written alongside as SRT files.
* Optionally re-encodes video with libx264, with a choice of quality (CRF), and crops it
  to an aspect ratio.
* Names converted episodes after their titles on TheTVDB, replacing characters as set in
  the settings file.
* Sends an SMS through Twilio when a conversion started with `convert --notify` finishes.

### Finding and downloading episodes
* Keeps an episode database of tracked series from TheTVDB's v4 API.
* Creates jobs for the episodes of tracked series as they air, searching with each
  series' search configs in order of preference, optionally only accepting posts from
  listed release groups, with download-only jobs whose downloads are copied unconverted.
* Searches a Newznab indexer, checks each NZB file for damage before downloading it, and
  downloads with SABnzbd, trying the next search result when a download fails. Magnet
  links are downloaded with Deluge.
* Converts finished downloads, choosing the largest episode file in a download.
* Optionally deletes a converted download from SABnzbd's completed folder, including its
  whole folder when it has one of its own, and removes it from SABnzbd's history.

### Command line
* Commands to convert files, manage the episode database, find episodes, and manage and
  process jobs, with tab completion of commands, job IDs, statuses, and series keywords.
* Only one job processing run, and one episode database update, runs at a time, so a
  scheduled run and one started by hand never overlap; a run started while another is in
  progress logs a warning and exits.
* Job files, the episode database, and NZB files are written so that other processes
  never read a partially written file.

### Web interface
* Run with `infield-fly web` behind a reverse proxy, logging in with the password of the
  account running it, checked with PAM, with failed logins limited and logged for
  fail2ban.
* Jobs: lists jobs by status, and shows each job's details. A job's status can be
  changed, with a magnet link or NZB file for the adding status, or a finished download,
  chosen from configured folders, for the pending status; its rejected downloads can be
  reset; and it can be removed. Jobs can be added.
* Series: shows the status of each tracked series from the episode database, and adds
  series found by searching TheTVDB, retrieving their episodes when saved, edits them,
  and stops tracking them.
* Settings: edits the other settings, hiding secrets unless shown, and tests the
  connections to SABnzbd, the Newznab indexer, and TheTVDB before saving.
* Processing: starts job processing, choosing the phases to run, and episode database
  updates, and follows the log of whichever is running, including scheduled runs, as it
  is written.
* The settings file is changed without losing settings the editors don't show, or its
  formatting, and changes made elsewhere since a page was opened are never overwritten.
* Shows the version of Infield Fly.

### Deployment and development
* `deploy/deploy.sh` installs Infield Fly and its systemd unit, Apache configuration, PAM
  service, and fail2ban filter and jail, showing the changes to each installed file and
  asking before replacing it.
* `infield-fly --version` shows the version, which is derived from git tags.
* Tests, coverage, and linting run on GitHub Actions with Python 3.9 and 3.10, failing
  the build if any code isn't covered by tests or pylint reports anything.

[Unreleased]: https://github.com/jimevans/infield-fly/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/jimevans/infield-fly/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/jimevans/infield-fly/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/jimevans/infield-fly/releases/tag/v1.0.0
