# -*- encoding: UTF-8 -*-

'''
Create one large inventory from small ones which are distributed in the various image directories.

This script is therefore intended to be run after inventory.py has gone around making the per-directory inventory files.
'''

import os
import argparse
import glob
import logging
from inventory import setup_logger, load_json, write_json



# globals
logger = None
args = None



def process_one_dir(path, directory_summary):
    """
    Return inventory contents for the given path, if there is an inventory file.
    """

    logger.info("Process path: %s" % path)

    contents = []
    invpath = os.path.join(path,args.inventory_file_name)
    try:
        if os.path.exists(invpath) and os.path.isfile(invpath):
            contents = load_json(invpath)
            directory_summary['count'] += 1
            directory_summary['inventories'].append(path)
            for idx in range(len(contents)):
                name,size,checksum = contents[idx]
                name = os.path.join(path,name)
                if args.path_trim:
                    assert name.startswith(args.path_trim), "Cannot trim: %s" % name
                    name = name[len(args.path_trim):]
                contents[idx] = name,size,checksum
    except OSError as err:
        logger.warning("Failed to read an inventory file.")
        logger.debug(err,exc_info=True)
        directory_summary['errors'] += 1

    if args.recursive:
        for thing in os.listdir(path):
            thing = os.path.join(path, thing)
            if os.path.isdir(thing):
                contents += process_one_dir(thing, directory_summary)

    return contents



def main():
    global args
    global logger

    # parse arguments
    parser = argparse.ArgumentParser(description='Create one large inventory from small ones which are distributed in the various image directories.')
    parser.add_argument('--recursive', help='crawl all subpaths inside the given paths', action="store_true")
    parser.add_argument('--merged-inventory', metavar='NAME', help='a file to write the merged inventory to (default: %(default)s)', default="merged-inventory.json")
    parser.add_argument('--path-trim', metavar='PATH', help='a component to remove from the front of paths written to the merged inventory')
    parser.add_argument('--inventory-file-name', metavar='NAME', help='the name of the inventory file to look for in each directory (default: %(default)s)', default="inventory.json")
    parser.add_argument('--log', metavar='PATH', help='base log file name (default: %(default)s)', default="merged_inventories.log")
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

        directory_summary = {'count': 0, 'inventories': [], 'errors': 0}
        merged_inventory = []
        for d in args.directories:
            merged_inventory += process_one_dir(d, directory_summary)
        logger.info("Found %d inventory files containing %d records." % (directory_summary['count'],len(merged_inventory)))
        logger.info("Encountered %d errors loading inventories." % directory_summary['errors'])

        write_json(args.merged_inventory, sorted(merged_inventory))

    except Exception as err:
        logger.error("Exception " + str(type(err)) + " while working.", exc_info=True)
        exit(-2)



if __name__ == '__main__':
    main()
    logger.info("Script has reached end.") # support being called as a script
else:
    logger = logging.getLogger(__name__)   # support being called as a library