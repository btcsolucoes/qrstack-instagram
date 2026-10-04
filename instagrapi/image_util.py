import io
import re
from typing import Union
try:
    from PIL import Image
except ImportError:
    raise Exception("You don't have PIL installed. Please install PIL or Pillow>=8.1.1")
import requests

def calc_resize(max_size, curr_size, min_size=(0, 0)):
    """
    Calculate if resize is required based on the max size desired
    and the current size

    :param max_size: tuple of (width, height)
    :param curr_size: tuple of (width, height)
    :param min_size: tuple of (width, height)
    :return:
    """
    max_width, max_height = max_size or (0, 0)
    min_width, min_height = min_size or (0, 0)
    if max_width and min_width > max_width or (max_height and min_height > max_height):
        raise ValueError('Invalid min / max sizes.')
    orig_width, orig_height = curr_size
    if max_width and max_height and (orig_width > max_width or orig_height > max_height):
        resize_factor = min(1.0 * max_width / orig_width, 1.0 * max_height / orig_height)
        new_width = int(resize_factor * orig_width)
        new_height = int(resize_factor * orig_height)
        return (new_width, new_height)
    elif min_width and min_height and (orig_width < min_width or orig_height < min_height):
        resize_factor = max(1.0 * min_width / orig_width, 1.0 * min_height / orig_height)
        new_width = int(resize_factor * orig_width)
        new_height = int(resize_factor * orig_height)
        return (new_width, new_height)

def calc_crop(aspect_ratios, curr_size):
    """
    Calculate if cropping is required based on the desired aspect
    ratio and the current size.

    :param aspect_ratios: single float value or tuple of (min_ratio, max_ratio)
    :param curr_size: tuple of (width, height)
    :return:
    """
    try:
        if len(aspect_ratios) == 2:
            min_aspect_ratio = float(aspect_ratios[0])
            max_aspect_ratio = float(aspect_ratios[1])
        else:
            raise ValueError('Invalid aspect ratios')
    except TypeError:
        min_aspect_ratio = float(aspect_ratios)
        max_aspect_ratio = float(aspect_ratios)
    curr_aspect_ratio = 1.0 * curr_size[0] / curr_size[1]
    if not min_aspect_ratio <= curr_aspect_ratio <= max_aspect_ratio:
        curr_width = curr_size[0]
        curr_height = curr_size[1]
        if curr_aspect_ratio > max_aspect_ratio:
            new_height = curr_height
            new_width = max_aspect_ratio * new_height
        else:
            new_width = curr_width
            new_height = new_width / min_aspect_ratio
        left = int((curr_width - new_width) / 2)
        top = int((curr_height - new_height) / 2)
        right = int((curr_width + new_width) / 2)
        bottom = int((curr_height + new_height) / 2)
        return (left, top, right, bottom)

def is_remote(media):
    """Detect if media specified is a url"""
    if re.match('^https?://', media):
        return True
    return False

def prepare_image(img, max_size=(1080, 1350), aspect_ratios=(4.0 / 5.0, 90.0 / 47.0), save_path=None, **kwargs):
    """
    Prepares an image file for posting.
    Defaults for size and aspect ratio from https://help.instagram.com/1469029763400082

    :param img: file path
    :param max_size: tuple of (max_width,  max_height)
    :param aspect_ratios: single float value or tuple of (min_ratio, max_ratio)
    :param save_path: optional output file path
    :param kwargs:
             - **min_size**: tuple of (min_width,  min_height)
    :return:
    """
    min_size = kwargs.pop('min_size', (320, 167))
    if is_remote(img):
        res = requests.get(img, timeout=5)
        im = Image.open(io.BytesIO(res.content))
    else:
        im = Image.open(img)
    if aspect_ratios:
        crop_box = calc_crop(aspect_ratios, im.size)
        if crop_box:
            im = im.crop(crop_box)
    new_size = calc_resize(max_size, im.size, min_size=min_size)
    if new_size:
        im = im.resize(new_size)
    if im.mode != 'RGB':
        im = im.convert('RGBA')
        im2 = Image.new('RGB', im.size, (255, 255, 255))
        im2.paste(im, (0, 0), im)
        im = im2
    if save_path:
        im.save(save_path)
    b = io.BytesIO()
    im.save(b, 'JPEG')
    return (b.getvalue(), im.size)

def prepare_story_image_fit(img, max_size=(1080, 1920), background_color: Union[str, tuple]='black', save_path=None):
    """
    Prepare an image for a Story without cropping the source media.
    """
    if is_remote(img):
        res = requests.get(img, timeout=5)
        source_image = Image.open(io.BytesIO(res.content))
    else:
        source_image = Image.open(img)
    im = source_image
    try:
        if im.mode != 'RGBA':
            im = im.convert('RGBA')
        im.thumbnail(max_size, Image.Resampling.LANCZOS)
        canvas = Image.new('RGB', max_size, background_color)
        left = int((max_size[0] - im.size[0]) / 2)
        top = int((max_size[1] - im.size[1]) / 2)
        canvas.paste(im, (left, top), im)
        if save_path:
            canvas.save(save_path)
        b = io.BytesIO()
        canvas.save(b, 'JPEG')
        return (b.getvalue(), canvas.size)
    finally:
        if im is not source_image:
            im.close()
        source_image.close()
