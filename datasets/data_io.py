import cv2
import numpy as np
import re
import sys
import struct
from PIL import Image


# Read image and rescale to specified size
def read_image(filename: str, h: int, w: int):
    img = Image.open(filename)

    # scale 0~255 to 0~1
    np_img = np.array(img, dtype=np.float32) / 255.
    original_h, original_w, _ = np_img.shape
    np_img = cv2.resize(np_img, (w, h), interpolation=cv2.INTER_LINEAR)

    return np_img, original_h, original_w


# Save images including binary mask (bool), float (0<= val <= 1), or int (as-is)
def save_image(filename: str, image):
    if image.dtype == np.bool:
        image = image.astype(np.uint8) * 255
    elif image.dtype == np.float32 or image.dtype == np.float64:
        image = image * 255
        image = image.astype(np.uint8)
    else:
        image = image.astype(np.uint8)
    Image.fromarray(image).save(filename)


# Read camera intrinsics, extrinsics, and depth values (min, max)
def read_cam_file(filename: str):
    with open(filename) as f:
        lines = [line.rstrip() for line in f.readlines()]
    # extrinsics: line [1,5), 4x4 matrix
    extrinsics = np.fromstring(' '.join(lines[1:5]), dtype=np.float32, sep=' ')
    extrinsics = extrinsics.reshape((4, 4))
    # intrinsics: line [7-10), 3x3 matrix
    intrinsics = np.fromstring(' '.join(lines[7:10]), dtype=np.float32, sep=' ')
    intrinsics = intrinsics.reshape((3, 3))
    # depth min and max: line 11
    depth_params = np.fromstring(lines[11], dtype=np.float32, sep=' ')

    return intrinsics, extrinsics, depth_params


# Read image pairs from text file; output is a list of tuples each containing the reference image id and a list of
# source image ids
def read_pair_file(filename):
    data = []
    with open(filename) as f:
        num_viewpoint = int(f.readline())
        for view_idx in range(num_viewpoint):
            ref_view = int(f.readline().rstrip())
            src_views = [int(x) for x in f.readline().rstrip().split()[1::2]]
            if len(src_views) != 0:
                data.append((ref_view, src_views))
    return data


# Read binary maps (depth or confidence) from pfm or bin format
def read_map(path: str):
    if path.endswith('.bin'):
        return read_bin(path)
    elif path.endswith('.pfm'):
        return read_pfm(path)
    else:
        raise Exception('Invalid input format; only pfm and bin are supported')


# Save binary maps (depth or confidence) in pfm or bin format
def save_map(path: str, data):
    if path.endswith('.bin'):
        save_bin(path, data)
    elif path.endswith('.pfm'):
        save_pfm(path, data)
    else:
        raise Exception('Invalid input format; only pfm and bin are supported')


# Read map from bin file (colmap)
def read_bin(path: str):
    with open(path, 'rb') as fid:
        width, height, channels = np.genfromtxt(fid, delimiter='&', max_rows=1,
                                                usecols=(0, 1, 2), dtype=int)
        fid.seek(0)
        num_delimiter = 0
        byte = fid.read(1)
        while True:
            if byte == b'&':
                num_delimiter += 1
                if num_delimiter >= 3:
                    break
            byte = fid.read(1)
        data = np.fromfile(fid, np.float32)
    data = data.reshape((width, height, channels), order='F')
    data = np.transpose(data, (1, 0, 2))
    return data, 1


# Save map in bin file (colmap)
def save_bin(path: str, data):
    if data.dtype != np.float32:
        raise Exception('Image data type must be float32.')

    if len(data.shape) == 2 or (len(data.shape) == 3 and data.shape[2] == 1):
        height, width = data.shape
        channels = 1
    elif len(data.shape) == 3 and data.shape[2] == 3:
        height, width, channels = data.shape
    else:
        raise Exception('Image must have H x W x 3, H x W x 1 or H x W dimensions.')

    with open(path, 'w') as fid:
        fid.write(str(width) + '&' + str(height) + '&' + str(channels) + '&')

    with open(path, 'ab') as fid:
        if len(data.shape) == 2 or (len(data.shape) == 3 and data.shape[2] == 1):
            image_trans = np.transpose(data, (1, 0))
        elif len(data.shape) == 3 and data.shape[2] == 3:
            image_trans = np.transpose(data, (1, 0, 2))
        else:
            raise Exception('Image must have H x W x 3, H x W x 1 or H x W dimensions.')
        data_1d = image_trans.reshape(-1, order='F')
        data_list = data_1d.tolist()
        endian_character = '<'
        format_char_sequence = ''.join(['f'] * len(data_list))
        byte_data = struct.pack(endian_character + format_char_sequence, *data_list)
        fid.write(byte_data)


# Read map from pfm file
def read_pfm(filename: str):
    # rb: binary file and read only
    file = open(filename, 'rb')

    header = file.readline().decode('utf-8').rstrip()
    if header == 'PF':
        color = True
    elif header == 'Pf':  # depth is Pf
        color = False
    else:
        raise Exception('Not a PFM file.')

    dim_match = re.match(r'^(\d+)\s(\d+)\s$', file.readline().decode('utf-8'))  # re is used for matching
    if dim_match:
        width, height = map(int, dim_match.groups())
    else:
        raise Exception('Malformed PFM header.')

    scale = float(file.readline().rstrip())
    if scale < 0:  # little-endian
        endian = '<'
        scale = -scale
    else:
        endian = '>'  # big-endian

    data = np.fromfile(file, endian + 'f')
    shape = (height, width, 3) if color else (height, width, 1)
    # depth: H*W
    data = np.reshape(data, shape)
    data = np.flipud(data)
    file.close()
    return data, scale


# Save map in pfm file
def save_pfm(filename: str, data, scale=1):
    file = open(filename, 'wb')

    data = np.flipud(data)
    # print(image.shape)

    if data.dtype.name != 'float32':
        raise Exception('Image data type must be float32.')

    if len(data.shape) == 3 and data.shape[2] == 3:  # color image
        color = True
    elif len(data.shape) == 2 or len(data.shape) == 3 and data.shape[2] == 1:  # greyscale
        color = False
    else:
        raise Exception('Image must have H x W x 3, H x W x 1 or H x W dimensions.')

    file.write('PF\n'.encode('utf-8') if color else 'Pf\n'.encode('utf-8'))
    file.write('{} {}\n'.format(data.shape[1], data.shape[0]).encode('utf-8'))

    endian = data.dtype.byteorder

    if endian == '<' or endian == '=' and sys.byteorder == 'little':
        scale = -scale

    file.write(('%f\n' % scale).encode('utf-8'))

    data.tofile(file)
    file.close()
