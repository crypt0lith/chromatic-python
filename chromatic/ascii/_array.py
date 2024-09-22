__all__ = ['get_glyph_masks', 'sort_ascii_glyphs', 'get_font_key', 'get_font_object', 'render_font_str',
           'render_font_char', 'preview_char_set', 'color_quantization', 'scale_saturation', 'ansi_quantization',
           'equalize_white_point', 'contrast_stretch', 'img2ascii', 'ascii2img', 'img2ansi', 'ansi2img',
           'shuffle_char_set']

import math
import random
from operator import truediv
from os import PathLike
from typing import Callable, Iterable, Literal, Optional, TypeVar, TypedDict, Union, Unpack, cast, overload

import cv2
import numpy as np
import numpy.typing as npt
import skimage as ski
from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont
from numpy import dtype, ndarray
from scipy import ndimage
from sklearn.cluster import KMeans

from chromatic._typing import Int3Tuple, RGBArrayLike, RGBVector
from chromatic.ansi.core import (
    AnsiColorParam,
    Color,
    ColorStr,
    SgrSequence, ansi_color_24Bit,
    ansi_color_4Bit,
    ansi_color_8Bit,
    get_ansi_type,
    get_default_ansi
)
from chromatic.ascii._curses import ascii_printable, cp437_printable
from chromatic.data import UserFont

_FontType = TypeVar('_FontType', ImageFont.FreeTypeFont, UserFont)
_AnsiColorFormat = TypeVar('_AnsiColorFormat', bound=AnsiColorParam)
_RGBVector = TypeVar('_RGBVector', Color, RGBVector)


def get_glyph_masks(__font: _FontType | str | int,
                    char_set: str = None,
                    dist_transform: bool = False) -> dict[str, npt.NDArray[int]]:
    char_set = char_set or ascii_printable()
    errors = []
    try:
        if type(char_set) is not str:
            raise TypeError(
                f"Expected 'char_set' to be {str.__qualname__!r}, "
                f"got {type(char_set).__qualname__!r} instead")
        if type(dist_transform) is not bool:
            raise TypeError(
                f"Expected 'dist_transform' to be {bool.__qualname__!r}, "
                f"got {type(dist_transform).__qualname__!r} instead")
    except TypeError as e:
        errors.append(e)
    finally:
        if errors:
            if len(errors) > 1:
                raise ExceptionGroup(
                    'Multiple type errors', errors)
            raise errors[0] from None

    font = get_font_object(__font)

    def _get_binary_mask(s: str):
        _, _binary_mask = cv2.threshold(np.array(render_font_str(s, font)), 0, 255, cv2.THRESH_BINARY)
        _binary_mask: npt.NDArray[int] = np.all(_binary_mask == [255, 255, 255], axis=-1).astype(int)
        if dist_transform:
            _binary_mask = ndimage.distance_transform_edt(_binary_mask)
        return _binary_mask

    empty_mask = _get_binary_mask(' ')
    no_repr_mask = _get_binary_mask('\uFFFD')
    glyph_masks = {}
    for char in set(char_set):
        binary_mask = _get_binary_mask(char)
        if np.array_equal(binary_mask, no_repr_mask):
            binary_mask = empty_mask
        glyph_masks[char] = binary_mask
    return glyph_masks


def sort_ascii_glyphs(__s: str,
                      font: ImageFont.FreeTypeFont,
                      reverse: bool = False):
    glyph_dict = get_glyph_masks(font, __s, dist_transform=True)
    return ''.join(sorted(__s, key=lambda k: np.sum(glyph_dict[k]), reverse=reverse))


def get_font_key(font: ImageFont.FreeTypeFont):
    font = get_font_object(font)
    font_key = font.getname()
    if not all(font_key):
        missing = []
        s = 'font %s'
        if font_key[0] is None:
            missing.append(f"{s % 'name'!r}")
        if font_key[-1] is None:
            missing.append(f"{s % 'family'!r}")
        raise ValueError(
            f"Unable to generate font key due to missing fields {' and '.join(missing)}: {font_key}")
    return cast(tuple[str, str], font_key)


@overload
def get_font_object(
    font: _FontType | str | int,
    *,
    retpath: Literal[False] = False
) -> ImageFont.FreeTypeFont:
    ...


@overload
def get_font_object(
    font: _FontType | str | int,
    *,
    retpath: Literal[True]
) -> str:
    ...


def get_font_object(font: _FontType | str | int,
                    *,
                    retpath: bool = False) -> ImageFont.FreeTypeFont | str:
    if type(retpath) is not bool:
        raise TypeError(
            f"Expected 'retpath' to be {bool.__qualname__!r}, "
            f"got {type(retpath).__qualname__!r} instead")
    if (vt := type(font)) is not ImageFont.FreeTypeFont:
        if isinstance(font, PathLike):
            if retpath:
                return font.__fspath__()
            return ImageFont.truetype(font.__fspath__(), 24)
        if not isinstance(font, UserFont | int | str):
            raise TypeError(
                f"Expected {ImageFont.FreeTypeFont.__qualname__!r}, "
                f"got {vt.__qualname__!r} instead")
        if vt is not UserFont:
            if vt is str and font not in UserFont.__members__:
                try:
                    font = ImageFont.truetype(font, 24)
                except OSError:
                    raise FileNotFoundError(
                        font) from None
                return font.path if retpath else font
            font = UserFont(font) if isinstance(font, int) else UserFont[font]
        return font.path if retpath else ImageFont.truetype(font.path, 24)
    return font.path if retpath else font


def shuffle_char_set(char_set: Iterable[str],
                     *args: *tuple[Optional[int] | slice, ...]):
    if not isinstance(char_set, Iterable):
        raise TypeError(
            f"Expected 'char_set' to be iterable type, "
            f"got {type(char_set).__qualname__!r} instead")
    if args:
        try:
            assert (n_args := len(args)) in {1, 2, 3}, f"Unexpected extra args: expected max 3, got {n_args}"
            if n_args == 1:
                args = args[0]
                vt = type(args)
                assert vt in {int, slice}, f"Unexpected arg type: {vt.__qualname__!r}"
                if vt is int:
                    args = slice(args)
            else:
                none_type = type(None)
                assert all(map(lambda x: type(x) in {int, none_type}, args)), (
                        f"Multiple args must be {int.__qualname__!r} or {none_type.__qualname__}, "
                        'got %s instead' %
                        ', '.join(
                            map(
                                repr,
                                sorted(set(xt.__qualname__ for x in args if (xt := type(x)) not in {int, none_type})))))
                args = slice(*args)
        except AssertionError as e:
            raise ValueError(
                e) from None
    else:
        args = slice(0, None)
    char_list = list(char_set)
    random.shuffle(char_list)
    return ''.join(char_list)[args]


def render_font_str(__s: str, font: _FontType | str | int):
    font = get_font_object(font)
    if '\t' in __s:
        __s = __s.replace('\t', '   ')
    if len(__s) > 1:
        lines = __s.splitlines()
        return Image.fromarray(
            np.vstack(
                [np.hstack([np.array(render_font_char(c, font=font), dtype=np.uint8) for c in line]) for line in
                 map(lambda x, max_len=max(map(len, lines)): f'{x:<{max_len}}', lines)]))
    return render_font_char(__s, font)


def render_font_char(__s: str,
                     font: _FontType | str | int,
                     fill: Int3Tuple = (255, 255, 255)):
    if (s_len := len(__s)) > 1:
        raise TypeError(
            f"{render_font_char.__qualname__}() expected a character, but string of length {s_len} found")
    img = Image.new('RGB', size=(24, 24))
    draw = ImageDraw.Draw(img)
    draw.text((4, 0), __s, font=get_font_object(font), fill=fill)
    return img


class PreviewCharSetKwargs(TypedDict, total=False):
    char_set: str
    sort: bool | Literal['dist']


def preview_char_set(__font: _FontType | str | int,
                     **kwargs: Unpack[PreviewCharSetKwargs]):
    __font = get_font_object(__font)
    char_set = kwargs.get('char_set') or ascii_printable()
    if not isinstance(char_set, str):
        raise TypeError(
            f"Expected 'char_set' to be {str.__qualname__} instance, "
            f"got {type(char_set).__qualname__!r} instead")
    sort = kwargs.get('sort', False)
    glyph_dict = get_glyph_masks(__font, char_set=char_set, dist_transform=(sort == 'dist'))
    it = sorted(filter(str.strip, glyph_dict), key=(lambda x: np.sum(glyph_dict[x])) if bool(sort) else None)
    row_size = math.floor(math.sqrt(len(char_set)))
    str_parts = []
    while len(it) > row_size:
        str_parts.append(''.join(it.pop(0) for _ in range(row_size)))
    str_parts.append(f"{''.join(it[0:]):<{row_size}}")
    return render_font_str('\n'.join(str_parts), __font)


def get_rgb_array(__img: Image.Image | RGBArrayLike | str | PathLike[str]) -> RGBArrayLike:
    if isinstance(__img, PathLike | str):
        if '__fspath__' in dir(__img):
            __img = __img.__fspath__()
        img = ski.io.imread(__img)
    else:
        img = __img
    if (vt := type(img)) is not np.ndarray:
        img = np.array(img if vt is not Image.Image else img.convert('RGB'), dtype=np.uint8)
    if (n_channels := img.shape[-1]) == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
    elif n_channels == 2:
        img = cv2.cvtColor(img[:, :, 0], cv2.COLOR_GRAY2RGB)
    return img


class _ColorQuantKwargs(TypedDict, total=False):
    seed: int


type EqualizerKeywords = Literal['white_point', 'scale_saturation']


def color_quantization(img: Union[Image.Image, RGBArrayLike],
                       k: int,
                       **kwargs: Unpack[_ColorQuantKwargs]) -> RGBArrayLike:
    """
    Apply K-means color quantization to reduce the number of colors in the image to 'k' clusters.

    Parameters
    ----------
        img : Image.Image or ndarray[Any, dtype[uint8]]
            Input image, either as a PIL Image or RGB array.

        k : int
            The number of color clusters (i.e., the number of colors to quantize to).

    Keyword Args
    ------------
        seed : int, optional
            Seed to use for K-means random state. Uses a random integer from bytes by default.

    Returns
    -------
        ndarray[Any, dtype[uint8]]
            Color-quantized RGB array with 'k' unique colors.

    Raises
    ------
        TypeError
            If the input image is of an unexpected type.
    """
    if (vt := type(img)) not in (expected_types := {np.ndarray, Image.Image}):
        raise TypeError(
            "Expected %r or %r, got %r instead" % tuple(map(lambda x: x.__qualname__, (*expected_types, vt))))
    img = get_rgb_array(img)
    seed = kwargs.get('seed', int.from_bytes(random.randbytes(2)))
    kmeans = KMeans(n_clusters=k, init='k-means++', random_state=seed)
    kmeans.fit(img.reshape(-1, 3).astype(np.uint8))
    return img


def ansi_quantization(img: RGBArrayLike,
                      ansi_type: type[ansi_color_4Bit | ansi_color_8Bit],
                      *,
                      equalize: bool | EqualizerKeywords = True) -> RGBArrayLike:
    """
    Color-quantize an RGB array into ANSI 4-bit or 8-bit color space.

    Parameters
    ----------
        img : ndarray[Any, dtype[uint8]]
            Input image, as an RGB array.

        ansi_type : type[ansi_color_4Bit | ansi_color_8Bit]
            ANSI color format to map the quantized image to.

        equalize : bool or Literal['white_point'], default=True
            Apply contrast equalization.
            If True, performs contrast stretching;
            if 'white_point', applies white-point equalization.

    Raises
    ------
        TypeError
            If `ansi_type` is not `ansi_color_4Bit` or `ansi_color_8Bit`.

    Returns
    -------
        ansi_array : ndarray[Any, dtype[uint8]]
            The image with RGB values transformed into ANSI color space.
    """
    from chromatic.ansi import (
        nearest_ansi_4bit_rgb as approx4bit,
        nearest_ansi_8bit_rgb as approx8bit
    )

    ansi_types: dict[type[ansi_color_4Bit | ansi_color_8Bit], (Callable[[RGBArrayLike | tuple[int, ...]], Int3Tuple])]
    if not (approx_func := (ansi_types := {ansi_color_4Bit: approx4bit, ansi_color_8Bit: approx8bit}).get(ansi_type)):
        raise TypeError(
            "Expected %r or %r, got %r instead" % tuple(
                map(lambda x: (x if isinstance(x, type) else type(x)).__qualname__, (*ansi_types.keys(), ansi_type))))
    color_cache = {}

    def rgb_func(__x: RGBArrayLike | tuple[int, ...]) -> Int3Tuple:
        if __x in color_cache:
            return color_cache[__x]
        res = color_cache[__x] = approx_func(__x)
        return res

    if eqf := {True: contrast_stretch, 'white_point': equalize_white_point}.get(equalize):
        img = eqf(img)
    img_obj = Image.fromarray(img, mode='RGB')
    if img.size > 1024 ** 2:  # downsize for faster quantization
        w, h, _ = img.shape
        max_dim = max(w, h)
        scale_factor = 768 / max_dim
        new_w, new_h = int(w * scale_factor), int(h * scale_factor)
        img = np.array(img_obj.resize((new_h, new_w), resample=Image.Resampling.LANCZOS))
    return np.apply_along_axis(lambda x: np.uint8(rgb_func(tuple(map(int, x)))), 2, img)


def equalize_white_point(img: RGBArrayLike) -> RGBArrayLike:
    """
    Apply histogram equalization to the L-channel (lightness) in LAB color space.
    Enhances contrast while preserving color, ideal for pronounced light/dark effects.

    Parameters
    ----------
        img : ndarray[Any, dtype[uint8]]
            Input image

    Returns
    -------
        eq_img : ndarray[Any, dtype[uint8]]
            Image with equalized contrast

    See Also
    --------
        contrast_stretch
    """
    lab_img = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
    Lc, Ac, Bc = cv2.split(lab_img)
    Lc_eq = cv2.equalizeHist(Lc)
    lab_eq_img = cv2.merge((Lc_eq, Ac, Bc))
    img_eq = cv2.cvtColor(lab_eq_img, cv2.COLOR_LAB2RGB)
    return img_eq


def contrast_stretch(img: RGBArrayLike) -> RGBArrayLike:
    """
    Perform linear contrast stretching by rescaling intensities between the 2nd and 98th percentiles.
    Provides subtle, balanced contrast enhancement across both lightness and color.

    Parameters
    ----------
        img : ndarray[Any, dtype[uint8]]
            Input image

    Returns
    -------
        eq_img : ndarray[Any, dtype[uint8]]
            Image with stretched contrast

    See Also
    --------
        equalize_white_point
    """
    p2, p98 = np.percentile(img, (2, 98))
    return cast(RGBArrayLike, ski.exposure.rescale_intensity(cast(..., img), in_range=(p2, p98)))


def scale_saturation(img: RGBArrayLike, alpha: float = None) -> RGBArrayLike:
    img = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    img[:, :, 1] = cv2.convertScaleAbs(img[:, :, 1], alpha=alpha or 1.0)
    img[:] = cv2.cvtColor(img, cv2.COLOR_HSV2RGB)
    return img


def _get_asciidraw_objects(__img: Image.Image | RGBArrayLike | str | PathLike[str],
                           __font: _FontType | str | int):
    img = get_rgb_array(__img)
    font = get_font_object(__font)
    return img, font


class Img2AsciiKwargs(TypedDict, total=False):
    ret_img: bool


@overload
def img2ascii(
    __img: Image.Image | RGBArrayLike | str,
    __font: _FontType | str | int = ...,
    factor: int = ...,
    char_set: Optional[str] = ...,
    sort_glyphs: bool | type[reversed] = ...,
    *,
    ret_img: Literal[False] = False
) -> str:
    ...


@overload
def img2ascii(
    __img: Image.Image | RGBArrayLike | str,
    __font: _FontType | str | int = ...,
    factor: int = ...,
    char_set: Optional[str] = ...,
    sort_glyphs: bool | type[reversed] = ...,
    *,
    ret_img: Literal[True]
) -> tuple[str, RGBArrayLike]:
    ...


def img2ascii(__img: Image.Image | RGBArrayLike | str | PathLike[str],
              __font: _FontType | str | int = 'arial.ttf',
              factor: int = 100,
              char_set: Iterable[str] = None,
              sort_glyphs: bool | type[reversed] = True,
              *,
              ret_img: bool = False) -> str | tuple[str, RGBArrayLike]:
    """
    Convert an image to an ASCII string.

    Parameters
    ----------
        __img : Image.Image | ndarray[Any, dtype[uint8]] | str | PathLike[str]
            The image to convert into ASCII.
            Can be a PIL Image object, RGB array, or image filepath.

        __font : FreeTypeFont | UserFont | str | int
            Font to use for character aspect ratio, and glyph-to-greyscale comparisons (if sorted glyphs).
            Can be a FreeTypeFont object, UserFont enum/name/value, or TrueType font filepath (i.e., `.ttf`).

        factor : int
            The fixed row length of the output, in one-character strings. Affects level of visual detail.

        char_set : Iterable[str], optional
            The literal string or sequence of strings to use for greyscale interpolation and visualization.
            If None (default), the character set will be determined based on the `__font` parameter.

        sort_glyphs : bool or type[reversed], default=True
            Sort the character set by the sum of each character's glyph mask.
            If False, leave the character set unsorted and interpolate in-place;
            if True, sort values from lowest to highest, mapping [0.0, ..., 1.0] to greyscale;
            if builtin type `reversed`, use reverse sort order, mapping [1.0, ..., 0.0] to greyscale.

        ret_img : bool, default=False
            Return the output string and RGB array.
            If True, return the ASCII string and RGB array as tuple[str, ndarray[Any, dtype[uint8]]].
            Used by `img2ansi` to lazily obtain the base ASCII chars and original RGB array.

    Returns
    -------
        ascii_str : str
            The ASCII visualization of the input image, concatenated as a single multi-line string object.

    Raises
    ------
        TypeError
            If `char_set` is an unexpected type

    Notes
    -----
    * 'row length' and 'absolute width' are synonymous with the `factor` param.
    * `factor` equals n characters per row.

    * `char_set`: ASCII printable is default for most fonts, but some fonts are mapped to specific encodings.
    * For example, if `__font` is `UserFont.IBM_VGA_437_8X16`, the default will be printable characters from 'cp437'.

    * ASCII interpolation maps the greyscale value range (0.0 to 1.0) across characters of the character set.
    * To illustrate, take the literal string ' *#█' containing 4 characters: [' ', '*', '#', '█']
    * The chars map to interpolated ranges: {' ': (0.0, 0.25), '*': (0.25, 0.5), '#': (0.5, 0.75), '█': (0.75, 1.0)}
    * And if '* █#' was the string (unsorted): {'*': (0.0, 0.25), ' ': (0.25, 0.5), '█': (0.5, 0.75), '#': (0.75, 1.0)}

    See Also
    --------
        ascii2img : Render an ASCII string as an image.
    """
    img, font = _get_asciidraw_objects(__img, __font)
    greyscale = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    img_aspect = truediv(*greyscale.shape[::-1])
    char_aspect = math.ceil(truediv(*font.getbbox(' ')[2:][::-1]))
    new_height = int(factor / img_aspect / char_aspect)
    greyscale = ski.transform.resize(greyscale, (new_height, factor))
    if char_set is None:
        cursed_fonts = {UserFont.IBM_VGA_437_8X16: cp437_printable}
        char_set = shuffle_char_set(cursed_fonts.get(__font, ascii_printable)())
    elif type(char_set) is not str:
        char_set = ''.join(char_set)
    if sort_glyphs in {True, reversed}:
        char_set = sort_ascii_glyphs(char_set, font, reverse=(sort_glyphs is reversed))
    ascii_img = np.array(
        [[char_set[int(pixel * (len(char_set) - 1))] for pixel in row] for row in greyscale])
    ascii_str = '\n'.join(map(''.join, ascii_img))
    if ret_img:
        return ascii_str, img
    return ascii_str


def img2ansi(
    __img: Image.Image | RGBArrayLike | str | PathLike[str],
    __font: _FontType | str | int = 'arial.ttf',
    factor: int = 100,
    char_set: Iterable[str] = None,
    ansi_type: _AnsiColorFormat = None,
    sort_glyphs: bool | type[reversed] = True,
    equalize: bool | EqualizerKeywords = True,
    bg: _RGBVector = (0, 0, 0),
    **kwargs
):
    """
    Convert an image to an ANSI array.

    Parameters
    ----------
        __img : Image.Image | ndarray[Any, dtype[uint8]] | str | PathLike[str]
            The image to convert into ANSI.
            Can be a PIL Image object, RGB array, or image filepath.

        __font : FreeTypeFont | UserFont | str | int
            Font to use for character aspect ratio, and glyph-to-greyscale comparisons (if sorted glyphs).
            Can be a FreeTypeFont object, UserFont enum/name/value, or TrueType font filepath (i.e., `.ttf`).

        factor : int
            The fixed row length of the output, in one-character strings. Affects level of visual detail.

        char_set : Iterable[str], optional
            The literal string or sequence of strings to use for greyscale interpolation and visualization.
            If None (default), the character set will be determined based on the `__font` parameter.

        ansi_type : str or type[ansi_color_4Bit | ansi_color_8Bit | ansi_color_24Bit], optional
            ANSI color format to map the RGB values to.
            Can be 4-bit, 8-bit, or 24-bit ANSI color space.
            If 4-bit or 8-bit, the RGB array will be color-quantized and transformed into the ANSI color space;
            if 24-bit, colors are sourced from the base RGB array;
            if None (default), uses default ANSI type (4-bit or 8-bit, depending on system's VT processing).

        sort_glyphs : bool or type[reversed], default=True
            Sort the character set by the sum of each character's glyph mask.
            If False, leave the character set unsorted and interpolate in-place;
            if True, sort values from lowest to highest, mapping [0.0, ..., 1.0] to greyscale;
            if builtin type `reversed`, use reverse sort order, mapping [1.0, ..., 0.0] to greyscale.

        equalize : bool or Literal['white_point'], default=True
            Apply contrast equalization to the input image.
            If True, performs contrast stretching;
            if 'white_point', applies white-point equalization.

        bg : Color or sequence of ints or ndarray[Any, dtype[uint8]]
            Background color to use for all ColorStr objects in the array.

    Returns
    -------
        ansi_array : list[list[ColorStr]]
            The ANSI-converted image, as an array of ColorStr objects.

    Raises
    ------
        ValueError
            If `bg` cannot be coerced into a Color object.

        TypeError
            If `ansi_type` is not a valid ANSI type.

    Notes
    -----
    * Conversion times for ANSI 24-bit are faster than the other formats because of no intermediate color quantization.
    * The caveat to this is that ANSI 24-bit is not as widely supported by terminals as the other formats.
    * Terminal support only becomes a concern when printing ANSI to stdout; image renders can be in any format.

    See Also
    --------
        ansi2img : Render an ANSI array as an image.
        img2ascii : Used to obtain the base ASCII characters.
    """
    ansi_type = get_ansi_type(ansi_type) if ansi_type else get_default_ansi()
    if type(bg) is not Color:
        try:
            bg = Color(bg)
        except ValueError as e:
            if 'RGB value' in (es := str(e)):
                e = es.replace('RGB value', 'background color')
            raise ValueError(
                e) from None
    from chromatic.ansi.palette import color_str_wrapper

    bg_wrapper: ColorStr = color_str_wrapper(bg=bg, ansi_type=ansi_type).ansi
    base_ascii_chars, color_arr = img2ascii(__img, __font, factor, char_set, sort_glyphs, ret_img=True)
    lines = base_ascii_chars.splitlines()
    h, w = tuple(map(len, (lines, lines[0])))
    if ansi_type is not ansi_color_24Bit:
        color_arr = ansi_quantization(color_arr, ansi_type=ansi_type, equalize=equalize)
    elif eq_func := {
        True: contrast_stretch,
        'white_point': equalize_white_point,
        'scale_saturation': lambda img: scale_saturation(img, **dict(alpha=kwargs.get('alpha', 1.0)))
    }.get(equalize):
        color_arr = eq_func(color_arr)
    color_arr = np.array(
        Image.fromarray(color_arr, mode='RGB').resize((w, h), resample=Image.Resampling.LANCZOS),
        dtype=np.uint8)
    x = []
    for i in range(h):
        xs = []
        for j in range(w):
            char = lines[i][j]
            fg_color = Color(color_arr[i, j])
            if j > 0 and xs[-1].fg == fg_color:
                xs[-1] += char
            else:
                xs.append(bg_wrapper.replace('', char).recolor(fg=fg_color))
        x.append(xs)
    return x


def ascii2img(__ascii: str,
              __font: _FontType | str | int = 'arial.ttf',
              font_size=24,
              fg=(0, 0, 0),
              bg=(255, 255, 255)):
    """
    Render an ASCII string as an image.

    Parameters
    ----------
        __ascii : str
            The ASCII string to convert into an image.

        __font : FreeTypeFont | UserFont | str | int
            Font to use for rendering the ASCII characters.
            Can be a FreeTypeFont object, UserFont enum/name/value, or TrueType font filepath (i.e., `.ttf`).

        font_size : int
            Font size in pixels for the rendered ASCII characters.

        fg : tuple[int, int, int]
            Foreground color in RGB format.
            This is the color for the ASCII characters.

        bg : tuple[int, int, int]
            Background color in RGB format.

    Returns
    -------
        ascii_img : Image.Image
            A PIL Image object representing the rendered ASCII string.

    See Also
    --------
        img2ascii : Convert an image into an ASCII string.
    """
    font = ImageFont.truetype(get_font_object(__font, retpath=True), font_size)
    lines = __ascii.split('\n')
    n_rows, n_cols = len(lines), len(lines[0])
    cw, ch = font.getbbox(' ')[2:]
    iw = int(cw * n_cols)
    ih = int(ch * n_rows)
    img = Image.new('RGB', (iw, ih), cast(tuple[float, ...], bg))
    draw = ImageDraw.Draw(img)
    y_offset = 0
    for line in lines:
        draw.text((0, y_offset), line, font=font, fill=fg)
        y_offset += ch
    return img


def ansi2img(__ansi_array: list[list[ColorStr]],
             __font: _FontType | str | int = 'arial.ttf',
             font_size=24,
             default_bg: Int3Tuple | Literal['auto'] = (0, 0, 0)):
    """
    Render an ANSI array as an image.

    Parameters
    ----------
        __ansi_array : list[list[ColorStr]]
            An array-like, row-major list of lists of ColorStr objects

        __font : FreeTypeFont | UserFont | str | int
            Font to render the ANSI strings with.
            Can be a FreeTypeFont object, UserFont enum/name/value, or TrueType font filepath (i.e., `.ttf`).

        font_size : int
            Font size in pixels

        default_bg : tuple[int, int, int]
            Default background color, in RGB format.
            Used for the base color of the image, and when ColorStr objects have a `bg` attribute that is None.

    Returns
    -------
        ansi_img : Image.Image
            PIL Image of the rendered ANSI array

    Raises
    ------
        ValueError
            If the input ANSI array is empty

    See Also
    --------
        img2ansi : Create an ANSI array from an input image, font, and character set.
    """
    font = ImageFont.truetype(get_font_object(__font, retpath=True), font_size)
    n_rows = len(__ansi_array)
    auto = default_bg == 'auto'
    input_bg = default_bg if not auto else None
    if auto:
        default_bg = None
    default_fg = None
    if n_rows == 0:
        raise ValueError(
            'ANSI string input is empty')
    row_widths = []
    max_row_width = 0
    row_height = font.getbbox(' ')[3]
    for row in __ansi_array:
        row_width = sum(font.getbbox(color_str.base_str)[2] for color_str in row)
        row_widths.append(row_width)
        max_row_width = max(max_row_width, row_width)
    iw, ih = tuple(map(int, (max_row_width, (n_rows * row_height))))
    img = Image.new('RGB', (iw, ih), cast(tuple[float, ...], default_bg))
    draw = ImageDraw.Draw(img)
    y_offset = 0
    fg_color: Int3Tuple
    bg_color: Int3Tuple
    for row_idx, row in enumerate(__ansi_array):
        x_offset = 0
        for color_str in row:
            text_width = font.getbbox(color_str.base_str)[2]
            if color_str._sgr_.is_reset():
                default_fg = None
                default_bg = input_bg
            fg_color = getattr(color_str.fg, 'rgb', default_fg or None)
            bg_color = getattr(color_str.bg, 'rgb', default_bg or None)
            if fg_color:
                default_fg = fg_color
            if bg_color and auto:
                default_bg = bg_color
            draw.rectangle([x_offset, y_offset, x_offset + text_width, y_offset + row_height], fill=bg_color)
            draw.text((x_offset, y_offset), color_str.base_str, font=font, fill=fg_color)
            x_offset += text_width
        y_offset += row_height
    return img


def is_csi_param(__c: str):
    return __c == ';' or __c.isdigit()


def reshape_ansi(__str: str, h: int, w: int):
    size = (h * w)
    offsets = {row: 0 for row in range(h)}
    arr = [['\x00'] * w for _ in range(h)]
    flat_iter = (divmod(idx, w) for idx in range(size))
    str_len = len(__str)
    j = 0

    def increment(__c: str = ' '):
        nonlocal x, y
        arr[x][y] += __c
        offsets[x] += 1
        x, y = next(flat_iter)

    try:
        x, y = next(flat_iter)
        while j < str_len:
            if __str[j:(i := j + 2)] == '\x1b[':
                j = i
                while is_csi_param(c := __str[j]):
                    j += 1
                params = __str[i:j]
                if c == 'C':
                    for _ in range(int(params)):
                        increment()
                elif c == 'm':
                    arr[x][y] += str(SgrSequence(list(map(int, params.split(';')))))
            else:
                if (c := __str[j]) == '\n':
                    while y < w - 1:
                        increment()
                else:
                    increment(c)
            j += 1
    except StopIteration:
        pass
    return '\n'.join(' ' * (w - offsets[i]) + ''.join(row) for i, row in enumerate(arr)).translate({0: None})


def to_sgr_array(__str: str, ansi_type: AnsiColorParam = '4b'):
    _ansi_typ_ = get_ansi_type(ansi_type)
    x = []
    for line in __str.splitlines():
        xs = []
        line = line.translate({0x1b: '\x00\x1b'})
        for s in filter(None, line.split('\x00')):
            if s[:(i := min(2, len(s) - 1))] == '\x1b[':
                params, _, text = s[i:].partition('m')
                xs.append(
                    ColorStr(
                        text,
                        color_spec=SgrSequence(list(map(int, params.split(';')))),
                        ansi_type=_ansi_typ_,
                        no_reset=True))
            else:
                xs.append(ColorStr(s, ansi_type=_ansi_typ_, no_reset=True))
        x.append(xs)
    return x


def render_ansi_str(__str: str, h: int, w: int, *, default_bg: Literal['auto'] | tuple[int, int, int] = 'auto'):
    reshaped = to_sgr_array(reshape_ansi(__str, h, w))
    img = ansi2img(reshaped, UserFont.IBM_VGA_437_8X16, font_size=16, default_bg=default_bg)
    return img


def read_ans[AnyStr: (str, bytes)](fp: AnyStr | PathLike[AnyStr]):
    with open(fp, mode='r', encoding='cp437') as f:
        content = f.read()
    from chromatic.ascii._curses import ControlCharacter, cp437_translate

    ansi = cp437_translate(content, ignore=(ControlCharacter.ESC, ControlCharacter.SUB, ControlCharacter.NL))
    if (sub := ansi.rfind(chr(ControlCharacter.SUB))) != -1:
        ansi = ansi[:sub]
    return ansi