# -*- encoding: UTF-8 -*-

'''
Handle certain situations where images are duplicated under different names.
'''

import os
import re
import datetime
import argparse
import glob
import logging
from collections import defaultdict
from inventory import setup_logger, directory_inventory, all_media_files, elapsed_since, format_bytes, format_elapsed_seconds



# globals
logger = None
args = None



def process_one_dir(path, filter_summary):
    """
    Return an inventory for the given path.
    """

    def our_filter_func(name, countas=1):
        if name.startswith(".") or name == args.inventory_file_name:
            return False
        _, ext = os.path.splitext(name.lower())
        if not (args.also_non_image_files or ext in all_media_files):
            logger.debug("Filter reject: %s" % name)
            filter_summary['rejected'][ext or '[no ext]'] += countas
            if not ext: logger.debug("File without a name extension.")
            return False
        filter_summary['passed'][ext] += countas
        return True

    start_time = datetime.datetime.now()

    logger.info("Processing path '%s'." % path)
    inventory = directory_inventory(
        path,
        remove_directory = False,
        recursive        = args.recursive,
        filter_func      = our_filter_func,
        escape_non_ascii = False,
        parallel         = (not args.single_thread)
    )

    et = elapsed_since(start_time)
    if et > 2:
        etstr = format_elapsed_seconds(et)
        size = 0
        for _, file_size, _ in inventory:
            size += file_size
        szstr = format_bytes(size)
        logger.info("Spent %s on %s of files (%0.01f MB/sec)." % (etstr,szstr,(size/(1024.0*1024.0*et))))

    return inventory



def filename_score(path):
    """
    Determine how interesting the given file name is.  (There are sometimes versions with merely different caps.)
    """

    _, path = os.path.split(path)
    if re.match(r"^(img|mvi)_\d+\.(jpg|avi)$",path): return 1
    if re.match(r"^(IMG|MVI)_\d+\.(JPG|AVI)$",path): return 2
    if re.match(r"^(IMG|MVI)_\d+\.(jpg|avi)$",path): return 3
    return 4



def find_dupes(inventory):
    """
    Example the mega-inventory and find duplicates within it.
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
            logger.info("Overlapping paths (with unclear best choice):")
        for idx,p in enumerate(paths):
            logger.info("  %s (score %d)" % (p,scores[idx]))
            if scores[idx] < maxscore and count == 1:
                to_delete.append(p)

    # we can calculate how many files we expect to be deleted to keep things safe
    if dupes:
        logger.info("Files per id: %s" % ",".join(str(len(x)) for x in dupes.values()))
        delete_count = sum([len(x)-1 for x in dupes.values()])
        logger.info("We should delete %d paths." % delete_count)
        logger.info("The length of the delete list is %d items." % len(to_delete))
        assert delete_count == len(to_delete), "We don't want to delete the wrong number of things."
    else:
        logger.info("No dupes to report on.")

    # write out the list of commands
    with open(args.delete_command_file,"wb") as fh:
        for line in to_delete:
            if re.match(r"^[\w /:.-]+$",line):     # don't suggest running suspicious commands
                fh.write(("rm -f \""+line+"\"\n").encode("utf-8"))
            else:
                logger.warning("Do not clean up strange file name '%s'." % line)



def main():
    global args
    global logger

    # parse arguments
    parser = argparse.ArgumentParser(description='Examine the files in the given directories and print commands that could clean up duplicates.')
    parser.add_argument('--recursive', help='crawl all subpaths inside the given paths', action="store_true")
    parser.add_argument('--also-non-image-files', help='include almost any file in the inventory (by default, only common image formats are included)', action="store_true")
    parser.add_argument('--single-thread', help='process using only one thread (by default, uses one thread per CPU thread, up to 4)', action="store_true")
    parser.add_argument('--delete-command-file', metavar='NAME', help='a file to write a list of delete commands to (default: %(default)s)', default="deleteme.txt")
    parser.add_argument('--inventory-file-name', metavar='NAME', help='the name of the inventory file, in this script used only as a file to ignore when building inventories (default: %(default)s)', default="inventory.json")
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

        filter_summary = {'rejected': defaultdict(lambda: 0), 'passed': defaultdict(lambda: 0)}
        inventory = []
        for d in args.directories:
            inventory += process_one_dir(d, filter_summary)
        find_dupes(inventory)

        if filter_summary['passed']:
            logger.info("Files accepted by filter:")
            for kv in sorted(filter_summary['passed'].items(), key=lambda x: (x[1],x[0]), reverse=True):
                logger.info("  %s: %s" % kv)
        else:
            logger.info("No files processed.")

        if filter_summary['rejected']:
            logger.info("Files filtered:")
            for kv in sorted(filter_summary['rejected'].items(), key=lambda x: (x[1],x[0]), reverse=True):
                logger.info("  %s: %s" % kv)
        else:
            logger.info("No files filtered.")

    except Exception as err:
        logger.error("Exception " + str(type(err)) + " while working.", exc_info=True)
        exit(-2)



if __name__ == '__main__':
    main()
    logger.info("Script has reached end.") # support being called as a script
else:
    logger = logging.getLogger(__name__)   # support being called as a library