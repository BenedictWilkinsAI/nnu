#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
 Created on 01-02-2021 15:09:24

 [Description]
"""
__author__ ="Benedict Wilkins"
__email__ = "benrjw@gmail.com"
__status__ ="Development"

import torch
from torch.utils.data import Dataset, TensorDataset, DataLoader

import numpy as np


def chain(x, model, corrupt, n=5):
    def _chain(x):
        c = corrupt(x)
        yield c
        for i in range(n - 1):
            x = model(c)
            c = corrupt(x)
            yield c
    return [c for c in _chain(x)]

class WalkbackDataset(Dataset):
    # create a new dataset, this dataset contains all data (x,y) where y is an example from the original provided dataset
    # and x is an example generated by the walk-back corruption process, namely - corrupt(y), corrupt(model(corrupt(y)), ... 
    # the dataset takes a while to construct (as it must run the supplied dataset through the model n times). 
    # TODO - construct the dataset on the fly, storing the whole dataset is memory intensive. This would require some kind of batched indexer from a torch DataLoader

    def __init__(self, x, model, corrupt, n=5, batch_size=256, device="cpu"):
        super(WalkbackDataset, self).__init__()

        self.model = model
        self.corrupt = corrupt
        self.n = 2 + n
        self.batch_size = batch_size
        self.data = []
        self.device = device
        self.model_device = next(model.parameters()).device 

        # generate walkback data
        if not isinstance(x, Dataset):
            x = TensorDataset(x)

        loader = DataLoader(x, batch_size=batch_size, shuffle=False)
        with torch.no_grad():
            for batch_x, in loader:
                batch_x = batch_x.to(self.device, non_blocking=True)
                chain = [c.to(self.device, non_blocking=True) for c in self._chain(batch_x)]
                self.data.append((batch_x, chain))
    
    def _chain(self, x): # compute the walkback chain for a batch
        yield x # keep the original as part of the dataset
        
        x = x.to(self.model_device, non_blocking=True)
        c = self.corrupt(x)
        yield c
        for i in range(self.n - 2):
            x = self.model(c)
            c = self.corrupt(x)
            yield c
    
    def __getitem__(self, i):
        if 0 > i > len(self):
            raise IndexError()

        batch_size = self.batch_size 
        
        b_index = i // (batch_size * self.n)
        i = i % (batch_size * self.n)

        batch_size = len(self.data[b_index][0]) # handle edge case if last batch

        c_index = i // batch_size
        e_index = i % batch_size

        y, x = self.data[b_index]
        x = x[c_index][e_index]
        y = y[e_index]
        return x, y
        
    def __len__(self):
        return ((len(self.data) - 1) * (self.batch_size) * self.n) + (self.n * len(self.data[-1][0]))













