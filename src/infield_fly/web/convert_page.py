"""
Module containing the page from which files are converted, as with the convert command, such
as movies and other files that aren't episodes of tracked series
"""

import os
import shlex
import signal
import subprocess
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from infield_fly.conversion import (ConversionSettings, Converter, FileMapper,
                                    validate_video_options)
from infield_fly.database import EpisodeDatabase
from infield_fly.web import processing
from infield_fly.web.job_actions import (find_browse_root, get_series_choices, render_browser,
                                         with_query)
from infield_fly.web.templating import add_flash, app_path, templates

CHANNEL_NAMES = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}
CODEC_NAMES = {"subrip": "srt", "hdmv_pgs_subtitle": "pgs", "libx264": "h264, re-encoded"}


@dataclass
class ConvertForm:  # pylint: disable=too-many-instance-attributes

    """
    The choices of a conversion: the source file or folder, the name of a converted file,
    the series whose episode titles name the files converted from a folder, and the
    conversion options
    """

    source: str = ""
    name: str = ""
    keyword: str = ""
    convert_video: bool = False
    crf: str = ""
    aspect_ratio: str = ""
    convert_audio: bool = True
    convert_subtitles: bool = True
    overwrite: bool = False

    @classmethod
    def for_source(cls, source):
        """Creates a form for a source, naming its converted file after it"""

        name = os.path.splitext(os.path.basename(source.rstrip("/")))[0] if source else ""
        return cls(source=source, name=name)

    @classmethod
    def from_form_data(cls, form_data):
        """Creates a form from the data submitted from the convert page"""

        return cls(
            source=form_data.get("source", "").strip(),
            name=form_data.get("name", "").strip(),
            keyword=form_data.get("keyword", ""),
            convert_video=form_data.get("convert_video") is not None,
            crf=form_data.get("crf", "").strip(),
            aspect_ratio=form_data.get("aspect_ratio", "").strip(),
            convert_audio=form_data.get("convert_audio") is not None,
            convert_subtitles=form_data.get("convert_subtitles") is not None,
            overwrite=form_data.get("overwrite") is not None)

    @property
    def is_folder(self):
        """Gets a value indicating whether the source is a folder of episodes"""

        return os.path.isdir(self.source)

    @property
    def output_name(self):
        """Gets the file name of the converted file, ending in .mp4"""

        return self.name if self.name.lower().endswith(".mp4") else f"{self.name}.mp4"

    def validate(self, request, keywords):
        """Validates the form, returning a list of errors, which is empty if it is valid"""

        errors = []
        if self.source == "":
            return ["Choose the file or folder to convert."]
        if find_browse_root(request, self.source) is None:
            errors.append("The source must be in one of the folders downloads can be chosen "
                          "from.")
        elif not os.path.exists(self.source):
            errors.append(f"'{self.source}' does not exist.")
        elif self.is_folder:
            if self.keyword not in [""] + keywords:
                errors.append("Choose a tracked series, or to match series by file name.")
        elif self.name in ("", ".mp4") or "/" in self.name or self.name.startswith("."):
            errors.append("Enter a file name for the converted file, without folders.")

        if self.convert_video:
            errors.extend(self.validate_video_options())
        return errors

    def validate_video_options(self):
        """
        Validates the options for re-encoding video, checking here only that they are
        numbers, and their values as the convert command does
        """

        values = {}
        errors = []
        for name, value, error in (
                ("crf", self.crf, "The constant rate factor (CRF) must be a number."),
                ("aspect_ratio", self.aspect_ratio,
                 "The aspect ratio must be a number, such as 2.40.")):
            try:
                values[name] = float(value) if value else None
            except ValueError:
                errors.append(error)
        if errors:
            return errors
        return validate_video_options(True, values["aspect_ratio"], values["crf"])

    def get_settings(self):
        """Gets the conversion settings chosen"""

        return ConversionSettings(
            convert_video=self.convert_video, convert_audio=self.convert_audio,
            convert_subtitles=self.convert_subtitles,
            aspect_ratio=(float(self.aspect_ratio)
                          if self.convert_video and self.aspect_ratio else None),
            crf=(float(self.crf) if self.convert_video and self.crf
                 else ConversionSettings.crf))

    def get_destination(self, config):
        """Gets the path of the converted file, or the folder of the converted episodes"""

        final_directory = config.conversion.final_directory
        return final_directory if self.is_folder else os.path.join(final_directory,
                                                                   self.output_name)

    def get_arguments(self, config, progress_file):
        """Gets the arguments of the infield-fly command that converts the files"""

        arguments = ["convert", "--stage", "--progress-file", progress_file]
        if self.overwrite:
            arguments.append("--overwrite")
        if self.convert_video:
            arguments.append("--convert-video")
            if self.crf:
                arguments.extend(["--crf", self.crf])
            if self.aspect_ratio:
                arguments.extend(["-a", self.aspect_ratio])
        if not self.convert_audio:
            arguments.append("--no-convert-audio")
        if not self.convert_subtitles:
            arguments.append("--no-convert-subtitles")
        if self.is_folder and self.keyword:
            arguments.extend(["-k", self.keyword])
        return arguments + [self.source, self.get_destination(config)]


def describe_stream(stream):
    """Describes a stream of a file, such as "audio (dts, 5.1, eng)"."""

    details = [CODEC_NAMES.get(stream.codec, stream.codec)]
    if stream.is_audio and stream.channel_count > 0:
        details.append(CHANNEL_NAMES.get(stream.channel_count,
                                         f"{stream.channel_count} channels"))
    if stream.language:
        details.append(stream.language)
    if stream.title:
        details.append(f"“{stream.title}”")
    if stream.is_forced:
        details.append("forced")
    return f"{stream.codec_type} ({', '.join(details)})"


def describe_mapping(mapping):
    """Describes how a stream is written, as the source and the destination"""

    source = f"Source track {mapping.source.index}: {describe_stream(mapping.source)}"
    if mapping.output_file is not None:
        return source, os.path.basename(mapping.output_file)
    if mapping.codec == "copy":
        conversion = "copy"
    else:
        conversion = (f"{CODEC_NAMES.get(mapping.source.codec, mapping.source.codec)} -> "
                      f"{CODEC_NAMES.get(mapping.codec, mapping.codec)}")
        if mapping.channels is not None and mapping.channels > 0:
            conversion += f", {CHANNEL_NAMES.get(mapping.channels, mapping.channels)}"
    return source, f"Destination track {mapping.output_track} ({conversion})"


def review_file(config, form):
    """
    Reads the tracks of a source file, describing how it would be converted: the tracks
    written, the tracks left out, the files written, and the ffmpeg commands that write them
    """

    destination = form.get_destination(config)
    converter = Converter(form.source, destination, config.conversion.ffmpeg_location)
    settings = form.get_settings()
    mappings = converter.get_track_mappings(settings)
    commands = [converter.get_conversion_args(settings)]
    if converter.is_converting_subtitles(settings):
        commands.extend(converter.get_subtitle_extraction_args(subtitle_stream, subs_file)
                        for subtitle_stream, subs_file, _ in converter.get_subtitle_files())
    return {
        "tracks": [describe_mapping(x) for x in mappings],
        "omitted": [f"Source track {x.index}: {describe_stream(x)}"
                    for x in converter.get_omitted_streams(settings)],
        "outputs": [destination] + [x.output_file for x in mappings if x.output_file],
        "commands": [shlex.join(x) for x in commands],
    }


def review_folder(config, form):
    """
    Maps the episode files of a source folder to the names of their converted files,
    describing the files converted and the files skipped
    """

    episode_db = EpisodeDatabase.load_from_cache(config)
    mapper = FileMapper(episode_db,
                        file_name_substitutions=config.conversion.string_substitutions)
    files = mapper.map_files(form.source, form.get_destination(config), form.keyword or None)
    converted = {os.path.basename(source) for source, _ in files}
    return {
        "files": [(os.path.basename(source), os.path.basename(destination))
                  for source, destination in files],
        "skipped": sorted(x for x in os.listdir(form.source)
                          if x not in converted and not x.startswith(".")),
        "outputs": [destination for _, destination in files],
    }


def render_form(request, form, errors=None, status_code=200):
    """Shows the convert page, with the status of any conversion in progress"""

    config = request.app.state.load_config()
    status = processing.get_run_status(request.app, config, "convert")
    errors_output = ""
    error_file = processing.get_error_file(config, "convert")
    if status["state"] == "idle" and os.path.exists(error_file):
        with open(error_file, encoding="utf-8", errors="replace") as error_output:
            errors_output = error_output.read().strip()
    return templates.TemplateResponse(request, "convert.html", {
        "form": form, "errors": errors or [], "status": status,
        "series": get_series_choices(config),
        "lines": processing.LogFollower(config.conversion.log_file).start(
            processing.TAIL_LINES),
        "final_directory": config.conversion.final_directory,
        "last_errors": errors_output,
    }, status_code=status_code)


async def render_review(request, config, form, errors=None, status_code=200):
    """
    Shows how the chosen source would be converted, or the form with the reason it can't
    be, such as a file whose tracks can't be read
    """

    try:
        if form.is_folder:
            os.makedirs(config.conversion.final_directory, exist_ok=True)
            review = await run_in_threadpool(review_folder, config, form)
        else:
            review = await run_in_threadpool(review_file, config, form)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as ex:
        return render_form(request, form, [f"Couldn't read '{form.source}': {ex}"], 400)
    return templates.TemplateResponse(request, "convert_review.html", {
        "form": form, "review": review, "errors": errors or [],
        "destination": form.get_destination(config),
        "existing": [x for x in review["outputs"] if os.path.exists(x)],
    }, status_code=status_code)


async def show_convert(request):
    """Shows the convert page, choosing the source named in the request"""

    return render_form(request, ConvertForm.for_source(request.query_params.get("source", "")))


async def convert(request):
    """Reviews, starts, and cancels conversions"""

    config = request.app.state.load_config()
    form_data = await request.form()
    action = form_data.get("action", "review")
    if action == "cancel":
        cancel_conversion(request, config)
        return RedirectResponse(app_path(request, "convert"), 303)

    form = ConvertForm.from_form_data(form_data)
    errors = form.validate(request, [keyword for keyword, _ in get_series_choices(config)])
    if errors or action == "change":
        return render_form(request, form, errors, 400 if errors else 200)
    if action != "start":
        return await render_review(request, config, form)

    if processing.get_run_status(request.app, config, "convert")["state"] != "idle":
        add_flash(request, "warning", "A conversion is already running, so another was not "
                  "started.")
        return RedirectResponse(app_path(request, "convert"), 303)
    return await start_conversion(request, config, form)


async def start_conversion(request, config, form):
    """Starts converting the chosen source, unless its converted files would replace others"""

    if form.is_folder:
        os.makedirs(config.conversion.final_directory, exist_ok=True)
        review = await run_in_threadpool(review_folder, config, form)
        outputs = review["outputs"]
        if not outputs:
            return await render_review(request, config, form, [
                "No episode files in the folder can be named from a tracked series."], 400)
    else:
        outputs = [form.get_destination(config)]
    if not form.overwrite and any(os.path.exists(x) for x in outputs):
        return await render_review(request, config, form, [
            "Converted files already exist. Choose to replace them, or change the name."],
            409)

    progress_file = processing.get_conversion_progress_file(config)
    for path in (progress_file, f"{progress_file}.ffmpeg"):
        if os.path.exists(path):
            os.remove(path)
    processing.start_run(request.app, config, request.app.state.settings_file.path,
                         "convert", form.get_arguments(config, progress_file))
    add_flash(request, "success", f"Started converting {os.path.basename(form.source)}.")
    return RedirectResponse(app_path(request, "convert"), 303)


def cancel_conversion(request, config):
    """
    Stops the conversion in progress. A conversion started here leads its own process group,
    so ffmpeg is stopped with it; the conversion then deletes its partly converted files.
    """

    status = processing.get_run_status(request.app, config, "convert")
    if status["state"] == "running":
        pid = status["pid"]
    elif status["state"] == "starting":
        pid = processing.get_started_run(request.app, "convert")["pid"]
    else:
        add_flash(request, "warning", "No conversion is running.")
        return
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    add_flash(request, "success", "Cancelling the conversion. Its partly converted files are "
              "deleted from the staging directory.")


async def browse_sources(request):
    """Lists a browsable directory, from which the file or folder to convert is chosen"""

    browse_path = app_path(request, "convert_browse")
    convert_path = app_path(request, "convert")
    return render_browser(
        request, request.query_params.get("path"),
        browse_url=lambda path: with_query(browse_path, path=path),
        choose_url=lambda path: with_query(convert_path, source=path),
        back_url=convert_path, back_label="Convert", heading="Choose what to convert",
        intro=("Choose a file, such as a movie, or a folder of episodes to name from a "
               "tracked series."))
