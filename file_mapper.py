import os
import re


class FileMapper:

    def __init__(self,
                 data_file_location = None,
                 file_name_match_regex = r"(.*)s([0-9]+)e([0-9]+)(.*)\.mkv"):
        super().__init__()
        self.file_name_match_regex = file_name_match_regex
        if data_file_location is None:
            script_path = os.path.dirname(os.path.realpath(__file__))
            self.data_file_location = os.path.join(script_path, "episodes.txt")
        else:
            self.data_file_location = data_file_location
   

    def read_episode_name_data(self):
        lines=[]
        with open(self.data_file_location) as file_episodes:
            lines = file_episodes.readlines()

        show_name = ""
        episode_names={}
        for line in lines:
            if len(line) > 0 and not line.startswith("#"):
                try:
                    show_name, episode_id, episode_name = line.split(" - ", 2)
                except:
                    print("Error found in line of episode description file {}: {}".format(data_file_location, line))
                    exit()
                episode_names[episode_id.upper()] = episode_name.strip()
        return (show_name, episode_names)


    def map_files(self, source, destination):
        file_map = []
        if os.path.isdir(source):
            src_dir = os.path.dirname(source)
            dest_dir = destination
            if not os.path.isdir(destination):
                dest_dir = os.path.dirname(destination)
            show_name, episode_names = self.read_episode_name_data()
            file_list = os.listdir(src_dir)
            file_list.sort()
            for input_file in file_list:
                match = re.match(self.file_name_match_regex, input_file,
                    re.IGNORECASE)
                if match != None:
                    episode_id = "S{}E{}".format(match.group(2),
                        match.group(3))
                    if episode_id in episode_names:
                        episode_name = episode_names[episode_id]
                        converted_file_name = "{} - {} - {}.mp4".format(
                            show_name, episode_id.lower(), episode_name)
                        file_map.append(
                            (os.path.join(src_dir, input_file),
                             os.path.join(dest_dir, converted_file_name)))
        else:
            dest_file = destination
            if os.path.isdir(destination):
                source_file_name = os.path.basename(source)
                source_file_base, source_file_ext = os.path.splitext(
                    source_file_name)
                dest_file = os.path.join(destination, 
                                         source_file_base + ".mp4")
            file_map.append((source, dest_file))

        return file_map
