'''
Compare two inventory files (which may be single- or multi-directory) and show which images differ between them.
'''

import os
import argparse
import glob
import logging
from collections import defaultdict
import pygame    # requires at least version 2.1.3
from math import sin,cos
from time import sleep, time

# import from our other files
from inventory import setup_logger, load_json, parse_date_str, parse_dim_str
from slideshow import ImageCache, load_inventories, apply_screen_setting, text_box



# globals
logger = None
args = None



def build_date_checksum_list(image_info_list):
    """
    Convert a list of InventoryItem to a list of tuples (date,checksum) and a dict of checksums pointing indexes in the list of tuples.
    """

    image_list = set()
    for image_info in image_info_list:
        image_list.add((image_info.date,image_info.checksum))
    image_list = sorted(image_list)

    checksum_to_idx = dict()
    for idx,vals in enumerate(image_list):
        assert vals[1] not in checksum_to_idx, "Apparently we have images with the same checksum but different dates."
        checksum_to_idx[vals[1]] = idx

    logger.info("Combined list contains %d images." % len(image_list))
    return image_list, checksum_to_idx



def loading_image(dims):
    """
    Return a surface of the given dimentions which indicates a missing image.
    """

    rotation = int(time() * 32) % 32 + 3
    surf = pygame.Surface(dims)
    anglestep = 3.141529 * 2 / rotation
    radius = 0.4*min(dims)
    coords = tuple((dims[0]/2+radius*sin(r*anglestep),dims[1]/2+radius*cos(r*anglestep)) for r in range(rotation))
    pygame.draw.lines(surf, (0,255,0), True, coords)
    txt_srf = text_box("loading", (150,150,220), (0,0,0), size=16)
    surf.blit(txt_srf,((dims[0]-txt_srf.get_width())/2,(dims[1]-txt_srf.get_height())/2))
    return surf



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
    def __init__(self, image_info_list, all_images, checksum_to_idx, screen_srf, upper_left, dims, all_image_idx=0):
        self.image_info_list = image_info_list       # InventoryItem objects for our inventory file, theoretically sorted oldest first
        self.own_images, self.own_checksum_to_idx = build_date_checksum_list(image_info_list)
        self.own_checksum_to_info_idx = defaultdict(lambda: set())
        for idx,info in enumerate(self.image_info_list):
            self.own_checksum_to_info_idx[info.checksum].add(idx)
        for checksum in self.own_checksum_to_info_idx.keys():
            self.own_checksum_to_info_idx[checksum] = tuple(sorted(self.own_checksum_to_info_idx[checksum]))

        self.all_images = all_images                 # sorted tuples (date,checksum) for both inventory files, sorted oldest first
        self.all_checksum_to_idx = checksum_to_idx   # checksums pointing to indexes in 'all_images'
        self.all_image_idx = all_image_idx
        self.upper_left = upper_left
        self.dims = dims

        self.screen_srf = screen_srf
        self.main_image_idx = 3
        self.num_surfaces = 7
        self.surfaces = [None]*self.num_surfaces

        self.main_dims = (dims[0]/3, dims[1])
        self.small_dims = (dims[0]/9, 3*dims[1]/4)
        logger.info("Main surface dims: %s" % str(self.main_dims))
        logger.info("Small surface dims: %s" % str(self.small_dims))
        self.surface_corn = [None]*self.num_surfaces
        x = upper_left[0]
        for idx in range(self.num_surfaces):
            if idx == self.main_image_idx:
                self.surface_corn[idx] = (x,self.upper_left[1])
                x += self.main_dims[0]
            else:
                offset = (self.main_dims[1] - self.small_dims[1]) / 2
                self.surface_corn[idx] = (x,self.upper_left[1]+offset)
                x += self.small_dims[0]
        logger.info("Surface corners: %s" % str(self.surface_corn))
        self.main_missing = missing_image(self.main_dims)
        self.small_missing = missing_image(self.small_dims)

        # FIXME: maybe based on a de-duped list
        self.cache = ImageCache((self.main_dims,self.small_dims), [x.name for x in image_info_list], args.cache_count)

    def set_idx(self, new_all_image_idx):
        """
        Set the given image to be the main one.  The given index is the global-list index of an image which we may or may not have in this row.
        """

        diff = new_all_image_idx - self.all_image_idx
        if not diff:
            return
        
        new_surfaces = [None] * self.num_surfaces
        for old_idx in range(len(self.surfaces)):
            new_idx = old_idx - diff
            if new_idx >= 0 and new_idx < self.num_surfaces:
                new_surfaces[new_idx] = self.surfaces[old_idx]

        self.surfaces = new_surfaces
        self.all_image_idx = new_all_image_idx

    def get_our_idx(self, all_image_idx):
        """
        Given the global-list index of an image, return the our-list index for it, or None if we do not have it.
        """

        _, checksum = self.all_images[all_image_idx]
        return self.own_checksum_to_idx.get(checksum)

    def get_our_info_idx(self, all_image_idx):
        """
        Given the global-list index of an image, return the our-list index for it, or None if we do not have it.
        """

        _, checksum = self.all_images[all_image_idx]
        info = self.own_checksum_to_info_idx.get(checksum)
        if info == None:
            return None
        return info[0]

    def display(self):
        """
        Blit images to screen.
        """

        # determine if there are any images that are ready for display
        for surf_list_idx in range(self.num_surfaces):
            all_image_idx = surf_list_idx + self.all_image_idx - self.main_image_idx
            if all_image_idx < 0 or all_image_idx >= len(self.all_images):
                continue
            our_idx = self.get_our_info_idx(all_image_idx)
            if not (our_idx == None or self.surfaces[surf_list_idx]):
                srfs = self.cache.get_surface(our_idx, delay=0)
                if srfs:
                    logger.info("Got an image.")
                    self.surfaces[surf_list_idx] = srfs
                else:
                    logger.info("Did not get a surface for surf_list_idx %s." % surf_list_idx)

        # show images
        for surf_list_idx in range(self.num_surfaces):
            logger.debug("Draw surface %d." % surf_list_idx)
            corn = self.surface_corn[surf_list_idx]
            if surf_list_idx == self.main_image_idx:
                dims = self.main_dims
            else:
                dims = self.small_dims

            all_image_idx = surf_list_idx + self.all_image_idx - self.main_image_idx
            if all_image_idx < 0 or all_image_idx >= len(self.all_images):
                assert surf_list_idx != self.main_image_idx, "The center image should not leave the ends of the image list."
                srf = self.small_missing                                      # images beyond the end of the combined list
            else:
                our_idx = self.get_our_info_idx(all_image_idx)
                if our_idx == None:                                           # images our row lacks
                    if surf_list_idx == self.main_image_idx:
                        srf = self.main_missing
                    else:
                        srf = self.small_missing
                elif self.surfaces[our_idx]:                                  # an image we have loaded already
                    srf = self.surfaces[our_idx][0 if surf_list_idx == self.main_image_idx else 1]   # surface idx 0 is large, idx 1 is small
                elif surf_list_idx == self.main_image_idx:
                    srf = loading_image(self.main_dims)                       # working on loading it
                else:
                    srf = loading_image(self.small_dims)                      # working on loading it
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
            logger.info("Update chosen image.")
            upper_row.set_idx(idx)
            lower_row.set_idx(idx)
            new_idx_chosen = False
        else:
            logger.debug("Display.")
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
                    upper_row = ImageRow(image_info_list_1, all_images, checksum_to_idx, screen_srf, (0,0), row_dims, upper_row.idx)
                    lower_row = ImageRow(image_info_list_2, all_images, checksum_to_idx, screen_srf, (0,screen_res[1]/2), row_dims, upper_row.idx)

        if idx < 0: idx = 0
        if idx >= len(all_images): idx = len(all_images)-1

    pygame.quit()



def main():
    global args
    global logger

    # parse arguments
    parser = argparse.ArgumentParser(description='Compare two inventory files (which may be single- or multi-directory) and show which images differ between them.')
    parser.add_argument('--cache-count', metavar='NUM', type=int, help='how many images to load into RAM for rapid display (default: %(default)s)', default=100)
    parser.add_argument('--log', metavar='PATH', help='base log file name (default: %(default)s)', default="diff.log")
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