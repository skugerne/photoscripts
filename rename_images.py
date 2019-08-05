from PIL import Image, JpegImagePlugin
import piexif
import argparse
import datetime
import glob, os, shutil
from collections import defaultdict
import hashlib
import numpy
import re



def parsedate(datestr):
	# Have observed that with dateutil.parser.parse(datestr):
	#   b'2019:07:26 14:15:58' => 2019-08-02 14:15:58
	#   '20190726 141558' => 2019-08-02 14:15:58
	# Therefore implemented a parser using regex so that things are reliably strict.
	m = re.match("^(\d\d\d\d):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)$",datestr.decode('ascii'))
	if not m:
		raise Exception("Failed to parse: %s" % datestr)
	return datetime.datetime(*[int(m.group(x+1)) for x in range(6)])
	
	
	
def parsedate2(datestr):
	return "%s => %s" % (datestr,parsedate(datestr))
	
	
	
def checksum(data):
	hasher = hashlib.md5()
	hasher.update(data)
	return hasher.hexdigest()
			
			
			
def compare_files(path1, path2, expect_match):
	with open(path1, 'rb') as fh:
		checksum1 = checksum(fh.read())
	with open(path2, 'rb') as fh:
		checksum2 = checksum(fh.read())
	if expect_match != bool(checksum1 == checksum2):
		raise Exception("Expect match: %s, A: %s, B: %s" % (expect_match,checksum1,checksum2))
			
			
			
def compare_images(path1, path2, expect_match):
	im = Image.open(path1)
	checksum1 = checksum(numpy.asarray(im, dtype=numpy.uint8).tobytes())  # probably the tour through numpy is not needed, but it works
	im = Image.open(path2)
	checksum2 = checksum(numpy.asarray(im, dtype=numpy.uint8).tobytes())
	if expect_match != bool(checksum1 == checksum2):
		raise Exception("Expect match: %s, A: %s, B: %s" % (expect_match,checksum1,checksum2))



def one_image(path):
	if os.path.splitext(path.lower())[1] not in (".jpg",".jpeg"):
		return 0
		
	# a raw image may or may not exist, but we generally would want to keep any raw sync-ed up with its jpeg
	path_raw = os.path.splitext(path)[0] + ".cr2"
		
	if args.edit_in_place or args.dry_run:
		outpath = path
		outpath_raw = path_raw
	elif args.output_path:
		_, filepart = os.path.split(path)
		outpath = os.path.join(args.output_path, filepart)
		_, filepart = os.path.split(path_raw)
		outpath_raw = os.path.join(args.output_path, filepart)
		
	with Image.open(path) as imgobj:
		if 'exif' in imgobj.info:
			exif_dict = piexif.load(imgobj.info['exif'])
		else:
			print("Keys in image info: %s" % sorted(imgobj.info.keys()))
			raise Exception("The file '%s' does not appear to contain exif data." % path)
		
		if args.filter_min_size and (imgobj.size[0] <= args.filter_min_size[0] or imgobj.size[1] <= args.filter_min_size[1]):
			return 0
		if args.filter_min_pixels and (imgobj.size[0] * imgobj.size[1] <= args.filter_min_pixels[0] * args.filter_min_pixels[1]):
			return 0
			
		if args.print_some_tags:
			print("Dims 1: %s x %s" % (exif_dict["0th"].get(piexif.ImageIFD.ImageWidth),exif_dict["0th"].get(piexif.ImageIFD.ImageLength)))
			print("Dims 2: %s x %s" % (exif_dict["Exif"].get(piexif.ExifIFD.PixelXDimension),exif_dict["Exif"].get(piexif.ExifIFD.PixelYDimension)))
			print("Date 1: %s" % parsedate2(exif_dict["0th"][piexif.ImageIFD.DateTime]))
			print("Date 2: %s" % parsedate2(exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal]))
			print("Date 3: %s" % parsedate2(exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized]))
		
		if args.print_all_tags:
			for ifd in ("0th", "Exif", "GPS", "1st"):
				for tag in exif_dict[ifd]:
					val = exif_dict[ifd][tag]
					try:
						if len(val) > 100:
							val = "%s len %s" % (str(type(val)),len(val))
					except TypeError:
						pass
					print("(%s) (%s) %s = %s" % (ifd,tag,piexif.TAGS[ifd][tag]["name"], val))
					
		if args.adjust_date:
			changeto = parsedate(exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized]) + datetime.timedelta(minutes=args.adjust_date)
			exif_dict["0th"][piexif.ImageIFD.DateTime] = changeto.strftime("%Y:%m:%d %H:%M:%S")
			exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = changeto.strftime("%Y:%m:%d %H:%M:%S")
			exif_bytes = piexif.dump(exif_dict)
			shutil.copy2(path, args.temporary_file)
			compare_files(path, args.temporary_file, True)
			compare_images(path, args.temporary_file, True)
			piexif.insert(exif_bytes, args.temporary_file)
			compare_files(path, args.temporary_file, False)
			compare_images(path, args.temporary_file, True)
			
		if args.scale_percent:
			newdims = (int(imgobj.size[0] * args.scale_percent),int(imgobj.size[1] * args.scale_percent))
			exif_dict["0th"][piexif.ImageIFD.ImageWidth] = newdims[0]
			exif_dict["0th"][piexif.ImageIFD.ImageLength] = newdims[1]   # fix dims or they are wrong in exif
			exif_bytes = piexif.dump(exif_dict)
			quantization = getattr(imgobj, 'quantization', None)
			subsampling = JpegImagePlugin.get_sampling(imgobj)
			quality = 100 if quantization is None else 0
			imgobj2 = imgobj.resize(newdims, resample=Image.LANCZOS)
			# include exif or else it is lost
			# also attempt to compress with the settings that were used previously
			imgobj2.save(args.temporary_file, exif=exif_bytes, format='jpeg', subsampling=subsampling, qtables=quantization, quality=quality)
			compare_files(path, args.temporary_file, False)
			compare_images(path, args.temporary_file, False)
			
		# the image object is closed here, now we can replace or delete the original jpeg
			
	if args.adjust_date or args.scale_percent:
		# we have theoretically made a temporary file with the contents we want, now we can get it where it needs to go
		if args.dry_run:
			print("Edit '%s'." % path)
		else:
			shutil.move(args.temporary_file, outpath)
		if os.path.exists(path_raw) and path_raw != outpath_raw:
			# the optional raw file is handled differently, as there is no modification and no temporary raw file
			if args.edit_in_place:
				shutil.move(path_raw, outpath_raw)
			elif args.output_path:
				shutil.copy2(path_raw, outpath_raw)
			else:
				print("Edit '%s'." % path_raw)
				
	if args.rename_images:
		changeto = parsedate(exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal]).strftime("img_%Y%m%d_%H%M%S")
		outpath, filepart = os.path.split(outpath)
		extra = 0
		finaloutpath = os.path.join(outpath, changeto+".jpg")      # deal with name conflicts
		finaloutpath_raw = os.path.join(outpath, changeto+".cr2")  # raw follows along if it exists
		while os.path.exists(finaloutpath) and path != finaloutpath:
			extra += 1
			finaloutpath = os.path.join(outpath, "%s.%d.jpg" % (changeto,extra))
			finaloutpath_raw = os.path.join(outpath, "%s.%d.cr2" % (changeto,extra))
			if extra > 100:    # because I don't trust unbounded loops
				raise Exception("Apparent runaway extra for %s." % path)
				
		if path == finaloutpath:
			return 0
		
		if args.edit_in_place:
			func = shutil.move
		elif args.output_path:
			func = shutil.copy2
		else:
			func = lambda x,y: print("Move '%s' to '%s'." % (x,y))
		
		func(path, finaloutpath)
		if os.path.exists(path_raw):
			func(path_raw, finaloutpath_raw)
	
	return 1


	
def one_path(path, summary):
	def limitfunc():
		return bool(args.limit and sum(summary.values()) >= args.limit)
		
	if limitfunc(): return
		
	if os.path.isfile(path):
		dirpath, _ = os.path.split(path)
		summary[dirpath] += one_image(path)
	else:
		print("Process directory '%s'." % path)
		for dirpath, _, filenames in os.walk(path):
			for name in filenames:
				if limitfunc(): return
				fullpath = os.path.join(dirpath, name)
				summary[dirpath] += one_image(fullpath)
				
				
				
def do_work():
	paths = []
	for rawpath in args.targets:
		for path in glob.glob(rawpath):
			paths.append(path)

	summary = defaultdict(lambda: 0)
	for path in paths:
		one_path(path, summary)
	print("Processed: ")
	for kv in summary.items():
		print("  %s: %s" % kv)
		
		
		
def hhmm(input):
	try:
		m = re.match("^(-?)(\d+):(\d+)$",input)
		sign = -1 if m.group(1) else 1
		hours = int(m.group(2))
		mins = int(m.group(3))
		if mins < 0 or mins > 60: raise Exception("The given minutes are not acceptable.")
		return sign * (hours * 60 + minutes)
	except Exception as err:
		raise TypeError(str(err))
	
						
				
def dims(input):
	try:
		a,b = input.split(',')
		a,b = int(a),int(b)
		if a <= 0 or b <= 0: raise Exception("The given dimensions are not acceptable.")
		return a,b
	except Exception as err:
		raise TypeError(str(err))
	
	
	
parser = argparse.ArgumentParser(description='Rename, adjust, or examine images.')
parser.add_argument('targets', metavar='PATHS', nargs='+', help='one or more files or directories to process (recursive)')
group = parser.add_mutually_exclusive_group()
group.add_argument('--edit-in-place', help='edit images in place', action="store_true")
group.add_argument('--output-path', metavar='PATH', help='a path to write result files to, leaving the source files unmodified (unless the output overlaps with the source)')
group.add_argument('--dry-run', help='see what changes should be made, but don\'t actually make any', action="store_true")
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument('--print-all-tags', help='print all tags on target images', action="store_true")
group.add_argument('--print-some-tags', help='print the interesting tags on target images', action="store_true")
group.add_argument('--adjust-date', metavar='HH:MM', type=hhmm, help='adjust the stored Exif DateTime to be the specified number of hours and minutes different than DateTimeOriginal')
group.add_argument('--scale-percent', metavar='PERC', type=float, help='scale images to the given percentage of original size')
group.add_argument('--rename-images', help='rename images based on exif date data', action="store_true")
parser.add_argument('--filter-min-size', metavar='W,H', type=dims, help='only process images that are larger than the given dimentions')
parser.add_argument('--filter-min-pixels', metavar='W,H', type=dims, help='only process images that have more pixels than the given dimentions')
parser.add_argument('--limit', metavar='NUM', type=int, help='limit how many images to process')
parser.add_argument('--temporary-file', metavar='PATH', help='a file to use as a temporary file when outputting modified images')
args = parser.parse_args()

if args.rename_images or args.scale_percent or args.adjust_date:
	if not (args.edit_in_place or args.output_path or args.dry_run):
		print("When modifying images, one of --edit-in-place, --output-path or --dry-run must be given.")
		exit(1)
		
if not args.temporary_file:
	args.temporary_file = "rename_images_tempfile_pid_%d.jpg" % os.getpid()
	
if args.output_path and not os.path.exists(args.output_path):
	os.makedirs(args.output_path)

do_work()
exit(0)
