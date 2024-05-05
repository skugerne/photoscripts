# -*- encoding: UTF-8 -*-

'''
Create one large inventory from small ones which are distributed in the various image directories.

This script is therefore intended to be run after inventory.py has gone around making the per-directory inventory files.
'''

import os
import argparse
import glob
import logging
import re
from datetime import datetime

# import from our other scripts
from inventory import setup_logger, load_json, write_json



# globals
logger = None
args = None
current_year = datetime.now().year



def parse_date_str(datestr):
    """
    Parse a date string into a tuple.  Currently ignores timezone.  Rejects obviously incorrect values.
    """

    if not datestr:
        return None

    # ex: b'2005-06-27T09:56:05-04:00'
    # ex: b'2006:05:22 19:17:28\x00'
    m = re.match(rb"^(\d\d\d\d)[:-](\d\d)[:-](\d\d)[T ](\d\d):(\d\d):(\d\d)Z?(\000|[+-]\d\d:\d\d)?$",datestr)
    if not m:
        logger.warning("Failed to parse: %s" % datestr)
        return None

    res = tuple(int(m.group(x+1)) for x in range(6))
    bounds = ((1998,current_year), (1,12), (1,31), (0,24), (0,59), (0,59))    # anything before the year 1998 is considered obviously incorrect
    for idx in range(6):
        if res[idx] < bounds[idx][0] or res[idx] > bounds[idx][1]:
            return None   # ignore obviously incorrect dates

    return res



def parse_dim_str(dimstr):
    """
    Convert size string to a tuple.
    """

    if not dimstr:
        return None

    m = re.match(r"^(\d+)x(\d+)$",dimstr)
    if not m:
        raise ValueError("Unable to parse dimentions string.")
    return int(m.group(0)), int(m.group(1))



class InventoryItem():
    """
    Class to parse an inventory entry for a file, for cases where fancy code is called for.
    """

    def __init__(self, bits):
        self.name = bits[0]                       # with or without path
        self.size = bits[1]                       # bytes, int
        self.checksum = bits[2]                   # checksum, by default sha256 hex digest in lower case
        if len(bits) == 5:
            self.date = parse_date_str(bits[3])   # "2000-01-01 01:01:01" or a limited number of variations on that
            self.dims = parse_dim_str(bits[4])    # "WxH"
        else:
            self.date = None
            self.dims = None

    def as_tuple(self,):
        """
        Return a 3- or 5-element tuple (name, size, checksum) optionally with (date, dims)
        """
        if self.date and self.dims:
            dt = "%04d-%02d-%02d %02d:%02d:%02d" % self.date
            sz = "%dx%d" % self.dims
            return (self.name, self.size, self.checksum, dt, sz)
        return (self.name, self.size, self.checksum)

    def __lt__(self, other):
        return (self.name, self.size, self.checksum) < (other.name, other.size, other.checksum)

    def __le__(self, other):
        return (self.name, self.size, self.checksum) <= (other.name, other.size, other.checksum)

    def __eq__(self, other):
        return (self.name, self.size, self.checksum) == (other.name, other.size, other.checksum)

    def __ne__(self, other):
        return (self.name, self.size, self.checksum) != (other.name, other.size, other.checksum)

    def __gt__(self, other):
        return (self.name, self.size, self.checksum) > (other.name, other.size, other.checksum)

    def __ge__(self, other):
        return (self.name, self.size, self.checksum) >= (other.name, other.size, other.checksum)



def process_one_dir(path, directory_summary):
    """
    Return inventory contents for the given path, if there is an inventory file.
    """

    logger.info("Process path: %s" % path)

    contents = []
    invpath = os.path.join(path,args.inventory_file_name)
    try:
        if os.path.exists(invpath) and os.path.isfile(invpath):
            directory_summary['count'] += 1
            raw_contents = load_json(invpath)

            for item in raw_contents:
                item = InventoryItem(item)

                # name ending filters
                if args.filter_include_name_endings:
                    if not any([item.name.lower().endswith(e) for e in args.filter_include_name_endings]):
                        continue
                if args.filter_exclude_name_endings:
                    if any([item.name.lower().endswith(e) for e in args.filter_exclude_name_endings]):
                        continue

                # dimention filters (rejects any inventory entry that lacks this property)
                if args.filter_min_dimentions:
                    if (not item.dims) or item.dims[0] < args.filter_min_dimentions[0] or item.dims[1] < args.filter_min_dimentions[1]:
                        continue

                # filter duplicate files (keep the first example of a duplicate which makes it past the other filters)
                if args.filter_dupes:
                    if (item.size, item.checksum) in directory_summary['ids']:
                        continue

                # path adjustment
                item.name = os.path.join(path,item.name)     # enhance the file paths with the directory
                if args.path_trim:                           # optionally remove parts of the path
                    assert item.name.startswith(args.path_trim), "Cannot trim: %s" % item.name
                    item.name = item.name[len(args.path_trim):]

                if args.filter_dupes:
                    directory_summary['ids'].add((item.size, item.checksum))
                contents.append(item.as_tuple())
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

    def csv(v):
        return v.lower().split(',')

    # parse arguments
    parser = argparse.ArgumentParser(description='Create one large inventory from small ones which are distributed in the various image directories.')
    parser.add_argument('--recursive', action="store_true", help='crawl all subpaths inside the given paths')
    parser.add_argument('--merged-inventory', metavar='NAME', help='a file to write the merged inventory to (default: %(default)s)', default="merged-inventory.json")
    parser.add_argument('--path-trim', metavar='PATH', help='a component to remove from the front of paths written to the merged inventory')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--filter-include-name-endings', metavar='CSV', type=csv, help='one or more file name endings (case-insensitive) to include in the result')
    group.add_argument('--filter-exclude-name-endings', metavar='CSV', type=csv, help='one or more file name endings (case-insensitive) to exclude in the result')
    parser.add_argument('--filter-min-dimentions', metavar='HxW', type=parse_dim_str, help='the minimum dimentions an image must have to be included (dimentions are obtained from the inventory file and not present for all file types)')
    parser.add_argument('--filter-dupes', action="store_true", help='include only the first example of any duplicate files found')
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

        directory_summary = {'count': 0, 'errors': 0, 'ids': set()}
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