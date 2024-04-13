# -*- encoding: UTF-8 -*-

'''
Make a semi-random slideshow from one or more directories or directory trees.
'''

import os
import argparse
import glob, re
import logging
import datetime
from collections import defaultdict
import pygame_sdl2 as pygame
import piexif
from PIL import Image, JpegImagePlugin
from inventory import setup_logger, load_json, write_json



# globals
logger = None
args = None



def parsedate(datestr):
	# Have observed that with dateutil.parser.parse(datestr):
	#   b'2019:07:26 14:15:58' => 2019-08-02 14:15:58
	#   '20190726 141558' => 2019-08-02 14:15:58
	# Therefore implemented a parser using regex so that things are reliably strict.
	m = re.match("^(\d\d\d\d):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)$",datestr.decode('ascii'))
	if not m:
		raise Exception("Failed to parse: %s" % datestr)
	return datetime.datetime(*[int(m.group(x+1)) for x in range(6)])



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
        
        for idx, in range(len(contents['images'])):
            dt,path = contents['images'][idx]
            dt = parsedate(dt)
            contents['images'][idx] = dt,path
        
    except Exception as err:
        logger.error("Failed to load: %s" % args.database_name)
        logger.debug(err, exc_info=True)
        return None
    


def write_database(contents):
    """
    Write the database data to disk so we don't need to build it next time.
    """
        
    for idx, in range(len(contents['images'])):
        dt,path = contents['images'][idx]
        dt = dt.strftime('%Y:%m:%d %H:%M:%S')
        contents['images'][idx] = dt,path

    write_json(args.database_name, contents)



def get_date_for_image(path):
    """
    Find a date in the image EXIF data.
    """

    with Image.open(path) as imgobj:
        if 'exif' in imgobj.info:
            exif_dict = piexif.load(imgobj.info['exif'])
        else:
            logger.warning("Failed to read exif: %s" + path)
            return None
        
        dt = parsedate(exif_dict["0th"][piexif.ImageIFD.DateTime])
        dt = dt or parsedate(exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal])
        dt = dt or parsedate(exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized])
        return dt or None



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
            for name,_,_ in range(len(contents)):
                name = os.path.join(path,name)
                if name.lower.endswith(".jpg"):
                    dt = get_date_for_image(path)
                    if dt:
                        filtered_contents.append((dt,name))
    except OSError as err:
        logger.warning("Failed to read an inventory file.")
        logger.debug(err,exc_info=True)
        directory_summary['errors'] += 1
        contents = []

    if args.recursive:
        for thing in os.listdir(path):
            thing = os.path.join(path, thing)
            if os.path.isdir(thing):
                contents += process_one_dir(thing, directory_summary)

    return contents



def start_show(database_content):
    logger.info("Start.")



def main():
    global args
    global logger

    # parse arguments
    parser = argparse.ArgumentParser(description='Make a semi-random slideshow from one or more directories or directory trees.')
    parser.add_argument('--recursive', help='crawl all subpaths inside the given paths', action="store_true")
    parser.add_argument('--refresh-database', help='rebuild the database file from inventory files', action="store_true")
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

        if args.refresh_database or not database_content:
            directory_summary = {'count': 0, 'inventories': [], 'errors': 0}
            database_content = {'directories': sorted(args.directories), 'images': []}
            for d in args.directories:
                database_content['images'] += process_one_dir(d, directory_summary)
            logger.info("Found %d inventory files containing %d records." % (directory_summary['count'],len(database_content['images'])))
            logger.info("Encountered %d errors loading inventories." % directory_summary['errors'])

            write_database(database_content)

        start_show(database_content)

    except Exception as err:
        logger.error("Exception " + str(type(err)) + " while working.", exc_info=True)
        exit(-2)



if __name__ == '__main__':
    main()
    logger.info("Script has reached end.") # support being called as a script
else:
    logger = logging.getLogger(__name__)   # support being called as a library