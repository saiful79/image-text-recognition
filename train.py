# -*- coding: utf-8 -*-
################################################################
# Discription : Image text to recognition using RCNN+LSTM with CTC loss function   
# Author      : Saiful Islam
# Email       : saiful79@gmail.com
# Copyright   : saiful79/github.io
# licence     : MIT
#################################################################
import os
import itertools
import codecs
import re
import datetime
import cairocffi as cairo
import editdistance
import numpy as np
from scipy import ndimage
import pylab
from keras import backend as K
from keras.layers.convolutional import Conv2D, MaxPooling2D
from keras.layers import Input, Dense, Activation, BatchNormalization, Dropout
from keras.layers import Reshape, Lambda
from keras.layers.merge import add, concatenate
from keras.models import Model
from keras.layers.recurrent import GRU
from keras.optimizers import SGD, Adam, RMSprop
from keras.utils.data_utils import get_file
from keras.preprocessing import image
import keras.callbacks
import os
import string
import random
import cv2
import skimage as sk
from scipy import ndimage
from matplotlib import pyplot as plt
import tensorflow as tf
from keras.backend.tensorflow_backend import set_session
import glob

config = tf.ConfigProto()
config.gpu_options.per_process_gpu_memory_fraction = 0.2

set_session(tf.Session(config=config))

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

OUTPUT_DIR = 'models'


regex = r'^[a-z ]+$'
alphabet = string.digits + string.punctuation + 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ '
np.random.seed(55)

def paint_text(image_file_name, w, h, rotate=False, ud=False, multi_fonts=False):
    img = cv2.imread(image_file_name)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray,(w,h))
    a = gray.astype(np.float32) / 255
    a = np.expand_dims(a, 0)
    return a


def shuffle_mats_or_lists(matrix_list, stop_ind=None):
    ret = []
    assert all([len(i) == len(matrix_list[0]) for i in matrix_list])
    len_val = len(matrix_list[0])
    if stop_ind is None:
        stop_ind = len_val
    assert stop_ind <= len_val

    a = list(range(stop_ind))
    np.random.shuffle(a)
    a += list(range(stop_ind, len_val))
    for mat in matrix_list:
        if isinstance(mat, np.ndarray):
            ret.append(mat[a])
        elif isinstance(mat, list):
            ret.append([mat[i] for i in a])
        else:
            raise TypeError('`shuffle_mats_or_lists` only supports '
                            'numpy.array and list objects.')
    return ret


# Translation of characters to unique integer values
def text_to_labels(text):
    ret = []
    for char in text:
        # exclude punc char
        if char in '"%\')(+-,:':
            char = ' ' # replace punc by ' '
        ret.append(alphabet.find(char))
    return ret
# Reverse translation of numerical classes back to characters
def labels_to_text(labels):
    ret = []
    for c in labels:
        if c == len(alphabet):  # CTC Blank
            ret.append("")
        else:
            ret.append(alphabet[c])
    return "".join(ret)


# only a-z and space..probably not to difficult
# to expand to uppercase and symbols

def is_valid_str(in_str):
    # search = re.compile(regex, re.UNICODE).search
    # return bool(search(in_str))
    return True


# Uses generator functions to supply train/test with
class TextImageGenerator(keras.callbacks.Callback):

    def __init__(self, image_data_path, minibatch_size,
                 img_w, img_h, downsample_factor, val_split,
                 absolute_max_string_len=20):

        self.image_data_path = image_data_path
        self.minibatch_size = minibatch_size
        self.img_w = img_w
        self.img_h = img_h
        self.downsample_factor = downsample_factor
        self.val_split = val_split
        self.blank_label = self.get_output_size() - 1
        self.absolute_max_string_len = absolute_max_string_len


        self.images_dir = image_data_path

    def get_output_size(self):
        return len(alphabet) + 1

    # num_words can be independent of the epoch size due to the use of generators
    # as max_string_len grows, num_words can grow
    def build_word_list(self, num_words, max_string_len=None):
        assert max_string_len <= self.absolute_max_string_len
        assert num_words % self.minibatch_size == 0
        assert (self.val_split * num_words) % self.minibatch_size == 0
        
        self.num_words = num_words
        self.string_list = [''] * self.num_words
        tmp_string_list = []
        self.max_string_len = max_string_len
        self.Y_data = np.ones([self.num_words, self.absolute_max_string_len]) * -1
        self.X_text = []
        self.X_text_image_file_name = []
        self.Y_len = [0] * self.num_words
        # randomly shuffle list each time built the list

        def _is_length_of_word_valid(word):
            # print('in')
            #todo by saiful
            return len(word) <= 20 # here use maximum character sequence

        #todo by saiful
        image_dir_list  = glob.glob(self.images_dir+"/*")
        random.shuffle(image_dir_list)

        gt_text_and_fname_pair = [] 
        for image_file in image_dir_list:

            img_abs_path = image_file

            if len(gt_text_and_fname_pair) == self.num_words:
                break
            text = image_file.split("/")[-1].split('_')[1]
            
            if is_valid_str(text) and _is_length_of_word_valid(text):
                gt_text_and_fname_pair.append((text, img_abs_path))
        
        # print('total word found:', len(gt_text_and_fname_pair))
        if len(gt_text_and_fname_pair) != self.num_words:
            raise IOError('Could not pull enough words from supplied image data directory.')


        for i, data in enumerate(gt_text_and_fname_pair):
            word = data[0]
            f_name = data[1]
            self.Y_len[i] = len(word)
            self.Y_data[i, 0:len(word)] = text_to_labels(word)
            self.X_text.append(word)
            self.X_text_image_file_name.append(f_name)
        
        self.Y_len = np.expand_dims(np.array(self.Y_len), 1)
        self.cur_val_index = self.val_split
        self.cur_train_index = 0

    def get_batch(self, index, size, train):
        if K.image_data_format() == 'channels_first':
            X_data = np.ones([size, 1, self.img_w, self.img_h])
        else:
            X_data = np.ones([size, self.img_w, self.img_h, 1])

        labels = np.ones([size, self.absolute_max_string_len])
        input_length = np.zeros([size, 1])
        label_length = np.zeros([size, 1])
        source_str = []
        for i in range(size):
            _data = self.paint_func(self.X_text_image_file_name[index + i])[0, :, :].T
            # print('data_shape', _data.shape)
            X_data[i, 0:self.img_w, :, 0] = _data 
            
            labels[i, :] = self.Y_data[index + i]
            input_length[i] = self.img_w // self.downsample_factor - 2
            label_length[i] = self.Y_len[index + i]
            source_str.append(self.X_text[index + i])

        inputs = {
            'the_input': X_data,
            'the_labels': labels,
            'input_length': input_length,
            'label_length': label_length,
            'source_str': source_str  # used for visualization only
        }
        outputs = {'ctc': np.zeros([size])}  # dummy data for dummy loss function
        
        return (inputs, outputs)

    def next_train(self):
        while 1:
            ret = self.get_batch(self.cur_train_index,
                                 self.minibatch_size, train=True)
            self.cur_train_index += self.minibatch_size
            if self.cur_train_index >= self.val_split:
                self.cur_train_index = self.cur_train_index % 32
            yield ret

    def next_val(self):
        while 1:
            ret = self.get_batch(self.cur_val_index,
                                 self.minibatch_size, train=False)
            self.cur_val_index += self.minibatch_size
            if self.cur_val_index >= self.num_words:
                self.cur_val_index = self.val_split + self.cur_val_index % 32
            yield ret

    def on_train_begin(self, logs={}):
        print('on_train_begin')
        #todo by saiful
        self.build_word_list(9280, 20) #9280
        self.paint_func = lambda text: paint_text(
            text, self.img_w, self.img_h,
            rotate=False, ud=False, multi_fonts=False)

    def on_epoch_begin(self, epoch, logs={}):
        print('access funciton on_epoch_begin')
        if 6 <= epoch < 9:
            #todo by saiful
            self.build_word_list(9280, 20)
            print('6 <= epoch < 9')

        elif epoch >= 9 and epoch <= 20:
            #todo by saiful
            self.build_word_list(9280, 20)
            print('epoch >= 9 and epoch <= 20')

        elif epoch >= 21 and epoch < 40:
            #todo by saiful
            self.build_word_list(9280, 20)
            print('epoch >= 21 and epoch < 40')

        
        if epoch >= 40:
            #todo by saiful
            self.build_word_list(9280, 20)
            print('epoch >= 40')



def ctc_lambda_func(args):
    y_pred, labels, input_length, label_length = args
    # the 2 is critical here since the first couple outputs of the RNN
    # tend to be garbage:
    y_pred = y_pred[:, 2:, :]
    return K.ctc_batch_cost(labels, y_pred, input_length, label_length)


# For a real OCR application, this should be beam search with a dictionary
# and language model.  For this example, best path is sufficient.

def decode_batch(test_func, word_batch):
    out = test_func([word_batch])[0]
    ret = []
    for j in range(out.shape[0]):
        out_best = list(np.argmax(out[j, 2:], 1))
        out_best = [k for k, g in itertools.groupby(out_best)]
        outstr = labels_to_text(out_best)
        ret.append(outstr)
    return ret


class VizCallback(keras.callbacks.Callback):

    def __init__(self, run_name, test_func, text_img_gen, num_display_words=6):
        self.test_func = test_func
        self.output_dir = os.path.join(
            OUTPUT_DIR, run_name)
        self.text_img_gen = text_img_gen
        self.num_display_words = num_display_words
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def show_edit_distance(self, num):
        num_left = num
        mean_norm_ed = 0.0
        mean_ed = 0.0
        while num_left > 0:
            word_batch = next(self.text_img_gen)[0]
            num_proc = min(word_batch['the_input'].shape[0], num_left)
            decoded_res = decode_batch(self.test_func,
                                       word_batch['the_input'][0:num_proc])
            for j in range(num_proc):
                edit_dist = editdistance.eval(decoded_res[j],
                                              word_batch['source_str'][j])
                mean_ed += float(edit_dist)
                mean_norm_ed += float(edit_dist) / len(word_batch['source_str'][j])
            num_left -= num_proc
        mean_norm_ed = mean_norm_ed / num
        mean_ed = mean_ed / num
        print('\nOut of %d samples:  Mean edit distance:'
              '%.3f Mean normalized edit distance: %0.3f'
              % (num, mean_ed, mean_norm_ed))

    def on_epoch_end(self, epoch, logs={}):
        # results = [logs['loss'], logs['val_loss'], logs['acc'], logs['val_acc']]
        self.model.save_weights(
            os.path.join(self.output_dir, 'weights-fine-tune%02d-loss-%02f.h5' % (epoch, logs['loss'])))
        self.show_edit_distance(256)
        word_batch = next(self.text_img_gen)[0]
        res = decode_batch(self.test_func,
                           word_batch['the_input'][0:self.num_display_words])
        if word_batch['the_input'][0].shape[0] < 256:
            cols = 2
        else:
            cols = 1
        for i in range(self.num_display_words):
            pylab.subplot(self.num_display_words // cols, cols, i + 1)
            if K.image_data_format() == 'channels_first':
                the_input = word_batch['the_input'][i, 0, :, :]
            else:
                the_input = word_batch['the_input'][i, :, :, 0]
            pylab.imshow(the_input.T, cmap='Greys_r')
            pylab.xlabel(
                'Truth = \'%s\'\nDecoded = \'%s\'' %
                (word_batch['source_str'][i], res[i]))
        fig = pylab.gcf()
        fig.set_size_inches(10, 13)
        pylab.savefig(os.path.join(self.output_dir, 'e%02d.png' % (epoch)))
        pylab.close()


def train(run_name, start_epoch, stop_epoch, img_w):
    # Input Parameters
    img_h = 64
    #todo by saiful
    # words_per_epoch you have to calculate divided by minibatch_size
    words_per_epoch = 9280 
    val_split = 0.1
    val_words = int(words_per_epoch * (val_split))

    # Network parameters
    conv_filters = 16
    kernel_size = (3, 3)
    pool_size = 2
    time_dense_size = 32
    rnn_size = 512
    minibatch_size = 32

    if K.image_data_format() == 'channels_first':
        input_shape = (1, img_w, img_h)
    else:
        input_shape = (img_w, img_h, 1)


    
    imgs_data_dir = 'word' # data directory
    img_gen = TextImageGenerator(
        image_data_path=imgs_data_dir,
        minibatch_size=minibatch_size,
        img_w=img_w,
        img_h=img_h,
        downsample_factor=(pool_size ** 2),
        val_split=words_per_epoch - val_words
    )
    

    act = 'relu'

    input_data = Input(name='the_input', shape=input_shape, dtype='float32')
    inner = Conv2D(conv_filters, kernel_size, padding='same',
                   activation=act, kernel_initializer='he_normal',
                   name='conv1')(input_data)
    inner = MaxPooling2D(pool_size=(pool_size, pool_size), name='max1')(inner)
    inner = Conv2D(conv_filters, kernel_size, padding='same',
                   activation=act, kernel_initializer='he_normal',
                   name='conv2')(inner)
    inner = MaxPooling2D(pool_size=(pool_size, pool_size), name='max2')(inner)
    # inner = Dropout(0.3)(inner)
    # conv3 added
    inner = Conv2D(16, (2, 2), padding='same',
                   activation=act, kernel_initializer='he_normal',
                   name='conv3')(inner)
    # inner = BatchNormalization()(inner)
    # inner = Dropout(0.2)(inner)

    conv_to_rnn_dims = (img_w // (pool_size ** 2),
                        (img_h // (pool_size ** 2)) * conv_filters)
    inner = Reshape(target_shape=conv_to_rnn_dims, name='reshape')(inner)

    # cuts down input size going into RNN:
    inner = Dense(time_dense_size, activation=act, name='dense1')(inner)

    # Two layers of bidirectional GRUs
    # GRU seems to work as well, if not better than LSTM:
    gru_1 = GRU(rnn_size, return_sequences=True,
                kernel_initializer='he_normal', name='gru1')(inner)
    gru_1b = GRU(rnn_size, return_sequences=True,
                 go_backwards=True, kernel_initializer='he_normal',
                 name='gru1_b')(inner)
    gru1_merged = add([gru_1, gru_1b])
    gru_2 = GRU(rnn_size, return_sequences=True,
                kernel_initializer='he_normal', name='gru2')(gru1_merged)
    gru_2b = GRU(rnn_size, return_sequences=True, go_backwards=True,
                 kernel_initializer='he_normal', name='gru2_b')(gru1_merged)

    # transforms RNN output to character activations:
    inner = Dense(img_gen.get_output_size(), kernel_initializer='he_normal',
                  name='dense2')(concatenate([gru_2, gru_2b]))
    y_pred = Activation('softmax', name='softmax')(inner)
    Model(inputs=input_data, outputs=y_pred).summary()
    # exit()

    labels = Input(name='the_labels',
                   shape=[img_gen.absolute_max_string_len], dtype='float32')
    input_length = Input(name='input_length', shape=[1], dtype='int64')
    label_length = Input(name='label_length', shape=[1], dtype='int64')
    # Keras doesn't currently support loss funcs with extra parameters
    # so CTC loss is implemented in a lambda layer
    loss_out = Lambda(
        ctc_lambda_func, output_shape=(1,),
        name='ctc')([y_pred, labels, input_length, label_length])
        
    lr = 0.001 / 30.0 # decrease 20 % 
    optimizer = Adam(lr=lr)
    model = Model(inputs=[input_data, labels, input_length, label_length],
                  outputs=loss_out)

    # the loss calc occurs elsewhere, so use a dummy lambda func for the loss
    model.compile(loss={'ctc': lambda y_true, y_pred: y_pred}, optimizer=optimizer)
    if start_epoch > 0:
        weight_file = os.path.join(
            OUTPUT_DIR,
            os.path.join(run_name, 'weights%02d.h5' % (start_epoch - 1))
        )
        model.load_weights(weight_file)
    # captures output of softmax so we can decode the output during visualization
    test_func = K.function([input_data], [y_pred])

    viz_cb = VizCallback(run_name, test_func, img_gen.next_val())

    model.fit_generator(
        generator=img_gen.next_train(),
        steps_per_epoch=(words_per_epoch - val_words) // minibatch_size,
        epochs=stop_epoch,
        validation_data=img_gen.next_val(),
        validation_steps=val_words // minibatch_size,
        callbacks=[viz_cb, img_gen],
        initial_epoch=start_epoch
    )
    # serialize model to JSON
    # model_json = model.to_json()
    # with open("model.json", "w") as json_file:
    #     json_file.write(model_json)


if __name__ == '__main__':
    run_name = 'model_dir'
    train(run_name, 0, 500, 128)