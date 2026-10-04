import json
import random
import time
from pathlib import Path
from uuid import uuid4
from instagrapi import config
from instagrapi.exceptions import PhotoNotUpload
from instagrapi.image_util import prepare_image, prepare_story_image_fit
from instagrapi.types import StoryResizeMode
try:
    from PIL import Image
except ImportError:
    raise Exception("You don't have PIL installed. Please install PIL or Pillow>=8.1.1")

class UploadPhotoMixin:
    """
    Helpers for uploading photo
    """

    def photo_rupload(self, path: Path, upload_id: str='', to_album: bool=False, for_story: bool=False, resize_mode: StoryResizeMode='fill') -> tuple:
        """
        Upload photo to Instagram

        Parameters
        ----------
        path: Path
            Path to the media
        upload_id: str, optional
            Unique upload_id (String). When None, then generate automatically. Example from video.video_configure
        to_album: bool, optional
        for_story: bool, optional
            Useful for resize util only
        resize_mode: str, optional
            Story resize mode: "fill" crops oversized Story media to fill the canvas, "fit" letterboxes it.

        Returns
        -------
        tuple
            (Upload ID for the media, width, height)
        """
        assert isinstance(path, Path), f'Path must been Path, now {path} ({type(path)})'
        if resize_mode not in {'fill', 'fit'}:
            raise ValueError('resize_mode must be "fill" or "fit"')
        if resize_mode == 'fit' and (not for_story):
            raise ValueError('resize_mode="fit" is only supported for story uploads')
        valid_extensions = ['.jpg', '.jpeg', '.png', '.webp']
        if path.suffix.lower() not in valid_extensions:
            raise ValueError('Invalid file format. Only JPG/JPEG/PNG/WEBP files are supported.')
        image_type = 'image/jpeg'
        if path.suffix.lower() == '.png':
            image_type = 'image/png'
        elif path.suffix.lower() == '.webp':
            image_type = 'image/webp'
        upload_id = upload_id or str(int(time.time() * 1000))
        assert path, 'Not specified path to photo'
        waterfall_id = str(uuid4())
        upload_name = '{upload_id}_0_{rand}'.format(upload_id=upload_id, rand=random.randint(1000000000, 9999999999))
        rupload_params = {'retry_context': '{"num_step_auto_retry":0,"num_reupload":0,"num_step_manual_retry":0}', 'media_type': '1', 'xsharing_user_ids': '[]', 'upload_id': upload_id, 'image_compression': json.dumps({'lib_name': 'moz', 'lib_version': '3.1.m', 'quality': '80'})}
        if to_album:
            rupload_params['is_sidecar'] = '1'
        if for_story and resize_mode == 'fit':
            photo_data, photo_size = prepare_story_image_fit(str(path))
        elif for_story:
            photo_data, photo_size = prepare_image(str(path), max_side=1080, aspect_ratios=(9 / 16, 90 / 47), max_size=(1080, 1920))
        else:
            photo_data, photo_size = prepare_image(str(path), max_side=1080)
        photo_len = str(len(photo_data))
        headers = self.private_headers({'Accept-Encoding': 'gzip', 'X-Instagram-Rupload-Params': json.dumps(rupload_params), 'X_FB_PHOTO_WATERFALL_ID': waterfall_id, 'X-Entity-Type': image_type, 'Offset': '0', 'X-Entity-Name': upload_name, 'X-Entity-Length': photo_len, 'Content-Type': 'application/octet-stream', 'Content-Length': photo_len})
        response = self.private.post('https://{domain}/rupload_igphoto/{name}'.format(domain=config.API_DOMAIN, name=upload_name), data=photo_data, headers=headers)
        self.request_log(response)
        if response.status_code != 200:
            self.logger.error('Photo Upload failed with the following response: %s', response)
            last_json = self.last_json
            raise PhotoNotUpload(response.text, response=response, **last_json)
        if for_story and resize_mode == 'fit':
            width, height = photo_size
        else:
            with Image.open(path) as im:
                width, height = im.size
        return (upload_id, width, height)
