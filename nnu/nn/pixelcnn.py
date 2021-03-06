#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    Created on 27-01-2021 16:15:42
    Implementation taken from: https://github.com/anordertoreclaim/PixelCNN and adapted.
"""
__author__ ="Benedict Wilkins"
__email__ = "benrjw@gmail.com"
__status__ ="Development"

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

class CroppedConv2d(nn.Conv2d):
    def __init__(self, *args, **kwargs):
        super(CroppedConv2d, self).__init__(*args, **kwargs)

    def forward(self, x):
        x = super(CroppedConv2d, self).forward(x)

        kernel_height, _ = self.kernel_size
        res = x[:, :, 1:-kernel_height, :]
        shifted_up_res = x[:, :, :-kernel_height-1, :]

        return res, shifted_up_res


class MaskedConv2d(nn.Conv2d):
    def __init__(self, *args, mask_type, data_channels, **kwargs):
        super(MaskedConv2d, self).__init__(*args, **kwargs)

        assert mask_type in ['A', 'B'], 'Invalid mask type.'

        out_channels, in_channels, height, width = self.weight.size()
        yc, xc = height // 2, width // 2

        mask = np.zeros(self.weight.size(), dtype=np.float32)
        mask[:, :, :yc, :] = 1
        mask[:, :, yc, :xc + 1] = 1

        def cmask(out_c, in_c):
            a = (np.arange(out_channels) % data_channels == out_c)[:, None]
            b = (np.arange(in_channels) % data_channels == in_c)[None, :]
            return a * b

        for o in range(data_channels):
            for i in range(o + 1, data_channels):
                mask[cmask(o, i), yc, xc] = 0

        if mask_type == 'A':
            for c in range(data_channels):
                mask[cmask(c, c), yc, xc] = 0

        mask = torch.from_numpy(mask).float()

        self.register_buffer('mask', mask)

    def forward(self, x):
        self.weight.data *= self.mask
        x = super(MaskedConv2d, self).forward(x)
        return x

class CausalBlock(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, data_channels):
        super(CausalBlock, self).__init__()
        self.split_size = out_channels

        self.v_conv = CroppedConv2d(in_channels,
                                    2 * out_channels,
                                    (kernel_size // 2 + 1, kernel_size),
                                    padding=(kernel_size // 2 + 1, kernel_size // 2))
        self.v_fc = nn.Conv2d(in_channels,
                              2 * out_channels,
                              (1, 1))
        self.v_to_h = nn.Conv2d(2 * out_channels,
                                2 * out_channels,
                                (1, 1))

        self.h_conv = MaskedConv2d(in_channels,
                                   2 * out_channels,
                                   (1, kernel_size),
                                   mask_type='A',
                                   data_channels=data_channels,
                                   padding=(0, kernel_size // 2))
        self.h_fc = MaskedConv2d(out_channels,
                                 out_channels,
                                 (1, 1),
                                 mask_type='A',
                                 data_channels=data_channels)

    def forward(self, image):
        v_out, v_shifted = self.v_conv(image)
        v_out += self.v_fc(image)
        v_out_tanh, v_out_sigmoid = torch.split(v_out, self.split_size, dim=1)
        v_out = torch.tanh(v_out_tanh) * torch.sigmoid(v_out_sigmoid)

        h_out = self.h_conv(image)
        v_shifted = self.v_to_h(v_shifted)
        h_out += v_shifted
        h_out_tanh, h_out_sigmoid = torch.split(h_out, self.split_size, dim=1)
        h_out = torch.tanh(h_out_tanh) * torch.sigmoid(h_out_sigmoid)
        h_out = self.h_fc(h_out)

        return v_out, h_out


class GatedBlock(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, data_channels):
        super(GatedBlock, self).__init__()
        self.split_size = out_channels

        self.v_conv = CroppedConv2d(in_channels,
                                    2 * out_channels,
                                    (kernel_size // 2 + 1, kernel_size),
                                    padding=(kernel_size // 2 + 1, kernel_size // 2))
        self.v_fc = nn.Conv2d(in_channels,
                              2 * out_channels,
                              (1, 1))
        self.v_to_h = MaskedConv2d(2 * out_channels,
                                   2 * out_channels,
                                   (1, 1),
                                   mask_type='B',
                                   data_channels=data_channels)

        self.h_conv = MaskedConv2d(in_channels,
                                   2 * out_channels,
                                   (1, kernel_size),
                                   mask_type='B',
                                   data_channels=data_channels,
                                   padding=(0, kernel_size // 2))
        self.h_fc = MaskedConv2d(out_channels,
                                 out_channels,
                                 (1, 1),
                                 mask_type='B',
                                 data_channels=data_channels)

        self.h_skip = MaskedConv2d(out_channels,
                                   out_channels,
                                   (1, 1),
                                   mask_type='B',
                                   data_channels=data_channels)

        self.label_embedding = nn.Embedding(10, 2*out_channels)

    def forward(self, x):
        v_in, h_in, skip, label = x[0], x[1], x[2], x[3]
        
        label_embedded = self.label_embedding(label).unsqueeze(2).unsqueeze(3)

        v_out, v_shifted = self.v_conv(v_in)
        v_out += self.v_fc(v_in)
        
        v_out += label_embedded
        v_out_tanh, v_out_sigmoid = torch.split(v_out, self.split_size, dim=1)
        v_out = torch.tanh(v_out_tanh) * torch.sigmoid(v_out_sigmoid)

        h_out = self.h_conv(h_in)
        v_shifted = self.v_to_h(v_shifted)
        h_out += v_shifted
        h_out += label_embedded
        h_out_tanh, h_out_sigmoid = torch.split(h_out, self.split_size, dim=1)
        h_out = torch.tanh(h_out_tanh) * torch.sigmoid(h_out_sigmoid)

        # skip connection
        skip = skip + self.h_skip(h_out)

        h_out = self.h_fc(h_out)

        # residual connections
        h_out = h_out + h_in
        v_out = v_out + v_in

        return {0: v_out, 1: h_out, 2: skip, 3: label}


class PixelCNN(nn.Module):
    def __init__(self, in_channels = 3, hidden_fmaps = 30, out_hidden_fmaps = 10, 
                       color_levels = 8, causal_ksize = 7, hidden_ksize = 7, hidden_layers = 6):
        super(PixelCNN, self).__init__()

        self.hidden_fmaps = hidden_fmaps
        self.color_levels = color_levels

        self.causal_conv = CausalBlock(in_channels,
                                       hidden_fmaps,
                                       causal_ksize,
                                       data_channels=in_channels)

        self.hidden_conv = nn.Sequential(
            *[GatedBlock(hidden_fmaps, hidden_fmaps, hidden_ksize, in_channels) for _ in range(hidden_layers)]
        )

        self.label_embedding = nn.Embedding(10, self.hidden_fmaps)

        self.out_hidden_conv = MaskedConv2d(hidden_fmaps,
                                            out_hidden_fmaps,
                                            (1, 1),
                                            mask_type='B',
                                            data_channels=in_channels)

        self.out_conv = MaskedConv2d(out_hidden_fmaps,
                                     in_channels* color_levels,
                                     (1, 1),
                                     mask_type='B',
                                     data_channels=in_channels)

    def forward(self, image, label):
        count, data_channels, height, width = image.size()

        v, h = self.causal_conv(image)

        _, _, out, _ = self.hidden_conv({0: v,
                                         1: h,
                                         2: image.new_zeros((count, self.hidden_fmaps, height, width), requires_grad=True),
                                         3: label}).values()

        label_embedded = self.label_embedding(label).unsqueeze(2).unsqueeze(3)

        # add label bias
        out += label_embedded
        out = F.relu(out)
        out = F.relu(self.out_hidden_conv(out))
        out = self.out_conv(out)

        out = out.view(count, self.color_levels, data_channels, height, width)

        return out

    def sample(self, shape, count, label=None, device='cuda'):
        channels, height, width = shape

        samples = torch.zeros(count, *shape).to(device)
        if label is None:
            labels = torch.randint(high=10, size=(count,)).to(device)
        else:
            labels = (label*torch.ones(count)).to(device).long()

        with torch.no_grad():
            for i in range(height):
                for j in range(width):
                    for c in range(channels):
                        unnormalized_probs = self.forward(samples, labels)
                        pixel_probs = torch.softmax(unnormalized_probs[:, :, c, i, j], dim=1)
                        sampled_levels = torch.multinomial(pixel_probs, 1).squeeze().float() / (self.color_levels - 1)
                        samples[:, c, i, j] = sampled_levels

        return samples