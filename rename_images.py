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
	if path.lower().endswith(".cr2") and args.scale_percent:
		return 0
	elif os.path.splitext(path.lower())[1] not in (".jpg",".jpeg",".cr2"):
		return 0
		
	with Image.open(path) as imgobj:
		if 'exit' in imgobj.info:
			exif_dict = piexif.load(imgobj.info['exif'])
		else:
			print("Keys in image info: %s" % sorted(imgobj.info.keys()))
			raise Exception("The file '%s' does not appear to contain exif data." % path)
		
		if args.filter_min_size and (imgobj.size[0] <= args.filter_min_size[0] or imgobj.size[1] <= args.filter_min_size[1]):
			return 0
		if args.filter_min_pixels and (imgobj.size[0] * imgobj.size[1] <= args.filter_min_pixels[0] * args.filter_min_pixels[1]):
			return 0
		
		if args.edit_in_place:
			outpath = path
		elif args.output_path:
			_, filepart = os.path.split(path)
			outpath = os.path.join(args.output_path, filepart)
			
		if args.print_some_tags:
			print("Dims:   %s x %s" % (exif_dict["0th"][piexif.ImageIFD.ImageWidth],exif_dict["0th"][piexif.ImageIFD.ImageLength]))
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
			changeto = parsedate(exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized]) + datetime.timedelta(hours=args.adjust_date)
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
			
	if args.adjust_date or args.scale_percent:
		shutil.move(args.temporary_file, outpath)
		if args.edit_in_place and os.path.exists(path):
			os.unlink(path)
				
	if args.rename_images:
		changeto = parsedate(exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized]).strftime("img_%Y%m%d_%H%M%S")
		outpath, filepart = os.path.split(outpath)
		extra = 0
		finaloutpath = os.path.join(outpath, changeto+".jpg")
		while os.path.exists(finaloutpath):
			extra += 1
			finaloutpath = os.path.join(outpath, "%s.%d.jpg" % (changeto,extra))
			if extra > 100:    # because I don't trust unbounded loops
				raise Exception("Apparent runaway extra for %s." % path)
		if path == finaloutpath:
			return 0
		if args.edit_in_place:
			shutil.move(path, finaloutpath)
		else:
			shutil.copy2(path, finaloutpath)
	
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
				
				
				
def dims(input):
	try:
		a,b = input.split(',')
		return int(a), int(b)
	except Exception as err:
		raise TypeError(str(err))
	
	
	
parser = argparse.ArgumentParser(description='Rename, adjust, or examine images.')
parser.add_argument('targets', metavar='PATHS', nargs='+', help='one or more files or directories to process (recursive)')
group = parser.add_mutually_exclusive_group()
group.add_argument('--edit-in-place', help='edit images in place', action="store_true")
group.add_argument('--output-path', metavar='PATH', help='a path to write result files to')
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument('--print-all-tags', help='print all tags on target images', action="store_true")
group.add_argument('--print-some-tags', help='print the interesting tags on target images', action="store_true")
group.add_argument('--adjust-date', metavar='HOURS', type=int, help='adjust the stored Exif DateTime to be the specified number of hours different than DateTimeOriginal')
group.add_argument('--scale-percent', metavar='PERC', type=float, help='scale images to the given percentage of original size')
group.add_argument('--rename-images', help='rename images based on exif date data', action="store_true")
parser.add_argument('--filter-min-size', metavar='W,H', type=dims, help='only process images that are larger than the given dimentions')
parser.add_argument('--filter-min-pixels', metavar='W,H', type=dims, help='only process images that have more pixels than the given dimentions')
parser.add_argument('--limit', metavar='NUM', type=int, help='limit how many images to process')
parser.add_argument('--temporary-file', metavar='PATH', help='a file to use as a temporary file when outputting modified images')
args = parser.parse_args()

if args.rename_images or args.scale_percent or args.adjust_date:
	if not (args.edit_in_place or args.output_path):
		print("When modifying images, one of --edit-in-place or --output-path must be given.")
		exit(1)
		
if not args.temporary_file:
	args.temporary_file = "rename_images_tempfile_pid_%d.jpg" % os.getpid()
	
if args.output_path and not os.path.exists(args.output_path):
	os.makedirs(args.output_path)

do_work()
exit(0)
