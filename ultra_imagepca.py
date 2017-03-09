"""
11-21-2016 updated 3-8-2017 Matthew Faytak
This script reads midpoint frames in from an Experiment object (https://github.com/rsprouse/ultratils) and performs PCA on the image data. 
Intended for use on data structures generated by ultracomm (https://github.com/rsprouse/ultracomm).
Returns a .csv file containing frame-by-frame scores for some number of PCs and associated frame metadata. Currently runs on a single subject at a time.
----
Expected usage: $ python ultra_imagepca.py (-f -v etc) directory num_components
See arg parser below for details
----

"""
from __future__ import absolute_import, division, print_function

import os
import re
import argparse
import audiolabel
import subprocess
import numpy as np
from scipy import ndimage
import matplotlib.pyplot as plt
from ultratils.exp import Exp
from ultratils.utils import is_white_bpr
from ultratils.utils import is_frozen_bpr

# for PCA business
from sklearn import decomposition
from sklearn.decomposition import PCA

# Read in and parse the arguments, getting directory info and whether or not data should flop
parser = argparse.ArgumentParser()
parser.add_argument("directory", help="Experiment directory containing all subjects")
parser.add_argument("num_components", help="Number of principal components to output")
parser.add_argument("-er", "--include_er", help="Run PCA over vowels including ER.", action="store_true")
parser.add_argument("-r", "--include_r", help="Run PCA over vowels and R.", action="store_true")
parser.add_argument("-l", "--include_l", help="Run PCA over vowels and L.", action="store_true")
parser.add_argument("-f", "--flop", help="Horizontally flip the ultrasound data", action="store_true")
parser.add_argument("-c", "--convert", help="Scan-convert the data before analysis", action="store_true")
parser.add_argument("-v", "--visualize", help="Produce plots of PC loadings on fan",action="store_true")
args = parser.parse_args()

# check for appropriate directory
try:
    expdir = args.directory
except IndexError:
    print("\tDirectory provided doesn't exist")
    ArgumentParser.print_usage
    ArgumentParser.print_help
    sys.exit(2)

# assemble experiment object and do setup
e = Exp(expdir=args.directory)
e.gather()

# check for appropriate number of components
if args.num_components > (len(e.acquisitions) - 1):
    print("EXITING: Number of components requested definitely exceeds number to be produced")
    sys.exit(2)

# subset data: create boolean arrays to ID acquisitions as part of certain sets.
exp_length = len(e.acquisitions)
L_bool = np.zeros([exp_length], dtype=bool)
R_bool = np.zeros([exp_length], dtype=bool)
ER_bool = np.zeros([exp_length], dtype=bool)

for idx, st in enumerate(e.acquisitions):
    with open (st.abs_stim_file, "r") as mystim:
        key = mystim.read().rstrip('\n')
        if key == "bolus":
            continue
        if key.startswith("A "):
            key = key.split(" ")[1]
        # all keys containing R or L will be in the baseline (English) set.
        if "R" in key:
            if key == "BURR" or key == "PER":
                ER_bool[idx] = True
            else:
                R_bool[idx] = True
        # if key == "POLE": # TODO could be changed to include more vowel contexts
        if "L" in key:
            L_bool[idx] = True

pc_out = os.path.join(e.expdir,"pc_out.txt")

logfile = os.path.join(e.expdir,"issues_log.txt")

frames = None
threshhold = 0.020 # threshhold value in s for moving away from acoustic midpoint measure
phase = []
trial = []
phone = []
tstamp = []

# adjust shape of array for pre-converted BPR images if desired
if args.convert:
    conv_frame = e.acquisitions[0].image_reader.get_frame(0)
    conv_img = test.image_converter.as_bmp(conv_frame)

# # # # Comb the experiment object for data, and output numpy arrays to run PCs on. # # # #
for idx,a in enumerate(e.acquisitions):

    # a.gather()
    print("Now working on {}".format(a.timestamp))
    
    # setup for PC and audio
    wav = a.abs_ch1_audio_file
    tg = os.path.splitext(wav)[0] + '.TextGrid'
    stim_file = a.abs_stim_file
    ts_file = os.path.join(a.abspath,'ts.txt')
    pm = audiolabel.LabelManager(from_file=tg, from_type="praat")
    v,m = pm.tier('phone').search(vre, return_match=True)[-1] # return last V = target V
    myword = pm.tier('word').label_at(v.center).text

    # get R or L timepoints from words, if present, rather than vowels' timepoints
    # TODO does not change match object returned above - problem?
    if (pm.tier('phone').next(v).text == "L") or (pm.tier('phone').next(v).text == "R"):
        v = pm.tier('phone').next(v)
    if (pm.tier('phone').prev(v).text == "L") or (pm.tier('phone').prev(v).text == "R"):
        v = pm.tier('phone').prev(v)

    # collect PC information.
    if frames is None:
        if args.convert:
            frames = np.empty([len(e.acquisitions)] + list(conv_img.shape))
        else:
            frames = np.empty([len(e.acquisitions)] + list(a.image_reader.get_frame(0).shape)) * np.nan
    
    phase.append(a.runvars.phase)
    trial.append(idx) # TODO does this work for counterbalance order?
    tstamp.append(a.timestamp)

    # HOOF fix - CMUdict has UW1 for the word
    if myword == "HOOF":
        phone.append("UH1")
    else:
        phone.append(v.text)

    if args.convert:
        mid, mid_lab, mid_repl = a.frame_at(v.center,missing_val="prev", convert=True)
    else:
        mid, mid_lab, mid_repl = a.frame_at(v.center,missing_val="prev")

        
    # image checking and exclusion from set if stuck or "white"
    # TODO initial frame comparisons occasionally catch nothing??
    if is_white_bpr(a.abs_image_file):
        with open(logfile, "a") as log:
            log.write("SKIPPING acq {:} ({:})\tWhite fan of death in BPR file\n".format(a.timestamp, v))
        continue

    if is_frozen_bpr(a.abs_image_file):
        with open(logfile, "a") as log:
            log.write("SKIPPING acq {:} ({:})\tFrozen BPR file\n".format(a.timestamp, v))
        continue

    # checking that the midpoint frame was actually recorded; excludes acquisition if closest available frame is too far away
    if mid is None:
        if mid_repl is None:
            with open(logfile, "a") as log:
                log.write("SKIPPING acq {:} ({:})\tNo frames to re-select\n".format(a.timestamp, v))
                continue
        else:
            if abs(mid_lab.center - v.center) > threshhold:   
                with open(logfile, "a") as log:
                    log.write("SKIPPING acq {:} ({:})\tNo frames to re-select\n".format(a.timestamp, v))
                    continue
            else:
                mid = mid_repl
                
    # TODO log compression or thresholding brightening algorithm?
    mid = ndimage.median_filter(mid, 5) # comment out if no denoising median filter desired
    frames[idx,:,:] = mid

# # # generate PCA objects over collected arrays # # #

# subset data to remove unwanted subsets of trials before running PCA. 
# By default, the script will filter out all L, R, ER words. using "include_" flags includes relevant set.

def make_removal_bool(*bools):
    """Pass in some number of boolean arrays that pick out as True the segments that want to be removed.
    Return a single boolean array that marks these segments as False."""
    return(np.invert([np.any(f) for f in zip(*bools)]))

NAN_bool = np.isnan(frames).any(axis=(1,2))

# subset based on values in args and whether or not NAN.
if args.include_r:
    if args.include_l:
        if args.include_er:
            # remove only NANs
            mybool = make_removal_bool(NAN_bool)
        else:
            # remove ER (and NANs ...)
            mybool = make_removal_bool(ER_bool, NAN_bool)
    else:
        if args.include_er:
            # remove L
            mybool = make_removal_bool(L_bool, NAN_bool)
        else:
            # remove L and ER
            mybool = make_removal_bool(L_bool, ER_bool, NAN_bool)
else:
    if args.include_l:
        if args.include_er:
            # remove R
            mybool = make_removal_bool(R_bool, NAN_bool)
        else:
            # remove R and ER
            mybool = make_removal_bool(R_bool, ER_bool, NAN_bool)
    else:
        if args.include_er:
            # remove L and R
            mybool = make_removal_bool(R_bool, L_bool, NAN_bool)
        else:
            # remove ER, L, and R
            mybool = make_removal_bool(R_bool, L_bool, ER_bool, NAN_bool)

def subset(mylist,mybool):
    """Remove values from a list that are False for some bool."""
    myarray = np.array(mylist)
    return(np.squeeze(myarray.take(np.where(mybool),axis=0)))

# remove any indices for all objects generated above where frames have NaN values (due to skipping or otherwise)
kept_frames = subset(frames,mybool)
kept_phone = subset(phone,mybool)
kept_trial = subset(trial,mybool)
kept_phase = subset(phase,mybool)
kept_tstamp = subset(tstamp,mybool)

# the PCA is run over the kept_frames array.

# get component output count from args
n_components = args.num_components
pca = PCA(n_components=n_components)
frames_reshaped = kept_frames.reshape([kept_frames.shape[0], kept_frames.shape[1]*kept_frames.shape[2]])

pca.fit(frames_reshaped)
analysis = pca.transform(frames_reshaped)

meta_headers = ["phase","trial","timestamp","phone"]
pc_headers = ["pc"+str(i+1) for i in range(0,n_components)] # determine number of PC columns in output; changes w.r.t. n_components
headers = meta_headers + pc_headers

# save everything to pc_out for analysis.
d = np.row_stack((headers,np.column_stack((kept_phase,kept_trial,kept_tstamp,kept_phone,analysis))))
np.savetxt(pc_out, d, fmt="%s", delimiter =',')

print("Data saved. Explained variance ratio of PCs: %s" % str(pca.explained_variance_ratio_))

# # # output images describing component min/max loadings, if desired. # # #
# not advisable for very large numbers of components.
# TODO output average frame?

if args.visualize:
    image_shape = (416,69)

    for n in range(0,n_components):
        d = pca.components_[n].reshape(image_shape)
        mag = np.max(d) - np.min(d)
        d = (d-np.min(d))/mag*255
        pcn = np.flipud(e.acquisitions[0].image_converter.as_bmp(d)) # converter from any frame will work; here we use the first

        if args.flop:
            pcn = np.fliplr(pcn)

        plt.title("PC{:} min/max loadings, Part. {:}".format((n+1), e.expdir))
        plt.imshow(pcn, cmap="Greys_r") 
        file_ending = "subj{:}-pc{:}.pdf".format(e.expdir, (n+1)) # TODO figure out where this goes.
        savepath = os.path.join(e.expdir,file_ending)
        plt.savefig(savepath)

end = time.time()

elapsed_time = end - start

print("Elapsed time was about {:} seconds".format(elapsed_time))
