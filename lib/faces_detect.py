#!/usr/bin python3
""" Face and landmarks detection for faceswap.py """
import logging

from zlib import compress, decompress

import cv2
import numpy as np

from lib.aligner import Extract as AlignerExtract, get_align_mat, get_matrix_scaling

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class DetectedFace():
    """ Detected face and landmark information

    Holds information about a detected face, it's location in a source image
    and the face's 68 point landmarks.

    Methods for aligning a face are also callable from here.

    Parameters
    ----------
    image: np.ndarray, optional
        This is a generic image placeholder that should not be relied on to be holding a particular
        image. It may hold the source frame that holds the face, a cropped face or a scaled image
        depending on the method using this object.
    x: int
        The left most point (in pixels) of the face's bounding box as discovered in
        :mod:`plugins.extract.detect`
    w: int
        The width (in pixels) of the face's bounding box as discovered in
        :mod:`plugins.extract.detect`
    y: int
        The top most point (in pixels) of the face's bounding box as discovered in
        :mod:`plugins.extract.detect`
    h: int
        The height (in pixels) of the face's bounding box as discovered in
        :mod:`plugins.extract.detect`
    landmarks_xy: list
        The 68 point landmarks as discovered in :mod:`plugins.extract.align`. Should be a ``list``
        of 68 `(x, y)` ``tuples`` with each of the landmark co-ordinates.
    mask: dict
        The generated mask(s) for the face as generated in :mod:`plugins.extract.mask`. Must be a
        dict of {**name** (`str`): :class:`Mask`}.
    """
    def __init__(self, image=None, x=None, w=None, y=None, h=None,
                 landmarks_xy=None, mask=None, filename=None):
        logger.trace("Initializing %s: (image: %s, x: %s, w: %s, y: %s, h:%s, "
                     "landmarks_xy: %s, mask: %s, filename: %s)",
                     self.__class__.__name__,
                     image.shape if image is not None and image.any() else image,
                     x, w, y, h, landmarks_xy,
                     {k: v.shape for k, v in mask} if mask is not None else mask,
                     filename)
        self.image = image
        self.x = x  # pylint:disable=invalid-name
        self.w = w  # pylint:disable=invalid-name
        self.y = y  # pylint:disable=invalid-name
        self.h = h  # pylint:disable=invalid-name
        self.landmarks_xy = landmarks_xy
        self.mask = dict() if mask is None else mask
        self.filename = filename
        self.hash = None
        self.face = None
        """ str: The hash of the face. This cannot be set until the file is saved due to image
        compression, but will be set if loading data from :func:`from_alignment` """

        self.aligned = dict()
        self.feed = dict()
        self.reference = dict()
        logger.trace("Initialized %s", self.__class__.__name__)

    @property
    def left(self):
        """int: Left point (in pixels) of face detection bounding box within the parent image """
        return self.x

    @property
    def top(self):
        """int: Top point (in pixels) of face detection bounding box within the parent image """
        return self.y

    @property
    def right(self):
        """int: Right point (in pixels) of face detection bounding box within the parent image """
        return self.x + self.w

    @property
    def bottom(self):
        """int: Bottom point (in pixels) of face detection bounding box within the parent image """
        return self.y + self.h

    @property
    def training_coverage(self):
        """ The coverage ratio to add for training images """
        return 1.0

    def add_mask(self, name, mask, affine_matrix, frame_dims, interpolator, storage_size=128):
        """ Add a :class:`Mask` to this detected face

        The mask should be the original output from  :mod:`plugins.extract.mask`
        If a mask with this name already exists it will be overwritten by the given
        mask.

        Parameters
        ----------
        name: str
            The name of the mask as defined by the :attr:`plugins.extract.mask._base.name`
            parameter.
        mask: numpy.ndarray
            The mask that is to be added as output from :mod:`plugins.extract.mask`
            It should be in the range 0.0 - 1.0 ideally with a ``dtype`` of ``float32``
        affine_matrix: numpy.ndarray
            The transformation matrix required to transform the mask to the original frame.
        frame_dims: tuple
            The `(height, width)` dimensions of the original frame that this mask was created from.
        interpolator, int:
            The CV2 interpolator required to transform this mask to it's original frame
        storage_size, int (optional):
            The size the mask is to be stored at.
        """
        logger.trace("name: '%s', mask shape: %s, affine_matrix: %s, frame_dims: %s, "
                     "interpolator: %s", name, mask.shape, affine_matrix, frame_dims, interpolator)
        fsmask = Mask(storage_size=storage_size)
        fsmask.add(mask, affine_matrix, frame_dims, interpolator)
        self.mask[name] = fsmask

    def to_alignment(self):
        """  Return the detected face formatted for an alignments file

        returns
        -------
        alignment: dict
            The alignment dict will be returned with the keys ``x``, ``w``, ``y``, ``h``,
            ``landmarks_xy``, ``mask``, ``hash``.
        """

        alignment = dict()
        alignment["x"] = self.x
        alignment["w"] = self.w
        alignment["y"] = self.y
        alignment["h"] = self.h
        alignment["landmarks_xy"] = self.landmarks_xy
        alignment["hash"] = self.hash
        alignment["mask"] = {name: mask.to_dict() for name, mask in self.mask.items()}
        logger.trace("Returning: %s", alignment)
        return alignment

    def from_alignment(self, alignment, image=None):
        """ Set the attributes of this class from an alignments file and optionally load the face
        into the ``image`` attribute.

        Parameters
        ----------
        alignment: dict
            A dictionary entry for a face from an alignments file containing the keys
            ``x``, ``w``, ``y``, ``h``, ``landmarks_xy``.
            Optionally the key ``hash`` will be provided, but not all use cases will know the
            face hash at this time.
            Optionally the key ``mask`` will be provided, but legacy alignments will not have
            this key.
        image: numpy.ndarray, optional
            If an image is passed in, then the ``image`` attribute will
            be set to the cropped face based on the passed in bounding box co-ordinates
        """

        logger.trace("Creating from alignment: (alignment: %s, has_image: %s)",
                     alignment, bool(image is not None))
        self.x = alignment["x"]
        self.w = alignment["w"]
        self.y = alignment["y"]
        self.h = alignment["h"]
        self.landmarks_xy = alignment["landmarks_xy"]
        # Manual tool does not know the final hash so default to None
        self.hash = alignment.get("hash", None)
        # Manual tool and legacy alignments will not have a mask
        if alignment.get("mask", None) is not None:
            self.mask = dict()
            for name, mask_dict in alignment["mask"].items():
                self.mask[name] = Mask()
                self.mask[name].from_dict(mask_dict)
        if image is not None and image.any():
            self.image = image
            self._image_to_face(image)
        logger.trace("Created from alignment: (x: %s, w: %s, y: %s. h: %s, "
                     "landmarks: %s, mask: %s)",
                     self.x, self.w, self.y, self.h, self.landmarks_xy, self.mask)

    def _image_to_face(self, image):
        """ set self.image to be the cropped face from detected bounding box """
        logger.trace("Cropping face from image")
        self.face = image[self.top: self.bottom,
                          self.left: self.right]

    # <<< Aligned Face methods and properties >>> #
    def load_aligned(self, image, size=256, coverage_ratio=1.0, dtype=None):
        """ Align a face from a given image.

        Aligning a face is a relatively expensive task and is not required for all uses of
        the :class:`~lib.faces_detect.DetectedFace` object, so call this function explicitly to
        load an aligned face.

        This method plugs into :mod:`lib.aligner` to perform face alignment based on this face's
        ``landmarks_xy``. If the face has already been aligned, then this function will return
        having performed no action.

        Parameters
        ----------
        image: numpy.ndarray
            The image that contains the face to be aligned
        size: int
            The size of the output face in pixels
        coverage_ratio: float
            The metric determining the field of view of the returned face
        dtype: str, optional
            Optionally set a ``dtype`` for the final face to be formatted in. Default: ``None``

        Notes
        -----
        This method must be executed to get access to the following `properties`:
            - :func:`original_roi`
            - :func:`aligned_landmarks`
            - :func:`aligned_face`
            - :func:`adjusted_interpolators`
        """
        if self.aligned:
            # Don't reload an already aligned face
            logger.trace("Skipping alignment calculation for already aligned face")
        else:
            logger.trace("Loading aligned face: (size: %s, dtype: %s)", size, dtype)
            self.aligned["size"] = size
            self.aligned["padding"] = self._padding_from_coverage(size, coverage_ratio)
            self.aligned["matrix"] = get_align_mat(self)
            self.aligned["face"] = None
        if image is not None and self.aligned["face"] is None:
            logger.trace("Getting aligned face")
            face = AlignerExtract().transform(
                image,
                self.aligned["matrix"],
                size,
                self.aligned["padding"])
            self.aligned["face"] = face if dtype is None else face.astype(dtype)

        logger.trace("Loaded aligned face: %s", {k: str(v) if isinstance(v, np.ndarray) else v
                                                 for k, v in self.aligned.items()
                                                 if k != "face"})

    @staticmethod
    def _padding_from_coverage(size, coverage_ratio):
        """ Return the image padding for a face from coverage_ratio set against a
            pre-padded training image """
        padding = int((size * (coverage_ratio - 0.625)) / 2)
        logger.trace(padding)
        return padding

    def load_feed_face(self, image, size=64, coverage_ratio=0.625, dtype=None):
        """ Align a face in the correct dimensions for feeding into a model.

        Parameters
        ----------
        image: numpy.ndarray
            The image that contains the face to be aligned
        size: int
            The size of the face in pixels to be fed into the model
        coverage_ratio: float, optional
            the ratio of the extracted image that was used for training. Default: `0.625`
        dtype: str, optional
            Optionally set a ``dtype`` for the final face to be formatted in. Default: ``None``

        Notes
        -----
        This method must be executed to get access to the following `properties`:
            - :func:`feed_face`
            - :func:`feed_interpolators`
        """
        logger.trace("Loading feed face: (size: %s, coverage_ratio: %s, dtype: %s)",
                     size, coverage_ratio, dtype)

        self.feed["size"] = size
        self.feed["padding"] = self._padding_from_coverage(size, coverage_ratio)
        self.feed["matrix"] = get_align_mat(self)

        face = AlignerExtract().transform(image,
                                          self.feed["matrix"],
                                          size,
                                          self.feed["padding"])
        self.feed["face"] = face if dtype is None else face.astype(dtype)

        logger.trace("Loaded feed face. (face_shape: %s, matrix: %s)",
                     self.feed_face.shape, self.feed_matrix)

    def load_reference_face(self, image, size=64, coverage_ratio=0.625, dtype=None):
        """ Align a face in the correct dimensions for reference against the output from a model.

        Parameters
        ----------
        image: numpy.ndarray
            The image that contains the face to be aligned
        size: int
            The size of the face in pixels to be fed into the model
        coverage_ratio: float, optional
            the ratio of the extracted image that was used for training. Default: `0.625`
        dtype: str, optional
            Optionally set a ``dtype`` for the final face to be formatted in. Default: ``None``

        Notes
        -----
        This method must be executed to get access to the following `properties`:
            - :func:`reference_face`
            - :func:`reference_landmarks`
            - :func:`reference_matrix`
            - :func:`reference_interpolators`
        """
        logger.trace("Loading reference face: (size: %s, coverage_ratio: %s, dtype: %s)",
                     size, coverage_ratio, dtype)

        self.reference["size"] = size
        self.reference["padding"] = self._padding_from_coverage(size, coverage_ratio)
        self.reference["matrix"] = get_align_mat(self)

        face = AlignerExtract().transform(image,
                                          self.reference["matrix"],
                                          size,
                                          self.reference["padding"])
        self.reference["face"] = face if dtype is None else face.astype(dtype)

        logger.trace("Loaded reference face. (face_shape: %s, matrix: %s)",
                     self.reference_face.shape, self.reference_matrix)

    @property
    def original_roi(self):
        """ numpy.ndarray: The location of the extracted face box within the original frame.
        Only available after :func:`load_aligned` has been called, otherwise returns ``None``"""
        if not self.aligned:
            return None
        roi = AlignerExtract().get_original_roi(self.aligned["matrix"],
                                                self.aligned["size"],
                                                self.aligned["padding"])
        logger.trace("Returning: %s", roi)
        return roi

    @property
    def aligned_landmarks(self):
        """ numpy.ndarray: The 68 point landmarks location transposed to the extracted face box.
        Only available after :func:`load_aligned` has been called, otherwise returns ``None``"""
        if not self.aligned:
            return None
        landmarks = AlignerExtract().transform_points(self.landmarks_xy,
                                                      self.aligned["matrix"],
                                                      self.aligned["size"],
                                                      self.aligned["padding"])
        logger.trace("Returning: %s", landmarks)
        return landmarks

    @property
    def aligned_face(self):
        """ numpy.ndarray: The aligned detected face. Only available after :func:`load_aligned`
        has been called with an image, otherwise returns ``None`` """
        return self.aligned.get("face", None)

    @property
    def _adjusted_matrix(self):
        """ numpy.ndarray: Adjusted matrix for size/padding combination. Only available after
        :func:`load_aligned` has been called, otherwise returns ``None``"""
        if not self.aligned:
            return None
        mat = AlignerExtract().transform_matrix(self.aligned["matrix"],
                                                self.aligned["size"],
                                                self.aligned["padding"])
        logger.trace("Returning: %s", mat)
        return mat

    @property
    def adjusted_interpolators(self):
        """ tuple:  Tuple of (`interpolator` and `reverse interpolator`) for the adjusted matrix.
        Only available after :func:`load_aligned` has been called, otherwise returns ``None``"""
        if not self.aligned:
            return None
        return get_matrix_scaling(self._adjusted_matrix)

    @property
    def feed_face(self):
        """ numpy.ndarray: The aligned face sized for feeding into a model. Only available after
        :func:`load_feed_face` has been called with an image, otherwise returns ``None`` """
        if not self.feed:
            return None
        return self.feed["face"]

    @property
    def feed_landmarks(self):
        """ numpy.ndarray: The 68 point landmarks location transposed to the feed face box.
        Only available after :func:`load_reference_face` has been called, otherwise returns
        ``None``"""
        if not self.feed:
            return None
        landmarks = AlignerExtract().transform_points(self.landmarks_xy,
                                                      self.feed["matrix"],
                                                      self.feed["size"],
                                                      self.feed["padding"])
        logger.trace("Returning: %s", landmarks)
        return landmarks

    @property
    def feed_matrix(self):
        """ numpy.ndarray: The adjusted matrix face sized for feeding into a model. Only available
        after :func:`load_feed_face` has been called with an image, otherwise returns ``None`` """
        if not self.feed:
            return None
        mat = AlignerExtract().transform_matrix(self.feed["matrix"],
                                                self.feed["size"],
                                                self.feed["padding"])
        logger.trace("Returning: %s", mat)
        return mat

    @property
    def feed_interpolators(self):
        """ tuple:  Tuple of (`interpolator` and `reverse interpolator`) for the adjusted feed
        matrix. Only available after :func:`load_feed_face` has been called, otherwise returns
        ``None``"""
        if not self.feed:
            return None
        return get_matrix_scaling(self.feed_matrix)

    @property
    def reference_face(self):
        """ numpy.ndarray: The aligned face sized for reference against a face coming out of a
        model. Only available after :func:`load_reference_face` has been called, otherwise
        returns ``None``"""
        if not self.reference:
            return None
        return self.reference["face"]

    @property
    def reference_landmarks(self):
        """ numpy.ndarray: The 68 point landmarks location transposed to the reference face box.
        Only available after :func:`load_reference_face` has been called, otherwise returns
        ``None``"""
        if not self.reference:
            return None
        landmarks = AlignerExtract().transform_points(self.landmarks_xy,
                                                      self.reference["matrix"],
                                                      self.reference["size"],
                                                      self.reference["padding"])
        logger.trace("Returning: %s", landmarks)
        return landmarks

    @property
    def reference_matrix(self):
        """ numpy.ndarray: The adjusted matrix face sized for refence against a face coming out of
         a model. Only available after :func:`load_reference_face` has been called, otherwise
         returns ``None``"""
        if not self.reference:
            return None
        mat = AlignerExtract().transform_matrix(self.reference["matrix"],
                                                self.reference["size"],
                                                self.reference["padding"])
        logger.trace("Returning: %s", mat)
        return mat

    @property
    def reference_interpolators(self):
        """ tuple:  Tuple of (`interpolator` and `reverse interpolator`) for the reference
        matrix. Only available after :func:`load_reference_face` has been called, otherwise
        returns ``None``"""
        if not self.reference:
            return None
        return get_matrix_scaling(self.reference_matrix)


class Mask():
    """ Face Mask information and convenience methods

    Holds a Faceswap mask as generated from :mod:`plugins.extract.mask` and the information
    required to transform it to its original frame.

    Holds convenience methods to handle the warping, storing and retrieval of the mask.

    Parameters
    ----------
    storage_size: int, optional
        The size (in pixels) that the mask should be stored at. Default: 128.

    Attributes
    ----------
    stored_size: int
        The size, in pixels, of the stored mask across its height and width.
    """

    def __init__(self, storage_size=128):
        self.stored_size = storage_size

        self._mask = None
        self._affine_matrix = None
        self._frame_dims = None
        self._intepolator = None

    @property
    def mask(self):
        """ numpy.ndarray: The mask at the size of :attr:`stored_size` """
        return np.frombuffer(decompress(self._mask),
                             dtype="uint8").reshape((self.stored_size, self.stored_size, 1))

    @property
    def full_frame_mask(self):
        """ numpy.ndarray: The mask affined to the original full frame """
        frame = np.zeros(self._frame_dims + (1, ), dtype="uint8")
        mask = cv2.warpAffine(self.mask,
                              self._affine_matrix,
                              self._frame_dims,
                              frame,
                              flags=cv2.WARP_INVERSE_MAP | self._intepolator,
                              borderMode=cv2.BORDER_CONSTANT)
        logger.trace("mask shape: %s, mask dtype: %s, mask min: %s, mask max: %s",
                     mask.shape, mask.dtype, mask.min(), mask.max())
        return mask

    def add(self, mask, affine_matrix, frame_dims, interpolator):
        """ Add a Faceswap mask to this :class:`Mask`.

        The mask should be the original output from  :mod:`plugins.extract.mask`

        Parameters
        ----------
        mask: numpy.ndarray
            The mask that is to be added as output from :mod:`plugins.extract.mask`
            It should be in the range 0.0 - 1.0 ideally with a ``dtype`` of ``float32``
        affine_matrix: numpy.ndarray
            The transformation matrix required to transform the mask to the original frame.
        frame_dims: tuple
            The `(height, width)` dimensions of the original frame that this mask was created from.
        interpolator:
            The CV2 interpolator required to transform this mask to it's original frame
        """
        logger.trace("mask shape: %s, mask dtype: %s, mask min: %s, mask max: %s, "
                     "affine_matrix: %s, frame_dims: %s, interpolator: %s", mask.shape, mask.dtype,
                     mask.min(), mask.max(), affine_matrix, frame_dims, interpolator)
        self._affine_matrix = self._adjust_affine_matrix(mask.shape[0], affine_matrix)
        self._frame_dims = frame_dims
        self._intepolator = interpolator
        mask = (cv2.resize(mask,
                           (self.stored_size, self.stored_size),
                           interpolation=cv2.INTER_AREA) * 255.0).astype("uint8")
        self._mask = compress(mask)

    def _adjust_affine_matrix(self, mask_size, affine_matrix):
        """ Adjust the affine matrix for the mask's storage size

        Parameters
        ----------
        mask_size: int
            The original size of the mask.
        affine_matrix: numpy.ndarray
            The affine matrix to transform the mask at original size to the parent frame.

        Returns
        -------
        affine_matrix: numpy,ndarray
            The affine matrix adjusted for the mask at its stored dimensions.
        """
        zoom = self.stored_size / mask_size
        zoom_mat = np.array([[zoom, 0, 0.], [0, zoom, 0.]])
        adjust_mat = np.dot(zoom_mat, np.concatenate((affine_matrix, np.array([[0., 0., 1.]]))))
        logger.trace("storage_size: %s, mask_size: %s, zoom: %s, original matrix: %s, "
                     "adjusted_matrix: %s", self.stored_size, mask_size, zoom, affine_matrix.shape,
                     adjust_mat.shape)
        return adjust_mat

    def to_dict(self):
        """ Convert the mask to a dictionary for saving to an alignments file

        Returns
        -------
        dict:
            The :class:`Mask` for saving to an alignments file. Contains the keys ``mask``,
            ``affine_matrix``, ``frame_dims``, ``intepolator``, ``stored_size``
        """
        retval = dict()
        for key in ("mask", "affine_matrix", "frame_dims", "intepolator", "stored_size"):
            retval[key] = getattr(self, self._attr_name(key))
        logger.trace({k: v if k != "mask" else type(v) for k, v in retval.items()})
        return retval

    def from_dict(self, mask_dict):
        """ Populates the :class:`Mask` from a dictionary loaded from an alignments file.

        Parameters
        ----------
        mask_dict: dict
            A dictionary stored in an alignments file containing the keys ``mask``,
            ``affine_matrix``, ``frame_dims``, ``intepolator``, ``stored_size``
        """
        for key in ("mask", "affine_matrix", "frame_dims", "intepolator", "stored_size"):
            setattr(self, self._attr_name(key), mask_dict[key])
            logger.trace("{} - {}", key, mask_dict[key] if key != "mask" else type(mask_dict[key]))

    @staticmethod
    def _attr_name(dict_key):
        """ The :class:`Mask` attribute name for the given dictionary key

        Parameters
        ----------
        dict_key: str
            The key name from an alignments dictionary

        Returns
        -------
        attribute_name: str
            The attribute name for the given key for :class:`Mask`
        """
        retval = "_{}".format(dict_key) if dict_key != "stored_size" else dict_key
        logger.trace("dict_key: %s, attribute_name: %s", dict_key, retval)
        return retval


def rotate_landmarks(face, rotation_matrix):
    """ Rotates the 68 point landmarks and detection bounding box around the given rotation matrix.

    Parameters
    ----------
    face: DetectedFace or dict
        A :class:`DetectedFace` or an `alignments file` ``dict`` containing the 68 point landmarks
        and the `x`, `w`, `y`, `h` detection bounding box points.
    rotation_matrix: numpy.ndarray
        The rotation matrix to rotate the given object by.

    Returns
    -------
    DetectedFace or dict
        The rotated :class:`DetectedFace` or `alignments file` ``dict`` with the landmarks and
        detection bounding box points rotated by the given matrix. The return type is the same as
        the input type for ``face``
    """
    logger.trace("Rotating landmarks: (rotation_matrix: %s, type(face): %s",
                 rotation_matrix, type(face))
    rotated_landmarks = None
    # Detected Face Object
    if isinstance(face, DetectedFace):
        bounding_box = [[face.x, face.y],
                        [face.x + face.w, face.y],
                        [face.x + face.w, face.y + face.h],
                        [face.x, face.y + face.h]]
        landmarks = face.landmarks_xy

    # Alignments Dict
    elif isinstance(face, dict) and "x" in face:
        bounding_box = [[face.get("x", 0), face.get("y", 0)],
                        [face.get("x", 0) + face.get("w", 0),
                         face.get("y", 0)],
                        [face.get("x", 0) + face.get("w", 0),
                         face.get("y", 0) + face.get("h", 0)],
                        [face.get("x", 0),
                         face.get("y", 0) + face.get("h", 0)]]
        landmarks = face.get("landmarks_xy", list())

    else:
        raise ValueError("Unsupported face type")

    logger.trace("Original landmarks: %s", landmarks)

    rotation_matrix = cv2.invertAffineTransform(
        rotation_matrix)
    rotated = list()
    for item in (bounding_box, landmarks):
        if not item:
            continue
        points = np.array(item, np.int32)
        points = np.expand_dims(points, axis=0)
        transformed = cv2.transform(points,
                                    rotation_matrix).astype(np.int32)
        rotated.append(transformed.squeeze())

    # Bounding box should follow x, y planes, so get min/max
    # for non-90 degree rotations
    pt_x = min([pnt[0] for pnt in rotated[0]])
    pt_y = min([pnt[1] for pnt in rotated[0]])
    pt_x1 = max([pnt[0] for pnt in rotated[0]])
    pt_y1 = max([pnt[1] for pnt in rotated[0]])
    width = pt_x1 - pt_x
    height = pt_y1 - pt_y

    if isinstance(face, DetectedFace):
        face.x = int(pt_x)
        face.y = int(pt_y)
        face.w = int(width)
        face.h = int(height)
        face.r = 0
        if len(rotated) > 1:
            rotated_landmarks = [tuple(point) for point in rotated[1].tolist()]
            face.landmarks_xy = rotated_landmarks
    else:
        face["left"] = int(pt_x)
        face["top"] = int(pt_y)
        face["right"] = int(pt_x1)
        face["bottom"] = int(pt_y1)
        rotated_landmarks = face

    logger.trace("Rotated landmarks: %s", rotated_landmarks)
    return face
