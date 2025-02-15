"""
Takes as input a dictionary file and python pickle file containing a 
dictionary with pretrained word embeddings, and computes an initial encoding 
matrix W_emb based on these.

Note: Certain (dialogue-specific) words are not looked up in the pretrained word embedding dictionary, including start-of-utterance, end-of-utterance and others. 

Usage example for MT embeddings:

    python convert-wordemb-dict2emb-matrix.py tests/data/ttrain.dict.pkl WordEmb/D_german_50k_500k_168h.pkl --emb_dim 15 temb_pretrained
    python convert-wordemb-dict2emb-matrix.py Data/Training.dict.pkl WordEmb/D_german_50k_500k_168h.pkl --apply_spelling_corrections --emb_dim 300 OutMat

Usage example for Word2Vec embeddings:

    python convert-wordemb-dict2emb-matrix.py Data/Training.dict.pkl WordEmb/GoogleNews-vectors-negative300.bin --apply_spelling_corrections --emb_dim 300 Word2Vec_WordEmb

@author Iulian Vlad Serban
"""


import collections
import numpy
import operator
import os
import sys
import logging
import cPickle
import itertools
from collections import Counter
from utils import *

from sklearn.decomposition import PCA
from sklearn import preprocessing

from wordsegment import segment
import enchant
enchd = enchant.Dict("en_US")

alphabet = 'abcdefghijklmnopqrstuvwxyz'

def safe_pickle(obj, filename):
    if os.path.isfile(filename):
        logger.info("Overwriting %s." % filename)
    else:
        logger.info("Saving to %s." % filename)

    with open(filename, 'wb') as f:
        cPickle.dump(obj, f, protocol=cPickle.HIGHEST_PROTOCOL)

# Takes a word as input and creates a set of one-character changes to the word (splits, transposes, replaces, insert etc.).
def edits1(word):
    splits     = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    
    # We exclude deletes because it much more frequent that a word in a movie manuscript (or Twitter status?) has
    # a letter extra than a letter less.
    #deletes    = [a + b[1:] for a, b in splits if b]
    deletes = []
    transposes = [a + b[1] + b[0] + b[2:] for a, b in splits if len(b)>1]
    replaces   = [a + c + b[1:] for a, b in splits for c in alphabet if b]
    inserts    = [a + c + b     for a, b in splits for c in alphabet]
    return set(deletes + transposes + replaces + inserts)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('covert-wordemb-dict2emb-matrix')

# These are the words, which won't be looked upn in the pretrained word embedding dictionary
non_word_tokens = ['<s>', '</s>', '<t>', '</t>', '<unk>', '.', ',', '``', '\'\'', '[', ']', '`', '-', '--', '\'', '<pause>', '<first_speaker>', '<second_speaker>', '<third_speaker>', '<minor_speaker>', '<voice_over>', '<off_screen>', '</d>']

print 'The following non-word tokens will not be extracted from the pretrained embeddings: ', non_word_tokens

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("model_dictionary", type=str, help="Model dictionary generated by convert-text2dict.py")
parser.add_argument("embedding_dictionary", type=str, help="Python vocabulary (pkl file) which contains a dictionary for every word, or Word2vec word embedding (bin file)")
parser.add_argument("--emb_dim", type=int, default=300, help="Dimensionality of the generated word embedding matrix. PCA is performed to reduce dimensionality to this size.")
parser.add_argument("--std_dev", type=float, default=0.01, help="Standard deviation of the produced word embedding.")

parser.add_argument("--apply_spelling_corrections", action='store_true', help="If true, will apply spelling corrections to all words not found in the pretrained word embedding dictionary. These corrections are based on regex expressions and the enchant spelling corrector.")


parser.add_argument("output_matrix", type=str, help="Generated word embedding matrix (pkl file)")


args = parser.parse_args()

if args.embedding_dictionary[-4:] == '.bin':
    logger.info("Word2Vec embeddings given as input")
    uses_word2vec = True
else:
    logger.info("Python (pkl) file given as input")
    uses_word2vec = False


if args.apply_spelling_corrections:
    logger.info("Applying spelling corrections")
else:
    logger.info("No spelling corrections will be applied")

emb_dim = args.emb_dim
logger.info("Final word embedding dim: %d" % emb_dim)

std_dev = args.std_dev
logger.info("Final standard deviation: %f" % std_dev)

if not os.path.isfile(args.model_dictionary):
    raise Exception("Model dictionary file not found!")

if not os.path.isfile(args.embedding_dictionary):
    raise Exception("Embedding dictionary file not found!")


# Load model dictionary
model_dict = cPickle.load(open(args.model_dictionary, 'rb'))

str_to_idx = dict([(tok, tok_id) for tok, tok_id, _, _ in model_dict])
i_dim = len(str_to_idx.keys())
logger.info("Vocabulary size: %d" % i_dim)

word_freq = dict([(tok_id, freq) for _, tok_id, freq, _ in model_dict])

# Load pretrained word embeddings
if uses_word2vec:
    import gensim, logging
    embedding_dict = gensim.models.Word2Vec.load_word2vec_format(args.embedding_dictionary, binary=True)
else:
    embedding_dict = cPickle.load(open(args.embedding_dictionary, "rb" ) )

if uses_word2vec:
    raw_emb_dim = embedding_dict['hello'].shape[0]
else:
    raw_emb_dim = embedding_dict[embedding_dict.keys()[0]].shape[0]

logger.info("Raw word embedding dim: %d" % raw_emb_dim)

W_emb_raw = numpy.zeros((i_dim, raw_emb_dim))

words_found = 0
unique_word_indices_found = []

unique_words_left_out = []
unique_word_indices_left_out = []
word_freq_left_out = []
total_freq_left_out = 0
total_freq = 0

total_freq_non_word = 0

# Go through every word in the model dictionary and add the corresponding word embedding to W_emb_raw
for key in str_to_idx.iterkeys():
    index = str_to_idx[key]
    total_freq = total_freq + word_freq[index]

    if key in non_word_tokens:
        unique_words_left_out.append(key)
        unique_word_indices_left_out.append(index)
        word_freq_left_out.append(word_freq[index])
        total_freq_left_out = total_freq_left_out + word_freq[index]
        total_freq_non_word = total_freq_non_word + word_freq[index]
    elif key in embedding_dict: # Otherwise, check if word is in word embedding dict
        W_emb_raw[index, :] = embedding_dict[key]
        unique_word_indices_found.append(index)
        words_found = words_found + 1
    elif len(key) > 3 and (key[-1] == '.' and key[0:len(key)-1] in embedding_dict): # Remove punctuation mark
        print 'Assuming ' + str(key) + ' -> ' + str(key[0:len(key)-1])
        W_emb_raw[index, :] = embedding_dict[key[0:len(key)-1]]
        unique_word_indices_found.append(index)
        words_found = words_found + 1
    elif key.title() in embedding_dict: # Check if word with capital first letter exists in word embedding dict
        W_emb_raw[index, :] = embedding_dict[key.title()]
        unique_word_indices_found.append(index)
        words_found = words_found + 1
    elif key.upper() in embedding_dict: # Check if word capitalized exists in word embedding dict
        W_emb_raw[index, :] = embedding_dict[key.upper()]
        unique_word_indices_found.append(index)
        words_found = words_found + 1
    else: # Use spelling checker and heuristics to assign word an embedding vector
        word_was_found = False
        if args.apply_spelling_corrections == True:
            # Many words contain accidental '-' symbols, check if there are valid words without them and between them
            if key.replace('-', '') in embedding_dict:
                W_emb_raw[index, :] = embedding_dict[key.replace('-', '')]
                words_found = words_found + 1
                word_was_found = True
            elif '-' in key:
                subwords_split = key.split('-')
                for subword in subwords_split:
                    if subword in embedding_dict:
                        W_emb_raw[index, :] = embedding_dict[subword]
                        words_found = words_found + 1
                        word_was_found = True
                        break

            # Last resort is to use a spelling checker to correct the word
            if word_was_found == False:
                suggestions = enchd.suggest(key)

                # Suggestions are only allowed to be two "edits" away from the original word
                allowed_suggestions = set(e2 for e1 in edits1(key) for e2 in edits1(e1))
                for suggestion in suggestions:
                    if (suggestion in allowed_suggestions) and (suggestion in embedding_dict):
                        W_emb_raw[index, :] = embedding_dict[suggestion]
                        words_found = words_found + 1
                        word_was_found = True
                        print 'Correcting ' + str(key) + ' -> ' + str(suggestion)
                        break

        if word_was_found == True:
            unique_word_indices_found.append(index)
        elif word_was_found == False:
            unique_words_left_out.append(key)
            unique_word_indices_left_out.append(index)
            word_freq_left_out.append(word_freq[index])
            total_freq_left_out = total_freq_left_out + word_freq[index]


unique_words_missing = i_dim - words_found
  
logger.info("Unique words found in model dictionary and word embeddings: %d" % words_found)
logger.info("Unique words left out: %d" % unique_words_missing)
logger.info("Terms in corpus: %d" % total_freq)
logger.info("Terms left out: %d" % total_freq_left_out)
logger.info("Percentage terms left out: %f" % (float(total_freq_left_out)/float(total_freq)))

logger.info("non-word terms in corpus: %d" % total_freq_non_word)
logger.info("Percentage non-word terms in corpus: %f" % (float(total_freq_non_word)/float(total_freq)))


print 'unique_words_left_out', unique_words_left_out

assert(raw_emb_dim >= emb_dim)

# Use PCA to reduce dimensionality appropriately
if raw_emb_dim > emb_dim:
    pca = PCA(n_components=emb_dim)
    W_emb = numpy.zeros((i_dim, emb_dim))
    W_emb[unique_word_indices_found, :] = pca.fit_transform(W_emb_raw[unique_word_indices_found])
else: # raw_emb_dim == emb_dim:
    W_emb = W_emb_raw

# Set mean to zero and standard deviation to 0.01
W_emb[unique_word_indices_found, :] = preprocessing.scale(W_emb[unique_word_indices_found], with_std=std_dev)

# Initialize words without embeddings randomly
seed = 123456
rng = numpy.random.RandomState(seed)
randmat = NormalInit(rng, unique_words_missing, emb_dim)
for i in range(unique_words_missing):
    W_emb[unique_word_indices_left_out[i], :] = randmat[i, :]

# Create mask matrix, which represents word embeddings not pretrained
W_emb_nonpretrained_mask = numpy.zeros((i_dim, emb_dim))
for i in range(unique_words_missing):
    W_emb_nonpretrained_mask[unique_word_indices_left_out[i], :] = 1

safe_pickle([W_emb, W_emb_nonpretrained_mask], args.output_matrix + ".pkl")

