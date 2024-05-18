# -*- encoding: UTF-8 -*-

'''
A script to upload images to AWS S3.

A certain amount of configuration on the AWS side is not shown here, for example lifecycle rules and user setup.

Example test command:
  python .\s3_backup.py --sync --workstation-upload cleanup --recursive --bucket bucket-xyz --local-tree-root '..\huge image cleanup pile' '..\huge image cleanup pile'

FIXME: Specifying a subpath to process causes the files to be re-uploaded.
'''

import argparse, logging
import io
import glob, os
from collections import defaultdict
import datetime
import re
import boto3
import gnupg   # pip install python-gnupg
from urllib.parse import urlencode
import zipfile

# local files
from inventory import setup_logger, elapsed_since, format_elapsed_seconds
from rename_images import checksum



# globals
logger = None
args = None
phrase = None
gpg = None
s3_observed_keys = set()            # strings: S3 key (in S3)
local_observed_keys = set()         # strings: S3 key (from local paths and params)

# only encrypt and compress certain files
secret_files = set(('.jpg','.jpeg','.png','mp4','.mov','.avi','.wmv','.mpg','.cr2'))
other_files = set(('.json',))



def prefix():
    """
    Give the S3 prefix (ending in a '/') we are working with.
    """

    # we have a rigid system to route uploads into one of three main paths
    # these paths are significant for IAM permissions and lifecycle rules
    if args.temp_upload:
        return "temp/%s/" % (args.temp_upload)
    elif args.workstation_upload:
        return "workstation/%s/" % (args.workstation_upload)
    elif args.server_upload:
        return "server/%s/" % (args.server_upload)

    raise Exception("Seem to be missing a parameter when building the S3 key.")



def one_file(path):
    """
    Given an file path to process, return a string representing the action taken.
    """

    _, ext = os.path.splitext(path.lower())
    if not (ext in other_files or ext in secret_files):
        return 'ignored'

    info = os.stat(path)
    if info.st_size > 512 * 1024 and (not args.temp_upload):
        tags = {'Tapeworthy': 'True'}
    else:
        tags = {'Tapeworthy': 'False'}

    if tags['Tapeworthy'] == 'True' or args.temp_upload:
        storage_class = 'STANDARD'
    else:
        storage_class = 'ONEZONE_IA'   # minimum 30 days charged

    # determine the key we would use in S3
    # keep a record that we saw it to assist with cleanup in S3 (given --sync)
    path2 = os.path.abspath(path)
    if not path2.startswith(args.local_tree_root):
        logger.info("Path: %s" % path)
        logger.info("Abs path: %s" % path2)
        logger.info("Local tree root: %s" % args.local_tree_root)
        raise Exception("Somehow messed up the path base.")
    path2 = path2[len(args.local_tree_root):]
    key = path2.replace('\\','/').strip('/')
    key = prefix() + key
    if ext in secret_files:           # only encrypt and compress certain files
        key += ".gpg.zip"
    local_observed_keys.add(key)

    with open(path,"rb") as fh:
        dat = fh.read(16*1024)

        # use a tag to notice when the local copy differs from S3 without processing the whole file
        vals = (
            info.st_size,
            info.st_mtime,
            path2.encode('utf8'),
            key.encode('utf8'),
            phrase.encode('utf8') if ext in secret_files else b''
        )
        tags['UniqueCheck'] = checksum((b"%d:%d:%s:%s:%s:" % vals) + dat)
        if key in s3_observed_keys:
            unique_tag = get_s3_unique_tag(key)
            if unique_tag == tags['UniqueCheck']:
                if not args.make_changes:
                    logger.debug("The key '%s' looks good in S3." % key)
                return 'already in S3 with the right tag'   # this file is already in S3 with the expected UniqueCheck tag value
            else:
                logger.debug("The key '%s' is present in S3, but with the wrong value for 'UniqueCheck'." % key)  # wrong password, changed file, etc
                logger.debug("  local: %s, S3: %s" % (tags['UniqueCheck'],unique_tag))
                logger.debug("  st_size: %d, st_mtime: %d" % (info.st_size,info.st_mtime))

        dat += fh.read()    # it had better fit in RAM

    if not args.make_changes:
        logger.debug("The key '%s' needs to be uploaded." % key)

    if ext in secret_files:           # only encrypt and compress certain files
        # encrypt
        encrypted_data = gpg.encrypt(dat, None, symmetric=True, passphrase=phrase)
        decrypted_stream = io.BytesIO(encrypted_data.data)
        encrypted_data = decrypted_stream.read()

        # pack the encrypted data into a zip archive held in RAM
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, mode='w', compression=zipfile.ZIP_DEFLATED) as ziparchive:
             with ziparchive.open(os.path.split(path)[1]+".gpg", 'w') as fh:
                fh.write(encrypted_data)
        archive.seek(0)
        upload_data = archive.read()
    else:
        upload_data = dat

    if args.make_changes:
        logger.info("Upload to key: %s" % key)
        s3_client.put_object(
            Body         = upload_data,
            Bucket       = args.bucket,
            Key          = key,
            StorageClass = storage_class,
            Tagging      = urlencode(tags)
        )
    else:
        logger.info("Would have uploaded to key: %s" % key)
        logger.info("Tags: %s" % urlencode(tags))
        if ext in secret_files:
            logger.info("Data inflation gpg: %d to %d (%0.01f%%)." % (len(dat),len(encrypted_data),100.0*len(encrypted_data)/len(dat)))
            logger.info("Data inflation zip: %d to %d (%0.01f%%)." % (len(dat),len(upload_data),100.0*len(upload_data)/len(dat)))

    if args.make_changes:
        return 'uploaded'
    return 'would have uploaded'



def one_path(path, summary, first=False):
    """
    Recursively process files and directories.
    """

    def limitfunc():
        res = bool(args.limit and sum(summary.values()) >= args.limit)
        if res: logger.info("Stopping crawl early due to --limit.")
        return res

    if limitfunc(): return

    if os.path.isfile(path):
        logger.debug("Process file '%s'." % path)
        action = one_file(path)
        summary[action] += 1

    elif first or args.recursive:
        logger.info("Process directory '%s'." % path)
        for a_thing in os.listdir(path):
            one_path(os.path.join(path, a_thing), summary)
            if limitfunc(): return



def get_s3_unique_tag(key):
    """
    Get the value of the UniqueCheck tag on the given S3 key.
    """

    tags = s3_client.get_object_tagging(Bucket=args.bucket, Key=key)  # slow extra API call
    try:
        unique = [kv['Value'] for kv in tags['TagSet'] if kv['Key'] == 'UniqueCheck'][0]
        return unique
    except (IndexError,KeyError):
        logger.warning("The key '%s' has some issue with the UniqueCheck tag.")
        return None



def list_s3():
    """
    See what files are present in S3 already.
    """

    # this could go faster for cases where specific files have been selected for upload
    logger.info("List S3 to find what keys exist.")
    
    start_time = datetime.datetime.now()

    paginator = s3_client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=args.bucket, Prefix=prefix()):
        for file in page.get('Contents',[]):
            s3_observed_keys.add(file['Key'])

    logger.info("Have %d items in S3 for the given prefix." % len(s3_observed_keys))

    et = format_elapsed_seconds(elapsed_since(start_time))
    logger.info("Took %s to list S3." % et)



def do_s3_deletes(summary):
    """
    Clean up files in S3.
    """

    if args.limit:
        logger.info("No cleanup because the --limit param was given.")
        return

    logger.info("Have %d files in S3 and %d files locally." % (len(s3_observed_keys),len(local_observed_keys)))

    if not args.sync:
        logger.info("Will not consider any S3 cleanup because --sync is not given.")

    if args.make_changes:
        delete_status_key = 'delete from S3'
    else:
        delete_status_key = 'would delete from S3'

    for k in sorted(s3_observed_keys):
        # for finding files in S3 to delete, the fancy unique checksum is not relevant

        if k in local_observed_keys:
            summary['in S3 and local disk'] += 1
            if not args.make_changes:
                logger.debug("The key '%s' is in S3 and local disk." % k)
        else:
            summary[delete_status_key] += 1
            if not args.make_changes:
                logger.debug("The key '%s' needs cleanup in S3." % k)

            if args.make_changes:
                logger.info("Delete the key '%s' in S3." % k)
                s3_client.delete_object(Bucket=args.bucket, Key=k)
            else:
                logger.info("Would delete the key '%s' in S3 because it was not observed locally." % k)
                if args.limit and summary[delete_status_key] >= args.limit:
                    logger.info("Stopping the delete loop early due to --limit.")
                    break



def do_s3_sync():
    """
    Call the recursive processing on the list of paths from the CLI, print summary.
    """

    paths = []
    for rawpath in args.targets:
        for path in glob.glob(rawpath):
            paths.append(path)
    logger.info("List of globbed paths is %d elements." % len(paths))

    if not paths:
        logger.error("After expanding shell wildcards no paths remain to process.")
        logger.error("The program can not continue.")
        exit(-1)

    for path in paths:
        path = os.path.abspath(path)
        if not path.startswith(args.local_tree_root):
            logger.error("Path does not appear to be inside --local-tree-root: %s" % path)
            logger.error("The program can not continue.")
            exit(-1)

    summary = defaultdict(lambda: 0)
    start_time = datetime.datetime.now()

    for path in paths:
        one_path(path, summary, first=True)

    et = format_elapsed_seconds(elapsed_since(start_time))
    logger.info("Took %s to handle uploads." % et)

    do_s3_deletes(summary)
    logger.info("Processed: ")
    for kv in summary.items():
        logger.info("  %s: %s" % kv)



def main():
    global args
    global logger
    global phrase
    global gpg
    global s3_client

    def subpath(v):
        if not re.match(r"^[\w./-]+$",v):
            raise ValueError("Didn't like the subpath.")
        return v

    parser = argparse.ArgumentParser(description='Upload files to AWS S3.')
    parser.add_argument('targets', metavar='PATHS', nargs='+', help='one or more files or directories to process, all of which must be equal to or inside --local-tree-root')
    parser.add_argument('--local-tree-root', help='the local path which is to be the root used to derive S3 keys', required=True)
    parser.add_argument('--bucket', help='the S3 bucket to use', required=True)
    parser.add_argument('--recursive', help='crawl all subpaths inside the given paths', action="store_true")
    parser.add_argument('--make-changes', help='actually upload and/or delete files in S3, depending on --sync and --uploads-only', action="store_true")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--sync', help='upload to S3 and delete files in S3 that are not present locally (note this might interact poorly with listing a subset of local files)', action='store_true')
    group.add_argument('--uploads-only', help='upload to S3 if S3 does not contain the given files, but delete nothing', action='store_true')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--temp-upload', metavar="SUBPATH", type=subpath, help='upload to temp/ with the given subpath')
    group.add_argument('--workstation-upload', metavar="SUBPATH", type=subpath, help='upload to workstation/ with the given subpath')
    group.add_argument('--server-upload', metavar="SUBPATH", type=subpath, help='upload to server/ with the given subpath')
    parser.add_argument('--limit', metavar='NUM', type=int, help='limit how many files to process')
    parser.add_argument('--log', metavar='PATH', help='base log file name (default: %(default)s)', default="s3_backup.log")
    args = parser.parse_args()

    # set up a standard logger object
    logger = setup_logger(args.log, console_level=logging.INFO)

    args.local_tree_root = os.path.abspath(args.local_tree_root)
    logger.info("Local tree root is given as: %s" % args.local_tree_root)
    if not os.path.isdir(args.local_tree_root):
        logger.error("The value for --local-tree-root must be a directory.")
        logger.error("The program can not continue.")
        exit(-1)

    try:
        logging.getLogger('gnupg').setLevel(logging.INFO)
        gpg = gnupg.GPG()
        phrase = input("Enter the passphrase to encrypt the file(s): ")
        s3_client = boto3.client('s3')     # rely on env vars, or .aws/config, or magical IAM Role
        list_s3()
        do_s3_sync()
    except Exception as err:
        logger.error("Exception " + str(type(err)) + " while working.", exc_info=True)
        exit(-2)



if __name__ == '__main__':
    main()
    logger.info("Script has reached end.")
