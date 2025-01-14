#!/bin/python3
from pexpect.exceptions import TIMEOUT
from tqdm import tqdm

import os
import sys
import traceback
import subprocess
import pexpect
import math
import telepot
import signal
import time


ROOT_PATH = os.getenv('ROOT_PATH', '/media')
BACKUP_DIR = os.getenv('BACKUP_DIR', '.backup')
BOT_KEY = os.getenv('BOT_KEY', '')
CHAT_ID = os.getenv('CHAT_ID', '')
HOST = os.getenv('HOST', '')
CRF = os.getenv('CRF', '16')
H265_TRANSCODE = os.getenv('H265_TRANSCODE', 'false')
H265_MB_H = os.getenv('H265_MB_H', '1000')
DEBUG_ON = os.getenv("DEBUG_ON", 'false')


stopping = False
currentMessage = None
bot = None


def transcode(file, pbar, desc, frames):
	global currentMessage
	previous_frame = 0
	new_size = 0

	# cmd = 'ffmpeg -y -i "{}" -max_muxing_queue_size 9999 -map 0:a:0? -map 0:v:0 -c:a copy -c:v libx265 -preset veryfast -x265-params crf={} "{}.new.mkv"'.format(file, CRF, file)
	cmd = 'ffmpeg -y -i "{}" -max_muxing_queue_size 9999 -map 0:v:0 -map 0:a? -map 0:s? -map 0:m:language:eng? -c:a copy -c:s copy -c:v libx265 -preset veryfast -x265-params crf={} "{}.new.mkv"'.format(file, CRF, file)

	if DEBUG_ON == 'true':
		print("\nStarting ffmpeg: {}".format(cmd))

	thread = pexpect.spawn(cmd)

	cpl = thread.compile_pattern_list([
		pexpect.EOF,
		"frame= *\d+",
		"video:.*? audio:.*? subtitle:.*? other streams:.*? global headers:.*? muxing overhead: .*?"
		'(.+)'
	])

	original = os.path.getsize(file)
	finished = True

	currentMessage = None
	update_message(prepare_message(os.path.basename(file), original, 0, 0))

	success = False

	counter = 0
	while True:
		i = thread.expect_list(cpl, timeout=None)

		if stopping:
			print("Killing ffmpeg")
			thread.kill(9)
			time.sleep(0.1)
			if thread.isalive():
				try:
					while True:
						g = thread.expect_list(cpl, timeout=2)
						if g == 0:
							break
				except TIMEOUT:
					pass
			finished = False
			break

		if i == 0:
			if DEBUG_ON == 'true':
				print("ffmpeg exited")
			break
		elif i == 1:
			line = thread.match.group(0).decode("utf-8")

			try:
				frame_number = int(line.split('=')[-1])
				frame_count = frame_number - previous_frame
				previous_frame = frame_number

				if os.path.isfile(file + '.new.mkv'):
					new_size = os.path.getsize(file + '.new.mkv')
					if new_size > original * 0.8 or (previous_frame / frames > 0.03 and new_size / (previous_frame / frames) > original * 0.825):
						thread.kill(9)
						time.sleep(0.1)
						if thread.isalive():
							try:
								while True:
									g = thread.expect_list(cpl, timeout=2)
									if g == 0:
										break
							except TIMEOUT:
								pass
						finished = False
						break

					pbar.set_description(desc + " ({})".format(convert_size(new_size)))

					if counter % 10 == 0:
						update_message(prepare_message(os.path.basename(file), original, new_size, (previous_frame / frames) * 100))

					counter = counter + 1

				pbar.update(frame_count)
			except ValueError:
				print(line)
				traceback.print_exc(file=sys.stdout)

		elif i == 2:
			success = True
			if DEBUG_ON == 'true':
				print("Finished successfully!")

		elif i == 3:
			# unknown_line = thread.match.group(0)
			# print("UN", unknown_line)
			pass

	if not success:
		finished = False

	if not stopping:
		converted = 0

		if os.path.exists(file + '.new.mkv'):
			converted = os.path.getsize(file + '.new.mkv')

		if not success or converted > original * 0.8:
			os.rename(file, file.rsplit('.', 1)[0] + '-SKIP.' + file.rsplit('.', 1)[1])
			if os.path.exists(file + '.new.mkv'):
				os.remove(file + '.new.mkv')
		elif converted > 1000000 and success:
			basename = os.path.basename(file)
			basename = basename.rsplit('.', 1)[0] + '-CVT265.mkv'
			basename = basename.replace('.x264', '')
			basename = basename.replace('.X264', '')
			basename = basename.replace('.h.264', '')
			basename = basename.replace('.H.264', '')
			basename = basename.replace('.h264', '')
			basename = basename.replace('.H264', '')
			basename = basename.replace('x264', '')
			basename = basename.replace('X264', '')
			basename = basename.replace('h.264', '')
			basename = basename.replace('H.264', '')
			basename = basename.replace('h264', '')
			basename = basename.replace('H264', '')
			basename = basename.replace('264', '')
			newfile = os.path.dirname(file) + '/' + basename
			os.rename(file + '.new.mkv', newfile)
			os.chmod(newfile, 0o777)
			os.rename(file, ROOT_PATH + '/' + BACKUP_DIR + '/' + os.path.basename(file))
		else:
			finished = False

		return original, converted, finished

	print("Stopping...")

	update_message(prepare_stopping_message(os.path.basename(file), original, new_size, (previous_frame / frames) * 100))

	if os.path.exists(file + '.new.mkv'):
		os.remove(file + '.new.mkv')

	return -1, -1, finished


def process(file, desc, data):
	frames = get_frames(data)

	open(file + '.converting', 'a').close()

	result = (0, 0, True)

	try:
		pbar = tqdm(total=frames, leave=False, unit='', desc=desc)
		result = transcode(file, pbar, desc, frames)
		pbar.close()
	except:
		traceback.print_exc(file=sys.stdout)
		pass

	os.remove(file + '.converting')

	return result


def get_frames(data):
	frames = 0

	for i in data['stream']:
		temp = data['stream'][i]['nb_frames']
		if temp != 'N/A':
			if int(temp) < frames:
				frames = int(temp)

	if frames > 0:
		return frames

	return int(get_fps(data) * get_duration(data))


def get_fps(data):
	fps = get_key_from_stream(data, 'r_frame_rate')

	if fps == 'N/A':
		fps = get_key_from_stream(data, 'avg_frame_rate')

	fps = fps.split("/")
	return int(fps[0]) / int(fps[1])


def get_duration(data):
	if data['format']['duration'] == 'N/A':
		return 0

	return int(round(float(data['format']['duration'])))


def get_key_from_stream(data, key):
	for i in data['stream']:
		if data['stream'][i][key] != 'N/A' and data['stream'][i][key] != '0/0':
			return data['stream'][i][key]


def get_data(file):
	cmd = ['ffprobe', '-v', '0', '-show_format', '-show_streams', file]
	process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

	data = {'stream': {}}

	stream = -1
	tag = None
	for line in process.stdout:
		line = line.rstrip().decode("utf-8")
		if line.startswith('['):
			if tag is None:
				tag = line[1:-1].lower()
				if tag == 'stream':
					stream = stream + 1
					data[tag][stream] = {}
				else:
					data[tag] = {}
			else:
				tag = None
		else:
			kv = line.split("=")
			if tag == 'stream':
				data[tag][stream][kv[0]] = kv[1]
			else:
				data[tag][kv[0]] = kv[1]

	return data


def has_accessors(file):
	process = subprocess.Popen('lsof', stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

	for line in process.stdout:
		if file in line.rstrip().decode("utf-8"):
			return True

	return False


def is_transcodable(file, data):
	if len(data['stream']) == 0:
		return False

	found_h265 = False
	for i in data['stream']:
		if data['stream'][i]['codec_name'] == 'h265' or data['stream'][i]['codec_name'] == 'hevc':
			found_h265 = True

	transcode_h265 = False

	if found_h265 and H265_TRANSCODE == 'true' and H265_MB_H != '':
		size = os.path.getsize(file)
		duration = get_duration(data)
		if duration > 0:
			mb_h = ((size / duration) / 1000000) * 3600
			if mb_h > int(H265_MB_H):
				transcode_h265 = True

	if found_h265 and not transcode_h265:
		return False

	if file.endswith("partial~"):
		return False

	if os.path.isfile(file + ".converting"):
		return False

	directory = os.path.dirname(file)

	if os.path.isfile(file + ".processed"):
		os.rename(file + ".processed", directory + "/." + os.path.basename(file) + ".processed")

	if os.path.isfile(directory + "/." + os.path.basename(file) + ".processed"):
		return False

	if os.path.isfile(directory + "/.transcodeignore"):
		return False

	if has_accessors(file):
		return False

	return True


def convert_size(size_bytes):
	if size_bytes == 0:
		return "0B"

	size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
	i = int(math.floor(math.log(size_bytes, 1024)))
	p = math.pow(1024, i)
	s = round(size_bytes / p, 2)

	return "%s %s" % (s, size_name[i])


def str2bool(v):
	return v.lower() in ("yes", "true", "t", "1")


def search(path, name, depth=0, prefix='', last=True):
	global stopping

	if stopping:
		return

	desc = prefix + '+-'

	if depth > 0:
		print(desc, end='')

	if os.path.isdir(path):
		if path.find('__thumb') >= 0:
			print(name + ' [ignored]')
			return

		files = os.listdir(path)
		files.sort()
		length = len(files)

		if ".transcodeignore" in files:
			print(name + ' [ignored]')
		else:
			print(name)

			for i in range(length):
				if not last:
					search(path + '/' + files[i], files[i], depth + 1, prefix + '| ', i + 1 == length)
				else:
					search(path + '/' + files[i], files[i], depth + 1, prefix + '  ', i + 1 == length)
	else:
		if path.find('265') >= 0 or path.find('HEVC') >= 0 or path.find('-SKIP.') >= 0 or os.path.getsize(path) / 1048576 < 450:
			print(name)
		else:
			try:
				data = get_data(path)
			except:
				data = None
				traceback.print_exc(file=sys.stdout)

			if data is not None:
				if is_transcodable(path, data):
					print(name + '... ', end='')

					result = process(path, desc + name, data)

					if result[0] > 0:
						diff = round((result[1] / result[0]) * 100, 2)

						oldsize = convert_size(result[0])
						newsize = convert_size(result[1])

						if result[2]:
							if result[1] > result[0]:
								print('{} -> {} ({}%) (kept old)'.format(oldsize, newsize, diff))
								update_message('*{}*\n*Size:* {} --> {} ({}%)\n*Status:* Kept old'.format(name, oldsize, newsize, diff))
							else:
								print('{} -> {} ({}%)'.format(oldsize, newsize, diff))
								update_message('*{}*\n*Size:* {} --> {} ({}%)\n*Status:* Replaced with new'.format(name, oldsize, newsize, diff))
						else:
							if result[1] > result[0]:
								print('{} -> {} ({}%) (kept old)'.format(oldsize, newsize, diff))
								update_message('*{}*\n*Size:* {} --> Gave up at {} ({}%)\n*Status:* Kept old'.format(name, oldsize, newsize, diff))
							else:
								print('{} -> {} ({}%) (kept old)'.format(oldsize, newsize, diff))
								update_message('*{}*\n*Size:* {} --> Failed at {} ({}%)\n*Status:* Kept old'.format(name, oldsize, newsize, diff))
					elif result[0] == 0:
						print('failed')

				else:
					print(name)


def prepare_message(filename, original_size, current_size, percentage_complete):
	diff = round((current_size / original_size) * 100, 2)
	
	expected = 0
	if percentage_complete > 0:
		expected = convert_size(current_size * 100 / percentage_complete)
	
	return '*{}*' \
	       '\n*Size:* {} --> {} ({}%)' \
	       '\n*Status:* Transcoding: {}%' \
	       '\n*Expected Size:* {}' \
		.format(filename, convert_size(original_size), convert_size(current_size), diff, round(percentage_complete, 2), expected)


def prepare_stopping_message(filename, original_size, current_size, percentage_complete):
	diff = round((current_size / original_size) * 100, 2)
	return '*{}*' \
	       '\n*Size:* {} --> {} ({}%)' \
	       '\n*Status:* Stopped at {}%' \
		.format(filename, convert_size(original_size), convert_size(current_size), diff, round(percentage_complete, 2))


def update_message(message):
	global currentMessage, bot
	if bot is not None:
		if HOST != '':
			message += '\n*Host:* {}'.format(HOST)

		try:
			if currentMessage is None:
				sent = bot.sendMessage(
					chat_id=CHAT_ID,
					text=message,
					parse_mode='Markdown'
				)
				currentMessage = telepot.message_identifier(sent)
			else:
				bot.editMessageText(
					currentMessage,
					text=message,
					parse_mode='Markdown'
				)
		except:
			traceback.print_exc(file=sys.stdout)
			print("Failed to update TG message!")


def send_message(message):
	global bot
	if bot is not None:
		if HOST != '':
			message += '\n*Host:* {}'.format(HOST)

		try:
			bot.sendMessage(
				chat_id=CHAT_ID,
				text=message,
				parse_mode='Markdown'
			)
		except:
			traceback.print_exc(file=sys.stdout)
			print("Failed to send TG message!")


def sigterm_handler(_signo, _stack_frame):
	global stopping
	print("Caught", _signo)
	stopping = True


def scan():
	signal.signal(signal.SIGTERM, sigterm_handler)

	send_message("*Transcoder Started*")
	search(ROOT_PATH, ROOT_PATH)
	send_message("*Transcoder Stopped*")

	print()
	print("Stopped")


if __name__ == "__main__":
	if BOT_KEY != '' and CHAT_ID != '':
		bot = telepot.Bot(token=BOT_KEY)

	scan()

	sys.exit(0)
