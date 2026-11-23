# SPDX-License-Identifier: GPL-2.0-only
"""Get a thumbnail from the Windows Shell using its registered providers."""
import ctypes as c
from ctypes import wintypes as w
from functools import lru_cache
import os
from uuid import UUID

from PIL import Image


class Bitmap(c.Structure):
    _fields_ = [
        ('type', w.LONG), ('width', w.LONG), ('height', w.LONG), ('row_bytes', w.LONG),
        ('planes', w.WORD), ('bits_per_pixel', w.WORD), ('bits', c.c_void_p),
    ]


class BitmapInfoHeader(c.Structure):
    _fields_ = [
        ('size', w.DWORD), ('width', w.LONG), ('height', w.LONG), ('planes', w.WORD),
        ('bit_count', w.WORD), ('compression', w.DWORD), ('size_image', w.DWORD),
        ('x_pixels_per_meter', w.LONG), ('y_pixels_per_meter', w.LONG),
        ('colors_used', w.DWORD), ('colors_important', w.DWORD),
    ]


class Size(c.Structure):
    _fields_ = [('width', w.LONG), ('height', w.LONG)]


def _bind(dll, name, restype, *argtypes):
    function = getattr(dll, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


@lru_cache(maxsize=1)
def _services():
    ole32, shell32, gdi32 = (c.WinDLL(name) for name in ('ole32','shell32','gdi32'))
    pointer = c.c_void_p
    return (
        _bind(ole32,'CoInitializeEx',c.c_long,pointer,w.DWORD),
        _bind(ole32,'CoUninitialize',None),
        _bind(shell32,'SHCreateItemFromParsingName',c.c_long,w.LPCWSTR,pointer,pointer,c.POINTER(pointer)),
        _bind(gdi32,'GetObjectW',c.c_int,w.HANDLE,c.c_int,pointer),
        _bind(gdi32,'CreateCompatibleDC',w.HDC,w.HDC),
        _bind(gdi32,'GetDIBits',c.c_int,w.HDC,w.HBITMAP,w.UINT,w.UINT,pointer,pointer,w.UINT),
        _bind(gdi32,'DeleteDC',w.BOOL,w.HDC),
        _bind(gdi32,'DeleteObject',w.BOOL,w.HANDLE),
    )


# IShellItemImageFactory and GetImage flags defined in the Windows SDK.
_IMAGE_FACTORY = UUID('bcc18b79-ba16-442f-80c4-8a59c30c463b').bytes_le
_THUMBNAIL_ONLY = 0x08
_APARTMENT_THREADED = 0x02
_COM_ALREADY_INITIALIZED_OTHER_MODE = 0x80010106


def _bitmap_image(handle, describe, make_dc, get_bits, delete_dc, requested):
    bitmap = Bitmap()
    if describe(handle,c.sizeof(bitmap),c.byref(bitmap)) != c.sizeof(bitmap):
        return None
    width, height = bitmap.width, abs(bitmap.height)
    if not (0 < width <= 2048 and 0 < height <= 2048):
        return None
    # Request 32-bit BGR rows in top-down order, so each row is naturally DWORD aligned.
    info = BitmapInfoHeader(size=c.sizeof(BitmapInfoHeader),width=width,height=-height,planes=1,bit_count=32)
    buffer = c.create_string_buffer(width * height * 4)
    dc = make_dc(None)
    if not dc:
        return None
    try:
        if get_bits(dc,handle,0,height,buffer,c.byref(info),0) != height:
            return None
    finally:
        delete_dc(dc)
    image = Image.frombytes('RGB',(width,height),buffer.raw,'raw','BGRX')
    image.thumbnail(requested,Image.Resampling.LANCZOS)
    return image


def windows_thumbnail(path, size):
    """Return a PIL thumbnail or None, without asking the Shell for an icon."""
    if os.name != 'nt':
        return None
    initialize, uninitialize, create, describe, make_dc, get_bits, delete_dc, delete_object = _services()
    status = initialize(None,_APARTMENT_THREADED)
    if status < 0 and (status & 0xffffffff) != _COM_ALREADY_INITIALIZED_OTHER_MODE:
        return None
    try:
        item, bitmap = c.c_void_p(), c.c_void_p()
        if create(str(path),None,_IMAGE_FACTORY,c.byref(item)) < 0 or not item:
            return None
        vtable = c.cast(item,c.POINTER(c.POINTER(c.c_void_p))).contents
        release = c.WINFUNCTYPE(w.ULONG,c.c_void_p)(vtable[2])
        try:
            get_image = c.WINFUNCTYPE(c.c_long,c.c_void_p,Size,c.c_uint,c.POINTER(c.c_void_p))(vtable[3])
            if get_image(item,Size(*size),_THUMBNAIL_ONLY,c.byref(bitmap)) < 0 or not bitmap:
                return None
            return _bitmap_image(bitmap,describe,make_dc,get_bits,delete_dc,size)
        finally:
            if bitmap:
                delete_object(bitmap)
            release(item)
    finally:
        if status >= 0:
            uninitialize()
