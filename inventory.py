# -*- encoding: UTF-8 -*-

'''
Make and verify inventory files.
'''

import os
import sys
import shutil
import datetime
import json, re
import logging
from logging.handlers import RotatingFileHandler
import argparse, glob
from collections import defaultdict
from multiprocessing import cpu_count
from multiprocessing.dummy import Pool as ThreadPool
import PIL.Image as Image
import piexif



# globals
logger = None
args = None
filter_summary = {'rejected': defaultdict(lambda: 0), 'passed': defaultdict(lambda: 0)}
all_inventories = dict()
current_year = datetime.datetime.now().year

all_media_files = ('.jpg','.jpeg','.png','.tif','.tiff','.gif','.mp4','.mov','.avi','.wmv','.mpg','.cr2','.mp3')
checkable_image_files = ('.jpg','.jpeg','.png')



def setup_logger(name, path=None, file_level=logging.DEBUG, console_level=logging.WARN, num_old_logs=2, use_pid=False, use_log_subdir=True, log_threadids=False, rotate_mbytes=None, log_time_to_console=False):
    """
    Setup a standard 'root logger' object.

    Returns a logging.Logger object.

    name:                   the log file name (can contain a path component), ex: log.txt or logs/log.log
    path:                   the path to write the log file at (optional)
    file_level:             the logging level for file output
    console_level:          the logging level for console output
    num_old_logs:           keep a number of old logs around (when autoRotateBytes is False-y, these are logs from previous runs)
    use_pid:                when True-ey, add the PID to the log file name (useful when multiple scripts might run the same place at the same time)
    use_log_subdir:         when True-ey, if the log file name has no path component, and there is a log directory present, so we'll use it
    log_threadids:          when True-ey, include thread IDs in messages written to the log file
    rotate_mbytes:          when True-ey, the log file should be rotated at the given size
    log_time_to_console:    When True-ey, include time in console output
    """

    class BotoOnlyFilter(logging.Filter):
        """
        A filter to collect everything that seems to be coming from boto.
        """

        def filter(self, record):
            return record.name.startswith("boto") or record.name.startswith("s3transfer")

    class BotoSpamFilter(logging.Filter):
        """
        A filter to squash boto3 logging spam while allowing it to arrive in a detailed boto log file.
        """

        def filter(self, record):
            if not (record.name.startswith("boto") or record.name.startswith("s3transfer")):
                return True
            # Using GdoUtility/gp.py in a GP-tool required this "extra import" statement
            import logging
            return record.levelno > logging.INFO

    if path:
        # a path has been given, this will be added to the name
        name = os.path.join(path,name)
    elif use_log_subdir:
        path, _ = os.path.split(name)
        if not path and os.path.exists('log') and os.path.isdir('log'):
            # the log file name has no path component, and there is a log directory present, so we'll default to using it
            name = os.path.join('log',name)

    path, filename = os.path.split(name)
    if use_pid:
        pid = os.getpid()
        filename, filext = os.path.splitext(filename)
        name = os.path.join(path, '%s.pid%s%s' % (filename,pid,filext))
        boto_log_name = os.path.join(path, 'boto.pid%s.log' % pid)
    else:
        boto_log_name = os.path.join(path, 'boto.log')
    if name == boto_log_name:
        raise Exception("The name 'boto.log' is reserved.")

    # keep a number of old log files (naming convention file.log, file.log.1, file.log.2 etc)
    for target in [name,boto_log_name]:
        idx = num_old_logs
        while idx > 0:
            log1 = target if idx == 1 else target + "." + str(idx-1)
            log2 = target + "." + str(idx)
            if os.path.exists(log1):
                shutil.move(log1, log2)
            idx -= 1

    # create logger
    newLogger = logging.getLogger()
    newLogger.setLevel(logging.DEBUG)

    # create log file handler (immitate the console log format)
    if rotate_mbytes:
        print("GdoUtility  : INFO     Use log '%s' (rotating log)." % name)
        file_handler = RotatingFileHandler(name, mode='w', encoding="UTF-8", maxBytes=(rotate_mbytes*1024*1024), backupCount=num_old_logs)
    else:
        print("GdoUtility  : INFO     Use log '%s'." % name)
        file_handler = logging.FileHandler(name, mode='w', encoding="UTF-8")
    file_handler.setLevel(file_level)
    file_handler.addFilter(BotoSpamFilter())

    # set up log file formatter
    if log_threadids:
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(thread)s - %(message)s')
    else:
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    # create a handler for a log that contains everything boto
    boto_handler = RotatingFileHandler(boto_log_name, mode='w', encoding="UTF-8", maxBytes=(min(rotate_mbytes or 64,64)*1024*1024), backupCount=num_old_logs, delay=True)
    boto_handler.setLevel(1)
    boto_handler.addFilter(BotoOnlyFilter())
    boto_handler.setFormatter(formatter)

    # create console handler
    try:
        console_handler = logging.StreamHandler(sys.stdout)
    except TypeError:
        print("TypeError while setting up console handler.")
        console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.addFilter(BotoSpamFilter())

    # set up console formatter
    if log_time_to_console:
        formatter = logging.Formatter('%(asctime)s - %(name)-12s: %(levelname)-8s %(message)s')
    else:
        formatter = logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s')
    console_handler.setFormatter(formatter)

    # add the handlers to the logger
    newLogger.addHandler(file_handler)
    newLogger.addHandler(boto_handler)
    newLogger.addHandler(console_handler)

    return newLogger



def running_on_windows():
    """
    Determine if the current script appears to be running on Windows.
    """

    return sys.platform.startswith("win")



def isclose(a, b, rel_tol=1e-09, abs_tol=0.0):
    """
    Determine if two floating point values are approximately equal (from python 3.5).
    """

    return abs(a-b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)



def similarfloats(a, b, rel_tol=1e-09, abs_tol=0.0):
    """
    Determine if two objects are both non-None and approximately equal floating point values.
    """

    if a is None or b is None:
        return False
    return isclose(a, b, rel_tol, abs_tol)



def file_lister(in_path, file_list, recursive=False, join_func=os.path.join, ignore_files=None, filter_func=None, filter_func_full=None, prefix="", max_files=None):
    """
    Make a list of files, including their paths.

    in_path:           path to search for files
    file_list:         list to add files to (file names and sub-paths are appended to in_path)
    recursive:         when True-ey, descend into sub-directories
    join_func:         specify a special function (callback) to build paths, defaults to os.path.join()
    ignore_files:      when specified, is a list of files or directories to be excluded from the result (applied also in subdirectories)
    filter_func:       when specified, must return a True-ey value for any acceptable file name to be included (path is not included, called once per file on all files)
    filter_func_full:  when specified, must return a True-ey value for any acceptable file name to be included (path is included, called once per file on all files)
    prefix:            only list files or the contents of directories which start with the given prefix and are in the root (at 'in_path')
    max_files:         only return the given number of files
    """

    for a_file in os.listdir(in_path):
        if max_files and len(file_list) >= max_files:
            break
        if (ignore_files and a_file in ignore_files) or (prefix and not a_file.startswith(prefix)):
            logger.debug("In file_lister(): ignoring path: '%s'." % a_file)
            continue
        full_file = join_func(in_path, a_file)
        if os.path.isdir(full_file):
            if recursive:
                file_lister(full_file, file_list, recursive=True, ignore_files=ignore_files, filter_func=filter_func, prefix="", max_files=max_files)
        elif ((not filter_func) or filter_func(a_file)) and ((not filter_func_full) or filter_func_full(full_file)):
            file_list.append(full_file)



def is_unicode(v):
    """
    Return True if the given value is native unicode, False otherwise.
    """
    return bool(sys.version_info >= (3,0,0) and type(v) == str) or (sys.version_info < (3,0,0) and type(v) == unicode)  # pylint: disable=undefined-variable



def is_bytes(v):
    """
    Return True if the given value is native bytes, False otherwise.
    """
    return bool(sys.version_info >= (3,0,0) and type(v) == bytes) or (sys.version_info < (3,0,0) and type(v) == str)  # pylint: disable=undefined-variable



def parse_date_str(datestr):
    """
    Parse a date string into a tuple.  Currently ignores timezone.  Rejects obviously incorrect values.
    """

    if not datestr:
        return None

    # we operate on binary since sometimes we want to use this on binary strings that, apparently, contain a null byte
    if is_unicode(datestr):
        datestr = datestr.encode('ascii')

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



def format_date_tuple(datetuple):
    """
    Convert a date tuple to our standard string format.
    """
    return "%04d-%02d-%02d %02d:%02d:%02d" % datetuple



def parse_dim_str(dimstr):
    """
    Convert dimention string to a tuple.
    """

    if not dimstr:
        return None

    m = re.match(r"^(\d+)x(\d+)$",dimstr)
    if not m:
        raise ValueError("Unable to parse dimentions string.")
    return int(m.group(1)), int(m.group(2))



def format_dim_tuple(dimtuple):
    """
    Convert a dimention tuple to our standard string format.
    """
    return "%dx%d" % dimtuple



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

    def as_tuple(self):
        """
        Return a 3- or 5-element tuple (name,size,checksum[,date,dims]).
        """

        if self.date and self.dims:
            dt = format_date_tuple(self.date)
            sz = format_dim_tuple(self.dims)
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

    def __hash__(self):
        return hash((self.name, self.size, self.checksum))



def make_file_id(path, calculate_checksums=True):
    """
    For the given file, return a tuple of its size and checksum.  The checksum is a hex-encoded string (does not begin with '0x').

    path:                 the path to the file
    calculate_checksums:  when True-ey, calculate a checksum, by default sha256, with "crc32" and "md5" as valid alternatives
    """

    import hashlib, zlib

    if not os.path.exists(path):
        raise Exception("Path '%s' does not exist." % path)
    if not os.path.isfile(path):
        raise Exception("Path '%s' is not a file." % path)

    size = os.stat(path).st_size
    if not calculate_checksums:
        return (size,0)

    blocksize = 32 * 1024 * 1024
    with open(path, 'rb') as fh:
        buf = fh.read(blocksize)
        if calculate_checksums and calculate_checksums is not True and calculate_checksums.lower() == 'crc32':

            val = 0
            while len(buf) > 0:
                val = zlib.crc32(buf, val)
                buf = fh.read(blocksize)
            checksum = "%08x" % (val & 0xffffffff)   # example result: 'fa6d8142'

        else:
            if calculate_checksums and calculate_checksums is not True and calculate_checksums.lower() == 'md5':
                hasher = hashlib.md5()               # example result: 'b9fe6231c1831dca0089efd740454f2c'
            else:
                hasher = hashlib.sha256()            # example result: '8005342b30e0743d73d78429a8da79678ae4f8827a688a9e8d0195ab44adaef0'

            while len(buf) > 0:
                hasher.update(buf)
                buf = fh.read(blocksize)
            checksum = hasher.hexdigest()

    assert is_unicode(checksum), "The checksum should be unicode."
    return (size,checksum)



def obtain_image_info(path):
    """
    For jpg and png files, open them to check for validity, to look for exif dates, and to determine dimentions.

    Return (date as str, dims as str), one or both of which can be None.
    """

    with Image.open(path) as imgobj:
        if 'exif' in imgobj.info:
            try:
                exif_dict = piexif.load(imgobj.info['exif'])
            except Exception as err:
                logger.warning("Failed to read exif: %s" % path)
                logger.debug(err, exc_info=True)
                dt = None
            else:
                try:
                    # the piexif lib gives us what it can find in the exif data
                    dt = parse_date_str(exif_dict["0th"].get(piexif.ImageIFD.DateTime))
                    dt = dt or parse_date_str(exif_dict["Exif"].get(piexif.ExifIFD.DateTimeOriginal))
                    dt = dt or parse_date_str(exif_dict["Exif"].get(piexif.ExifIFD.DateTimeDigitized))
                    dt = dt or None
                    if dt:
                        dt = format_date_tuple(dt)
                except KeyError:
                    logger.warning("Failed to read exif (missing key): %s" % path)
                    dt = None
        else:
            logger.debug("Did not find exif data: %s" % path)
            dt = None

        # pillow will give us the actual observed image size
        sz = format_dim_tuple(imgobj.size) if imgobj.size[0] and imgobj.size[1] else None

        return dt,sz



def directory_inventory(directory, remove_directory=None, recursive=True, ignore_files=None, filter_func=None, calculate_checksums=True, escape_non_ascii=True, parallel=True):
    """
    Produce a list of tuples (name,size,checksum[,date,dims]) for the files in the given directory.  Name is native unicode.

    directory:            the directory to process
    remove_directory:     the given directory name is removed from the start of the paths stored in the tuples, when True the entire root directory is removed
    recursive:            also inventory subdirectories
    ignore_files:         a list() of files or directories to be excluded from the inventory check (possibly the inventory file itself), considered independent of path
    filter_func:          when specified, must return a True-ey value for any acceptable file name to be included (path is not included)
    calculate_checksums:  when True-ey, calculate a checksum for each file, by default sha256, with "crc32" and "md5" as valid alternatives
    escape_non_ascii:     when True-ey, replace various characters in the filenames with a (0xFF) notation, note does not escape existing (0xFF) in filenames
    """

    # remove all directory components except the last
    # note that other True-ey values are not handled here
    if remove_directory is True:
        remove_directory = directory

    logger.debug("Build inventory (recursive=%s) for directory: %s" % (recursive,directory))

    files = list()
    file_lister(directory, files, recursive=recursive, ignore_files=ignore_files, filter_func=filter_func)
    if len(files) > 1 and cpu_count() > 1 and parallel:
        pool = ThreadPool(min(4,cpu_count()))   # generally python does best with a small number of threads
    else:
        pool = None

    inventory_items = list()
    for idx,a_file in enumerate(files):
        def worker(idx,a_file):
            try:
                logger.debug(u"ID file %s of %s: '%s'" % (idx+1,len(files),cleanse_bytes(a_file)))
                size,checksum = make_file_id(a_file, calculate_checksums)

                # get image properties for certain file types
                _, ext = os.path.splitext(a_file.lower())
                more = []
                if ext in checkable_image_files:
                    dt,dim = obtain_image_info(a_file)
                    if dt or dim:
                        more = [dt, dim]

                if remove_directory:
                    if a_file.startswith(remove_directory):
                        a_file = a_file[len(remove_directory):]
                        a_file = a_file.lstrip(os.path.sep)   # if part of the path is removed, what remains can not start with the directory separator
                    else:
                        raise Exception("Unable to remove directory from path.")

                if running_on_windows():
                    a_file = a_file.replace("\\","/")       # we need to standardize the path separator so it works between platforms
                a_file = cleanse_bytes(a_file, non_compliance_long_notation=escape_non_ascii)

                inventory_items.append([a_file, size, checksum] + more)
            except Exception as err:
                logger.error("Exception " + str(type(err)) + " while examining a file.", exc_info=True)
        if pool:
            pool.apply_async(worker, (idx,a_file))
        else:
            worker(idx,a_file)

    if pool:
        pool.close()
        pool.join()

    if len(inventory_items) != len(files):
        raise Exception("It seems that not all worker jobs suceeded (%s vs %s)." % (len(inventory_items),len(files)))

    # sort by the file names to make comparison easy
    inventory_items = sorted(inventory_items)
    return inventory_items



def compare_inventories_inner(inventory1, inventory2, print_limit=5, callback=None):
    """
    Compare two directory inventories, return a tuple (identical, problematic differences).

    inventory1:     a result from directory_inventory(): a sorted list of (name,size,checksum[,date,dims]) tuples for files in a directory
    inventory2:     another object similar to the first
    print_limit:    limit how many differences to print
    callback:       a function to invoke with two tuples (name,size,checksum[,date,dims]), the first from 'inventory1', when a difference is found
    """

    logger.debug("The lengths of the inventories are (old) %s and (new) %s." % (len(inventory1),len(inventory2)))

    try:
        diffs0 = list()   # make a list for each of three different categories of differences
        diffs1 = list()
        diffs2 = list()
        alldiffs = 0

        idx1 = 0
        idx2 = 0
        while idx1 < len(inventory1) and idx2 < len(inventory2):
            if inventory1[idx1][0] == inventory2[idx2][0]:               # the file name is the main thing
                if inventory1[idx1] != inventory2[idx2]:
                    alldiffs += 1                                        # we take a minor note of date/dim differences (plus size/checksum)
                    if inventory1[idx1][0:3] != inventory2[idx2][0:3]:   # we want size & checksum to match (NOTE: list != tuple)
                        if not (callback and callback(inventory1[idx1],inventory2[idx2])):
                            if(inventory1[idx1][1] != inventory2[idx2][1]):
                                diffs0.append("Size mismatch for '%s' (oi=%s, ni=%s)." % (inventory1[idx1][0],idx1,idx2))
                            elif(inventory1[idx1][2] != inventory2[idx2][2]):
                                diffs0.append("Checksum mismatch for '%s' (oi=%s, ni=%s)." % (inventory1[idx1][0],idx1,idx2))
                idx1 += 1
                idx2 += 1
            elif inventory1[idx1][0] > inventory2[idx2][0]:
                alldiffs += 1
                if not (callback and callback(None,inventory2[idx2])):
                    diffs1.append("The new inventory contains an extra file '%s' (oi=%s, ni=%s)." % (inventory2[idx2][0],idx1,idx2))
                idx2 += 1
            else:
                alldiffs += 1
                if not (callback and callback(inventory1[idx1],None)):
                    diffs2.append("The old inventory contains an extra file '%s' (oi=%s, ni=%s)." % (inventory1[idx1][0],idx1,idx2))
                idx1 += 1

        # notice if all of one came before all of the other
        if (idx1 == 0 and idx2 != 0) or (idx1 != 0 and idx2 == 0):
            logger.warning("Perhaps the directory paths for the inventories do not match.")

        # finish the stragglers
        while idx2 < len(inventory2):
            alldiffs += 1
            if not (callback and callback(None,inventory2[idx2])):
                diffs1.append("The new inventory contains an extra file '%s' (oi=%s, ni=%s)." % (inventory2[idx2][0],idx1,idx2))
            idx2 += 1

        # finish the stragglers
        while idx1 < len(inventory1):
            alldiffs += 1
            if not (callback and callback(inventory1[idx1],None)):
                diffs2.append("The old inventory contains an extra file '%s' (oi=%s, ni=%s)." % (inventory1[idx1][0],idx1,idx2))
            idx1 += 1

        # report while limiting spam
        truncated = False
        for diffs in [diffs0,diffs1,diffs2]:
            counter = 0
            for diff in diffs:
                counter += 1
                if counter <= print_limit:
                    logger.warning(diff)
                elif counter > print_limit:   # note that counter == print_limit does nothing
                    truncated = True
                    break

        if truncated:
            logger.error("Not all differences have been logged due to a log-limit %s per type of difference." % print_limit)

        if alldiffs > 0:
            if diffs0 or diffs1 or diffs2:
                logger.error("The two inventories do not match (%s differences)." % alldiffs)
                return False, True
            else:
                logger.info("The two inventories do not match (%s differences) but the changes were OK." % alldiffs)
                return False, False

        logger.info("The two inventories match (checked %s files)." % len(inventory1))
        return True, False
    except (IndexError,KeyError) as err:
        logger.error("Exception " + str(type(err)) + " while comparing manifests.", exc_info=True)

    return False, True



def seconds_until(some_date, utc_date=False):
    """
    Give the number of seconds (as a float) between the current time and the given future datetime.datetime object.

    Returns negative if the given date is in the past.

    some_date: a datetime.datetime object
    utc_date:  when True-ey, the given date is assumed to be in UTC time
    """

    return -1 * elapsed_since(some_date=some_date, utc_date=utc_date)



def elapsed_since(some_date, utc_date=False):
    """
    Give the number of elapsed seconds (as a float) since the given datetime.datetime object.

    Returns negative if the given date is in the future.

    some_date: a datetime.datetime object
    utc_date:  when True-ey, the given date will be compared to the current time in UTC (no effect if the system clock is UTC)
    """

    if utc_date:
        now = datetime.datetime.utcnow()         # if the system clock is UTC, this is the same as datetime.now()
    else:
        now = datetime.datetime.now()

    if some_date.tzinfo:
        from dateutil import tz
        if utc_date:
            now = now.replace(tzinfo=tz.tzutc())           # add a time zone to our current time
        else:
            now = now.replace(tzinfo=tz.tzlocal())         # add a time zone to our current time

    return (now - some_date).total_seconds()



def format_elapsed_seconds(elapsed, positive_ending="", negative_ending="ago"):
    """
    Format a number of elapsed seconds into something a bit nice, like "5 seconds" or "2 weeks".

    elapsed: a number of seconds, probably either int or float
    """

    from numbers import Number
    from math import floor, log

    if elapsed is None:
        return None

    if not isinstance(elapsed, Number):
        logger.debug("Input type %s to format_elapsed_seconds()." % str(type(elapsed)))

    # the output format will depend on the input format
    if type(elapsed) == float:
        if 0 < elapsed < 1:
            # provide 2 sig figures
            formatter = "%0." + ("%02d" % (1 + (-1 * floor(log(elapsed,10.0))))) + "f"
        else:
            # provide one decimal place, note for any reasonable elapsed time the result number isn't large
            formatter = "%.01f"
        roundFunc = float    # harmlessly cast to a float
    else:
        formatter = "%d"
        roundFunc = round    # round to an int

    # handle times in the past
    if elapsed < 0 and negative_ending:
        extra = " " + negative_ending
        elapsed *= -1
    elif elapsed > 0 and positive_ending:
        extra = " " + positive_ending
    else:
        extra = ""

    # always cast as a float to help with rounding off
    elapsed = float(elapsed)

    if elapsed < 90:                     # less than 90 seconds
        return (formatter + " seconds" + extra) % elapsed
    if elapsed < 90 * 60:                # less than 90 minutes
        return (formatter + " minutes" + extra) % roundFunc(elapsed / 60)
    if elapsed < 90 * 60 * 24:           # less than 36 hours
        return (formatter + " hours" + extra) % roundFunc(elapsed / (60 * 60))
    if elapsed < 60 * 60 * 24 * 7 * 2:   # less than two weeks
        return (formatter + " days" + extra) % roundFunc(elapsed / (60 * 60 * 24))
    if elapsed < 60 * 60 * 24 * 30 * 2:  # less than two months
        return (formatter + " weeks" + extra) % roundFunc(elapsed / (60 * 60 * 24 * 7))
    return (formatter + " months" + extra) % roundFunc(elapsed / (60 * 60 * 24 * 30))



def format_bytes(bytes, strict=True):
    """
    Take a number of bytes, and format it into a human-readable unit (based on 1024 bytes = 1 kbyte).
    """

    from numbers import Number

    if not isinstance(bytes, Number):
        logger.debug("Input type %s to format_bytes()." % str(type(bytes)))

    if not bytes:
        if not strict:
            return bytes
        return '0 B'

    suffixes = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']

    bytes = int(bytes)
    if bytes < 0:
        bytes *= -1
        neg = "-"
    else:
        neg = ""

    i = 0
    while bytes >= 3000 and i < len(suffixes)-1:    # the threshold of 3000 is chosen arbitrarily, for readability of result
        bytes /= 1024.
        i += 1
    f = ('%.2f' % bytes).rstrip('0').rstrip('.')
    return '%s%s %s' % (neg, f, suffixes[i])



def cleanse_bytes(some_object, non_compliance_long_notation=False, output_encoding=None, catch_exceptions=False, return_score=False):
    """
    Try to push the given object into a sensible string, were 'sensible' is by default a native python unicode (unicode python2, str python3).

    The result unicode object will also have non-printable, non-ASCII, non-Norsk, non-Saami characters replaced (see non_compliance_long_notation).

    Will throw an exception if the object does not have a string/unicode/bytes representation.

    some_object:                  an input object, likely not a python-native unicode object
    non_compliance_long_notation: when True-ey, replace non-complaint chars with a unicode/str like (0xFF), complete with parenthesis as shown
                                  when False-ey, replace non-complaint chars with unicode 0xFFFD 'REPLACEMENT CHARACTER', in utf-8: 0xEF 0xBF 0xBD (efbfbd)
    output_encoding:              if the result shouldn't be a native python unicode (unicode python2, str python3), give an encoding
    catch_exceptions:             when True-ey, catch all exceptions and return False on error (if you pass a string, there should be no exceptions)
    return_score:                 return the number of input characters were not replaced (for multi-byte encodings, one char can count as multiple)
    """

    # NOTE: this is called by what_am_i() so it should never call that function ...

    # this list of chars is used (1) to eliminate crap we can't use anyway, and (2) pick best-fit encoding if UTF-8 decode of bytes fails
    # even native unicode strings undergo filtering before they are returned
    blessed_unichars = set([ord(x) for x in u"ÆæØøÅåÖöÜü"])

    def printablechars(input_string):
        str_is_unicode = is_unicode(input_string)
        if not (str_is_unicode or is_bytes(input_string)):
            raise ValueError("The given input is not a recognized string type.")

        if str_is_unicode:                 joiner = u""; noncompliant = u"(0x%02X)"
        elif sys.version_info >= (3,0,0):  joiner = b""; noncompliant = b"(0x%02X)"
        else:                              joiner =  ""; noncompliant =  "(0x%02X)"

        score = 0
        res = [c for c in input_string]
        for idx,c in enumerate(input_string):
            c = ord(c)
            if c in (9,10,13) or 31 < c < 127 or (str_is_unicode and c in blessed_unichars):
                score += 1
            elif non_compliance_long_notation or not str_is_unicode:
                res[idx] = noncompliant % c
            else:
                res[idx] = chr(0xFFFD) if sys.version_info >= (3,0,0) else unichr(0xFFFD)  # pylint: disable=undefined-variable
        return joiner.join(res), score

    def inner_func():

        if (sys.version_info < (3,0,0) and type(some_object) == unicode) or (sys.version_info >= (3,0,0) and type(some_object) == str):  # pylint: disable=undefined-variable
            return printablechars(some_object)

        # things which are not a string-bytes-like object need to transform themselves into a string of some kind
        if sys.version_info < (3,0,0) and type(some_object) != str:
            inner_some_object = str(some_object)
            if type(inner_some_object) == unicode:  # pylint: disable=undefined-variable
                return printablechars(inner_some_object)
        elif sys.version_info >= (3,0,0) and type(some_object) != bytes:
            inner_some_object = str(some_object)
            if type(inner_some_object) == str:
                return printablechars(inner_some_object)
        else:
            inner_some_object = some_object

        try:
            # notes on the utf-8-sig codec:
            #   "On encoding a UTF-8 encoded BOM will be prepended to the UTF-8 encoded bytes."
            #   "For decoding an optional UTF-8 encoded BOM at the start of the data will be skipped."
            utf8 = inner_some_object.decode("utf-8-sig")
            return printablechars(utf8)

        except UnicodeDecodeError:
            # cp1252 / latin-1:  used by default in the legacy components of Microsoft Windows in various places, should cover ISO-8859-1
            # cp865:             used under DOS to write Nordic languages, very similar to cp437 (original IBM PC)
            # ISO-8859-10        designed to cover the Nordic languages, including Saami
            results = list()
            for encoding in ("cp1252","cp865","ISO-8859-10"):
                try:
                    results.append(printablechars(inner_some_object.decode(encoding)))  # append string, score
                except UnicodeDecodeError:
                    pass

            if results:
                return sorted(results, key=lambda x: x[1], reverse=True)[0]
            raise Exception("It seems the string could not be decoded by any of the listed encodings, this should be impossible.")

    def final_encode():
        resstr, score = inner_func()
        if output_encoding:
            resstr = resstr.encode(output_encoding)
        if return_score:
            return resstr, score
        return resstr

    if not catch_exceptions:
        return final_encode()
    else:
        try:
            return final_encode()
        except Exception as err:
            logger.debug("Error in cleanse_bytes(): %s" % err)
            if return_score:
                return False, False
            return False



def read_utf8_file(file_path):
    """
    Return all text (one string, unicode) from a file which may or may not be UTF-8 and if so, might have a Byte Order Mark.

    Will replace non-printable, non-ASCII, non-ÆØÅ characters with unicode 0xFFFD 'REPLACEMENT CHARACTER'.
    """

    if not os.path.isfile(file_path):
        raise Exception("The given path '%s' is not a file." % file_path)

    with open(file_path, 'rb') as fh:
        data = fh.read()

    return cleanse_bytes(data)



def load_json(json_file):
    """
    Read a file and interpret it as JSON, strings will be unicode.  Attempt to handle various byte encodings.

    Will replace non-printable, non-ASCII, non-ÆØÅ characters with unicode 0xFFFD 'REPLACEMENT CHARACTER'.
    """

    text = read_utf8_file(json_file)
    try:
        return json.loads(text)
    except Exception:
        logger.info("Got %d unicode characters." % len(text))
        raise



def write_json(out_path, some_object):
    """
    Write an object to a file as JSON, encoded as UTF8 with a BOM.

    If your object contains keys or values which are encoded non-ASCII bytes already, it is likely that you will get an encoding error.
    """

    with open(out_path,'wb') as fh:
        txt = json.dumps(some_object, indent=2, ensure_ascii=False)    # indent: formatted JSON so people can read it
        fh.write(txt.encode('utf-8-sig'))



def read_stdin_yesno(q):
    """
    Prompt the user for a yes/no and return True or False.
    """

    first = True
    while True:
        if first:
            logger.info(q)
        else:
            logger.info("(enter 'y' or 'n' or similar)")
            
        line = sys.stdin.readline().strip()
        if line:
            if line.lower() in ('y','yes'):
                return True
            if line.lower() in ('n','no'):
                return False
        logger.info("line: %s" % line)
        first = False



def is_approved(filename, extlist):
    """
    Determine if the given file should be forgiven for appearing or disappearing.
    """

    if not extlist:
        return False
    _, ext = os.path.splitext(filename.lower())
    return bool(ext in extlist)



def compare_inventories(old_inventory, inventory):
    """
    Compare two inventories, and patch the new one if appropriate.

    Return the a tuple including the return from compare_inventories_inner(), and the new inventory, which may have been revised.

    The reason for patching the new inventory is to allow some changes to be approved, and put focus on not-approved changes.
    """

    to_delete = []
    to_add = []

    if args.patch or args.patch_approve_add or args.patch_approve_remove:
        logger.debug("Patching callback to be used.")
        def comparison_callback(old_tuple, new_tuple):
            if old_tuple and new_tuple:
                # some file has been changed
                assert new_tuple[0] == old_tuple[0], "File names are expected to match in the tuples."
                assert is_unicode(new_tuple[0]) and is_unicode(old_tuple[0]), "File names should be unicode."
                assert is_unicode(new_tuple[2]) and is_unicode(old_tuple[2]), "Checksums should be unicode."
                if args.patch:
                    logger.info("Sizes: old %d vs new %d, Checksums: old %s vs new %s." % (old_tuple[1],new_tuple[1],old_tuple[2],new_tuple[2]))
                    if read_stdin_yesno("Should we update the inventory entry for file: %s" % old_tuple[0]):
                        logger.info("Change accepted; Should leave the entry in the new inventory.")
                        return True
                    else:
                        # force new inventory to contain the old stuff
                        logger.debug("Change rejected; Should replace entry in the new inventory with the old values.")
                        to_delete.append(new_tuple)
                        to_add.append(old_tuple)
            elif old_tuple:
                # some file has gone away
                if is_approved(old_tuple[0], args.patch_approve_remove):
                    return True
                if args.patch:
                    if read_stdin_yesno("Should we remove the inventory entry for file: %s" % old_tuple[0]):
                        logger.info("Change accepted; Should leave entry out of the new inventory.")
                        return True
                    else:
                        # force new inventory to contain the old file
                        logger.debug("Change rejected; Should add entry to new inventory (because it should still be there).")
                        to_add.append(old_tuple)
            else:
                # some file has been added
                if is_approved(new_tuple[0], args.patch_approve_add):
                    return True
                if args.patch:
                    if read_stdin_yesno("Should we add an inventory entry for file: %s" % new_tuple[0]):
                        logger.info("Change accepted; Should leave the entry in the new inventory.")
                        return True
                    else:
                        # force new inventory to not include the new file (just like the old inventory)
                        logger.debug("Change rejected; Should delete entry from the new inventory (because its not supposed to be there).")
                        to_delete.append(new_tuple)
            return False
    else:
        logger.debug("Patching callback not in use.")
        comparison_callback = None

    # compare
    identical_invs, problems = compare_inventories_inner(old_inventory, inventory, callback=comparison_callback)

    # deal with changes approved by the callback
    # these approved changes have already theoretically been stopped from effecting 'problems'
    if to_add or to_delete:
        inventory = inventory[:]   # copy the inventory which was provided

        # add and remove items from the copy as requested by the 'patch'
        for item in to_delete:
            logger.debug("Remove from inventory: %s" % str(item))
            inventory.remove(item)
        for item in to_add:
            logger.debug("Add to inventory: %s" % str(item))
            if item in inventory:
                raise Exception("Trying to add something to the inventory that exists already, must be programmer error.")
            inventory.append(item)
        inventory = sorted(inventory)

    return identical_invs, problems, inventory



def process_one_directory(d):
    """
    Given a directory, create a new inventory for it and compare it to the existing inventory, if such a file exists.

    Return a tuple (happy, message).
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

    try:
        start_time = datetime.datetime.now()

        logger.info("Process: %s" % d)

        invpath = os.path.join(d,args.inventory_file_name)
        if os.path.exists(invpath) and args.create_only:
            return True, "skipped"
        
        inventory = directory_inventory(
            d,
            remove_directory = True,
            recursive        = False,
            filter_func      = our_filter_func,
            escape_non_ascii = False,
            parallel         = (not args.single_thread)
        )
        all_inventories[d] = inventory

        et = elapsed_since(start_time)
        if et > 2:
            etstr = format_elapsed_seconds(et)
            size = 0
            for bits in inventory:
                size += bits[1]
            szstr = format_bytes(size)
            logger.info("Spent %s on %s of files (%0.01f MB/sec)." % (etstr,szstr,(size/(1024.0*1024.0*et))))

        revised_inventory = False
        if os.path.exists(invpath) and not args.replace_inventory_files:
            logger.info("An inventory file exists.")
            try:
                old_inventory = load_json(invpath)
            except ValueError as err:
                logger.error("Failure loading old inventory, unable to compare it to the new one.")
                logger.error("JSON: " + str(err), exc_info=True)
                identical_invs = False
                problems = True
            else:
                old_inventory = sorted([x for x in old_inventory if our_filter_func(x[0],countas=0)])  # applies the current filter to the old inventory
                identical_invs, problems, revised_inventory = compare_inventories(old_inventory, inventory)
                if revised_inventory == inventory:
                    logger.debug("The new inventory was not revised via patching.")
                    if identical_invs:
                        revised_inventory = False   # identical_invs and no revisions, so don't overwrite on disk
                else:
                    logger.debug("The new inventory has been revised via patching.")
                    inventory = revised_inventory

            assert identical_invs in (True,False)
        else:
            # inventory did not exist, or we don't care to look for it (--replace-inventory-files)
            identical_invs = None
            problems = False

        if identical_invs is None or (revised_inventory and (args.patch or not problems)):
            logger.info("An inventory file will be writen.")
            write_json(invpath, inventory)

            if identical_invs is False:
                return bool(not problems), "replaced with fix"
            else:
                return True, "created"
        elif identical_invs:
            logger.debug("An inventory file will not be writen because there are no differences.")
            return True, "matched"
        else:
            logger.debug("An inventory file will not be writen because there are differences, but no instruction to fix this.")
            return False, "differences"
    except Exception as err:
        logger.error("Exception " + str(type(err)) + " while processing a directory.", exc_info=True)
        return False, "exception"



def process_all_subdirectories(d, summary):
    """
    Recursively process directories, adding and/or verifying inventory files in each.
    """

    happy, message = process_one_directory(d)
    summary['counts'][message] += 1
    if not happy:
        summary['failed paths'].append(d)

    for a_file in os.listdir(d):
        fullFile = os.path.join(d, a_file)
        if os.path.isdir(fullFile):
            process_all_subdirectories(fullFile, summary)



def summarize_duplicates():
    """
    Print out a summary of duplicate images found between all directories.
    """

    ids_to_dirs = defaultdict(lambda: [])
    for dir,inventory in all_inventories.items():
        for bits in inventory:
            ids_to_dirs[tuple(bits[1:2])].append(dir)   # size, checksum

    dir_duplicate_counts = defaultdict(lambda: 0)
    for dirs in ids_to_dirs.values():
        if len(dirs) > 1:   # the key occurred more than once (in the same dir, or a different one)
            for d in dirs:
                dir_duplicate_counts[d] += 1

    items = sorted(dir_duplicate_counts.items(), key=lambda x: x[1], reverse=True)
    if items:
        logger.info("Duplicate images per directory (files in same or different dir):")
        for dir,count in items:
            logger.info("  %s: %s" % (dir,count))
    else:
        logger.info("No exact duplicate images found.")



def main():
    global args
    global logger

    def csv(v):
        return v.lower().split(',')

    # parse arguments
    parser = argparse.ArgumentParser(description='Create or verify an inventory for a directory, optionally recursively.')
    parser.add_argument('--recursive', help='make inventory files recursively, one file in each directory, otherwise only process files in a single directory', action="store_true")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--create-and-check', help='create new inventories and check existing ones, but do not update any (except as allowed by --patch-approve-add and --patch-approve-remove)', action="store_true")
    group.add_argument('--create-only', help='create new inventories but do nothing for directories that lack them (note there is no option to not create missing inventories)', action="store_true")
    group.add_argument('--patch', help='query to approve updates to the inventory files', action="store_true")
    group.add_argument('--replace-inventory-files', help='replace inventory files without considering their correctness', action="store_true")
    parser.add_argument('--patch-approve-add', metavar='CSV', type=csv, help='one or more file extensions (with leading dots, CSV list, case-insensitive) to approve adding to existing inventories without promting, valid in the --patch and ---create-and-check modes')
    parser.add_argument('--patch-approve-remove', metavar='CSV', type=csv, help='one or more file extensions (with leading dots, CSV list, case-insensitive) to approve removing from existing inventories without promting, valid in the --patch and --create-and-check modes')
    parser.add_argument('--also-non-image-files', help='include almost any file in the inventory (by default, only common image formats are included)', action="store_true")
    parser.add_argument('--inventory-file-name', metavar='NAME', help='the name of the per-directory inventory file, without path (default: %(default)s)', default="inventory.json")
    parser.add_argument('--single-thread', help='process using only one thread (by default, uses one thread per CPU thread, up to 4)', action="store_true")
    parser.add_argument('--log', metavar='PATH', help='base log file name (default: %(default)s)', default="inventory.log")
    parser.add_argument('directories', metavar='DIR', nargs='+', help='directories to process')
    args = parser.parse_args()

    # set up a standard logger object
    logger = setup_logger(args.log, console_level=logging.INFO)

    try:
        if (args.patch_approve_add or args.patch_approve_remove) and not (args.create_and_check or args.patch):
            logger.error("Can only use --patch-approve-add and/or --patch-approve-remove with --create-and-check or --patch.")
            logger.error("The program can not continue.")
            exit(-1)

        # on Windows, expand special characters (on Linux, would perhaps expand previously escaped characters)
        targets = []
        for t in args.directories:
            globbed = glob.glob(t.rstrip(r'\/'))
            targets += globbed
        args.directories = targets

        if not args.directories:
            logger.error("No directories remain after glob-ing (remember sneaky pictures/bilder folder rename on Windows).")
            logger.error("The program can not continue.")
            exit(-1)

        for d in args.directories:
            if not os.path.isdir(d):
                logger.error("The parameter '%s' is not a directory." % d)
                logger.error("The program can not continue.")
                exit(-1)

        if args.replace_inventory_files and (args.patch_approve_add or args.patch_approve_remove):
            logger.error("Not allowed to combine --replace-inventory-files with --patch-approve-add or --patch-approve-remove.")
            logger.error("The program can not continue.")
            exit(-1)

        start_time = datetime.datetime.now()

        for d in args.directories:
            if args.recursive:
                summary = {'counts': defaultdict(lambda: 0), 'failed paths': list()}
                process_all_subdirectories(d, summary)

                if summary['counts']:
                    logger.info("Processing results (one per directory):")
                    for kv in sorted(summary['counts'].items()):
                        logger.info("  %s: %s" % kv)
                if summary['failed paths']:
                    logger.info("Paths which had problems:")
                    for d in sorted(summary['failed paths']):
                        logger.info("  %s" % d)
            else:
                happy, message = process_one_directory(d)
                logger.info("Processing result: happy=%s, message='%s'." % (happy,message))

        logger.info("Took %s to generate inventories." % format_elapsed_seconds(elapsed_since(start_time)))

        summarize_duplicates()

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