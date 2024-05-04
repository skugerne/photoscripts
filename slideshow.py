# -*- encoding: UTF-8 -*-

'''
Make a semi-random slideshow from one or more directories or directory trees.
'''

import os
import argparse
import glob, re
import logging
from collections import defaultdict
import pygame
import piexif
from PIL import Image
from time import sleep, time
from datetime import datetime
from random import choice
from threading import Thread, Condition

# import from our other files
from inventory import setup_logger, load_json, write_json



# globals
logger = None
args = None
current_year = datetime.now().year



def parsedate(datestr):
    """
    Parse a date string (as raw bytes) into a tuple.
    """

    if not datestr:
        return None

    # ex: b'2005-06-27T09:56:05-04:00'
    # ex: b'2006:05:22 19:17:28\x00'
    m = re.match(rb"^(\d\d\d\d)[:-](\d\d)[:-](\d\d)[T ](\d\d):(\d\d):(\d\d)(\000|[+-]\d\d:\d\d)?$",datestr)
    if not m:
        logger.warning("Failed to parse: %s" % datestr)
        return None

    res = tuple(int(m.group(x+1)) for x in range(6))
    bounds = ((2000,current_year), (1,12), (1,31), (0,24), (0,59), (0,59))
    for idx in range(6):
        if res[idx] < bounds[idx][0] or res[idx] > bounds[idx][1]:
            return None   # ignore obviously incorrect dates

    return res



def load_database():
    """
    Load the database file, return the contents if sucessful.  If the directories in the fle do not match expectations, return nothing.
    """

    if not (os.path.exists(args.database_name) and os.path.isfile(args.database_name)):
        return None

    try:
        contents = load_json(args.database_name)
        if contents['directories'] != sorted(args.directories):
            logger.warning("The directories in the database do not match the parameters, building new database.")
            return None
        
        if not contents['images']:
            logger.warning("The database constains no images, building new database.")
            return None
        
        return contents
    except Exception as err:
        logger.error("Failed to load: %s" % args.database_name)
        logger.debug(err, exc_info=True)
        return None



def convert_props_for_image(dt,sz):
    """
    Convert date and size strings to the formats we want.  Assumes both values are True-ey strings.
    """

    dt = parsedate(dt.encode('ascii'))

    m = re.match(r"^(\d+)x(\d+)$",sz)
    if not m:
        return None,None
    sz = int(m.group(0)) * int(m.group(1))

    return dt,sz



def get_props_for_image(path):
    """
    Find a date and image dimentions in the image EXIF data.
    
    Return a tuple of (the date as a 6-element tuple), (pixel count).
    """

    with Image.open(path) as imgobj:
        if 'exif' in imgobj.info:
            exif_dict = piexif.load(imgobj.info['exif'])
        else:
            logger.warning("Failed to read exif: %s" % path)
            return None,None
        
        try:
            dt = parsedate(exif_dict["0th"].get(piexif.ImageIFD.DateTime))
            dt = dt or parsedate(exif_dict["Exif"].get(piexif.ExifIFD.DateTimeOriginal))
            dt = dt or parsedate(exif_dict["Exif"].get(piexif.ExifIFD.DateTimeDigitized))
            dt = dt or None

            x, y = imgobj.size
            if y > x:
                logger.info("img %d x %d" % (x,y))
            sz = x * y or None

            return dt,sz
        except KeyError:
            logger.warning("Failed to read exif (missing key): %s" % path)
            return None,None



def process_one_dir(path, directory_summary):
    """
    Return inventory contents for the given path, if there is an inventory file.
    """

    logger.info("Process path: %s" % path)

    invpath = os.path.join(path,args.inventory_file_name)
    try:
        if os.path.exists(invpath) and os.path.isfile(invpath):
            contents = load_json(invpath)
            filtered_contents = []
            directory_summary['count'] += 1
            directory_summary['inventories'].append(path)
            for line in contents:
                if len(line) == 3:
                    name = line[0]                     # ignore checksum and byte-size from the inventory file
                    dt,sz = None,None                  # short inventory format lacks date and size
                else:
                    name,_,_,dt,sz = line              # extended inventory format has date and size
                name = os.path.join(path,name)
                if name.lower().endswith(".jpg"):      # only jpg images
                    if dt and sz:                      # skip looking at the image header if we can
                        dt,sz = convert_props_for_image(dt,sz)
                    if not (dt and sz):
                        dt,sz = get_props_for_image(name)
                    if dt and sz and sz > 1600*1200:   # only images with date, size metadata, and of a certain min resolution
                        filtered_contents.append((dt,name))
    except OSError as err:
        logger.warning("Failed to read an inventory file.")
        logger.debug(err,exc_info=True)
        directory_summary['errors'] += 1
        filtered_contents = []

    if args.recursive:
        for thing in os.listdir(path):
            thing = os.path.join(path, thing)
            if os.path.isdir(thing):
                filtered_contents += process_one_dir(thing, directory_summary)

    return filtered_contents



def select_image_subset(database_content):
    """
    Select a subset of images to show.  Return a tuple: (paths, dict() mapping the paths to date tuples)
    """

    # group by year, month
    grouped = defaultdict(lambda: [])
    path_to_date = dict()
    for dt,path in database_content['images']:
        grouped[(dt[0],dt[1])].append((dt,path))
        path_to_date[path] = tuple(dt)

    # drop the year/month keys, key only lists
    grouped = [v for _,v in sorted(grouped.items())]

    # if a month has too few pictures, combine it with the following month(s)
    re_grouped = [[]]
    for g in grouped:
        if len(re_grouped[-1]) < 500:
            re_grouped[-1] = re_grouped[-1] + g
        else:
            re_grouped.append(g)
    assert sum(len(x) for x in re_grouped) == sum(len(x) for x in grouped), "Lost some images."

    # select two per bucket
    paths = []
    for g in re_grouped:
        for _ in range(3):
            paths.append(choice(g))

    # sort by date, then drop the date
    paths = [path for _,path in sorted(paths)]

    logger.debug("Lets show: %s" % str(paths))
    logger.info("Have chosen %d images to show." % len(paths))

    return paths,path_to_date



def more_images(current_idx, current_paths, path_to_date, database_content):
    """
    Extend the subset of images to show, centered on the given image.  Return the new index and paths.
    """

    # arrange images by dates (which are 6-element tuples)
    dt_to_paths = defaultdict(lambda: [])
    for dt,path in database_content['images']:
        dt_to_paths[tuple(dt)].append(path)

    # get all possible dates in a list sorted list
    all_date_list = sorted(dt_to_paths.keys())

    # find the date index of our currently displayed image (so we can search near it)
    dt_index = None
    for dt,paths in dt_to_paths.items():
        if path in paths:
            dt_index = all_date_list.index(dt)

    assert dt_index != None, "Failed to find our date index."

    pathset = set(current_paths)
    added = []

    def addfunc(dt_idx):
        if dt_idx >= 0 and dt_idx < len(all_date_list):
            dt = all_date_list[dt_idx]
            for path in dt_to_paths[dt]:
                if path not in pathset:
                    logger.info("Add path: %s" % path)
                    added.append(path)

    # find nearby images that we don't already have
    working_dt_idx_offset = 1
    while len(added) < 20 and len(added) + len(current_paths) < len(database_content['images']):
        addfunc(dt_index - working_dt_idx_offset)
        addfunc(dt_index + working_dt_idx_offset)
        working_dt_idx_offset += 1
        assert working_dt_idx_offset < len(all_date_list), "Runaway date list index."

    new_paths = []
    for path in current_paths+added:
        dt = path_to_date[path]
        new_paths.append((dt,path))
    new_paths = [x[1] for x in sorted(new_paths)]
    new_idx = new_paths.index(current_paths[current_idx])

    logger.debug("New paths: %s" % str(new_paths))

    return new_idx, new_paths



class ImageCache():
    def __init__(self, screen_res, paths):
        self.screen_res = screen_res      # tuple for the resolution to cache images at
        self.paths = paths                # paths to image files, sorted by date (but the date is not provided here)
        self.path_to_idx = dict((p,i) for i,p in enumerate(paths))
        self.run = True

        self.image_cache = dict()         # map indexes of cached images to pygame surfaces
        self.current_idx = 0
        self.image_cache_lock = Condition()

        self.thread = Thread(target=self.worker)
        self.thread.daemon = True
        self.thread.start()

    def worker(self):
        try:
            while self.run:
                with self.image_cache_lock:
                    # we'll be doing some work with the lock held because the list of paths can be changed on us

                    # find images most suitable to load and to unload
                    best_idx = None
                    best_score = len(self.paths)+1
                    worst_idx = None
                    worst_score = 0
                    for maybe_idx in range(len(self.paths)):
                        score = abs(maybe_idx - self.current_idx)
                        if maybe_idx in self.image_cache:
                            if score > worst_score:
                                worst_score = score
                                worst_idx = maybe_idx
                        else:
                            if score < best_score:
                                best_score = score
                                best_idx = maybe_idx

                    if best_idx == None:
                        logger.info("Everything seems to be cached (%d images in list, current idx %d)." % (len(self.paths),self.current_idx))
                        assert len(self.image_cache) == len(self.paths), "Somehow not all images where cached when expected."
                        return
                    logger.debug("Maybe load idx %d?" % best_idx)
                    path = self.paths[best_idx]

                    # clean up the cache
                    if len(self.image_cache) > args.cache_count and worst_score > best_score+1:
                        logger.debug("Unload idx %d." % worst_idx)
                        assert worst_idx != None, "Somehow the worst index was not set."
                        assert worst_idx != best_idx, "Somehow the best and worst indexes match."
                        del self.image_cache[worst_idx]

                if len(self.image_cache) <= args.cache_count+1:

                    # load an image
                    logger.info("Load: %s" % path)
                    srf = pygame.image.load(path)
                    wid,hig = srf.get_size()

                    with self.image_cache_lock:
                        # we'll be doing some work with the lock held because the screen res can be changed on us

                        widr = wid / self.screen_res[0]
                        higr = hig / self.screen_res[1]
                        if widr >= higr:
                            # too wide, black bars top & bottom (or perfect fit)
                            scale_res = (self.screen_res[0],self.screen_res[1]*(higr/widr))
                            paste_at = (0,(self.screen_res[1]-scale_res[1])/2)
                        else:
                            # too tall, black bars left & right
                            scale_res = (self.screen_res[0]*(widr/higr),self.screen_res[1])
                            paste_at = ((self.screen_res[0]-scale_res[0])/2,0)
                        srf = pygame.transform.smoothscale(srf,scale_res)
                        blksrf = pygame.Surface(self.screen_res)
                        blksrf.blit(srf,paste_at)

                        # store the result, careful to protect against the list of paths having changed
                        if path in self.path_to_idx:
                            idx = self.path_to_idx[path]
                            self.image_cache[idx] = blksrf

                            # let the main thread know, in case its waiting
                            self.image_cache_lock.notify_all()
                        else:
                            logger.info("Freshly-loaded image no longer has a valid path.")

                else:
                    logger.debug("Background thread sleeping.")
                    sleep(0.5)
        except Exception as err:
            logger.error("Error in background thread.")
            logger.error(err,exc_info=True)

    def get_surface(self, idx):
        """
        Attempt to get the image surface, return None after a short delay if not found.
        """

        with self.image_cache_lock:
            self.current_idx = idx
            if idx not in self.image_cache:
                self.image_cache_lock.wait(0.05)
            return self.image_cache.get(idx)

    def set_screen(self, screen_res):
        """
        Throw out images that have been cached with the wrong resolution.
        """

        with self.image_cache_lock:
            # we need to hold the lock to coordinate with any image that may be in the loading process
            self.screen_res = screen_res
            self.image_cache.clear()

    def set_paths(self, paths):
        """
        Replace the current list of paths with new ones, keep cached images when possible.
        """

        logger.debug("Load new image list with %d items." % len(paths))

        with self.image_cache_lock:
            old_cache = self.image_cache
            old_path_to_idx = self.path_to_idx

            # if our currently selected image continues to exist, use that index
            # otherwise, set our current image to the start of the new list
            current_path = self.paths[self.current_idx]
            if current_path in paths:
                self.current_idx = paths.index(current_path)
            else:
                self.current_idx = 0

            overlap = set(self.paths).intersection(set(paths))
            logger.debug("There are %d images to carry over from the old cache." % len(overlap))

            self.image_cache = dict()
            self.path_to_idx = dict((p,i) for i,p in enumerate(paths))
            self.paths = paths

            for path in overlap:
                old_idx = old_path_to_idx[path]
                new_idx = self.path_to_idx[path]
                if old_idx in old_cache:
                    # reference the image data in the new dict()
                    self.image_cache[new_idx] = old_cache[old_idx]



def apply_screen_setting(fullscreen):
    """
    Change between windowed and fullscreen mode.
    """

    desktops = pygame.display.get_desktop_sizes()
    if len(desktops) != 1:
        logger.warning("There are %d desktops." % len(desktops))
    num_screens = pygame.display.get_num_displays()
    if num_screens != 1:
        logger.warning("There are %d screens." % num_screens)

    if fullscreen:
        logger.info("Fullscreen mode.")
        more = {'flags': pygame.FULLSCREEN}
        screen_res = desktops[0]
    else:
        logger.info("Windowed mode.")
        screen_res = (1024,768)
        more = {}

    pygame.display.set_mode(size=screen_res, **more)
    screen_srf = pygame.display.get_surface()
    pygame.display.set_caption('Slideshow')

    return screen_res, screen_srf



def text_box(text, textcolor, backgroundcolor):
    """
    Create a surface with text on it.
    """

    font = pygame.font.Font(pygame.font.get_default_font(), 28)
    spacing = 4
    text_surface = font.render(text, True, textcolor, backgroundcolor)
    text_surface_2 = pygame.surface.Surface((text_surface.get_size()[0]+spacing*2,text_surface.get_size()[1]+spacing*2))
    text_surface_2.fill(backgroundcolor)
    text_surface_2.blit(text_surface, dest=(spacing,spacing))
    return text_surface_2



def start_show(database_content):
    logger.info("Start.")

    paths,path_to_date = select_image_subset(database_content)

    pygame.init()
    pygame.display.init()
    screen_res, screen_srf = apply_screen_setting(False)

    cache = ImageCache(screen_res, paths)

    fullscreen = False
    stop = False
    manual = True
    idx = 0
    start_at = 0
    direction = 1
    flip_time = 2
    while not stop:
        if manual or time() - start_at > flip_time:
            if not manual:
                idx += direction
            srf = cache.get_surface(idx)
            if srf:
                logger.info("Show image #%d of %d." % (idx+1,len(paths)))
                screen_srf.blit(srf,(0,0))
                txt_srf = text_box("%04d-%02d-%02d" % tuple(path_to_date[paths[idx]][0:3]), (255,255,255), (0,0,0))
                screen_srf.blit(txt_srf,(0,0))
                pygame.display.flip()
                start_at = time()
                manual = False
            else:
                logger.debug("Image #%d of %d not available yet." % (idx+1,len(paths)))
        else:
            sleep(0.05)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
               stop = True
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE or event.key == pygame.K_q:
                    stop = True
                elif event.key == pygame.K_LEFT:
                    idx -= 1
                    manual = True
                elif event.key == pygame.K_RIGHT:
                    idx += 1
                    manual = True
                elif event.key == pygame.K_UP:
                    flip_time += 1
                elif event.key == pygame.K_DOWN:
                    flip_time = max(1,flip_time-1)
                elif event.key == pygame.K_f:       # toggle fullscreen
                    fullscreen = not fullscreen
                    screen_res, screen_srf = apply_screen_setting(fullscreen)
                    cache.set_screen(screen_res)
                elif event.key == pygame.K_m:       # add more nearby images
                    idx,paths = more_images(idx,paths,path_to_date,database_content)
                    cache.set_paths(paths)
                elif event.key == pygame.K_SPACE:
                    if direction: direction = 0     # toggle flipping pause
                    else:         direction = 1

        if idx < 0: idx = 0
        if idx >= len(paths): idx = len(paths)-1

    pygame.quit()



def main():
    global args
    global logger

    # parse arguments
    parser = argparse.ArgumentParser(description='Make a semi-random slideshow from one or more directories or directory trees.')
    parser.add_argument('--recursive', help='crawl all subpaths inside the given paths', action="store_true")
    parser.add_argument('--refresh-database', help='rebuild the database file from inventory files (rebuild is automatic if the database is empty or is based on different base paths)', action="store_true")
    parser.add_argument('--database-name', metavar='NAME', help='the name of a file to serve as a database of images to consider showing (default: %(default)s)', default="slideshow-db.json")
    parser.add_argument('--inventory-file-name', metavar='NAME', help='the name of the inventory file to look for in each directory (default: %(default)s)', default="inventory.json")
    parser.add_argument('--cache-count', metavar='NUM', type=int, help='how many images to load into RAM for rapid display (default: %(default)s)', default=100)
    parser.add_argument('--log', metavar='PATH', help='base log file name (default: %(default)s)', default="slideshow.log")
    parser.add_argument('directories', metavar='DIR', nargs='+', help='directories to process')
    args = parser.parse_args()

    # set up a standard logger object
    logger = setup_logger(args.log, console_level=logging.INFO)

    if args.cache_count < 10:
        logger.error("The parameter --cache-count cannot be less than 10.")
        logger.error("The program can not continue.")
        exit(-1)

    try:
        # on Windows, expand special characters (on Linux, would perhaps expand previously escaped characters)
        targets = []
        for t in args.directories:
            globbed = glob.glob(t.rstrip(r'\/'))
            targets += globbed
        args.directories = targets

        if not args.directories:
            logger.error("No directories remain after glob-ing (remember sneaky pictures/bilder rename on Windows).")
            logger.error("The program can not continue.")
            exit(-1)

        for d in args.directories:
            if not os.path.isdir(d):
                logger.error("The parameter '%s' is not a directory." % d)
                logger.error("The program can not continue.")
                exit(-1)

        if not args.refresh_database:
            database_content = load_database()
        else:
            database_content = None

        # FIXME: notice when the directories in the DB differ from the CLI options, also the --recursive flag

        if args.refresh_database or not database_content:
            directory_summary = {'count': 0, 'inventories': [], 'errors': 0}
            database_content = {'directories': sorted(args.directories), 'images': []}
            for d in args.directories:
                database_content['images'] += process_one_dir(d, directory_summary)
            logger.info("Found %d inventory files containing %d records." % (directory_summary['count'],len(database_content['images'])))
            logger.info("Encountered %d errors loading inventories." % directory_summary['errors'])

            write_json(args.database_name, database_content)

        start_show(database_content)

    except Exception as err:
        logger.error("Exception " + str(type(err)) + " while working.", exc_info=True)
        exit(-2)



if __name__ == '__main__':
    main()
    logger.info("Script has reached end.") # support being called as a script
else:
    logger = logging.getLogger(__name__)   # support being called as a library