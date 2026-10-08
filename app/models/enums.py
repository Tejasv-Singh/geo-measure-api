from enum import StrEnum


class FileStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementKind(StrEnum):
    AREA = "AREA"
    LENGTH = "LENGTH"
    NONE = "NONE"


class MeasurementStatus(StrEnum):
    OK = "OK"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    EMPTY = "EMPTY"
