# Changelog
All notable changes to Infield Fly are recorded here, newest first. Versions follow
[semantic versioning](https://semver.org), and each is tagged in git as `v` followed by the
version, such as `v1.0.0`, from which the installed version is derived. See Releases in
README.md for how to make one.

## [Unreleased]

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

[Unreleased]: https://github.com/jimevans/infield-fly/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/jimevans/infield-fly/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/jimevans/infield-fly/releases/tag/v1.0.0
