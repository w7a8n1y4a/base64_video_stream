from enum import Enum


class EncoderAction(Enum):
    LEFT = "Left"
    RIGHT = "Right"
    ONE = "One"
    DOUBLE = "Double"
    LONG = "Long"


class VideoStatus(Enum):
    READY = "Ready"
    PROCESSING = "Processing"
    PENDING = "Pending"
    ERROR = "Error"
    WARNING = "Warning"
