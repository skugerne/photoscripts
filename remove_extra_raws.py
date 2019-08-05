import argparse
import datetime
import os, shutil



def bits(path,file):
	base,ext = os.path.splitext(file.lower())
	return {
		'path': os.path.join(path,file),   # used for deleting
		'base': base,                      # used for comparison
		'ext': ext                         # used for filtering
	}



parser = argparse.ArgumentParser(description='Remove CR2 images that have no corresponding JPGs.')
parser.add_argument('jpegs', metavar='PATH', help='a directory containing jpeg (.jpg or .jpeg) images (not recursive)')
parser.add_argument('raws', metavar='PATH', help='a directory containing raw (.cr2) images (not recursive, can be the same as the jpeg path)')
parser.add_argument('--do-deletes', help='delete the extra raw images, otherwise just list them', action="store_true")
parser.add_argument('--allow-missing-raws', help='when a jpeg image does not have a corresponding raw, continue without raising an error', action="store_true")
args = parser.parse_args()

# examine the jpeg dir
print("Target dir: '%s'. " % args.jpegs)
jpegs = [bits(args.jpegs,x) for x in os.listdir(args.jpegs)]
jpegs = [x for x in jpegs if x['ext'] in ('.jpg','.jpeg')]
jpegs = sorted(jpegs, key=lambda x: x['base'])  # removing the ext changes sorting in some cases
if jpegs:
	print("Have found %s jpeg images." % len(jpegs))
else:
	if not os.path.isdir(args.jpegs):
		print("The given target for jpeg images does not appear to exist, or is not a directory.")
		exit(1)
	print("Failed to find any jpeg images in '%s'." % args.jpegs)
	exit(1)

# examine the raw dir
args.raws = args.raws.strip('"').strip("'")   # windows: trailing slashes before a quote can in fact cause the quote to be included
print("Target dir: '%s'. " % args.raws)
raws = [bits(args.raws,x) for x in os.listdir(args.raws)]
raws = [x for x in raws if x['ext'] == '.cr2']
raws = sorted(raws, key=lambda x: x['base'])  # removing the ext changes sorting in some cases
if raws:
	print("Have found %s raw images." % len(raws))	
else:
	if not os.path.isdir(args.raws):
		print("The given target for raw images does not appear to exist, or is not a directory.")
		exit(1)
	print("Failed to find any raw images in '%s'." % args.raws)
	exit(1)

# build a list of things to delete	
jpegidx = 0
rawidx = 0
rawstodelete = list()
while(jpegidx < len(jpegs) and rawidx < len(raws)):
	if jpegs[jpegidx]['base'] > raws[rawidx]['base']:
		rawstodelete.append(rawidx)
		rawidx += 1
	elif jpegs[jpegidx]['base'] < raws[rawidx]['base']:
		if args.allow_missing_raws:
			jpegidx += 1
		else:
			raise Exception("There exists a jpeg '%s' for which there is no raw." % jpegs[jpegidx]["path"])
	else:
		jpegidx += 1
		rawidx += 1
		
if not (args.allow_missing_raws or jpegidx == len(jpegs)):
	raise Exception("Did not reach end of jpeg list first.")
		
if rawidx < len(raws):
	print("There are %s raw images after running out of jpegs." % (len(raws) - rawidx))
	while rawidx < len(raws):
		rawstodelete.append(rawidx)
		rawidx += 1
		
print("Determined that %s of %s raw images are extra." % (len(rawstodelete),len(raws)))
	
# do the deletes	
for idx in rawstodelete:
	if args.do_deletes:
		print("Delete '%s'." % raws[idx]["path"])
		os.unlink(raws[idx]["path"])
	else:
		print("Would delete '%s' with --do-deletes." % raws[idx]["path"])
		
if len(rawstodelete) > 20:
	print("Determined that %s of %s raw images are extra." % (len(rawstodelete),len(raws)))