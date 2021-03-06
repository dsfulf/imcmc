from cycler import Cycler, cycler
from matplotlib.axes import Axes
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.pyplot import axes
import numpy as np
from PIL import Image
import pymc3 as pm
import theano  # type: ignore
import theano.tensor as tt
from tqdm import tqdm
import scipy

from typing import Tuple


def get_rainbow() -> Cycler:
    """Creates a rainbow color cycle"""
    return cycler('color', [
        '#FF0000',
        '#FF7F00',
        '#FFFF00',
        '#00FF00',
        '#0000FF',
        '#4B0082',
        '#9400D3',
    ])


def load_image(image_file, mode=None) -> np.ndarray:
    """Load filename into a numpy array, filling in transparency with 0's.

    Parameters
    ----------
    image_file : str
        File to load. Usually works with .jpg and .png.

    Returns
    -------
    numpy.ndarray of resulting image. Has shape (w, h), (w, h, 3), or (w, h, 4)
        if black and white, color, or color with alpha channel, respectively.
    """
    image = Image.open(image_file)
    if mode is None:
        mode = image.mode
    alpha = image.convert('RGBA').split()[-1]
    background = Image.new("RGBA", image.size, (255, 255, 255, 255,))
    background.paste(image, mask=alpha)
    img = np.flipud(np.asarray(background.convert(mode)))
    img = img / 255
    if mode == 'L':  # I don't know how images work, but .png's are inverted
        img = 1 - img
    return img


class ImageLikelihood(theano.Op):
    """
    Custom theano op for turning a 2d intensity matrix into a density
    distribution.
    """

    itypes = [tt.dvector]
    otypes = [tt.dvector]

    def __init__(self, img):
        self.width, self.height = img.shape
        self.density = scipy.interpolate.RectBivariateSpline(
            x=np.arange(self.width),
            y=np.arange(self.height),
            z=img)

    def perform(self, node, inputs, output_storage):
        """Evaluates the density of the image at the given point."""
        x, y = inputs[0]
        if x < 0 or x > self.width or y < 0 or y > self.height:
            output_storage[0][0] = np.array([np.log(0)])
        else:
            output_storage[0][0] = np.log(self.density(x, y))[0]


def sample_grayscale(image, samples=5000, tune=100, nchains=4, threshold=0.2,
                     cores=1, **kwargs) -> pm.backend.base.MultiTrace:
    """Run MCMC on a 1 color image. Works best on logos or text.

    Parameters
    ----------
    image : numpy.ndarray
        Image array from `load_image`.  Should have `image.ndims == 2`.

    samples : int
        Number of samples to draw from the image

    tune : int
        Number of tuning steps to take. Note that this adjusts the step size:
            if you want smaller steps, make tune closer to 0.

    nchains : int
        Number of chains to sample with. This will later turn into the number
        of colors in your plot. Note that you get `samples * nchains` of total
        points in your final scatter.

    threshold : float
        Float between 0 and 1. It looks nicer when an image is binarized, and
        this will do that. Use `None` to not binarize. In theory you should get
        fewer samples from lighter areas, but your mileage may vary.

    cores : int
        Number of CPU cores to utilize. Windows machines may need to use
        default value of 1.

    kwargs :
        Other keyword arguments passed to `pm.sample()`.

    Returns
    -------
    pymc3.MultiTrace of samples from the image. Each sample is an (x, y) float
        of indices that were sampled, with the variable name 'image'.
    """
    # preprocess
    image_copy = image.copy()
    if threshold != -1:
        image_copy[image < threshold] = 0
        image_copy[image >= threshold] = 1

    # need an active pixel to start on
    active_pixels = np.array(
        list(zip(*np.where(image_copy == image_copy.max()))))
    idx = np.random.randint(0, len(active_pixels), nchains)
    start = active_pixels[idx]

    with pm.Model():
        pm.DensityDist('image', ImageLikelihood(image_copy), shape=2)
        trace = pm.sample(samples,
                          cores=cores,
                          tune=tune,
                          chains=nchains, step=pm.Metropolis(),
                          start=[{'image': x} for x in start],
                          **kwargs
                          )
    return trace


def sample_color(image, samples=5000, tune=1000, nchains=4, cores=1, **kwargs
        ) -> pm.backend.base.MultiTrace:
    """Run MCMC on a color image. EXPERIMENTAL!

    Parameters
    ----------
    image : numpy.ndarray
        Image array from `load_image`.  Should have `image.ndims == 2`.

    samples : int
        Number of samples to draw from the image

    tune : int
        All chains start at the same spot, so it is good to let them wander
        apart a bit before beginning

    nchains : int
        Number of chains to sample with. This will later turn into the number
        of colors in your plot. Note that you get `samples * nchains` of total
        points in your final scatter.

    cores : int
        Number of CPU cores to utilize. Windows machines may need to use
        default value of 1.

    kwargs :
        Other keyword arguments passed to `pm.sample()`.

    Returns
    -------
    pymc3.MultiTrace of samples from the image. Each sample is an (x, y) float
        of indices that were sampled, with three variables named 'red',
        'green', 'blue'.
    """

    with pm.Model():
        pm.DensityDist('red', ImageLikelihood(image[:, :, 0]), shape=2)
        pm.DensityDist('green', ImageLikelihood(image[:, :, 1]), shape=2)
        pm.DensityDist('blue', ImageLikelihood(image[:, :, 2]), shape=2)

        trace = pm.sample(samples, cores=cores, chains=nchains,
                          tune=tune, step=pm.Metropolis())
    return trace


def plot_multitrace(trace, image, max_size=10, colors=None, **plot_kwargs
        ) -> Tuple[plt.Figure, plt.axes.Axes]:
    """Plot an image of the grayscale trace.

    Parameters
    ----------
    trace : pymc3.MultiTrace
        Get this from sample_grayscale

    image : numpy.ndarray
        Image array from `load_image`, used to produce the trace.

    max_size : float
        Used to set the figsize for the image, maintaining the aspect ratio.
        In inches!

    colors : iterable
        You can set custom colors to cycle through! Default is the rainbow.

    plot_kwargs :
        Other keyword arguments passed to the trace plotting. Some useful
        examples are marker='.' in case you sampled lots of points, alpha=0.3
        to add transparency to the points, or linestyle='-', so you can see the
        actual path the chains took.

    Returns
    -------
    (figure, axis)
        The matplotlib figure and axis with the plot
    """
    default_kwargs = {'marker': 'o', 'linestyle': '', 'alpha': 0.4}
    default_kwargs.update(plot_kwargs)
    if colors is None:
        colors = get_rainbow()
    else:
        colors = cycler('color', colors)

    vals = [trace.get_values('image', chains=chain) for chain in trace.chains]

    fig, ax = plt.subplots(figsize=get_figsize(image, max_size))
    ax.set_prop_cycle(colors)
    ax.set_xlim((0, image.shape[1]))
    ax.set_ylim((0, image.shape[0]))
    ax.axis('off')

    for val in vals:
        ax.plot(val[:, 1], val[:, 0], **default_kwargs)
    return fig, ax


def make_gif(trace, image, steps=200, leading_point=True,
             filename='output.gif', max_size=10, interval=30, dpi=20,
             blit=True, colors=None, repeat_delay=3000, **plot_kwargs) -> FuncAnimation:
    """Make a gif of the grayscale trace.

    Parameters
    ----------
    trace : pymc3.MultiTrace
        Get this from sample_grayscale

    image : numpy.ndarray
        Image array from `load_image`, used to produce the trace.

    steps : int
        Number of frames in the resulting .gif

    leading_point : bool
        If true, adds a large point at the head of each chain, so you can
        follow the path easier.

    filename : str
        Place to save the resulting .gif to

    max_size : float
        Used to set the figsize for the image, maintaining the aspect ratio.
        In inches!

    interval : int
        How long each frame lasts in milliseconds.

    dpi : int
        Quality of the resulting .gif Seems like larger values make the gif
        bigger too.

    blit : bool
        Use blitting to only draw updated pixels.

    colors : iterable
        You can set custom colors to cycle through! Default is the rainbow.

    repeat_delay : int
        Number of milliseconds after last frame before restarting the animation.

    plot_kwargs :
        Other keyword arguments passed to the trace plotting. Some useful
        examples are marker='.' in case you sampled lots of points, alpha=0.3
        to add transparency to the points, or linestyle='-', so you can see the
        actual path the chains took.

    Returns
    -------
    str
        filename where the gif was saved
    """
    kwargs = {'marker': '', 'linestyle': '', 'alpha': 0.4, 'markersize': '10'}
    kwargs.update(plot_kwargs)
    kwargs.setdefault('ms', kwargs.pop('markersize'))
    ms = kwargs['ms']

    if colors is None:
        colors = get_rainbow()
    else:
        colors = cycler('color', colors)

    vals = [trace.get_values('image', chains=chain) for chain in trace.chains]
    intervals = np.linspace(0, vals[0].shape[0] - 1, num=steps + 1, dtype=int)[1:]  # noqa

    fig, ax = plt.subplots(figsize=get_figsize(image, max_size))
    ax.set_prop_cycle(colors)
    ax.set_xlim((0, image.shape[1]))
    ax.set_ylim((0, image.shape[0]))
    ax.axis('off')

    lines, points = [], []
    for _ in vals:
        lines.append(ax.plot([], [], animated=True, **kwargs)[0])
        if leading_point:
            points.append(ax.plot([], [], 'o', c=lines[-1].get_color(), markersize=ms, animated=True)[0])   # noqa
        else:
            points.append(None)

    def init():
        for pts, lns in zip(points, lines):
            lns.set_data([], [])
            pts.set_data([], [])

        return (*lines, *points)

    def update(idx):
        if idx < len(intervals):
            for pts, lns, val in zip(points, lines, vals):
                lns.set_data(val[:intervals[idx], 1], val[:intervals[idx], 0])
                if leading_point:
                    pts.set_data(val[intervals[idx], 1],
                                 val[intervals[idx], 0])
        elif idx == len(intervals) and leading_point:
            for pts in points:
                pts.set_data([], [])

        return (*lines, *points)

    anim = FuncAnimation(fig, update, init_func=init, frames=np.arange(steps + 20),
                         interval=interval, blit=blit, repeat_delay=repeat_delay)   # noqa

    if filename.split('.')[-1] == 'mp4':
        writer = 'ffmpeg'
        extra_args = []  # ['-r', f'{15:0d}']
    else:
        writer = 'imagemagick'
        extra_args = ['-layers', 'Optimize']

    anim.save(filename, dpi=dpi, writer=writer, extra_args=extra_args)
    return anim


def get_figsize(image, max_size=10) -> Tuple[float, float]:
    """Helper to scale figures"""
    scale = max_size / max(image.shape)
    return (scale * image.shape[1], scale * image.shape[0])


def _process_image_trace(trace, image, blur) -> List[np.ndarray]:
    """Adds Gaussian blur"""
    w, h = image.shape[:2]
    colors = ('red', 'green', 'blue')
    channels = [np.zeros((w, h)) for color in colors]
    for color, channel in zip(colors, channels):
        for idx in np.array(np.round(trace[color]), dtype=int):
            x, y = idx
            channel[min(x, w - 1), min(y, h - 1)] += 1
    return [scipy.ndimage.filters.gaussian_filter(channel, blur) for channel in channels]   # noqa


def plot_multitrace_color(trace, image, blur=8, channel_max=None) -> Image:
    """Plot the trace from a color image

    Does additive blending of the three channels using Pillow. Higher `blur`
    make the colors look right, but the image look blurrier.

    Parameters
    ----------
    trace : pymc3.MultiTrace
        Get this from sample_color

    image : numpy.ndarray
        Image array from `load_image`, used to produce the trace.

    blur : float
        Each point only colors in a single pixel, but a gaussian blur makes the
        samples blend well. This typically must be tuned by eye.

    channel_max : list or None
        This is used internally to normalize channels for making a gif

    Returns
    -------
    PIL.Image
        RGB image of the samples
    """
    smoothed = _process_image_trace(trace, image, blur)
    if channel_max is None:
        channel_max = [channel.max() for channel in smoothed]

    pils = []
    for channel, c_max in zip(smoothed, channel_max):
        pils.append(Image.fromarray(
            np.uint8(255 * np.flipud(channel / c_max))))
    return Image.merge('RGB', pils)


def make_color_gif(trace, image, blur=8, steps=200, max_size=10, filename='output.gif',
                   interval=30, dpi=20) -> FuncAnimation:
    """Make a gif of the color trace. SUPER EXPERIMENTAL!

    Tries to grab portions of the trace from


    Parameters
    ----------
    trace : pymc3.MultiTrace
        Get this from sample_grayscale

    image : numpy.ndarray
        Image array from `load_image`, used to produce the trace.

    blur : float
        Each point only colors in a single pixel, but a gaussian blur makes the
        samples blend well.  This typically must be tuned by eye.
    steps : int
        Number of frames in the resulting .gif

    max_size : float
        Used to set the figsize for the image, maintaining the aspect ratio. In
        inches!

    leading_point : bool
        If true, adds a large point at the head of each chain, so you can
        follow the path easier.

    filename : str
        Place to save the resulting .gif to

    interval : int
        How long each frame lasts in milliseconds.

    dpi : int
        Quality of the resulting .gif Seems like larger values make the gif
        bigger too.

    Returns
    -------
    str
        filename where the gif was saved
    """
    figsize = get_figsize(image, max_size=max_size)

    intervals = np.linspace(0, len(trace) - 1, num=steps + 1, dtype=int)[1:]

    fig, ax = plt.subplots(figsize=figsize)
    ax.imshow(np.zeros_like(image))
    ax.axis('off')
    channel_max = [channel.max() for channel in _process_image_trace(trace, image, blur)]  # noqa

    with tqdm(total=steps) as pbar:
        def update(idx):
            color_image = plot_multitrace_color(trace[:intervals[idx]], image, blur=blur,
                                                channel_max=channel_max)
            ax.imshow(color_image)
            pbar.update(1)
            return ax

        anim = FuncAnimation(fig, update, frames=np.arange(steps), interval=interval)  # noqa
        anim.save(filename, dpi=dpi, writer='imagemagick')
    return anim
