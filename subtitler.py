import os
import re
import platform
import shlex,subprocess
import shutil
import argparse
import json
from twilio.rest import Client

def notify_complete(src_dir):
  account_sid = 'AC865235b2fd568c6f0494634e392ba58b'
  auth_token = 'fee046bebc80bbc077a453b5b34b6b40'
  client = Client(account_sid, auth_token)

  message = client.messages.create(body='Subtitle addition for {} complete'.format(src_dir), from_='+18587578121', to='+18137286127')

def get_system_path_info():
  path_separator = '/'
  exe = ''
  if platform.system() == 'Windows':
    path_separator = '\\'
    exe = '.exe'
  return (path_separator, exe)

script_dir = os.path.dirname(os.path.realpath(__file__))

path_separator, exe = get_system_path_info()

src_dir = '/Users/james.evans/Desktop/vids/Justice League/Season 02/'
subs_dir = '/Users/james.evans/Desktop/vids/subs/justice.league.s02/'
dest_dir = '/Users/james.evans/Desktop/vids/converted/Justice League/Season 02/'
subtitle_file_pattern = '{}Justice League (2001) - {}.eng.srt'
file_list = os.listdir(src_dir)
file_list.sort()
for input_file in file_list:
  print('Found file: ' + input_file)
  show_name, episode_id, episode_name = input_file.split(' - ', 2)
  ffmpeg_args = []
  ffmpeg_args.append('ffmpeg')
  ffmpeg_args.append('-hide_banner')
  ffmpeg_args.append('-i')
  ffmpeg_args.append('{}{}'.format(src_dir, input_file))
  ffmpeg_args.append('-i')
  ffmpeg_args.append(subtitle_file_pattern.format(subs_dir, episode_id))
  ffmpeg_args.append('-map')
  ffmpeg_args.append('0:0')
  ffmpeg_args.append('-map')
  ffmpeg_args.append('0:1')
  ffmpeg_args.append('-map')
  ffmpeg_args.append('0:2')
  ffmpeg_args.append('-map')
  ffmpeg_args.append('1:0')
  ffmpeg_args.append('-c:v')
  ffmpeg_args.append('copy')
  ffmpeg_args.append('-c:a:0')
  ffmpeg_args.append('copy')
  ffmpeg_args.append('-c:a:1')
  ffmpeg_args.append('copy')
  ffmpeg_args.append('-c:s')
  ffmpeg_args.append('mov_text')
  ffmpeg_args.append('-map_chapters')
  ffmpeg_args.append('0')
  ffmpeg_args.append('-metadata:s:s:0')
  ffmpeg_args.append('language=eng')
  ffmpeg_args.append('{}{}'.format(dest_dir, input_file))
  print('Conversion arguments:')
  print(ffmpeg_args)
  p = subprocess.Popen(ffmpeg_args)
  p.wait()

#notify_complete(src_dir)
