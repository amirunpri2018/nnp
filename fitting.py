import math
import pyglet
from pyglet import clock
from pyglet.window import key
import cv2
import numpy as np
from pyglet.gl import *
import scipy.misc
from faceswap import doalign
import argparse
import datetime
import random
import sys
import time
import os
from plat.utils import offset_from_string, vectors_from_json_filelist, json_list_to_array
from plat.bin.atvec import do_roc
from plat.grid_layout import grid2img
from plat.sampling import real_glob
from plat.fuel_helper import get_dataset_iterator
from PIL import Image
from scipy.misc import imread, imsave, imresize
import subprocess

import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# global app of messy state
theApp = None

# actual pyglet windows
windows = []

# camera settings
cam_width = 720
cam_height = 512
# cam_width = 400
# cam_height = 300
# cam_width = 560
# cam_height = 360

# constants - arrow up/down mode
ARROW_MODE_VECTOR_SOURCE = 1
ARROW_MODE_VECTOR_DEST = 2
ARROW_MODE_IMAGE_SOURCE = 3

APP_MODE_ATTRIBUTE = 1
APP_MODE_ONESHOT = 2
APP_MODE_CLASSIFY = 3

attribute_decoded_array = None
oneshot_decoded_array = None

roc_image_resize = 0.55
roc_image_width = None
roc_image_height = None
roc_hist_image_width = None

# initialize and return camera handle
def setup_camera(device_number):
    cam = cv2.VideoCapture(device_number)
    # result1 = cam.set(cv2.CAP_PROP_FRAME_WIDTH,cam_width)
    # result2 = cam.set(cv2.CAP_PROP_FRAME_HEIGHT,cam_height)
    result3 = cam.set(cv2.CAP_PROP_FPS,1)
    return cam

def shutdown_camera(device_number):
    cv2.VideoCapture(device_number).release()

# given a camera handle, return image in RGB format
def get_camera_image(camera):
    retval, img = camera.read()
    img = cv2.flip(img, 1)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return img

# convert an RGB image to a pyglet texture for display
def image_to_texture(img):
    sy,sx,number_of_channels = img.shape
    number_of_bytes = sy*sx*number_of_channels
    img  = np.flipud(img)
    img = img.ravel()
    image_texture = (GLubyte * number_of_bytes)( *img.astype('uint8') )
    pImg = pyglet.image.ImageData(sx,sy,'RGB',
           image_texture,pitch=sx*number_of_channels)
    return pImg

# return aligned image
def get_aligned(img):
    success, im_resize, rect = doalign.align_face_buffer(img, 256, max_extension_amount=0)
    return im_resize

# encode image into latent space of model
def encode_from_image(rawim, dmodel, scale_factor=None):
    if scale_factor is not None:
        rawim = imresize(rawim, 1.0 / scale_factor)
    mixedim = np.asarray([[rawim[:,:,0], rawim[:,:,1], rawim[:,:,2]]])
    entry = (mixedim / 255.0).astype('float32')
    encoded = dmodel.encode_images(entry)[0]
    return encoded

# value mapping utility
def pr_map(value, istart, istop, ostart, ostop):
    return ostart + (ostop - ostart) * ((value - istart) / float(istop - istart));

# big nasty switch statement of keypress
def do_key_press(symbol, modifiers):
    global cur_vector
    print("SO: {}".format(symbol))
    if(symbol == key.R):
        if theApp.use_camera:
            theApp.set_camera_recording(not theApp.camera_recording)
    if(symbol == key.T):
        theApp.show_camera = not theApp.show_camera
    elif(symbol == key.SPACE):
        print("SPACEBAR")
        snapshot(None);
    elif(symbol == key.ESCAPE):
        print("ESCAPE")
        cv2.destroyAllWindows()
        if theApp.use_camera:
            cv2.VideoCapture(0).release()
        sys.exit(0)

def get_date_str():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

pipeline_dir = "fitpipe/{}".format(get_date_str())
os.makedirs(pipeline_dir)
camera_dir = "{}/camera".format(pipeline_dir)
aligned_dir = "{}/aligned".format(pipeline_dir)
plat_dir = "{}/plat".format(pipeline_dir)
enhanced_dir = "{}/enhanced".format(pipeline_dir)
sequence_dir = "{}/sequence".format(pipeline_dir)
scrot_dir = "{}/scrot".format(pipeline_dir)
os.makedirs(camera_dir)
os.makedirs(aligned_dir)
os.makedirs(plat_dir)
os.makedirs(enhanced_dir)
os.makedirs(sequence_dir)
os.makedirs(scrot_dir)

# PLAT: ALIGNED -> PLAT
command = "(cd ../plat && CUDA_VISIBLE_DEVICES=0 \
    INROOT=../nips16_neural_puppet/{} \
    OUTROOT=../nips16_neural_puppet/{} \
    ./run_fit.sh)".format(aligned_dir, plat_dir)
with open("plat.sh", "w+") as text_file:
    text_file.write(command)

# ENANCE: PLAT -> ENHANCED
command = "CUDA_VISIBLE_DEVICES=1 \
/usr/local/anaconda2/envs/enhance/bin/python \
    ../neural-enhance3/enhance.py --model dlib_256_neupup1 --zoom 1 \
    --device gpu0 \
    --rendering-tile 256 \
    --rendering-overlap 1 \
    --input-directory {} \
    --output-directory {} \
    --watch".format(plat_dir, enhanced_dir)
with open("enhance.sh", "w+") as text_file:
    text_file.write(command)

# p=subprocess.Popen(command, shell=True)

sequences = []
new_sequences = []

class SequenceDir():
    is_valid = True

    """Just a container for unfortunate global state"""
    def __init__(self, directory, offset=None, min_index=None, max_index=None):
        self.x = 0
        self.y = 0
        self.frames = []
        files = sorted(real_glob("{}/*.{{jpg,png}}".format(directory)))
        num_files = len(files)
        print("There are {} files in {}".format(num_files, directory))
        if num_files == 0:
            self.is_valid = False
            return
        if min_index == None:
            min_index = 0
        if max_index == None:
            max_index = num_files
        for i in range(min_index, max_index):
            f = files[i]
            img = imread(f, mode='RGB')
            self.frames.append(image_to_texture(img))
        self.num_frames = len(self.frames)
        if offset is None:
            self.cur_frame = random.randint(0, self.num_frames-1)
        else:
            self.cur_frame = offset

    def move_to(self, x, y):
        self.x = x
        self.y = y

    def draw(self):
        if not self.is_valid:
            return
        self.frames[self.cur_frame].blit(self.x, self.y)
        self.cur_frame = (self.cur_frame + 1) % self.num_frames

def convert_and_process(indir):
    global new_sequences
    identifier = os.path.basename(indir)
    print("Conerting {}".format(identifier))
    outdir = "{}/{}".format(sequence_dir, identifier)
    os.makedirs(outdir)
    command = "mogrify -format jpg -resize 160x160 -path {} {}/*.png".format(outdir, indir)
    print("Running {}".format(command))
    os.system(command)
    command = "cp ../plat/latents/{}.json {}/.".format(identifier, outdir)
    os.system(command)
    newSeq = SequenceDir(outdir)
    new_sequences.append(newSeq)

# watcher to load win2 images on filesystem change
class InputFileHandler(FileSystemEventHandler):
    last_processed = None

    def process(self, infile):
        if theApp is None:
            print("Can't yet process {}, no app".format(infile))

        basename = os.path.basename(infile)
        if basename[0] == '.' or basename[0] == '_' or not basename.endswith(".png"):
            print("Skipping infile: {}".format(infile))
            return;

        # oh so brittle file name parsing. don't go changing anything
        seq_len = int(basename[-7:-4])
        cur_frame = int(basename[-11:-8])

        if seq_len != cur_frame + 1:
            print("Skipping non-final infile: {}".format(infile))
            return;

        if infile == self.last_processed:
            print("Skipping duplicate infile: {}".format(infile))
            return;
        else:
            print("Processing infile: {}".format(infile))
            self.last_processed = infile

        dir_name = os.path.dirname(infile)
        theApp.next_sequence = None
        theApp.next_sequence = SequenceDir(dir_name)

        convert_and_process(dir_name)

    def on_modified(self, event):
        if not event.is_directory:
            self.process(event.src_path)

event_handler = InputFileHandler()
observer = Observer()
observer.schedule(event_handler, path=enhanced_dir, recursive=True)
observer.start()

class MainApp():
    last_recon_face = None
    one_shot_source_vector = None
    debug_outputs = False
    framecount = 0
    timecount  = 0
    use_camera = True
    camera = None
    model_name = None
    model_name2 = None
    dmodel = None
    dmodel2 = None
    app_mode = APP_MODE_ATTRIBUTE
    gan_mode = False
    cur_vector_source = 0
    cur_vector_dest  = 0
    scale_factor = None
    arrow_mode = ARROW_MODE_IMAGE_SOURCE
    camera_recording = False
    show_camera = True
    num_steps = 0
    redraw_needed = True
    last_recon_tex = None
    setup_oneshot_camera = False
    scrot_enabled = False
    window_sizes = None
    cur_camera = None
    cur_camera_tex = None
    standard_hist_tex = None
    standard_roc_tex = None
    cur_hist_tex = None
    cur_roc_tex = None
    last_aligned = None
    last_aligned_tex = None
    current_sequence = None
    next_sequence = None
    main_screen_dirty = True
    snapshot_every = 600
    camera_every = 20
    last_snapshot = 0
    last_camera = 0
    unknown_person_tex = None

    """Just a container for unfortunate global state"""
    def __init__(self, window_sizes):
        self.window_sizes = window_sizes
        self.cur_frame = 0
        img = imread("images/unknown_face.png", mode='RGB')
        self.unknown_person_tex = image_to_texture(img)

    def setDebugOutputs(self, mode):
        self.debug_outputs = mode

    def write_cur_scrot(self, debugfile=False, datestr=None):
        if debugfile:
            datestr = "debug"
        elif datestr is None:
            datestr = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = "{}/{}.png".format(scrot_dir,datestr)
        os.system("scrot {}".format(filename))

    def set_camera_recording(self, state):
        theApp.camera_recording = state
        if (theApp.camera_recording):
            self.camera = setup_camera(self.camera_device)
        else:
            self.camera = shutdown_camera(self.camera_device)
        print("Camera recording {} is now {}".format(self.camera_device, self.camera_recording))

    def step(self, dt):
        global sequences, new_sequences

        if self.cur_frame == 5:
            print("Fake key presses")
            # do_key_press(key.LEFT, None)

        if self.cur_frame == 35:
            print("Fake key presses")
            # do_key_press(key.G, None)
            # do_key_press(key.LEFT, None)

        # this moves sequences over in a sane way
        process_queue = new_sequences
        new_sequences = []
        all_seq = sequences + process_queue
        # keep last 40
        sequences = all_seq[-40:]

        cur_time = time.time()
        if cur_time - theApp.last_camera> theApp.camera_every:
            theApp.last_camera = cur_time
            if theApp.use_camera:
                theApp.last_aligned = None
                theApp.main_screen_dirty = True
                theApp.set_camera_recording(True)
                if self.camera is not None and theApp.camera_recording:
                    self.cur_camera = get_camera_image(self.camera)
                    self.cur_camera_tex = image_to_texture(self.cur_camera)
                    candidate = get_aligned(self.cur_camera)
                    if candidate is not None:
                        theApp.redraw_needed = True
                        theApp.current_sequence = None
                        theApp.last_aligned = candidate
                        theApp.last_aligned_tex = image_to_texture(theApp.last_aligned)
                        self.write_cur_aligned()
                theApp.set_camera_recording(False)

    def draw_grid(self, dt, win_num):
        global windows, cur_vector, sequences
        win_width, win_height = self.window_sizes[win_num]

        cur_index = 0
        num_seq = len(sequences)
        for y in range(5):
            for x in range(8):
                if(cur_index < num_seq):
                    s = sequences[cur_index]
                    cur_index = cur_index + 1
                    s.move_to(x * 160, y * 160)
                    s.draw()

    def draw_photobooth(self, dt, win_num):
        if self.main_screen_dirty == False:
            return

        # self.main_screen_dirty = False
        # print("Drawing main screen")
        global windows, cur_vector
        win_width, win_height = self.window_sizes[win_num]

        if self.cur_camera is not None and self.show_camera:
            self.cur_camera_tex.blit(0, win_height - cam_height)

        if self.last_aligned is not None:
            aligned_tex = self.last_aligned_tex
        else:
            aligned_tex = self.unknown_person_tex
        aligned_tex.blit(0, 0)

        # I think we only want to throw away glTextures from this thread
        if self.next_sequence is not None:
            self.current_sequence = None
            self.current_sequence = self.next_sequence

        if self.current_sequence is not None:
            self.current_sequence.move_to(300, 0)
            self.current_sequence.draw()

    def write_cur_aligned(self, debugfile=False, datestr=None):
        if debugfile:
            datestr = "debug"
        elif datestr is None:
            datestr = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        if self.last_aligned is not None:
            filename = "{}/{}.png".format(aligned_dir,datestr)
            if not debugfile and os.path.exists(filename):
                return
            imsave(filename, theApp.last_aligned)

        if self.cur_camera is not None:
            filename = "{}/{}.png".format(camera_dir, datestr)
            if not debugfile and os.path.exists(filename):
                return
            imsave(filename, self.cur_camera)

def step(dt):
    global windows
    cur_time = time.time()
    try:
        theApp.step(dt)
        for i in range(len(windows)):
            if windows[i] != None:
                windows[i].switch_to()
                theApp.draw_functions[i](dt, i)
        if cur_time - theApp.last_snapshot > theApp.snapshot_every:
            snapshot(dt)
            theApp.last_snapshot = cur_time

    except IOError as e:
        print("BAD PROGRAM: step caught {}".format(e))

    theApp.num_steps += 1

# snapshot the current state and write a file to the processing queue
def snapshot(dt):
    # print("SNAPSHOT: saving")

    datestr = get_date_str()
    theApp.write_cur_aligned(datestr=datestr)
    if theApp.scrot_enabled:
        print("SCROT RUNNING")
        theApp.write_cur_scrot(datestr=datestr)
    # if theApp.app_mode == APP_MODE_ATTRIBUTE:
    #     theApp.write_recon_triple(datestr)
    # elif theApp.app_mode == APP_MODE_ONESHOT:
    #     theApp.write_oneshot_sixpack(datestr)

window_sizes = [
    # [1920, 1080],
    # [1920, 1080],
    # [1280, 800],
    [1000, 800],
    [1280, 800],
]
theApp = MainApp(window_sizes)
theApp.draw_functions = [
    theApp.draw_photobooth,
    theApp.draw_grid,
]

if __name__ == "__main__":
    # argparse
    parser = argparse.ArgumentParser(description='Let get NIPSy')
    parser.add_argument("--model", dest='model', type=str, default=None,
                        help="path to the saved model")
    parser.add_argument("--model2", dest='model2', type=str, default=None,
                        help="path to the saved ali model")
    parser.add_argument('--anchor-index', dest='anchor_index', default="0,1,2,3",
                        help="indexes to offsets in anchor-offset")    
    parser.add_argument('--anchor-index2', dest='anchor_index2', default="0,1,2,3",
                        help="indexes to offsets in anchor-offset2")    
    parser.add_argument('--no-camera', dest='no_camera', default=False, action='store_true',
                        help="disable camera")
    parser.add_argument('--scrot', dest='scrot', default=False, action='store_true',
                        help="enable screen grabs")
    parser.add_argument('--skip1', dest='skip1', default=False, action='store_true',
                        help="no window 1")
    parser.add_argument('--skip2', dest='skip2', default=False, action='store_true',
                        help="no window 2")
    parser.add_argument('--debug-outputs', dest='debug_outputs', default=False, action='store_true',
                        help="write diagnostic output files each frame")
    parser.add_argument('--full1', dest='full1', default=None, type=int,
                        help="Index to screen to use for window one in fullscreen mode")
    parser.add_argument('--full2', dest='full2', default=None, type=int,
                        help="Index to screen to use for window two in fullscreen mode")
    parser.add_argument('--camera', dest='camera', default=1, type=int,
                        help="Camera device number")
    parser.add_argument("--encoded-vectors", type=str, default=None,
                        help="Comma separated list of json arrays")
    parser.add_argument('--dataset', dest='dataset', default=None,
                        help="Source dataset (for labels).")
    parser.add_argument('--labels', dest='labels', default=None,
                        help="Text file with 0/1 labels.")
    parser.add_argument('--split', dest='split', default="valid",
                        help="Which split to use from the dataset (train/nontrain/valid/test/any).")
    parser.add_argument('--roc-limit', dest='roc_limit', default=10000, type=int,
                        help="Limit roc operation to this many vectors")
    parser.add_argument('--scale-factor', dest='scale_factor', default=None, type=float,
                        help="Scale up outputs of model")

    args = parser.parse_args()

    theApp.camera_device = args.camera
    theApp.scale_factor = args.scale_factor
    theApp.scrot_enabled = args.scrot

    display = pyglet.window.get_platform().get_default_display()
    screens = display.get_screens()

    if args.skip1:
        window1 = None
    elif args.full1 is not None:
        window1 = pyglet.window.Window(fullscreen=True, screen=screens[args.full1])
    else:
        window1 = pyglet.window.Window(window_sizes[0][0], window_sizes[0][1], resizable=False)
        # window1.set_location(0, 0)
    windows.append(window1)

    if args.skip2:
        window2 = None
    elif args.full2 is not None:
        window2 = pyglet.window.Window(fullscreen=True, screen=screens[args.full2])
    else:
        window2 = pyglet.window.Window(window_sizes[1][0], window_sizes[1][1], resizable=False)
    windows.append(window2)

    # window3 = pyglet.window.Window(fullscreen=True, screen=screens[2])
    # windows.append(window3)

    # window4 = pyglet.window.Window(fullscreen=True, screen=screens[3])
    # windows.append(window4)

    for window in windows:
        if window is not None:
            @window.event
            def on_key_press(symbol, modifiers):
                do_key_press(symbol, modifiers)

    theApp.use_camera = not args.no_camera
    theApp.model_name = args.model
    theApp.model_name2 = args.model2

    possible = [
        "fitpipe/20161212_180402/resized/20161212_180806",
        "fitpipe/20161212_180402/resized/20161212_182411",
        "fitpipe/20161212_180402/resized/20161212_190023",
        "fitpipe/20161212_191248/resized/20161212_191701",
        "fitpipe/20161212_191248/resized/20161212_192303",
        "fitpipe/20161212_191248/resized/20161212_192443",
        "paths/nips1", 
        "paths/nips2", 
        "paths/nips3", 
        "paths/nips4", 
        ]


    # for i in range(4):
    #     sequences.append(SequenceDir(random.choice(possible)))

    # sequences.append(SequenceDir("paths/nips1"))
    # sequences.append(SequenceDir("paths/nips2"))
    # sequences.append(SequenceDir("paths/nips3"))
    # sequences.append(SequenceDir("paths/nips4"))
    # sequences.append(SequenceDir("paths/nips3", 20))
    # sequences.append(SequenceDir("paths/nips4", 30))

    snapshot(None)
    pyglet.clock.schedule_interval(step, 0.1)
    pyglet.app.run()
