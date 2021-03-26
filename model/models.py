#!/usr/bin/python
# -*- encoding: utf-8 -*-
import math

import torch
import torch.nn as nn

from torch.nn import BatchNorm2d
from torchvision import models

'''
    As in the FixMatch paper, the wide resnet only considers the resnet of the pre-activated version, 
    and it only considers the basic blocks rather than the bottleneck blocks.
'''


class BasicBlockPreAct(nn.Module):
    def __init__(self, in_chan, out_chan, drop_rate=0, stride=1, pre_res_act=False):
        super(BasicBlockPreAct, self).__init__()
        self.bn1 = BatchNorm2d(in_chan, momentum=0.001)
        self.relu1 = nn.LeakyReLU(inplace=True, negative_slope=0.1)
        self.conv1 = nn.Conv2d(in_chan, out_chan, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn2 = BatchNorm2d(out_chan, momentum=0.001)
        self.relu2 = nn.LeakyReLU(inplace=True, negative_slope=0.1)
        self.dropout = nn.Dropout(drop_rate) if not drop_rate == 0 else None
        self.conv2 = nn.Conv2d(out_chan, out_chan, kernel_size=3, stride=1, padding=1, bias=False)
        self.downsample = None
        if in_chan != out_chan or stride != 1:
            self.downsample = nn.Conv2d(
                in_chan, out_chan, kernel_size=1, stride=stride, bias=False
            )
        self.pre_res_act = pre_res_act
        # self.init_weight()

    def forward(self, x):
        bn1 = self.bn1(x)
        act1 = self.relu1(bn1)
        residual = self.conv1(act1)
        residual = self.bn2(residual)
        residual = self.relu2(residual)
        if self.dropout is not None:
            residual = self.dropout(residual)
        residual = self.conv2(residual)

        shortcut = act1 if self.pre_res_act else x
        if self.downsample is not None:
            shortcut = self.downsample(shortcut)

        out = shortcut + residual
        return out

    def init_weight(self):
        # for _, md in self.named_modules():
        #     if isinstance(md, nn.Conv2d):
        #         nn.init.kaiming_normal_(
        #             md.weight, a=0, mode='fan_in', nonlinearity='leaky_relu')
        #         if md.bias is not None:
        #             nn.init.constant_(md.bias, 0)
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, a=0, mode='fan_in', nonlinearity='leaky_relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)


class WideResnetBackbone(nn.Module):
    def __init__(self, k=1, n=28, drop_rate=0):
        super(WideResnetBackbone, self).__init__()
        self.k, self.n = k, n
        assert (self.n - 4) % 6 == 0
        n_blocks = (self.n - 4) // 6
        n_layers = [16, ] + [self.k * 16 * (2 ** i) for i in range(3)]

        self.conv1 = nn.Conv2d(
            3,
            n_layers[0],
            kernel_size=3,
            stride=1,
            padding=1,
            bias=False
        )
        self.layer1 = self.create_layer(
            n_layers[0],
            n_layers[1],
            bnum=n_blocks,
            stride=1,
            drop_rate=drop_rate,
            pre_res_act=True,
        )
        self.layer2 = self.create_layer(
            n_layers[1],
            n_layers[2],
            bnum=n_blocks,
            stride=2,
            drop_rate=drop_rate,
            pre_res_act=False,
        )
        self.layer3 = self.create_layer(
            n_layers[2],
            n_layers[3],
            bnum=n_blocks,
            stride=2,
            drop_rate=drop_rate,
            pre_res_act=False,
        )
        self.bn_last = BatchNorm2d(n_layers[3], momentum=0.001)
        self.relu_last = nn.LeakyReLU(inplace=True, negative_slope=0.1)
        self.init_weight()

    def create_layer(
            self,
            in_chan,
            out_chan,
            bnum,
            stride=1,
            drop_rate=0,
            pre_res_act=False,
    ):
        layers = [
            BasicBlockPreAct(
                in_chan,
                out_chan,
                drop_rate=drop_rate,
                stride=stride,
                pre_res_act=pre_res_act), ]
        for _ in range(bnum - 1):
            layers.append(
                BasicBlockPreAct(
                    out_chan,
                    out_chan,
                    drop_rate=drop_rate,
                    stride=1,
                    pre_res_act=False, ))
        return nn.Sequential(*layers)

    def forward(self, x):
        feat = self.conv1(x)

        feat = self.layer1(feat)
        feat2 = self.layer2(feat)  # 1/2
        feat4 = self.layer3(feat2)  # 1/4

        feat4 = self.bn_last(feat4)
        feat4 = self.relu_last(feat4)
        return feat2, feat4

    def init_weight(self):
        # for _, child in self.named_children():
        #     if isinstance(child, nn.Conv2d):
        #         n = child.kernel_size[0] * child.kernel_size[0] * child.out_channels
        #         nn.init.normal_(child.weight, 0, 1. / ((0.5 * n) ** 0.5))
        #         #  nn.init.kaiming_normal_(
        #         #      child.weight, a=0.1, mode='fan_out',
        #         #      nonlinearity='leaky_relu'
        #         #  )
        #
        #         if child.bias is not None:
        #             nn.init.constant_(child.bias, 0)
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))

                nn.init.kaiming_normal_(m.weight, a=0, mode='fan_in', nonlinearity='leaky_relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()
            elif isinstance(m, nn.Linear):
                m.bias.data.zero_()


class WideResnet(nn.Module):
    '''
    for wide-resnet-28-10, the definition should be WideResnet(n_classes, 10, 28)
    '''

    def __init__(self, n_classes, k=1, n=28):
        super(WideResnet, self).__init__()
        self.n_layers, self.k = n, k
        self.backbone = WideResnetBackbone(k=k, n=n)
        if k < 4:
            self.classifier = nn.Linear(64 * self.k, n_classes, bias=True)
        else:
            num_ftrs = 64 * self.k
            self.classifier = nn.Sequential(nn.Linear(num_ftrs, num_ftrs//2),
                                    nn.ReLU(inplace=True),
                                    nn.Linear(num_ftrs//2, n_classes))

    def forward(self, x):
        feat = self.backbone(x)[-1]
        feat = torch.mean(feat, dim=(2, 3))
        feat = self.classifier(feat)
        return feat

    def init_weight(self):
        nn.init.xavier_normal_(self.classifier.weight)
        if not self.classifier.bias is None:
            nn.init.constant_(self.classifier.bias, 0)


class ResNet50WithEmbeddingHead(nn.Module):
    def __init__(self, num_classes, emb_dim=300, pretrained=True):
        super(ResNet50WithEmbeddingHead, self).__init__()
        self.model_resnet = models.resnet50(pretrained=pretrained)
        self.num_ftrs = self.model_resnet.fc.in_features
        self.model_resnet.fc = nn.Identity()
        self.fc_classes = nn.Linear(self.num_ftrs, num_classes)
        self.fc_emb = nn.Linear(self.num_ftrs, emb_dim)
#         self.fc_emb = nn.Sequential(nn.Linear(num_ftrs, num_ftrs//2),
#                                     nn.ReLU(inplace=True),
#                                     nn.Linear(num_ftrs//2, emb_dim))

    def forward(self, x):
        x = self.model_resnet(x)
        out1 = self.fc_classes(x)
        out2 = self.fc_emb(x)
        return out1, out2, x

    # to adapt the model to adaptation stage (if needed)
    def adapt(self, num_classes):
        self.fc_classes = nn.Linear(self.num_ftrs, num_classes)

class ResNet18WithEmbeddingHead(nn.Module):
    def __init__(self, num_classes, emb_dim=128, pretrained=True):
        super(ResNet18WithEmbeddingHead, self).__init__()
        self.model_resnet = models.resnet18(pretrained=pretrained)
        self.num_ftrs = self.model_resnet.fc.in_features
        self.model_resnet.fc = nn.Identity()
        self.fc_classes = nn.Linear(self.num_ftrs, num_classes)
        # self.fc_emb = nn.Linear(self.num_ftrs, emb_dim)
        self.fc_emb = nn.Sequential(nn.Linear(self.num_ftrs, 2048),
                                    nn.ReLU(inplace=True),
                                    nn.Linear(2048, emb_dim))

    def forward(self, x):
        x = self.model_resnet(x)
        out1 = self.fc_classes(x)
        out2 = self.fc_emb(x)
        return out1, out2, x

    # to adapt the model to adaptation stage (if needed)
    def adapt(self, num_classes):
        self.fc_classes = nn.Linear(self.num_ftrs, num_classes)


class WideResnetWithEmbeddingHead(nn.Module):

    def __init__(self, num_classes, k, n, emb_dim=128):
        super(WideResnetWithEmbeddingHead, self).__init__()
        self.model_base = WideResnet(num_classes, k=k, n=n)
        self.k = k
        self.num_ftrs = self.model_base.classifier.in_features
        self.model_base.classifier = nn.Identity()
        self.fc_classes = nn.Linear(self.num_ftrs, num_classes)
        self.fc_emb = nn.Sequential(nn.Linear(self.num_ftrs, 2048),
                                    nn.ReLU(inplace=True),
                                    nn.Linear(2048, emb_dim))
    def forward(self, x):
        x = self.model_base(x)
        out1 = self.fc_classes(x)
        out2 = self.fc_emb(x)
        return out1, out2, x

    # to adapt the model to adaptation stage (if needed)
    def adapt(self, num_classes):
        self.fc_classes = nn.Linear(self.num_ftrs, num_classes)
        # if self.k < 4:
        #     self.fc_classes = nn.Linear(self.num_ftrs, num_classes)
        # else:
        #     self.fc_classes = nn.Sequential(nn.Linear(self.num_ftrs, self.num_ftrs // 2),
        #                                     nn.ReLU(inplace=True),
        #                                     nn.Linear(self.num_ftrs // 2, num_classes))



if __name__ == "__main__":
    x = torch.randn(2, 3, 224, 224)
    lb = torch.randint(0, 100, (2,)).long()
    #
    # net = WideResnetBackbone()
    # out = net(x)
    # print(out[0].size())
    # del net, out
    #
    # net = WideResnet(n_classes=100)
    # criteria = nn.CrossEntropyLoss()
    # out = net(x)
    # loss = criteria(out, lb)
    # loss.backward()
    # print(out.size())
    net = ResNet50WithEmbeddingHead(100,300,True)
    net(x)

    net = WideResnetWithEmbeddingHead(100,3,28,128)
    out1, out2 = net(x)

    print('Done')
