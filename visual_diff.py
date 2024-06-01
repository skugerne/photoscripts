'''
Compare two inventory files (which may be single- or multi-directory) and show which images differ between them.
'''

import os
import argparse
import glob, re
import logging
from collections import defaultdict
import pygame    # requires at least version 2.1.3
import piexif
from PIL import Image
from time import sleep, time
from datetime import datetime
from random import choice
from threading import Thread, Condition

# import from our other files
from inventory import setup_logger, load_json, parse_date_str, parse_dim_str
from slideshow import ImageCache, load_inventories, apply_screen_setting, text_box



# globals
logger = None
args = None



class ImageRow():
    def __init__(self, image_info_list, all_images, checksum_to_idx, screen_res, screen_srf, upper_left):
        self.image_info_list = image_info_list       # InventoryItem objects for our inventory file, theoretically sorted oldest first
        self.all_images = all_images                 # sorted tuples (date,checksum) for both inventory files, sorted oldest first
        self.all_checksum_to_idx = checksum_to_idx   # checksums pointing to indexes in 'all_images'
        self.idx = 0
        self.cache = ImageCache(screen_res, [x.name for x in image_info_list])
        self.upper_left = upper_left

    def show(self, checksum):
        """
        Show the image with the given checksum, or a placeholder if it is missing.
        """
        pass



def start_show(image_info_list_1, image_info_list_2):
    logger.info("Start.")

    pygame.init()
    pygame.display.init()
    screen_res, screen_srf = apply_screen_setting(False)

    all_images = set()
    for image_info in image_info_list_1 + image_info_list_2:
        all_images.add((image_info.date,image_info.checksum))
    all_images = sorted(all_images)
    checksum_to_idx = dict((vals[1],idx) for idx,vals in enumerate(all_images))
    idx = 0

    upper_row = ImageRow(image_info_list_1, all_images, checksum_to_idx, screen_res, screen_srf, (0,0))
    lower_row = ImageRow(image_info_list_2, all_images, checksum_to_idx, screen_res, screen_srf, (0,screen_res[1]/2))
    upper_row.show(all_images[0][1])
    lower_row.show(all_images[0][1])

    fullscreen = False
    stop = False
    new_idx_chosen = True
    idx = 0
    start_at = 0
    direction = 1
    flip_time = 2
    while not stop:
        if new_idx_chosen or time() - start_at > flip_time:
            if not new_idx_chosen:
                idx += direction
                logger.debug("Increment/decrement image index to %d." % idx)
                new_idx_chosen = True
            srf = cache.get_surface(idx)
            if srf:
                logger.info("Show image #%d of %d." % (idx+1,len(paths)))
                screen_srf.blit(srf,(0,0))
                txt_srf = text_box("%04d-%02d-%02d" % tuple(path_to_date[paths[idx]][0:3]), (255,255,255), (0,0,0))
                screen_srf.blit(txt_srf,(0,0))
                pygame.display.flip()
                start_at = time()
                new_idx_chosen = False
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
                    new_idx_chosen = True
                elif event.key == pygame.K_RIGHT:
                    idx += 1
                    new_idx_chosen = True
                elif event.key == pygame.K_f:       # toggle fullscreen
                    fullscreen = not fullscreen
                    screen_res, screen_srf = apply_screen_setting(fullscreen)
                    upper_row = ImageRow(image_info_list_1, all_images, checksum_to_idx, screen_res, screen_srf, (0,0))
                    lower_row = ImageRow(image_info_list_2, all_images, checksum_to_idx, screen_res, screen_srf, (0,screen_res[1]/2))

        if idx < 0: idx = 0
        if idx >= len(paths): idx = len(paths)-1

    pygame.quit()



def main():
    global args
    global logger

    # parse arguments
    parser = argparse.ArgumentParser(description='Compare two inventory files (which may be single- or multi-directory) and show which images differ between them.')
    parser.add_argument('--cache-count', metavar='NUM', type=int, help='how many images to load into RAM for rapid display (default: %(default)s)', default=100)
    parser.add_argument('--log', metavar='PATH', help='base log file name (default: %(default)s)', default="slideshow.log")
    parser.add_argument('inventory_file_1', metavar='FILE', help='an inventory file to show images from')
    parser.add_argument('inventory_file_2', metavar='FILE', help='an inventory file to show images from')
    args = parser.parse_args()

    # set up a standard logger object
    logger = setup_logger(args.log, console_level=logging.INFO)

    if args.cache_count < 10:
        logger.error("The parameter --cache-count cannot be less than 10.")
        logger.error("The program can not continue.")
        exit(-1)

    try:
        # on Windows, expand special characters (on Linux, would perhaps expand previously escaped characters)
        args.inventory_file_1 = glob.glob(args.inventory_file_1.rstrip(r'\/'))
        if len(args.inventory_file_1) != 1:
            logger.error("Do not have exactly one first inventory file after glob-ing (remember sneaky pictures/bilder rename on Windows).")
            logger.error("The program can not continue.")
            exit(-1)
        args.inventory_file_1 = args.inventory_file_1[0]

        args.inventory_file_2 = glob.glob(args.inventory_file_2.rstrip(r'\/'))
        if len(args.inventory_file_2) != 1:
            logger.error("Do not have exactly one second inventory file after glob-ing (remember sneaky pictures/bilder rename on Windows).")
            logger.error("The program can not continue.")
            exit(-1)
        args.inventory_file_2 = args.inventory_file_2[0]

        if not (os.path.isfile(args.inventory_file_1) and os.path.isfile(args.inventory_file_1)):
            logger.error("One or both of the specified inventory files is not actually a file.")
            logger.error("The program can not continue.")
            exit(-1)

        image_info_list_1 = load_inventories([args.inventory_file_1], remove_dupes=False)
        image_info_list_2 = load_inventories([args.inventory_file_2], remove_dupes=False)

        if not (image_info_list_1 and image_info_list_2):
            logger.error("One or both of the specified inventory files did not yield usable images.")
            logger.error("The program can not continue.")
            exit(-1)

        start_show(image_info_list_1, image_info_list_2)

    except Exception as err:
        logger.error("Exception " + str(type(err)) + " while working.", exc_info=True)
        exit(-2)



if __name__ == '__main__':
    main()
    logger.info("Script has reached end.") # support being called as a script
else:
    logger = logging.getLogger(__name__)   # support being called as a library