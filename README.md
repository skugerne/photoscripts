# photoscripts

Scripts to assist with chores related to images.

The idea is to use fairly dumb software with complex directory structures filled with images.  Notably I/we are not interested in uploading everything to some online platform.

The challenges of this situation can include viewing and backups, along with identifying corrupted, duplicated or missing images.

## script summary

The philosophy is to have a lot of scripts that do relatively simple things, but are intended to be used in sequence.

| name                 | purpose |
|----------------------|---------|
| inventory.py         | create one inventory file per directory, or verify them, or patch them |
| merge_inventories.py | build one large inventory file from the inventory files which are placed in each directory |
| dedupe.py            | use the per-directory to identify images which are present more than once, suggest commands to clean up |
| sync.py              | compare two merged directory files and suggest commands to resolve the differences |
| slideshow.py         | based on the contents of inventory files, take a selection of images through time and show them a slideshow |
| rename_images.py     | rename images to date, adjust dates, resize images |
| remove_extra_raws.py | for cameras that produce raw & jpg images, where the jpg are subsequently thinned out, clean up the excess raw files |

## example commands

```bash

python3 merge_inventories.py --recursive --merged-inventory merged-inventory.usb1.json /media/gray/USB1/Pictures
python3 merge_inventories.py --recursive --merged-inventory merged-inventory.usb2.json /media/gray/USB2/Pictures
python3 sync.py --cp-both-ways merged-inventory.usb[12].json
```

## piexif library

I have included a copy of the piexif library (from a branch https://github.com/JEFuller/Piexif/ which appears to have been interested in code formatting and other packaging details) because neither my use nor piexif itself is changing much.