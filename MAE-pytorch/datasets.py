# --------------------------------------------------------
# Based on BEiT, timm, DINO and DeiT code bases
# https://github.com/microsoft/unilm/tree/master/beit
# https://github.com/rwightman/pytorch-image-models/tree/master/timm
# https://github.com/facebookresearch/deit
# https://github.com/facebookresearch/dino
# --------------------------------------------------------'
import os
import torch

from torchvision import datasets, transforms
from torchvision.datasets import DatasetFolder

from timm.data.constants import \
    IMAGENET_DEFAULT_MEAN, IMAGENET_DEFAULT_STD, IMAGENET_INCEPTION_MEAN, IMAGENET_INCEPTION_STD

from timm.data import create_transform

from masking_generator import RandomMaskingGenerator
from saliency_mask import SaliencyMaskGenerator
from dataset_folder import ImageFolder
from wilds import get_dataset


class DataAugmentationForMAE(object):
    def __init__(self, args, standard=False):
        imagenet_default_mean_and_std = args.imagenet_default_mean_and_std
        mean = IMAGENET_INCEPTION_MEAN if not imagenet_default_mean_and_std else IMAGENET_DEFAULT_MEAN
        std = IMAGENET_INCEPTION_STD if not imagenet_default_mean_and_std else IMAGENET_DEFAULT_STD

        self.transform = transforms.Compose([
            transforms.RandomResizedCrop(args.input_size),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=torch.tensor(mean),
                std=torch.tensor(std))
        ])

        self.standard = standard
        if standard:
            self.masked_position_generator = RandomMaskingGenerator(
                args.window_size, args.mask_ratio
            )

    def __call__(self, image):
        if self.standard:
            return self.transform(image), self.masked_position_generator()
        return self.transform(image)

    def __repr__(self):
        repr = "(DataAugmentationForBEiT,\n"
        repr += "  transform = %s,\n" % str(self.transform)
        if self.standard:
            repr += "  Masked position generator = %s,\n" % str(self.masked_position_generator)
        repr += ")"
        return repr


class ImageFolderWithAttMap(ImageFolder):
    def __init__(self, cfg, *args, **kwargs):
        self.att_root = cfg.att_root # location of the attention maps
        self.masked_position_generator = SaliencyMaskGenerator(cfg.window_size, cfg.mask_ratio)
        super().__init__(*args, **kwargs)
    
    def __getitem__(self, index: int):
        path, target = self.samples[index]
        sample = self.loader(path)
        if self.transform is not None:
            sample = self.transform(sample)
        if self.target_transform is not None:
            target = self.target_transform(target)

        fn = os.path.split(path)[-1].split('.')[0] + '.png'
        mask = self.masked_position_generator(os.path.join(self.att_root, fn))

        return (sample, mask), target
    def find_classes(self, dir):
        return [""], {"": 0}

def build_pretraining_dataset(args, standard=False):
    transform = DataAugmentationForMAE(args, standard=standard)
    print("Data Aug = %s" % str(transform))
    if standard:
        return ImageFolder(args.data_path, transform=transform)
    else:
        return ImageFolderWithAttMap(args, args.data_path, transform=transform)

def build_dataset(is_train, args):
    transform = build_transform(is_train, args)

    print("Transform = ")
    if isinstance(transform, tuple):
        for trans in transform:
            print(" - - - - - - - - - - ")
            for t in trans.transforms:
                print(t)
    else:
        for t in transform.transforms:
            print(t)
    print("---------------------------")

    if args.data_set == 'CIFAR':
        dataset = datasets.CIFAR100(args.data_path, train=is_train, transform=transform)
        nb_classes = 100
    elif args.data_set == 'IMNET':
        root = os.path.join(args.data_path, 'train' if is_train else 'val')
        dataset = datasets.ImageFolder(root, transform=transform)
        nb_classes = 1000
    elif args.data_set == "image_folder":
        root = args.data_path if is_train else args.eval_data_path
        dataset = ImageFolder(root, transform=transform)
        nb_classes = args.nb_classes
        assert len(dataset.class_to_idx) == nb_classes
    elif args.data_set == 'iwildcam':
        root = args.data_path if is_train else args.eval_data_path
        dataset_full = get_dataset('iwildcam', root_dir=root, download=True)
        split = 'train' if is_train else 'test'
        dataset = dataset_full.get_subset(split, transform=transform)
        nb_classes = args.nb_classes
        assert dataset._n_classes == nb_classes
    else:
        raise NotImplementedError()
    assert nb_classes == args.nb_classes
    print("Number of the class = %d" % args.nb_classes)

    return dataset, nb_classes


def build_transform(is_train, args):
    resize_im = args.input_size > 32
    imagenet_default_mean_and_std = args.imagenet_default_mean_and_std
    mean = IMAGENET_INCEPTION_MEAN if not imagenet_default_mean_and_std else IMAGENET_DEFAULT_MEAN
    std = IMAGENET_INCEPTION_STD if not imagenet_default_mean_and_std else IMAGENET_DEFAULT_STD

    if is_train:
        # this should always dispatch to transforms_imagenet_train
        transform = create_transform(
            input_size=args.input_size,
            is_training=True,
            color_jitter=args.color_jitter,
            auto_augment=args.aa,
            interpolation=args.train_interpolation,
            re_prob=args.reprob,
            re_mode=args.remode,
            re_count=args.recount,
            mean=mean,
            std=std,
        )
        if not resize_im:
            # replace RandomResizedCropAndInterpolation with
            # RandomCrop
            transform.transforms[0] = transforms.RandomCrop(
                args.input_size, padding=4)
        return transform

    t = []
    if resize_im:
        if args.crop_pct is None:
            if args.input_size < 384:
                args.crop_pct = 224 / 256
            else:
                args.crop_pct = 1.0
        size = int(args.input_size / args.crop_pct)
        t.append(
            transforms.Resize(size, interpolation=3),  # to maintain same ratio w.r.t. 224 images
        )
        t.append(transforms.CenterCrop(args.input_size))

    t.append(transforms.ToTensor())
    t.append(transforms.Normalize(mean, std))
    return transforms.Compose(t)
