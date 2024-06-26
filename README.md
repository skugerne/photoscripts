﻿# photoscripts

Scripts to assist with chores related to images.

The idea is to use fairly dumb software with complex directory structures filled with images.  Notably I/we are not interested in uploading everything to some online platform, nor using any proprietary system to maintain order.  This will be refined caveman digital image management.

The challenges of this situation can include viewing and backups, along with identifying corrupted, duplicated or missing images.  Updates may happen differently on different copies of the pile of images, therefore the tools should be able to point out differences.  The architecture is basically a very simple distributed database with scripts to interface with it.

## script summary

The philosophy is to have a lot of scripts that do relatively simple things, but are intended to be used in sequence.  Not entirely different than the unix philosophy, except without all the pipes.  Instead of pipes, we generate *inventory files*.  The inventory  are JSON format, and contain file paths, size, checksum and sometimes also the date and image dimentions.

| name                 | purpose | uses inventory files |
|----------------------|---------|----------------------|
| inventory.py         | create one inventory file per directory, or verify them, or patch them | Y |
| merge_inventories.py | build one large inventory file from the inventory files which are placed in each directory | Y |
| dedupe.py            | use the per-directory inventory files to identify images which are present more than once, suggest commands to clean up | Y |
| sync.py              | compare two merged directory files and suggest commands to resolve the differences | Y |
| slideshow.py         | based on the contents of inventory files, take a selection of images through time and show them as a slideshow | Y |
| rename_images.py     | rename images to a generic date-based name convention, adjust dates stored in images, resize images | N |
| remove_extra_raws.py | for cameras that produce raw & jpg images, where the jpg are subsequently thinned out, clean up the excess raw files | N |
| s3_backup.py         | a script to make backups to S3 in a particular way | N |

## example commands

Here we can efficiently discover new directories of images on two disks and suggest commands to sync any changes.  With the options as shown, the scripts assume that any inventory files that exist are accurate.

```bash
python3 inventory.py --create-only --recursive /media/gray/USB1/Pictures /media/gray/USB2/Pictures
python3 merge_inventories.py --recursive --merged-inventory merged-inventory.usb1.json /media/gray/USB1/Pictures
python3 merge_inventories.py --recursive --merged-inventory merged-inventory.usb2.json /media/gray/USB2/Pictures
python3 sync.py --cp-both-ways merged-inventory.usb[12].json
```

## case sensitivity

The scripts and inventory files are case-sensitive.  Its too annoying to consider how to handle situations in which there are files with overlapping names.

## piexif library

I have included a copy of the piexif library (from a fork https://github.com/JEFuller/Piexif/ which appears to have been interested in code formatting and other packaging details) because neither my use nor piexif itself is changing much.