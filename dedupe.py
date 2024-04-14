# -*- encoding: UTF-8 -*-

'''
Help cleanup (or understanding) in situations where images are duplicated under different names.

This relies on the per-directory inventory files created by inventory.py (so run that first).
'''

import os
import re
import argparse
import glob
import logging
from collections import defaultdict

# import things from other scripts
from inventory import setup_logger, load_json



# globals
logger = None
args = None



class PathNoGood(Exception):
    pass



def process_one_dir(path, directory_summary):
    """
    Return inventory contents for the given path, if there is an inventory file.
    """

    logger.info("Process path: %s" % path)

    invpath = os.path.join(path,args.inventory_file_name)
    try:
        if os.path.exists(invpath) and os.path.isfile(invpath):
            contents = load_json(invpath)
            for idx in range(len(contents)):
                name,size,checksum = contents[idx]
                name = os.path.join(path,name)           # enhance the file paths with the directory
                contents[idx] = name,size,checksum
            directory_summary['count'] += 1
            directory_summary['inventories'].append(path)
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



def filename_score(path):
    """
    Determine how interesting the given file name is.  (There are sometimes versions with merely different caps.)
    """

    dir, path = os.path.split(path)
    if args.bad_dirs and dir in args.bad_dirs: return 0
    if re.match(r"^(img|mvi)_\d+\.(jpg|avi)$",path): return 1
    if re.match(r"^(IMG|MVI)_\d+\.(JPG|AVI)$",path): return 2
    if re.match(r"^(IMG|MVI)_\d+\.(jpg|avi)$",path): return 3
    return 4



def find_dupes(inventory):
    """
    Examine the merged inventories (one or more lists concatinated into one) and find duplicates within it.
    """

    logger.info("Have %d files in the merged inventory." % len(inventory))
    inventory = sorted(set(tuple(x) for x in inventory))
    to_delete = []

    keys_to_names = defaultdict(lambda: [])
    for path,size,checksum in inventory:
        key = (size,checksum)
        keys_to_names[key].append(path)

    dupes = dict((k,v) for k,v in keys_to_names.items() if len(v) > 1)
    logger.info("Have %d distinct files, %d duped files." % (len(keys_to_names),len(dupes)))

    for key,paths in dupes.items():
        scores = [filename_score(p) for p in paths]
        maxscore = max(scores)
        count = sum([1 for x in scores if x == maxscore])
        if count == 1:
            logger.info("Overlapping paths (with one superior choice):")
        else:
            logger.warning("Overlapping paths (with unclear best choice):")
        for idx,p in enumerate(paths):
            logger.info("  %s (score %d)" % (p,scores[idx]))
            if scores[idx] < maxscore:
                to_delete.append(p)

    # we can calculate how many files we expect to be deleted to keep things safe
    if dupes:
        logger.info("Files per id: %s" % ",".join(str(len(x)) for x in dupes.values()))
        delete_count = sum([len(x)-1 for x in dupes.values()])      # leave one file per dupe list
        logger.info("We should delete %d paths." % delete_count)
        logger.info("The length of the delete list is %d items." % len(to_delete))
        if delete_count != len(to_delete):
            logger.error("The number of files to delete does not pass our sanity check.")
            logger.info("One cause of such a mismatch can be duplicates without one clear file to keep.")
            return
    else:
        logger.info("No dupes to report on.")

    # write out the list of commands
    with open(args.delete_command_file,"wb") as fh:
        for path in to_delete:
            if re.match(r"^[\w /:.-]+$",path):     # don't suggest running suspicious commands
                if os.path.isfile(path):
                    fh.write(('rm -f "%s"\n' % path).encode("utf-8"))
                else:
                    raise PathNoGood("File does not exist: %s" % path)
            else:
                raise PathNoGood("Do not clean up strange file name: %s" % path)



def main():
    global args
    global logger

    def csv(v):
        return [x.rstrip('/') for x in v.split(',')]

    # parse arguments
    parser = argparse.ArgumentParser(description='Examine existing inventory files, find duplicates inside them, and output a list of commands to remove duplicates.')
    parser.add_argument('--recursive', help='crawl all subpaths inside the given paths', action="store_true")
    parser.add_argument('--bad-dirs', metavar='CSV', type=csv, help='one or more directories where duplicate files are considered low priority')
    parser.add_argument('--delete-command-file', metavar='NAME', help='a file to write a list of delete commands to (default: %(default)s)', default="deleteme.txt")
    parser.add_argument('--inventory-file-name', metavar='NAME', help='the name of the inventory file to look for in each directory (default: %(default)s)', default="inventory.json")
    parser.add_argument('--log', metavar='PATH', help='base log file name (default: %(default)s)', default="dedupe.log")
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

        try:
            find_dupes(merged_inventory)
        except PathNoGood as err:
            logger.error(err)
            logger.error("The program can not continue.")
            exit(-1)

    except Exception as err:
        logger.error("Exception " + str(type(err)) + " while working.", exc_info=True)
        exit(-2)



if __name__ == '__main__':
    main()
    logger.info("Script has reached end.") # support being called as a script
else:
    logger = logging.getLogger(__name__)   # support being called as a library