# -*- encoding: UTF-8 -*-

'''
Make a semi-random slideshow from one or more directories or directory trees.
'''

import os
import argparse
import glob, re
import logging
from collections import defaultdict
import pygame_sdl2 as pygame
import piexif
from PIL import Image
from random import choice
from inventory import setup_logger, load_json, write_json



# globals
logger = None
args = None



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
    
    return tuple(int(m.group(x+1)) for x in range(6))



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

            x = exif_dict["0th"].get(piexif.ImageIFD.ImageWidth) or exif_dict["Exif"].get(piexif.ExifIFD.PixelXDimension) or 0
            y = exif_dict["0th"].get(piexif.ImageIFD.ImageLength) or exif_dict["Exif"].get(piexif.ExifIFD.PixelYDimension) or 0
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
            for name,_,_ in contents:                  # ignore checksum and byte-size from the inventory file
                name = os.path.join(path,name)
                if name.lower().endswith(".jpg"):      # only jpg images
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



def start_show(database_content):
    logger.info("Start.")

    grouped = defaultdict(lambda: defaultdict(lambda: []))
    for dt,path in database_content['images']:
        grouped[dt[0]][dt[1]].append(path)  # year, month

    paths = []
    for year in sorted(grouped.keys()):
        for month in sorted(grouped[year].keys()):
            paths.append(choice(grouped[year][month]))

    logger.info("Lets show: %s" % str(paths))



def main():
    global args
    global logger

    # parse arguments
    parser = argparse.ArgumentParser(description='Make a semi-random slideshow from one or more directories or directory trees.')
    parser.add_argument('--recursive', help='crawl all subpaths inside the given paths', action="store_true")
    parser.add_argument('--refresh-database', help='rebuild the database file from inventory files (rebuild is automatic if the database is empty or is based on different base paths)', action="store_true")
    parser.add_argument('--database-name', metavar='NAME', help='the name of a file to serve as a database of images to consider showing (default: %(default)s)', default="slideshow-db.json")
    parser.add_argument('--inventory-file-name', metavar='NAME', help='the name of the inventory file to look for in each directory (default: %(default)s)', default="inventory.json")
    parser.add_argument('--log', metavar='PATH', help='base log file name (default: %(default)s)', default="slideshow.log")
    parser.add_argument('directories', metavar='DIR', nargs='+', help='directories to process')
    args = parser.parse_args()

    # set up a standard logger object
    logger = setup_logger(args.log, console_level=logging.INFO)

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