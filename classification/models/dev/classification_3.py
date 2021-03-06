# Copyright 2017 Google Inc. and Skytruth Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function, division
import tensorflow as tf
from classification import utility
from collections import namedtuple
import tensorflow.contrib.slim as slim
import logging
import numpy as np

from classification.model import ModelBase

from classification.objectives import (SummaryObjective, TrainNetInfo,
                                       RegressionObjective,
                                       MultiClassificationObjective)

from .tf_layers import conv1d_layer, dense_layer, misconception_layer, dropout_layer
from .tf_layers import batch_norm, separable_conv1d_layer, leaky_rectify

TowerParams = namedtuple("TowerParams", ["filter_widths", "pool_size",
                                         "pool_stride", "keep_prob", "shunt"])


class Model(ModelBase):

    final_size = 96

    filter_count = 80
    tower_depth = 7

    def __init__(self, num_feature_dimensions, vessel_metadata, metrics):
        super(self.__class__, self).__init__(num_feature_dimensions,
                                             vessel_metadata)

        def length_or_none(mmsi):
            length = vessel_metadata.vessel_label('length', mmsi)
            if length == '':
                return None

            return np.float32(length)

        self.classification_objective = MultiClassificationObjective(
            "Multiclass", "Vessel-class", vessel_metadata, metrics=metrics)

        self.summary_objective = SummaryObjective(
            'histograms', 'Histograms', metrics=metrics)

        self.objectives = [self.classification_objective,
                           self.summary_objective]

    @property
    def max_window_duration_seconds(self):
        return 90 * 24 * 3600

    @property
    def window_max_points(self):
        length = self.final_size
        for _ in range(self.tower_depth - 1):
            length = 2 * length + 4
        length = 2 * length + 2
        return length

    def build_stack(self, current, is_training):

        for i in range(self.tower_depth):
            with tf.variable_scope('tower-segment-{}'.format(i + 1)):

                # Misconception stack

                mc = misconception_layer(
                    current,
                    self.filter_count,
                    is_training,
                    filter_size=3,
                    stride=2 if (i == 0) else 1,
                    padding="VALID",
                    name='misconception-{}a'.format(1))

                if i > 0:
                    mc = tf.nn.elu(batch_norm(mc, is_training=is_training))
                    mc = misconception_layer(
                        mc,
                        self.filter_count,
                        is_training,
                        filter_size=3,
                        stride=2,
                        padding="VALID",
                        name='misconception-{}b'.format(1))
                    shunt = tf.nn.avg_pool(
                        current, [1, 1, 5, 1], [1, 1, 2, 1], padding="VALID")
                    current = mc + shunt
                else:
                    current = mc

                current = tf.nn.elu(
                    batch_norm(
                        current, is_training=is_training))

        #
        current = slim.flatten(current)
        current = dropout_layer(current, is_training, 0.1)

        return current

    def build_model(self, is_training, current):

        self.summary_objective.build(current)

        with tf.variable_scope('classification-tower'):
            output = self.build_stack(current, is_training)
            self.classification_objective.build(output)

    def build_inference_net(self, features, timestamps, mmsis):

        self.build_model(tf.constant(False), features)

        evaluations = []
        for obj in self.objectives:
            evaluations.append(obj.build_evaluation(timestamps, mmsis))

        return evaluations

    def build_training_net(self, features, timestamps, mmsis):

        self.build_model(tf.constant(True), features)

        trainers = []
        for obj in self.objectives:
            trainers.append(obj.build_trainer(timestamps, mmsis))

        optimizer = tf.train.AdamOptimizer()

        return TrainNetInfo(optimizer, trainers)
