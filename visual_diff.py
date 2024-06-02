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



def build_date_checksum_list(image_info_list):
    """
    Convert a list of tuples (date,checksum) to a dict of checksums pointing to indexes.
    """

    image_list = set()
    for image_info in image_info_list:
        image_list.add((image_info.date,image_info.checksum))
    image_list = sorted(image_list)

    checksum_to_idx = defaultdict(lambda: set())
    for idx,vals in enumerate(image_list):
        checksum_to_idx[vals[1]].add(idx)
    checksum_to_idx2 = dict()
    for k in checksum_to_idx.keys():
        checksum_to_idx2[k] = sorted(checksum_to_idx[k])
    return image_list, checksum_to_idx2



def missing_image(dims):
    """
    Return a surface of the given dimentions which indicates a missing image.
    """

    surf = pygame.Surface(dims)
    pygame.draw.line(surf, (255,0,0), (0,0), dims)
    pygame.draw.line(surf, (0,0,255), (dims[0],0), (0,dims[1]))
    txt_srf = text_box("missing", (150,150,220), (0,0,0), size=16)
    surf.blit(txt_srf,((dims[0]-txt_srf.get_width())/2,(dims[1]-txt_srf.get_height())/2))
    return surf



class ImageRow():
    def __init__(self, image_info_list, all_images, checksum_to_idx, screen_srf, upper_left, dims):
        self.image_info_list = image_info_list       # InventoryItem objects for our inventory file, theoretically sorted oldest first
        self.own_images, self.own_checksum_to_idx = build_date_checksum_list(image_info_list)
        self.all_images = all_images                 # sorted tuples (date,checksum) for both inventory files, sorted oldest first
        self.all_checksum_to_idx = checksum_to_idx   # checksums pointing to indexes in 'all_images'
        self.idx = 0
        self.upper_left = upper_left
        self.dims = dims

        self.screen_srf = screen_srf
        self.num_surfaces = 7
        self.surfaces = [None]*self.num_surfaces

        self.main_dims = (dims[0]/3, dims[1])
        self.small_dims = (dims[0]/9, 3*dims[1]/4)
        logger.info("Main surface dims: %s" % str(self.main_dims))
        logger.info("Small surface dims: %s" % str(self.small_dims))
        self.surface_corn = [None]*self.num_surfaces
        x = upper_left[0]
        for idx in range(self.num_surfaces):
            if idx == 3:
                self.surface_corn[idx] = (x,self.upper_left[1])
                x += self.main_dims[0]
            else:
                offset = (self.main_dims[1] - self.small_dims[1]) / 2
                self.surface_corn[idx] = (x,self.upper_left[1]+offset)
                x += self.small_dims[0]
        logger.info("Surface corners: %s" % str(self.surface_corn))
        self.main_missing = missing_image(self.main_dims)
        self.small_missing = missing_image(self.small_dims)

        self.cache = ImageCache(self.main_dims, [x.name for x in image_info_list], args.cache_count)

    def set_idx(self, idx):
        """
        Show the image with the given checksum, or a placeholder if it is missing.
        """
        for idx in range(len(self.surfaces)):
            pass

    def display(self):
        """
        Blit images to screen.
        """

        for idx in range(self.num_surfaces):
            logger.info("Draw surface %d." % idx)
            corn = self.surface_corn[idx]
            if idx == 3:
                dims = self.main_dims
            else:
                dims = self.small_dims
            if self.surfaces[idx]:
                srf = self.surfaces[idx]
            elif idx == 3:
                srf = self.main_missing
            else:
                srf = self.small_missing
            self.screen_srf.blit(srf,corn)
            p = (
                corn,
                (corn[0],corn[1]+dims[1]),
                (corn[0]+dims[0],corn[1]+dims[1]),
                (corn[0]+dims[0],corn[1])
            )
            pygame.draw.lines(self.screen_srf, (120,120,120), True, p)



def start_show(image_info_list_1, image_info_list_2):
    logger.info("Start.")

    pygame.init()
    pygame.display.init()
    screen_res, screen_srf = apply_screen_setting(False)

    all_images, checksum_to_idx = build_date_checksum_list(image_info_list_1 + image_info_list_2)

    row_dims = (screen_res[0],screen_res[1]/2)
    upper_row = ImageRow(image_info_list_1, all_images, checksum_to_idx, screen_srf, (0,0), row_dims)
    lower_row = ImageRow(image_info_list_2, all_images, checksum_to_idx, screen_srf, (0,screen_res[1]/2), row_dims)

    fullscreen = False
    stop = False
    new_idx_chosen = True
    idx = 0
    while not stop:
        if new_idx_chosen:
            upper_row.set_idx(idx)
            lower_row.set_idx(idx)
            new_idx_chosen = False
        else:
            upper_row.display()
            lower_row.display()
            pygame.display.flip()
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
                    row_dims = (screen_res[0],screen_res[1]/2)
                    upper_row = ImageRow(image_info_list_1, all_images, checksum_to_idx, screen_srf, (0,0), row_dims)
                    lower_row = ImageRow(image_info_list_2, all_images, checksum_to_idx, screen_srf, (0,screen_res[1]/2), row_dims)

        if idx < 0: idx = 0
        if idx >= len(all_images): idx = len(all_images)-1

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