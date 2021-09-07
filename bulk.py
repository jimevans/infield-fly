import os
from file_converter import Converter

file_map = {
    "/home/user/Downloads/1408.2007.Directors.Cut.1080p.BluRay.x264-SiNNERS/1408.2007.Directors.Cut.1080p.BluRay.x264-SiNNERS.mkv":"1408 (2007).mp4",
    "/home/user/Downloads/Apt.Pupil.1998.1080p.BluRay.x264.DTS-FGT/Apt.Pupil.1998.1080p.BluRay.x264.DTS-FGT.mkv":"Apt Pupil (1998).mp4",
    "/home/user/Downloads/Carrie.2013.1080p.BluRay.x264-SPARKS/Carrie.2013.1080p.BluRay.x264-SPARKS.mkv":"Carrie (2013).mp4",
    "/home/user/Downloads/Cats.Eye.1985.REMASTERED.1080p.BluRay.X264-AMIABLE[rarbg]/Cats.Eye.1985.REMASTERED.1080p.BluRay.X264-AMIABLE.mkv":"Cat's Eye (1985).mp4",
    "/home/user/Downloads/Children.Of.The.Corn.1984.1080p.BluRay.x264-HANGOVER/Children.Of.The.Corn.1984.1080p.BluRay.x264-HANGOVER.mkv":"Children of the Corn (1984).mp4",
    "/home/user/Downloads/Creepshow.1982.REMASTERED.1080p.BluRay.X264-AMIABLE[rarbg]/Creepshow.1982.REMASTERED.1080p.BluRay.X264-AMIABLE.mkv":"Creepshow (1982).mp4",
    "/home/user/Downloads/Creepshow.2.1987.LE.Bluray.1080p.DTS-HD.x264-Grym/Creepshow.2.1987.LE.Bluray.1080p.DTS-HD.x264-Grym.mkv":"Creepshow 2 (1987).mp4",
    "/home/user/Downloads/Dreamcatcher.2003.1080p.BluRay.x264-HD4U/Dreamcatcher.2003.1080p.BluRay.x264-HD4U.mkv":"Dreamcatcher (2003).mp4",
    "/home/user/Downloads/Firestarter.1984.REMASTERED.1080p.BluRay.X264-AMIABLE/Firestarter.1984.REMASTERED.1080p.BluRay.X264-AMIABLE.mkv":"Firestarter (1984).mp4",
    "/home/user/Downloads/Geralds.Game.2017.1080p.NF.WEBRip.DD5.1.x264-NTG/Geralds.Game.2017.1080p.NF.WEB-DL.DD5.1.x264-NTG.mkv":"Gerald's Game (2017).mp4",
    "/home/user/Downloads/Hearts.in.Atlantis.2001.1080p.AMZN.WEBRip.DD5.1.x264-ABM/Hearts.in.Atlantis.2001.1080p.AMZN.WEBRip.DD5.1.x264-ABM.mkv":"Hearts in Atlantis (2001).mp4",
    "/home/user/Downloads/In.the.Tall.Grass.2019.1080p.NF.WEBRip.DDP5.1.Atmos.x264-NTG/In.the.Tall.Grass.2019.1080p.NF.WEB-DL.DDP5.1.x264-NTG.mkv":"In the Tall Grass (2019).mp4",
    "/home/user/Downloads/Maximum.Overdrive.1986.1080p.BluRay.x264-PSYCHD/Maximum.Overdrive.1986.1080p.BluRay.x264-PSYCHD.mkv":"Maximum Overdrive (1986).mp4",
    "/home/user/Downloads/Misery.1990.REMASTERED.1080p.BluRay.X264-AMIABLE[rarbg]/Misery.1990.REMASTERED.1080p.BluRay.X264-AMIABLE.mkv":"Misery (1990).mp4",
    "/home/user/Downloads/Pet.Sematary.1989.REMASTERED.1080p.BluRay.X264-AMIABLE[rarbg]/Pet.Sematary.1989.REMASTERED.1080p.BluRay.X264-AMIABLE.mkv":"Pet Sematary (1989).mp4",
    "/home/user/Downloads/Pet.Sematary.2019.1080p.BluRay.x264-GECKOS[rarbg]/pet.sematary.2019.1080p.bluray.x264-geckos.mkv":"Pet Sematary (2019).mp4",
    "/home/user/Downloads/Secret.Window.2004.1080p.BluRay.x264-HDMI/Secret.Window.2004.1080p.BluRay.x264-HDMI.mkv":"Secret Window (2004).mp4",
    "/home/user/Downloads/Silver.Bullet.1985.1080p.BluRay.X264-AMIABLE[rarbg]/Silver.Bullet.1985.1080p.BluRay.X264-AMIABLE.mkv":"Silver Bullet (1985).mp4",
    "/home/user/Downloads/Stephen.Kings.Cujo.1983.1080p.BluRay.x264.DD5.1-FGT/Stephen.Kings.Cujo.1983.1080p.BluRay.x264.DD5.1-FGT.mkv":"Cujo (1983).mp4",
    "/home/user/Downloads/Stephen.Kings.The.Dark.Half.1993.1080p.BluRay.x264-SADPANDA/The.Dark.Half.1993.1080p.BluRay.x264-SADPANDA.mkv":"The Dark Half (1993).mp4",
    "/home/user/Downloads/Stephens.Kings.Thinner.1996.1080p.BluRay.x264-MOOVEE/Stephens.Kings.Thinner.1996.1080p.BluRay.x264-MOOVEE.mkv":"Thinner (1996).mp4",
    "/home/user/Downloads/The.Dark.Tower.2017.1080p.BluRay.x264-DRONES[rarbg]/the.dark.tower.2017.1080p.bluray.x264-drones.mkv":"The Dark Tower (2017).mp4",
    "/home/user/Downloads/The.Dead.Zone.1983.1080p.BluRay.X264-AMIABLE/The.Dead.Zone.1983.1080p.BluRay.X264-AMIABLE.mkv":"The Dead Zone (1983).mp4",
    "/home/user/Downloads/The.Green.Mile.1999.1080p.BluRay.x264-HDMI/the.green.mile.1999.1080p.bluray.x264-hdmi.mkv":"The Green Mile (1999).mp4",
    "/home/user/Downloads/The.Lawnmower.Man.1992.DC.1080p.BluRay.x264-PSYCHD[rarbg]/The.Lawnmower.Man.1992.DC.1080p.BluRay.x264-PSYCHD.mkv":"The Lawnmower Man (1992).mp4",
    "/home/user/Downloads/The.Mist.2007.1080p.BluRay.x264.DTS-FGT/The.Mist.2007.1080p.BluRay.x264.DTS-FGT.mkv":"The Mist (2007).mp4",
    "/home/user/Downloads/The.Running.Man.1987.1080p.BluRay.x264-CiNEFiLE/The.Running.Man.1987.1080p.BluRay.x264-CiNEFiLE.mkv":"The Running Man (1987).mp4",
    "/home/user/Downloads/The.Shining.1980.REMASTERED.1080p.BluRay.X264-AMIABLE[rarbg]/The.Shining.1980.REMASTERED.1080p.BluRay.X264-AMIABLE.mkv":"The Shining (1980).mp4"
}

dry_run = False

src_dir = ""
dest_dir = "/home/user/staging"

script_dir = os.path.dirname(os.path.realpath(__file__))

for src_file in file_map:
    converter = Converter(os.path.join(src_dir, src_file), os.path.join(dest_dir, file_map[src_file]))
    converter.convert_file(dry_run=dry_run)


