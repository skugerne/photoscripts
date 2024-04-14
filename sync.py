# -*- encoding: UTF-8 -*-

'''
Write out the commands that would be needed to sync the two inventories ("cp" and/or "rm" commands in one or both directions).

The inventories must contain paths suitable for the commands.  Probably they will have been generated by merge_inventories.py.
'''

import os
import argparse
import logging
from inventory import setup_logger, load_json



# globals
logger = None
args = None



class DoesNotExist(Exception):
    pass

class DoesExist(Exception):
    pass



def overlap(s1,s2):
    idx = 0
    while idx < len(s1) and idx < len(s2):
        if s1[idx] != s2[idx]:
            break
        idx += 1

    assert s1[:idx] == s2[:idx], "Programmer error."
    return s1[:idx]



def get_root_dir(inv):
    """
    Find the shared root of all paths in the given inventory.
    """

    common = inv[0][0]
    for path,_,_ in inv[1:]:
        common = overlap(common,path)

    if not common.endswith("/"):
        bits = common.split("/")
        common = "/".join(bits[:-2])
    assert common == "" or common.endswith("/"), "The shared component was expected to be empty, or end on a / (directory separator)."
    return common



def do_work(inv1,inv2):
    """
    Compare the directories and return a list of commands to clean things up.
    """

    commands = []
    dirsmade = set()

    inv1 = sorted(inv1)
    inv2 = sorted(inv2)
    root1 = get_root_dir(inv1)
    root2 = get_root_dir(inv2)

    logger.info("The first location has shared root: %s" % root1)
    logger.info("The second location has shared root: %s" % root2)

    first_second_copy_sizes = []
    second_first_copy_sizes = []
    first_delete_sizes = []
    second_delete_sizes = []

    def cpcmd(fra, til, overwrite=False):
        logger.debug("Copy: %s --> %s" % (fra,til))
        if not os.path.isfile(fra):
            raise DoesNotExist("Copy source does not exist: %s" % fra)
        if os.path.exists(til) and not overwrite:
            raise DoesExist("Copy source exists already: %s" % til)
        d,_ = os.path.split(til)
        if not (d in dirsmade or os.path.exists(d)):
            logger.info("Make dir: %s" % d)
            dirsmade.add(d)
            commands.append('mkdir -p "%s"' % d)
        commands.append('cp "%s" "%s"' % (fra,til))

    def rmcmd(path):
        logger.debug("Remove: %s" % path)
        if not os.path.isfile(path):
            raise DoesNotExist("Copy target does not exist: %s" % path)
        commands.append('rm "%s"' % path)

    def first_missing(path2,path2b,size):
        logger.info("The first location is missing: %s" % path2b)
        if (args.second_to_first or args.cp_both_ways) and not args.only_rm:
            cpcmd(path2, root1+path2b)
            second_first_copy_sizes.append(size)
        elif args.first_to_second and not args.only_cp:
            rmcmd(path2)
            second_delete_sizes.append(size)

    def second_missing(path1,path1b,size):
        logger.info("The second location is missing: %s" % path1b)
        if (args.first_to_second or args.cp_both_ways) and not args.only_rm:
            cpcmd(path1, root2+path1b)
            first_second_copy_sizes.append(size)
        elif args.second_to_first and not args.only_cp:
            rmcmd(path1)
            first_delete_sizes.append(size)

    idx1 = 0
    idx2 = 0
    while idx1 < len(inv1) and idx2 < len(inv2):
        path1,size1,checksum1 = inv1[idx1]
        path2,size2,checksum2 = inv2[idx2]

        path1b = path1[len(root1):]
        path2b = path2[len(root2):]
        if path1b == path2b:
            if size1 == size2 and checksum1 == checksum2:
                logger.debug("Approved: %s" % path1b)
            else:
                logger.warning("Files with matching names have different size and/or checksum!")
                if args.overwrite_on_conflict and not args.only_rm:
                    if args.first_to_second:
                        cpcmd(path1, path2, overwrite=True)
                        first_second_copy_sizes.append(size1)
                    elif args.second_to_first:
                        cpcmd(path2, path1, overwrite=True)
                        second_first_copy_sizes.append(size2)
            idx1 += 1
            idx2 += 1
        elif path1b < path2b:
            second_missing(path1,path1b,size1)
            idx1 += 1
        else:
            first_missing(path2,path2b,size2)
            idx2 += 1

    while idx1 < len(inv1):
        path1,size1,checksum1 = inv1[idx1]
        path1b = path1[len(root1):]
        second_missing(path1,path1b)
        idx1 += 1

    while idx2 < len(inv2):
        path2,size2,checksum2 = inv2[idx2]
        path2b = path2[len(root2):]
        second_missing(path2,path2b)
        idx2 += 1

    logger.info("Would cp %d files worth %d bytes from first to second." % (len(first_second_copy_sizes),sum(first_second_copy_sizes)))
    logger.info("Would cp %d files worth %d bytes from second to first." % (len(second_first_copy_sizes),sum(second_first_copy_sizes)))
    logger.info("Would rm %d files worth %d bytes from first." % (len(first_delete_sizes),sum(first_delete_sizes)))
    logger.info("Would rm %d files worth %d bytes from second." % (len(second_delete_sizes),sum(second_delete_sizes)))

    return commands



def main():
    global args
    global logger

    # parse arguments
    parser = argparse.ArgumentParser(description='Write out the commands that would be needed to sync the two inventories.  No commands are executed.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--first-to-second', help='output commands (cp and/or rm) to make side #2 look like side #1', action="store_true")
    group.add_argument('--second-to-first', help='output commands (cp and/or rm) to make side #1 look like side #2', action="store_true")
    group.add_argument('--cp-both-ways', help='output commands to copy files to whichever side is missing them', action="store_true")
    parser.add_argument('--only-cp', help='only output cp commands', action="store_true")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument('--only-rm', help='only output rm commands (not compatible with --cp-both-ways)', action="store_true")
    group.add_argument('--overwrite-on-conflict', help='when the same file exists on both sides but with different properties, overwrite one with the other as directed by other parameters', action="store_true")
    parser.add_argument('--sync-command-file', metavar='NAME', help='a file to write a list of cp and rm commands to (default: %(default)s)', default="syncme.txt")
    parser.add_argument('--log', metavar='PATH', help='base log file name (default: %(default)s)', default="sync.log")
    parser.add_argument('inv1', metavar='INV', help='an inventory file to process')
    parser.add_argument('inv2', metavar='INV', help='an inventory file to process')
    args = parser.parse_args()

    # set up a standard logger object
    logger = setup_logger(args.log, console_level=logging.INFO)

    try:
        if args.cp_both_ways and (args.only_rm or args.overwrite_on_conflict):
            logger.error("Cannot combine --cp-both-ways and --only-rm or -overwrite-on-conflict.")
            logger.error("The program can not continue.")
            exit(-1)

        inv1 = load_json(args.inv1)
        inv2 = load_json(args.inv2)
        if not (inv1 and inv2):
            logger.error("Both inventory files must contain something.")
            logger.error("The program can not continue.")
            exit(-1)

        try:
            comlist = do_work(inv1,inv2)
        except (DoesExist,DoesNotExist) as err:
            logger.error(err)
            logger.info("(Maybe your inventories don't have global paths, or are incomplete.)")
            logger.error("The program can not continue.")
            exit(-1)

        logger.info("Have %d commands to write out." % len(comlist))
        with open(args.sync_command_file,"wb") as fh:
            for com in comlist:
                fh.write(com.encode('utf8')+b"\n")

        logger.info("Commands written to: %s" % args.sync_command_file)

    except Exception as err:
        logger.error("Exception " + str(type(err)) + " while working.", exc_info=True)
        exit(-2)



if __name__ == '__main__':
    main()
    logger.info("Script has reached end.") # support being called as a script
else:
    logger = logging.getLogger(__name__)   # support being called as a library