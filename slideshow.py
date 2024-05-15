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
from inventory import setup_logger, load_json, parse_date_str, parse_dim_str



# globals
logger = None
args = None



def load_inventories():
    """
    Load the inventory files and merge the contents.  Filter unusable content.
    """

    checksums = set()
    newlist = list()

    for f in args.inventory_files:
        invpathpart, _ = os.path.split(f)
        contents = load_json(f)
        logger.info("Inventory file contains %d items." % len(contents))
        for item in contents:
            if len(item) != 5:       # we want info about suitable image files, not non-image files
                continue
            if not item[3]:          # there can be entries with missing dates, which we can't use here
                continue
            if item[2] in checksums: # there can be duplicates
                continue
            checksums.add(item[2])
            pathpart,filepart = os.path.split(item[0])
            if invpathpart and not pathpart:
                filepath = os.path.join(invpathpart,filepart)
            else:
                filepath = item[0]
            assert os.path.isfile(filepath), "The path %s is not a file." % filepath
            newitem = (parse_date_str(item[3]), filepath)
            newlist.append(newitem)

    return sorted(newlist)



def select_image_subset(image_info_list):
    """
    Select a subset of images to show.  Return a tuple: (paths, dict() mapping the paths to date tuples)
    """

    # group by year, month
    grouped = defaultdict(lambda: [])
    path_to_date = dict()
    for dt,path in image_info_list:
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



def more_images(current_idx, current_paths, path_to_date, image_info_list):
    """
    Extend the subset of images to show, centered on the given image.  Return the new index and paths.
    """

    # arrange images by dates (which are 6-element tuples)
    dt_to_paths = defaultdict(lambda: [])
    for dt,path in image_info_list:
        dt_to_paths[tuple(dt)].append(path)

    # get all possible dates in a list sorted list
    all_date_list = sorted(dt_to_paths.keys())
    logger.info("The list of distinct dates is %d elements." % len(all_date_list))

    # find the date index of our currently displayed image (so we can search near it)
    dt_index = None
    for dt,paths in dt_to_paths.items():
        if current_paths[current_idx] in paths:
            dt_index = all_date_list.index(dt)

    logger.info("Have determined that our current index in the list of dates is %s." % dt_index)
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
    while len(added) < 20 and len(added) + len(current_paths) < len(image_info_list):
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
                    with Image.open(path) as imgobj:
                        oval = 1
                        exif_dict = piexif.load(imgobj.info['exif'])    # should not be here without usable EXIF
                        oval = exif_dict["0th"].get(piexif.ImageIFD.Orientation)
                        logger.debug("Orientation value: %s" % oval)
                        srf = pygame.image.frombytes(imgobj.tobytes(), imgobj.size, "RGB")
                        if oval == 6:
                            # rotate CW 90
                            srf = pygame.transform.rotate(srf,270)
                        elif oval == 8:
                            # rotate 90 CCW
                            srf = pygame.transform.rotate(srf,90)
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



def start_show(image_info_list):
    logger.info("Start.")

    paths,path_to_date = select_image_subset(image_info_list)

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
                    idx,paths = more_images(idx,paths,path_to_date,image_info_list)
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
    parser = argparse.ArgumentParser(description='Make a semi-random slideshow from one or more inventory files.')
    parser.add_argument('--cache-count', metavar='NUM', type=int, help='how many images to load into RAM for rapid display (default: %(default)s)', default=100)
    parser.add_argument('--log', metavar='PATH', help='base log file name (default: %(default)s)', default="slideshow.log")
    parser.add_argument('inventory_files', metavar='FILE', nargs='+', help='one or more inventory files to show images from')
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
        for t in args.inventory_files:
            globbed = glob.glob(t.rstrip(r'\/'))
            targets += globbed
        args.inventory_files = targets

        if not args.inventory_files:
            logger.error("No inventory files remain after glob-ing (remember sneaky pictures/bilder rename on Windows).")
            logger.error("The program can not continue.")
            exit(-1)

        for d in args.inventory_files:
            if not os.path.isfile(d):
                logger.error("The parameter '%s' is not a file." % d)
                logger.error("The program can not continue.")
                exit(-1)

        image_info_list = load_inventories()
        start_show(image_info_list)

    except Exception as err:
        logger.error("Exception " + str(type(err)) + " while working.", exc_info=True)
        exit(-2)



if __name__ == '__main__':
    main()
    logger.info("Script has reached end.") # support being called as a script
else:
    logger = logging.getLogger(__name__)   # support being called as a library